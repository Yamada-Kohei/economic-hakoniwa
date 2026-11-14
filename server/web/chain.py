"""産業の鎖の画面の中身の組み立て。見える施設と国全体の統計を誰にでも見せる。

品目ごとに、作った量・施設が使った量・市場で売買された量・輸入と輸出の量・値段の動きと、島で作る
施設と使う施設を並べ、品不足の品目を目立たせます。売りに出ているのに買い手の指値と合わずに売れ残った
品目は、品不足と分けてそう出します。止まっている工場は、待っているもの（材料・電力・
働き手・技術者・売れない）ごとにまとめます。ルールの計算はせず、保存した統計（StatRecord）・市場の
記録（MarketRecord）・約定（Trade）・施設の状態だけを使います。

輸入は、商社と海外の商人が島の市場で売った量、輸出は商社が島の市場で買った量です（二国間の契約で
国の倉庫に直に届く荷は含みません）。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import stops
from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.modules.records import shortfall
from hakoniwa_core.naming import facility_name
from server.economy.models import MarketRecord, Trade
from server.game import get_game
from server.web import display
from server.world import models as m
from server.world.repository import facility_from_row

YEAR = TURNS_PER_YEAR  # 値段の動きを比べるターン数（1年）
STREAK_TURNS = 12  # 品不足の続いたターン数を数える上限
TIERS = [(1, "原料"), (2, "中間財"), (3, "最終財")]
MERCHANT = "merchant"  # 海外の商人（約定の記録の売り手）

# 止まっている工場のわけの分け方は、理由の表（hakoniwa_core/stops.py）の組（W-34）。工事は別
CAUSE_NAMES = {
    stops.INPUTS: "材料を待っている",
    stops.G_POWER: "電力を待っている",
    stops.G_FUNDS: "お金が足りない",
    stops.G_WORKERS: "働き手が足りない",
    stops.G_ENGINEERS: "技術者が足りない",
    stops.G_EQUIPMENT: "設備が傷んでいる",
    stops.SALES: "売れ行きに合わせて減らしている",
    stops.G_HALTED: "災害で止まっている",
    stops.WAR: "戦争・暴動で止まっている",
    stops.OTHER: "生産方式の切り替え・その他",
    "construction": "工事が資材や資金を待っている",
}
CAUSE_ORDER = [g for g in stops.GROUPS if g in CAUSE_NAMES] + ["construction"]


def _reason(config, f) -> stops.Reason | None:
    """止まり・調整の一覧に出す理由の行。準備中・扱う荷や客に合わせた操業・理由なしは出さない。"""
    if not f.status:
        return None
    row = stops.show(config, f.status, f.ext)
    return row if row.stop_mark else None


def chain_view(
    island: m.Island,
    stats: dict[str, float],
    currency: str,
    mine: bool = False,
    viewer: int | None = None,
) -> dict[str, Any]:
    """品目の段ごとの品目と、見える施設だけの作り手・使い手・止まっている工場のまとめ。"""
    game = get_game()
    defs, config = game.defs, game.config
    power = config.get("power.good")
    share = config.get("records.shortage.share")
    state = display.island_for_naming(island)
    rows = [
        r
        for r in island.facilities.order_by("number")
        if r.kind in defs.facilities and display.sees_facility(r, mine, viewer)
    ]
    facilities = [(r, facility_from_row(r)) for r in rows]
    # 自動の施設名に付く同種の番号からも、見えない施設の存在を漏らさない。
    state.facilities = [f for _, f in facilities]
    traders = {f"fac:{r.number}" for r in rows if r.kind == config.get("trade.trader_facility")}

    makers: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    users: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    inputs: dict[str, set[str]] = defaultdict(set)
    for row, f in facilities:
        if fac.is_housing(config, f):
            continue
        recipe = fac.recipe_of(defs, f)
        why = _reason(config, f) if row.built else None
        stopped = why is not None and why.group != stops.SALES
        for table, goods in ((makers, recipe["outputs"]), (users, recipe["inputs"])):
            for good in goods:
                counts = table[good][row.kind]
                counts[0 if row.built else 2] += 1
                counts[1] += 1 if stopped else 0
        for good in recipe["outputs"]:
            inputs[good] |= set(recipe["inputs"]) - {power}

    turn = (
        MarketRecord.objects.filter(island=island)
        .order_by("-turn")
        .values_list("turn", flat=True)
        .first()
    )
    records: dict[str, dict[int, MarketRecord]] = defaultdict(dict)
    imported: dict[str, int] = defaultdict(int)
    exported: dict[str, int] = defaultdict(int)
    if turn is not None:
        recent = MarketRecord.objects.filter(
            island=island, turn__gte=turn - max(YEAR, STREAK_TURNS), turn__lte=turn
        )
        for r in recent:
            records[r.good][r.turn] = r
        for good, buyer, seller, qty in Trade.objects.filter(island=island, turn=turn).values_list(
            "good", "buyer", "seller", "quantity"
        ):
            if seller in traders or seller == MERCHANT:
                imported[good] += qty
            if buyer in traders:
                exported[good] += qty

    def short(record: MarketRecord | None) -> bool:
        if record is None or record.demand <= 0:
            return False
        # 売れ残りのある分は品不足に数えない（ターンログの知らせと同じ見方。records.shortfall）
        return shortfall(record) >= record.demand * share

    goods = []
    for good in display.goods_in_order(defs):
        gdef = defs.goods[good]
        by_turn = records.get(good, {})
        now = by_turn.get(turn) if turn is not None else None
        made = stats.get(f"production.{good}", 0.0)
        used = stats.get("power.used", 0.0) if good == power else stats.get(f"use.{good}", 0.0)
        if not (made or used or now or makers.get(good) or users.get(good)):
            continue
        streak = 0
        while turn is not None and streak < STREAK_TURNS and short(by_turn.get(turn - streak)):
            streak += 1
        row: dict[str, Any] = {
            "good": good,
            "name": defs.name("goods", good),
            "unit": defs.unit_name(good),
            "tier": gdef.get("tier", 3),
            "made": display.shown(good, made),
            "used": display.shown(good, used),
            "imported": display.amount(imported.get(good, 0), good),
            "exported": display.amount(exported.get(good, 0), good),
            "short": streak > 0,
            "streak": streak,
            "makers": _kinds(defs, makers.get(good, {})),
            "users": _kinds(defs, users.get(good, {})),
            "inputs": [defs.name("goods", g) for g in sorted(inputs.get(good, ()))],
        }
        if now is not None:
            row.update(
                price=display.price_text(good, now.price, currency),
                volume=display.amount(now.volume, good),
                demand=display.amount(now.demand, good),
                bought=round(now.volume * 100 / now.demand) if now.demand else None,
                # 売りも買いも残った: 売りの値段が買い手の指値より高くて約定しなかった
                unmatched=now.demand > now.volume and now.supply > now.volume,
                supply=display.amount(now.supply, good),
            )
            before = by_turn.get(turn - YEAR)
            if before is not None and before.price:
                change = round((now.price / before.price - 1) * 100)
                row["change"] = f"{change:+d}%"
        goods.append(row)
    tiers = [
        {"tier": t, "name": name, "goods": [g for g in goods if g["tier"] == t]}
        for t, name in TIERS
    ]
    return {
        "turn": turn,
        "tiers": [t for t in tiers if t["goods"]],
        "short": [g for g in goods if g["short"]],
        "stalled": _stalled(defs, config, state, facilities),
        "share": round(share * 100),
    }


def _kinds(defs, counts: dict[str, list[int]]) -> list[dict[str, Any]]:
    """施設の種類ごとの数（建った数・止まっている数・工事中の数）。"""
    return [
        {
            "kind": kind,
            "name": defs.name("facilities", kind),
            "built": built,
            "stopped": stopped,
            "building": building,
        }
        for kind, (built, stopped, building) in sorted(counts.items())
    ]


def _stalled(defs, config, state, facilities) -> list[dict[str, Any]]:
    """止まっている工場と工事を、待っているものごとに。"""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row, f in facilities:
        if fac.is_housing(config, f):
            continue
        if not row.built:
            if not any(f.ext.get(k) for k in ("waiting", "short_wages", "no_workers_turns")):
                continue
            cause, good = "construction", ""
        else:
            why = _reason(config, f)
            if why is None:
                continue
            cause, good = why.group, stops.short_good(row.status) if why.tail == stops.GOOD else ""
        groups[cause].append(
            {
                "number": row.number,
                "name": facility_name(defs, config, state, f),
                "kind_name": display.kind_label(defs, f.kind, f.recipe),
                "x": row.x,
                "y": row.y,
                "on_map": fac.on_map(f),
                "good": good,
                "status_text": display.status_text(f),
                "rate": round(row.operating_rate / 10) if row.built else None,
            }
        )
    return [
        {"cause": c, "name": CAUSE_NAMES[c], "rows": groups[c]}
        for c in CAUSE_ORDER
        if groups.get(c)
    ]
