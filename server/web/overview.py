"""国の要約と、前のターンからの差分（AI が遊ぶための入り口。GET /api/islands/<id>/summary と
changes）。

画面を何枚も読まなくても、国の今の状態と、あるターンより後に起きたことを1回で読めるようにする。
中身は画面と同じ値を集めて短くしただけで、新しい計算や先の見込みは出さない。見せる範囲も画面と同じ
（施設の帳簿・命令の列・公電は島の持ち主だけ。伏せる命令はほかの国の人に見せない）。
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from django.db.models import Max, Q
from django.urls import reverse

from hakoniwa_core import exploration
from hakoniwa_core import facilities as fac
from hakoniwa_core import politics as core_politics
from hakoniwa_core.economy import books, is_seized, is_stopped, power_fuel_notice, retail
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.modules.population import still_hungry
from hakoniwa_core.naming import facility_name
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
    OWNER_STATE,
    ZONE_INDUSTRIAL,
    IslandState,
    Tile,
)
from hakoniwa_core.trade import island_state as trade_state
from hakoniwa_core.trade import merchant
from server.accounts import keys
from server.board import services as board
from server.economy.models import FacilityBook
from server.game import get_game
from server.hall import services as hall
from server.letters import services as letters
from server.orders.models import Order
from server.turns.models import TurnLog
from server.web import country, diplomacy, display, military, rankings, reports, slowdown, todo
from server.web import politics as politics_views
from server.web.todo import Alert
from server.web.trade import food_merchant_view
from server.world import models as m
from server.world.repository import TILE_FIELDS, facility_from_row, island_from_row

LIST_MAX = 30  # 要約の一覧（止まった施設・工事・赤字の施設）に出す数
BONDS_DUE_TURNS = 12  # このターン数のうちに満期になる国債を出す
LOG_TURNS_MAX = 24  # 差分とターンログで1回に返すターン数
GUIDE_TURNS = 12  # 建国からこのターン数の間、持ち主に「次にすること」と画面の使い方を出す
NEXT_STEPS_MAX = 3  # 「次にすること」に出す数


def _currency(island: m.Island) -> str:
    return (island.names or {}).get("currency_symbol", "")


def _stats_at(island: m.Island, turn: int | None) -> dict[str, float]:
    """ターン turn（なければそれより前のいちばん近いターン）の統計。"""
    if turn is None:
        return {}
    at = island.stats.filter(turn__lte=turn).aggregate(t=Max("turn"))["t"]
    if at is None:
        return {}
    return reports.with_fiscal(dict(island.stats.filter(turn=at).values_list("name", "value")))


INDICATORS = [key for key, _, _ in display.SUMMARY_STATS] + ["fiscal.balance"]

# 比べ方・目安の幅・行き先。割合の統計は 0〜1、幸福度は画面に出す数のまま。
NOTABLE = {
    "population": ("relative", 0.01, "economy"),
    "unemployment": ("points", todo.UNEMPLOYMENT_RISE, "facilities"),
    "food.ratio": ("points", 0.05, "market"),
    "power.shortage": ("points", 0.05, "facilities"),
    "happiness": ("points", 3, "economy"),
    "gdp": ("relative", 0.05, "economy"),
    "inflation": ("points", 0.02, "economy"),
    "treasury": ("balance", 0.10, "fiscal"),
    "debt": ("balance", 0.10, "fiscal"),
    "firms.bankrupt": ("points", 1, "facilities"),
}


def notable_changes(now: dict[str, float], before: dict[str, float], currency: str) -> list[dict]:
    """観測済みの指標の変化を目安の幅で比べ、大きい順に3件まで返す（UI-62）。"""
    ranked = []
    for key, (mode, width, page) in NOTABLE.items():
        if key not in now or key not in before:
            continue
        change = abs(now[key] - before[key])
        if not change:
            continue
        base = 1
        if mode == "relative":
            base = abs(before[key])
        elif mode == "balance":
            base = max(abs(before[key]), abs(now.get("gdp", 0)))
        threshold = width * base
        size = change / threshold if threshold else math.inf
        if size < 1 and not math.isclose(size, 1, rel_tol=1e-12):
            continue
        label, unit = reports.stat_label(key)
        ranked.append(
            (
                size,
                {
                    "key": key,
                    "label": label,
                    "before": display.stat_text(key, before[key], unit, currency),
                    "now": display.stat_text(key, now[key], unit, currency),
                    "page": page,
                },
            )
        )
    ranked.sort(key=lambda row: -row[0])
    return [row for _, row in ranked[:3]]


def indicators(now: dict[str, float], before: dict[str, float], currency: str) -> list[dict]:
    """主な指標（島の画面の国勢と財政の収支）の今の値の文と、before から変わったものは前の値の文。

    値は画面と同じ文（「22%」「¤4,429,700」）。数の系列は GET /api/islands/<id>/economy で読める。
    """
    rows = []
    for key in INDICATORS:
        if key not in now:
            continue
        label, unit = reports.stat_label(key)
        row = {"key": key, "label": label, "now": display.stat_text(key, now[key], unit, currency)}
        if key in before and before[key] != now[key]:
            row["before"] = display.stat_text(key, before[key], unit, currency)
        rows.append(row)
    return rows


def island_head(island: m.Island) -> dict[str, Any]:
    """島の名前・持ち主・体制・資源など（島の画面の見出しと同じ）。"""
    defs = get_game().defs
    zone = (island.ext or {}).get("climate")
    return {
        "id": island.id,
        "name": island.name,
        "owner": island.owner.username if island.owner else "",
        "ai": keys.is_ai_island(island),
        "capital": (island.names or {}).get("capital", ""),
        "currency": (island.names or {}).get("currency", ""),
        "currency_symbol": _currency(island),
        "leader": (island.names or {}).get("leader", ""),
        "regime": {
            defs.regimes[r]["axis"]: defs.name("regimes", r)
            for r in (island.regime or {}).values()
            if r in defs.regimes
        },
        "resources": [defs.name("goods", g) for g in island.resources or []],
        "resource_ids": list(island.resources or []),
        # 地下資源から作れる財（島の画面の建設の欄で、作れない施設を見分けるのと同じ）
        "producible": sorted(fac.producible_goods(defs, island.resources or [])),
        "climate": defs.name("climate", zone) if zone else "",
        "founded_turn": (island.ext or {}).get("founded_turn", 0),
        # 探鉱（MI-68）: 探せる資源・探鉱中の資源と進み・探し終えた資源
        "exploration": exploration_brief(island),
    }


def exploration_brief(island: m.Island) -> dict[str, Any] | None:
    """探鉱のようす（MI-68。島の画面の見出しと国の要約で同じ）。お金の経済の前の島は None。

    explorable は探鉱を始められる資源と、探鉱隊を置ける地形（その資源を掘る施設の建つ地形）。"""
    game = get_game()
    defs, config = game.defs, game.config
    state = island_from_row(island)
    if not state.has_economy:
        return None
    for x, y, *rest in island.tiles.values_list("x", "y", *TILE_FIELDS):
        state.tiles[y * state.size + x] = Tile(x, y, **dict(zip(TILE_FIELDS, rest, strict=True)))
    kind = exploration.facility_kind(config)
    state.facilities = [facility_from_row(f) for f in island.facilities.filter(kind=kind)]
    now = exploration.status(state, defs, config)
    current = now["current"]
    if current is not None:
        current = {
            "good": current["good"],
            "name": defs.name("goods", current["good"]),
            "x": current["x"],
            "y": current["y"],
            "built": current["built"],
            "percent": round(current["progress"] * 100),
        }
    done = now["explored"]
    return {
        "explorable": [
            {
                "good": g,
                "name": defs.name("goods", g),
                "terrains": sorted(exploration.miner_terrains(defs, g)),
            }
            for g in now["explorable"]
        ],
        "current": current,
        "found": [defs.name("goods", g) for g, r in done.items() if r == exploration.FOUND],
        "none": [defs.name("goods", g) for g, r in done.items() if r == exploration.NONE],
        "turns": now["turns"],
    }


def _facility_rows(island: m.Island) -> list[m.Facility]:
    housing = fac.housing_kind(get_game().config)
    return list(island.facilities.exclude(kind=housing).order_by("number"))


def facility_state(
    island: m.Island, mine: bool = True, viewer: int | None = None
) -> dict[str, Any]:
    """止まった施設、満たない稼働のわけごとの数、工事中の施設、稼働準備中（まだ動いたことがない）の
    施設の数（地図の施設の状態と同じ。誰にでも。隠した施設は持ち主と見つけた国だけ。MI-62）。
    持ち主には、生産調整中の施設ごとの目標を下げたわけと打てる手（W-250）も出す（止まった施設の
    行にも slowdown を付ける）。"""
    game = get_game()
    state = display.island_for_naming(island)
    facts = slowdown.Facts(island, state) if mine else None
    stopped, works, slowed = [], [], []
    reasons: dict[str, int] = defaultdict(int)
    rows = [r for r in _facility_rows(island) if display.sees_facility(r, mine, viewer)]
    starting = 0
    on_sale = []
    currency = (state.names or {}).get("currency_symbol", "")
    for row in rows:
        f = facility_from_row(row)
        text = display.status_text(f)
        sale = display.sale_text(row, f, state, currency) if row.built and island.economy else ""
        if sale:  # 管財人のもと・閉鎖に入り売りに出している施設（FM-59・FM-60）
            on_sale.append(
                {
                    "number": row.number,
                    "name": facility_name(game.defs, game.config, state, f),
                    "kind_name": display.facility_label(f),
                    "x": fac.position(f)[0],
                    "y": fac.position(f)[1],
                    "sale_text": sale,
                }
            )
        x, y = fac.position(f)  # 町の中の店は町のマス（施設の行の位置は負）
        if row.built and row.status == fac.STATUS_STARTING:
            # 工事が済んでまだ動いたことがない。次の更新で動き出すので止まりと分ける（W-219）
            starting += 1
            continue
        if not row.built:
            works.append(
                {
                    "kind_name": display.facility_label(f),
                    "x": x,
                    "y": y,
                    "progress": round(row.progress / 1000, 1),
                    "turns": game.defs.facilities.get(row.kind, {})
                    .get("construction", {})
                    .get("turns"),
                    "status_text": text,
                }
            )
            continue
        why = slowdown.explain(facts, f) if facts is not None else None
        if row.operating_rate <= 0 and row.status:  # 地図の止まった印（赤い点）と同じ
            stopped.append(
                {
                    "number": row.number,
                    "name": facility_name(game.defs, game.config, state, f),
                    "kind_name": display.facility_label(f),
                    "x": x,
                    "y": y,
                    "status_text": text,
                    # 国営か民営か（持ち主だけ。市場の画面の止まった施設と同じ。W-225）
                    **(
                        {
                            "owner_name": display.message(f"owner_{f.owner}"),
                            "hint": why["hint"] if why and why["hint"] else display.status_hint(f),
                        }
                        if mine
                        else {}
                    ),
                    **({"slowdown": why} if why is not None else {}),
                }
            )
        elif row.operating_rate < fac.FULL and text:
            reasons[text] += 1
            if why is not None:  # 生産調整中（作り続けている）のわけ
                slowed.append(
                    {
                        "number": row.number,
                        "name": facility_name(game.defs, game.config, state, f),
                        "kind_name": game.defs.name("facilities", row.kind),
                        "x": x,
                        "y": y,
                        "operating_rate": round(row.operating_rate * 100 / fac.FULL),
                        "slowdown": why,
                    }
                )
    infra = (island.economy or {}).get("infra", {})
    return {
        "facilities": len(rows),
        "stopped_count": len(stopped),
        "starting_count": starting,
        "stopped": stopped[:LIST_MAX],
        "partial": [
            {"status_text": t, "count": n} for t, n in sorted(reasons.items(), key=lambda r: -r[1])
        ],
        **({"slowdown_count": len(slowed), "slowdown": slowed[:LIST_MAX]} if mine else {}),
        "on_sale_count": len(on_sale),
        "on_sale": on_sale[:LIST_MAX],
        "construction_count": len(works),
        "construction": works[:LIST_MAX],
        "infra_works": len(infra.get("queue", [])),
        "infra_why": display.works_why(island.economy),
    }


def losing_facilities(island: m.Island, currency: str) -> list[dict[str, Any]]:
    """直近の数ターン（施設の画面の損益の合計と同じターン数）の損益の合計が赤字の、独立採算の施設
    （持ち主だけ）。国庫直轄の施設（省庁・学校など）は収入を持たないので数えない。"""
    game = get_game()
    last = FacilityBook.objects.filter(island=island).aggregate(turn=Max("turn"))["turn"]
    if last is None:
        return []
    totals: dict[int, int] = defaultdict(int)
    for number, items in FacilityBook.objects.filter(
        island=island, turn__gt=last - display.BOOK_TURNS
    ).values_list("facility", "items"):
        totals[number] += (items or {}).get(books.PROFIT, 0)
    losing = sorted((v, n) for n, v in totals.items() if v < 0)
    rows = {f.number: f for f in island.facilities.filter(number__in=[n for _, n in losing])}
    state = display.island_for_naming(island)
    out = []
    for value, number in losing:
        row = rows.get(number)
        if row is None or row.accounting != ACCOUNTING_SELF:  # 取り壊した施設と国庫直轄
            continue
        f = facility_from_row(row)
        x, y = fac.position(f)
        out.append(
            {
                "number": number,
                "name": facility_name(game.defs, game.config, state, f),
                "x": x,
                "y": y,
                "profit_total": display.money_text(value, currency),
            }
        )
    return out


def _bonds_due(island: m.Island, turn: int, currency: str) -> list[dict[str, Any]]:
    keep = ("number", "term", "maturity", "rate", "outstanding", "central_bank")
    return [
        {k: b[k] for k in keep}
        for b in reports.bond_rows(island, currency, mine=False)
        if b["maturity"] <= turn + BONDS_DUE_TURNS
    ]


def _politics(island: m.Island, mine: bool) -> dict[str, Any]:
    """政情の要約。持ち主には移民の受け入れと出国の扱いも（政治の画面と同じく持ち主だけ）。"""
    data = politics_views.politics_data(island, mine)
    keep = (
        "leader",
        "level",
        "level_name",
        "stability",
        "support",
        "seats",
        "next_election",
        "transition",
        "press",
    )
    out = {k: data.get(k) for k in keep}
    if mine:
        migration = data["migration"]
        out["migration"] = {
            "immigration": migration["immigration_name"],
            "emigration": migration["emigration_name"],
        }
    return out


def _goods(
    island: m.Island, stats: dict[str, float], currency: str, mine: bool = False
) -> list[dict[str, Any]]:
    """財ごとの値段と値段の種類（約定・気配・持ち越し・未観測）と最後に約定したターン・売買量・
    買えなかった量・生産量・在庫と、持ち主には国の倉庫から国営の施設に渡した量（市場の画面の表と
    同じ。W-171・W-225）。"""
    keep = (
        "good",
        "name",
        "unit",
        "price",
        "price_kind_name",
        "observed_turn",
        "change",
        "volume",
        "unmet",
        "production",
        "stock",
        "to_state",
    )
    return [
        {k: r[k] for k in keep if k in r}
        for r in reports.market_table(island, stats, currency, mine)
    ]


FOOD_SHORT_TEXTS = {
    "money": "お金が足りず買えません",
    "goods": "市場の売りが足りず買えません",
    "outbid": "売りはありましたが、ほかの買い手に買い負けたか値段が合わず買えません",
}


def hunger(island: m.Island, stats: dict[str, float], currency: str) -> list[dict[str, Any]]:
    """飢えている家計の層（食べられた割合が 1 未満か、回復にまだかかる蓄えがある層）。層の名前、
    市場で食料を買う人の数、食べられた割合、買えなかったわけ（お金がないのか、売りが足りないのか）、
    1人あたりの所得、飢えの蓄え（栄養の不足の月数）、亡くなる人が出始める蓄え、いまの蓄えで亡くなる
    割合、このターンに飢えで亡くなった人。値は人口の段が層の状態と統計に残したもの
    （modules/population）。10-17 のカード「知らせて回7」。家族や村の支え合いは回7 で入れる。"""
    config = get_game().config
    settings = config.get("population.hunger")
    out = []
    for name, h in sorted((island.economy or {}).get("households", {}).items()):
        fed = h.get("fed")
        months = h.get("hunger", 0.0)
        # 次に全部食べれば蓄えが戻り、不足も体の持ちこたえる分までなら飢えていない（PO-58）
        if fed is None or (
            not still_hungry(months, settings) and 1 - fed / fac.FULL <= settings["tolerance"]
        ):
            continue
        persons = h.get("food_buyers", h.get("persons", 0))
        if persons <= 0:
            continue
        income = stats.get(f"layer.{name}.income", 0.0)
        count = stats.get(f"layer.{name}.persons", 0) or h.get("persons", 0)
        reason = h.get("food_short", "")
        out.append(
            {
                "layer": name,
                "layer_name": reports.LAYER_NAMES.get(name, name),
                "persons": persons,
                "fed": fed / fac.FULL,
                "reason": reason,
                "reason_text": FOOD_SHORT_TEXTS.get(reason, ""),
                "income_per_person": display.money_text(round(income / count), currency)
                if count
                else "",
                "months": months,
                "deaths_from": settings["reserve_min"],
                "at_risk": h.get("hunger_risk", 0.0),
                "starved": int(stats.get(f"layer.{name}.starved", 0)),
            }
        )
    return out


def hunger_text(row: dict[str, Any]) -> str:
    """飢えている層の注意の文（島の画面と国の要約で同じ）。"""
    text = f"{row['layer_name']}の家計（{row['persons']:,} 人）が飢えています: "
    if row["fed"] < 1:
        text += f"食べられたのは要る量の {math.floor(row['fed'] * 1000) / 10:g}%"
        if row["reason_text"]:
            text += f"、{row['reason_text']}"
        if row["income_per_person"]:
            text += f"（1人あたりの所得 {row['income_per_person']}）"
    else:
        text += "いまは食べられていますが、体はまだ戻っていません"
    if row["months"] > 0:
        text += (
            f"。栄養の不足は {row['months']:.1f} か月分で、{row['deaths_from']:g} か月分から"
            "亡くなる人が出始めます"
        )
        if row["at_risk"] > 0:
            text += f"（この不足までに、累計で {row['at_risk'] * 100:.1f}% が持ちこたえられません）"
    if row["starved"]:
        text += f"。このターンに {row['starved']:,} 人が亡くなりました"
    return text


def _alerts(
    stats: dict[str, float],
    politics: dict[str, Any],
    facilities: dict[str, Any],
    goods: list[dict[str, Any]],
    bonds: list[dict[str, Any]],
    hungry: list[dict[str, Any]],
    turn: int,
    yields: dict[str, Any] | None = None,
    planned: bool = False,
) -> list[Alert]:
    """気をつけたほうがよいこと（画面の値を言葉にしただけ。先の見込みは入れない）。種類は今月
    すべきことの並べ方と行き先に使う（todo.ORDER）。planned は計画経済の島（電気の打てる手が
    公定価格になる）。"""
    out = []
    if yields and yields["yield_capped"]:
        maximum = get_game().config.get("bonds.max_yield") * 100
        out.append(
            Alert(
                "yield_capped",
                f"国債の利回りが上限の {maximum:g}% に張り付いています。"
                "市場が求める利回りはこれより高いかもしれません",
            )
        )
    if politics.get("level") and politics["level"] != core_politics.CALM:
        out.append(Alert("politics", f"政情は「{politics['level_name']}」です"))
    food = stats.get("food.ratio")
    if food is not None and food < 1:
        # 99.9% を 100% と出さない（切り捨て。Codex の試し遊び）
        ratio = math.floor(food * 1000) / 10
        out.append(Alert("food", f"食料が足りていません（充足率 {ratio:.1f}%）"))
    out += [Alert("hunger", hunger_text(row)) for row in hungry]
    out += power_alerts(stats, planned)
    if facilities["stopped_count"]:
        out.append(
            Alert("stopped", f"止まっている施設が {facilities['stopped_count']} か所あります")
        )
    if facilities.get("on_sale_count"):
        count = facilities["on_sale_count"]
        out.append(Alert("on_sale", f"倒産・閉鎖で売りに出している施設が {count} か所あります"))
    unmet = [g["name"] for g in goods if g.get("unmet")]
    if unmet:
        out.append(Alert("unmet", "買いたい量に売りが届かなかった財: " + "・".join(unmet)))
    due = [b for b in bonds if b["maturity"] <= turn + 1]
    if due:
        out.append(Alert("bonds_due", f"次のターンに満期になる国債が {len(due)} 本あります"))
    if politics.get("transition"):
        until = politics["transition"]["until"]
        out.append(Alert("transition", f"体制を変えた後の混乱期間です（ターン {until} まで）"))
    return out


def _short_supply(stats: dict[str, float]) -> float:
    """発電を使い切って届かなかった電力の割合（UI-89 の power.short_supply。その統計のない前の
    ターンは、今までの power.shortage）。"""
    if "power.short_supply" in stats:
        return stats["power.short_supply"]
    return stats.get("power.shortage", 0.0)


def unaffordable_power(stats: dict[str, float]) -> float:
    """家計が発電の売れ残りがあるのに電気料金を払えずに使えなかった電力の割合（知らせの線
    power.household_notice より小さければ 0。UI-89）。"""
    share = stats.get("power.household_unaffordable", 0.0)
    return share if share >= get_game().config.get("power.household_notice") else 0.0


def power_alerts(stats: dict[str, float], planned: bool = False) -> list[Alert]:
    """電力の注意（UI-89）: 発電を使い切って届かなかったときは「電力が足りていません」、発電が売れ
    残っているのに家計が払えないときは「家計が電気料金を払えない」と打てる手。"""
    out = []
    short = _short_supply(stats)
    if short:
        ratio = math.ceil(short * 1000) / 10
        out.append(Alert("power", f"電力が足りていません（不足率 {ratio:.1f}%）"))
    unpaid = unaffordable_power(stats)
    if unpaid:
        percent = math.ceil(unpaid * 1000) / 10
        text = f"家計が電気料金を払えず、要る電気の {percent:.1f}% を使えていません"
        notes = []
        capacity = stats.get("power.capacity", 0.0)
        if capacity > 0:
            left = max(0.0, 1 - stats.get("power.used", 0.0) / capacity)
            notes.append(f"発電は {math.floor(left * 1000) / 10:.1f}% 売れ残っています")
        layers = {
            k.removeprefix("power.household."): v
            for k, v in stats.items()
            if k.startswith("power.household.")
        }
        if layers:
            worst = min(sorted(layers), key=lambda k: layers[k])
            name = reports.LAYER_NAMES.get(worst, worst)
            notes.append(
                f"一番使えていないのは{name}で、届いたのは要る量の "
                f"{math.floor(layers[worst] * 1000) / 10:.1f}%"
            )
        if notes:
            text += "（" + "。".join(notes) + "）"
        out.append(Alert("power_bill", text + "。打てる手: " + _power_remedies(planned)))
    return out


def _power_remedies(planned: bool) -> str:
    """家計が電気料金を払えないときに打てる手（UI-89）。"""
    if planned:
        return "電気の公定価格を下げる・失業手当を出す"
    return "所得税・消費税を下げる・失業手当を出す・電気料金に公共料金の上限を付ける"


def _is_planned(island: m.Island) -> bool:
    """国が持つ経済体制（計画経済）の島か。"""
    defs = get_game().defs
    economy = (island.regime or {}).get("economy", "")
    return defs.regimes.get(economy, {}).get("owner") == OWNER_STATE


def _board_unread(island: m.Island) -> int:
    """持ち主の、掲示板で自分の書き込みに付いた未読の返信の数。"""
    return board.unread_count(island.owner) if island.owner else 0


def _mine_alerts(
    island: m.Island, losing: list[dict[str, Any]], unread: int, replies: int = 0
) -> list[Alert]:
    """持ち主にだけ出す注意（赤字の施設・未読の公電・掲示板の返信）。"""
    out = []
    if losing:
        turns = display.BOOK_TURNS
        out.append(
            Alert("losing", f"直近 {turns} ターンの損益が赤字の施設が {len(losing)} か所あります")
        )
    if unread:
        out.append(Alert("letters", f"読んでいない公電が {unread} 通あります"))
    if replies:
        out.append(Alert("board", f"掲示板で自分の書き込みへの返信が {replies} 件あります"))
    ext = island.ext or {}
    resumed = ext.get("resumed_turn")
    inactive_from = ext.get("inactive_from")
    if (
        not island.inactive
        and resumed is not None
        and inactive_from is not None
        and 0 <= island.world.turn - resumed < GUIDE_TURNS
    ):
        treaties = ((island.world.ext or {}).get("diplomacy") or {}).get("treaties", [])
        count = sum(
            t.get("caretaker") == island.id
            and island.id in t.get("parties", [])
            and t.get("start") is not None
            and inactive_from <= t["start"] <= resumed
            for t in treaties
        )
        if count:
            text = f"休止の間に、停戦・講和を代わりに {count} 件結んでいます（外交の画面）"
            out.append(Alert("caretaker", text))
    return out


def _fuel_alerts(island: m.Island, mine: bool, viewer: int | None = None) -> list[Alert]:
    """市場の段が残した燃料の注意を、島の画面と国の要約に同じ文で出す。"""
    out = []
    for row in _facility_rows(island):
        if not display.sees_facility(row, mine, viewer):
            continue
        f = facility_from_row(row)
        if not f.built or is_stopped(f) or is_seized(f) or fac.is_halted(f, island.world.turn):
            continue
        for entry in f.economy.get(power_fuel_notice.NOTICES, {}).get("active", []):
            if entry["kind"] == power_fuel_notice.PRICE and f.status == "power_price":
                continue
            out.append(Alert("fuel", display.message(entry["kind"], **entry["args"])))
    return out


MERCHANT_NOTICE_TURNS = 24  # 海外の商人が量を減らし始める何ターン前から注意を出すか


def _merchant_alerts(island: m.Island) -> list[Alert]:
    """持ち主にだけ出す海外の商人の注意（量を減らし始める前と、減っている間。タブ 9 FX-27）。
    商人は次のターンの市場で売り買いするので、次のターンで見る（貿易の画面と同じ）。"""
    config = get_game().config
    here = island_from_row(island)
    coming = island.world.turn + 1
    food = _food_merchant_alerts(here)
    if not merchant.active(here, config, coming):
        return food
    left = merchant.turns_left(here, config, coming)
    share = merchant.taper(here, config, coming)
    if share < 1:
        return [
            Alert(
                "merchant",
                f"海外の商人が持ってくる量と買っていく量が減っています"
                f"（今は初めの {round(share * 100)}%。あと {left} ターン来て、"
                "その後は来なくなります）",
            )
        ] + food
    # 量が減る初めのターン（来てから taper_from ターンを過ぎたターン）まで、今から何ターンか
    start = trade_state(here)["merchant_from"] + config.get("foreign_merchant.taper_from") + 1
    soon = start - island.world.turn
    if soon <= MERCHANT_NOTICE_TURNS:
        return [
            Alert(
                "merchant",
                f"海外の商人は {soon} ターン後から持ってくる量と買っていく量を減らし始めます"
                f"（あと {left} ターン来て、その後は来なくなります）",
            )
        ] + food
    return food


def _food_merchant_alerts(here: IslandState) -> list[Alert]:
    """物がないときの海外の食料商人（FX-73。W-239）が来ていれば、その注意（貿易の画面と同じ値）。"""
    food = food_merchant_view(here)
    if food is None:
        return []
    return [
        Alert(
            "merchant",
            f"食料が足りず、海外の商人が{food['name']}を割高で売りに来ています"
            f"（このターンは {food['quantity']:g} {food['unit']}のうち"
            f" {food['sold']:g} {food['unit']}が売れました。島の食べ物が戻ると来なくなります）",
        )
    ]


def _diplomacy_alerts(brief: dict[str, Any]) -> list[Alert]:
    """持ち主にだけ出す外交の注意（返事待ちの条約の提案・戦争・破棄の予告）。"""
    out = []
    if brief.get("to_answer"):
        count = len(brief["to_answer"])
        out.append(Alert("treaty", f"返事をしていない条約の提案が {count} 件あります"))
    for w in brief["wars"]:
        out.append(Alert("war", f"{w['with']['name']}と{w['state_name']}です"))
    return out


def trade_todo(island: m.Island) -> list[dict[str, Any]]:
    """貿易の画面の「いますべきこと」（W-247 (6)）: 今月すべきことの貿易の種類の行
    （持ち主だけ）。"""
    return todo.trade_rows(island, _merchant_alerts(island))


def _owner_lists(
    island: m.Island,
    public: list[Alert],
    stats: dict[str, float],
    before: dict[str, float],
    facilities: dict[str, Any],
    goods: list[dict[str, Any]],
    hungry: list[dict[str, Any]],
    dip: dict[str, Any],
    losing: list[dict[str, Any]],
    unread: int,
    replies: int,
) -> tuple[list[Alert], list[dict[str, Any]], list[dict[str, Any]]]:
    """持ち主の「注意」（public は誰にでも出す注意。持ち主にだけ出す注意を足す）・「次にすること」・
    「今月すべきこと」（島のホームと国の要約で同じ値から作る。stats は直近の統計、before はその
    前のターンの統計）。"""
    alerts = public + _mine_alerts(island, losing, unread, replies)
    alerts += _merchant_alerts(island)
    alerts += _diplomacy_alerts(dip)
    steps = next_steps(island, stats, facilities, goods, hungry, losing)
    rows = todo.rows(island, alerts, steps, facilities, goods, dip, stats, before)
    return alerts, steps, rows


def home_lists(
    island: m.Island, mine: bool
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """島のホームの「注意」（国の要約の alerts と同じ文）と、持ち主の「次にすること」・
    「今月すべきこと」（国の要約の next_steps・todo と同じ）。"""
    world = island.world
    currency = _currency(island)
    stats_turn, latest = display.latest_stats(island)
    stats = reports.with_fiscal(latest)
    facilities = facility_state(island, mine)
    goods = _goods(island, latest, currency, mine)
    hungry = hunger(island, latest, currency)
    out = _alerts(
        stats,
        _politics(island, mine),
        facilities,
        goods,
        _bonds_due(island, world.turn, currency),
        hungry,
        world.turn,
        reports.yield_data(island),
        planned=_is_planned(island),
    )
    out += _fuel_alerts(island, mine)
    steps: list[dict[str, Any]] = []
    todo_rows: list[dict[str, Any]] = []
    if mine:
        before = _stats_at(island, stats_turn - 1) if stats_turn is not None else {}
        losing = losing_facilities(island, currency)
        unread = letters.inbox(island).filter(read_at__isnull=True).count()
        dip = diplomacy.brief(island, mine)
        replies = _board_unread(island)
        out, steps, todo_rows = _owner_lists(
            island, out, latest, before, facilities, goods, hungry, dip, losing, unread, replies
        )
    return [a.text for a in out], steps, todo_rows


def island_alerts(island: m.Island, mine: bool) -> tuple[list[str], list[dict[str, Any]]]:
    """島の画面の「注意」（国の要約の alerts と同じ文）と、持ち主の「次にすること」
    （国の要約の next_steps と同じ）。"""
    alerts, steps, _ = home_lists(island, mine)
    return alerts, steps


STEP_LINKS = {
    "map": "地図へ",
    "orders": "命令のページへ",
    "market": "市場を見る",
    "facilities": "施設の状態を見る",
    "chain": "産業の鎖を見る",
}


def _step(key: str, text: str, page: str, island: m.Island) -> dict[str, Any]:
    """「次にすること」の1行。page は行き先の画面（map は島の画面の中の地図）。"""
    if page == "map":
        url = reverse("island", args=[island.id]) + "#island-map"
    elif page:
        url = reverse(f"island_{page}", args=[island.id])
    else:
        url = ""
    link = STEP_LINKS.get(page, "")
    return {"key": key, "text": text, "page": page, "url": url, "link_text": link}


def next_steps(
    island: m.Island,
    stats: dict[str, float],
    facilities: dict[str, Any],
    goods: list[dict[str, Any]],
    hungry: list[dict[str, Any]],
    losing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """建国から GUIDE_TURNS の間、持ち主に出す「次にすること」（急ぐ順に NEXT_STEPS_MAX まで）。
    画面の値と島の状態から選ぶだけで、先の見込みは入れない。それより後は空。"""
    founded = (island.ext or {}).get("founded_turn", 0)
    if island.world.turn - founded >= GUIDE_TURNS:
        return []
    defs = get_game().defs
    planned = _is_planned(island)
    out = []
    if island.world.turn <= founded:  # まだ一度も更新されていない
        starting = facilities.get("starting_count", 0)
        out.append(
            _step(
                "first_update",
                f"まだ一度も更新されていません。初めの施設 {starting} か所は「稼働準備中」で、"
                "次の更新で動き出します。命令を出さなくても島は動きます",
                "",
                island,
            )
        )
    food = stats.get("food.ratio")
    if hungry or (food is not None and food < 1):
        out.append(
            _step(
                "food",
                "食料が足りていません。市場で、売りが足りないのか、お金が足りず買えないのかを見ます",
                "market",
                island,
            )
        )
    if _short_supply(stats):
        out.append(
            _step(
                "power",
                "電力が足りていません。発電所の燃料と、送電線でつながっている範囲を確かめます",
                "facilities",
                island,
            )
        )
    elif unaffordable_power(stats):
        out.append(
            _step(
                "power_bill",
                "家計が電気料金を払えず、電気を控えています（発電は売れ残っています）。命令の欄で、"
                + _power_remedies(planned)
                + "を考えます",
                "orders",
                island,
            )
        )
    block = (island.economy or {}).get("finance", {}).get("entry_block") or {}
    no_zone = not island.tiles.filter(zone=ZONE_INDUSTRIAL).exists()
    if not planned and no_zone:
        out.append(
            _step(
                "industrial_zone",
                "民営の工場は工業団地のマスにしか建ちません。地図で首都の近くの空いた平地を選び、"
                "「工業団地の指定」をすると、民間の会社が採算を見て工場を建てます",
                "map",
                island,
            )
        )
    elif not planned and block.get("reason") == "no_zone":
        name = defs.name("facilities", block.get("facility", ""))
        out.append(
            _step(
                "industrial_zone",
                f"工業団地に、民営の{name}に合う空いたマスがありません（地形・資源・通勤圏）。"
                "合うマスを工業団地にしてください",
                "map",
                island,
            )
        )
    elif block and block.get("reason") != "no_zone":
        reason = display.message(f"entry_block_{block['reason']}", **(block.get("args") or {}))
        name = defs.name("facilities", block.get("facility", ""))
        page = "chain" if block["reason"].startswith("inputs") else "facilities"
        out.append(_step("private_entry", f"民営の{name}が参入しません（{reason}）", page, island))
    if facilities["stopped_count"]:
        out.append(
            _step(
                "stopped",
                f"止まっている施設が {facilities['stopped_count']} か所あります。施設の画面で、"
                "人手・原料・電力のどれが足りないかを見ます",
                "facilities",
                island,
            )
        )
    unmet = [g["name"] for g in goods if g.get("unmet")]
    if unmet:
        names = "・".join(unmet[:3]) + ("など" if len(unmet) > 3 else "")
        out.append(
            _step(
                "unmet",
                f"{names}は、買いたい量に売りが届きませんでした。作る施設を建てるか、国の倉庫に在庫が"
                "あれば市場の画面の「国の倉庫の売り買い」で売り出します",
                "market",
                island,
            )
        )
    if losing:
        out.append(
            _step(
                "losing",
                f"直近の損益が赤字の施設が {len(losing)} か所あります。施設の画面で、売上と賃金・"
                "仕入れのどれが重いかを見ます",
                "facilities",
                island,
            )
        )
    if not island.orders.filter(status="pending").exists():
        out.append(
            _step(
                "orders",
                "命令の列が空です。命令は次の更新のときに上から実行されます（1ターンに実行できる数は"
                "行政力まで）",
                "orders",
                island,
            )
        )
    return out[:NEXT_STEPS_MAX]


POPULATION_CHANGES = [
    ("births", "population.births", "出生数"),
    ("deaths", "population.deaths", "死亡数（餓死を除く）"),
    ("starved", "population.starved", "餓死者数"),
    ("arrived", "migration.arrived", "入国者数（移住。帰国者を含む）"),
    ("left", "migration.left", "出国者数（移住）"),
    ("fled_to_villages", "population.fled_to_villages", "食料難による都市から農村への移住者数"),
    ("returned_to_towns", "population.returned_to_towns", "食料難の後に農村から都市へ戻った人数"),
]


def population_changes(stats: dict[str, float]) -> dict[str, Any]:
    """このターンの人口の増減のわけ（人数。記録のないものは 0。経済の画面のグラフと同じ統計）。"""
    out: dict[str, Any] = {key: int(stats.get(name, 0)) for key, name, _ in POPULATION_CHANGES}
    out["labels"] = {key: label for key, _, label in POPULATION_CHANGES}
    return out


SERVICES_STATS = [
    ("workers", "services.workers"),
    ("seats", "services.seats"),
    ("price", "services.price"),
    ("sales", "services.sales"),
    ("sold", "services.sold"),
    ("unused", "services.unused"),
]


def services_brief(stats: dict[str, float], currency: str) -> dict[str, Any] | None:
    """商業・サービス業の店（働く人・次のターンに雇う人数の目安・値段・売上・売れた量・売れ残り）。
    統計がなければ None（お金の経済の前の島）。"""
    if "services.workers" not in stats:
        return None
    out: dict[str, Any] = {}
    for key, name in SERVICES_STATS:
        if name in stats:
            label, unit = reports.stat_label(name)
            out[key] = stats[name]
            out[f"{key}_text"] = display.stat_text(name, stats[name], unit, currency)
    return out


WORKS_STATS = [
    ("workers", "works.workers"),
    ("direct", "works.direct"),
    ("contracted", "works.contracted"),
    ("contractor_price", "contractor.price"),
    ("machines", "contractor.machines"),
    ("machines_working", "contractor.machines_working"),
]


def works_brief(stats: dict[str, float], currency: str) -> dict[str, Any] | None:
    """公共事業の人手（W-270）: 従事者（直営と土木業者）・直営の作業員・業者が届けた工事・業者の
    単価。統計がなければ None。"""
    if "works.direct" not in stats:
        return None
    out: dict[str, Any] = {}
    for key, name in WORKS_STATS:
        if name in stats:
            label, unit = reports.stat_label(name)
            out[key] = stats[name]
            out[f"{key}_text"] = display.stat_text(name, stats[name], unit, currency)
    return out


SHIPPING_STATS = [
    ("charter_income", "trade.charter_income"),
    ("charter_paid", "trade.charter_paid"),
    ("services", "bop.services"),
]


def shipping_brief(stats: dict[str, float], currency: str) -> dict[str, Any] | None:
    """圏の荷を運ぶ船の貸し借り（貸した船の運賃の収入・借りた船に払った運賃）と、国際収支の
    サービス収支（運賃と港の使用料）。このターンに貸し借りがなければ None。"""
    if not stats.get("trade.charter_income") and not stats.get("trade.charter_paid"):
        return None
    out: dict[str, Any] = {}
    for key, name in SHIPPING_STATS:
        if name in stats:
            _, unit = reports.stat_label(name)
            out[key] = stats[name]
            out[f"{key}_text"] = display.stat_text(name, stats[name], unit, currency)
    return out


TRADE_LINE = [
    ("exports", "trade.exports"),
    ("imports", "trade.imports"),
    ("merchant", "trade.merchant"),
]


def trade_brief(stats: dict[str, float], currency: str) -> dict[str, Any] | None:
    """国のまとめの貿易の 1 行（W-247 (8)。誰にでも）: 輸出・輸入の額と、そのうち海外の商人に
    払った額（為替の画面と同じ統計）。輸出も輸入もなければ None。"""
    if not stats.get("trade.exports") and not stats.get("trade.imports"):
        return None
    out: dict[str, Any] = {}
    for key, name in TRADE_LINE:
        _, unit = reports.stat_label(name)
        out[key] = stats.get(name, 0.0)
        out[f"{key}_text"] = display.stat_text(name, out[key], unit, currency)
    out["text"] = (
        f"輸出 {out['exports_text']}・輸入 {out['imports_text']}"
        f"（うち海外の商人に払った額 {out['merchant_text']}）"
    )
    return out


RETAIL_STATS = [
    ("need", "retail.need"),
    ("sold", "retail.sold"),
    ("direct", "retail.direct"),
    ("direct_sales", "retail.direct_sales"),
]


def retail_brief(island: m.Island, stats: dict[str, float], currency: str) -> dict[str, Any] | None:
    """小売り（回7）: 家計が品物と一緒に買う売り場の働きの要る量・商店と百貨店が売った量・小さな店と
    屋台から割高に買った量と額、店の数と、百貨店の出店の規制と申請。統計がなければ None。"""
    if "retail.need" not in stats:
        return None
    out: dict[str, Any] = {}
    for key, name in RETAIL_STATS:
        if name in stats:
            _, unit = reports.stat_label(name)
            out[key] = stats[name]
            out[f"{key}_text"] = display.stat_text(name, stats[name], unit, currency)
    kinds = island.facilities.filter(kind__in=("shop", "department_store"), built=True)
    out["shops"] = kinds.filter(kind="shop").count()
    out["department_stores"] = kinds.filter(kind="department_store").count()
    policy = (island.policy or {}).get("large_store") or retail.NONE
    out["large_store"] = policy
    out["large_store_text"] = display.message(f"large_store_{policy}")
    found = (island.economy or {}).get("finance", {}).get(retail.APPLICATION)
    if found:
        out["store_application"] = {
            "turn": found["turn"],
            "x": found["x"],
            "y": found["y"],
            "approved": bool(found.get("approved")),
        }
    return out


def dismissal_brief(island: m.Island, stats: dict[str, float], currency: str) -> dict[str, Any]:
    """解雇の決まり（LB-43）: 今の段（決めていなければ経済体制の既定）と、そのターンに抱えていた
    余った人・解雇した人・解雇手当。"""
    view = display.dismissal_view(island)
    level = view["dismissal"] or view["dismissal_default"]
    out: dict[str, Any] = {
        "level": level,
        "level_text": view["dismissal_choices"].get(level, level),
        "regime_default": not view["dismissal"],
        **view["dismissal_terms"].get(level, {}),
    }
    for key, name in (
        ("held", "labor.held"),
        ("dismissed", "labor.dismissed"),
        ("severance", "labor.severance"),
    ):
        value = stats.get(name, 0)
        _, unit = reports.stat_label(name)
        out[key] = value
        out[f"{key}_text"] = display.stat_text(name, value, unit, currency)
    return out


def staff_plan_brief(island: m.Island, mine: bool) -> dict[str, Any] | None:
    """人員計画（PL-59。計画経済の島の持ち主だけ）: 人員の枠で人が足りない施設と、枠を決めた施設、
    公共事業の作業員と優先度。枠を決められる施設の全部は計画の画面（report の plan）の staff。"""
    if not mine:
        return None
    core = island_from_row(island)
    if not planning.is_planned(core, get_game().defs):
        return None
    rows = politics_views.staff_plan_rows(island, core)
    return {
        "short": [r for r in rows if r["capped"]][:LIST_MAX],
        "caps": [r for r in rows if r["cap"] is not None][:LIST_MAX],
        "works": politics_views.works_staff(core),
    }


def export_plan_brief(island: m.Island, mine: bool) -> dict[str, Any] | None:
    """輸出の計画（PL-62。計画経済の島の持ち主だけ）: 通貨ごとの要る外貨・持つ外貨・蓄えの目標・
    稼ぐ額、計画のある財、名指しした財、輸出できる財がなくて止まっているか。全部は計画の画面
    （report の plan）の export。"""
    if not mine:
        return None
    core = island_from_row(island)
    if not planning.is_planned(core, get_game().defs):
        return None
    view = politics_views.export_plan_view(island, (island.ext or {}).get(planning.KEY, {}))
    return {
        "money": view["money"],
        "rows": view["rows"][:LIST_MAX],
        "named": view["named"],
        "stuck": view["stuck"],
    }


def budget_shares(stats: dict[str, float]) -> list[dict[str, Any]]:
    """国庫の項目の予算が足りず、同じ優先度の施設に要る額の割合で分けた項目（LB-45）と、要る額に
    対して分けた割合。"""
    out = []
    for name, value in sorted(stats.items()):
        if name.startswith("budget_share."):
            label, unit = reports.stat_label(name)
            out.append(
                {
                    "item": name.removeprefix("budget_share."),
                    "label": label,
                    "share": value,
                    "text": display.stat_text(name, value, unit, ""),
                }
            )
    return out


def firms_brief(island: m.Island, stats: dict[str, float], mine: bool) -> dict[str, Any] | None:
    """企業（回7）: 民営の企業の数・施設が2つ以上の企業の数・一番大きい企業の施設の割合
    （誰にでも）と、施設の多い順の企業の一覧（施設の画面の企業の表と同じ中身。他国にはお金の数字を
    見せない）。"""
    if "firms.count" not in stats:
        return None
    out: dict[str, Any] = {
        "count": stats["firms.count"],
        "groups": stats.get("firms.groups", 0),
        "largest_share": round(stats.get("firms.largest_share", 0) * 100, 1),
    }
    state = display.island_for_naming(island)
    rows = reports.firm_rows(island, state, _currency(island), mine=mine)
    rows.sort(key=lambda r: (-r["count"], r["firm"]))
    out["list"] = rows[:LIST_MAX]
    return out


def summary(island: m.Island, mine: bool, viewer: int | None = None) -> dict[str, Any]:
    """国の要約（GET /api/islands/<id>/summary）。viewer は見ている人の島の番号。"""
    world = island.world
    currency = _currency(island)
    stats_turn, latest = display.latest_stats(island)
    stats = reports.with_fiscal(latest)
    before = _stats_at(island, stats_turn - 1) if stats_turn is not None else {}
    facilities = facility_state(island, mine, viewer)
    goods = _goods(island, latest, currency, mine)
    bonds = _bonds_due(island, world.turn, currency)
    politics = _politics(island, mine)
    hungry = hunger(island, latest, currency)
    logs = display.visible_logs(TurnLog.objects.filter(island=island), mine, island)
    log_turn = logs.aggregate(t=Max("turn"))["t"]
    data: dict[str, Any] = {
        "island": island_head(island),
        "mine": mine,
        "turn": world.turn,
        "date": display.turn_text(world.turn),
        "next_turn_at": world.next_turn_at.isoformat() if world.next_turn_at else None,
        "turn_schedule": display.turn_schedule(world),
        "stats_turn": stats_turn,
        "indicators": indicators(stats, before, currency),
        "notable_changes": notable_changes(stats, before, currency),
        **reports.yield_data(island),
        "politics": politics,
        "diplomacy": diplomacy.brief(island, mine),
        "military": military.brief(island, mine, viewer),
        "country": country.brief(island, mine),
        "strengths": rankings.island_strengths(island, latest),
        "facilities": facilities,
        "fuel_reserves": display.fuel_reserves(island, mine, viewer),
        "goods": goods,
        "services": services_brief(latest, currency),
        "public_works": works_brief(latest, currency),
        "shipping": shipping_brief(latest, currency),
        "firms": firms_brief(island, latest, mine),
        "dismissal": dismissal_brief(island, latest, currency),
        "staff_plan": staff_plan_brief(island, mine),
        "export_plan": export_plan_brief(island, mine),
        "budget_shares": budget_shares(latest),
        "retail": retail_brief(island, latest, currency),
        "population_changes": population_changes(latest),
        "hunger": hungry,
        "bonds_due": bonds,
        "events_turn": log_turn,
        "events": [
            {k: v for k, v in e.items() if k != "turn"}
            for e in display.log_entries(logs.filter(turn=log_turn).order_by("id"))
        ]
        if log_turn is not None
        else [],
        "trade": trade_brief(latest, currency),
    }
    alerts = _alerts(
        stats,
        politics,
        facilities,
        goods,
        bonds,
        hungry,
        world.turn,
        reports.yield_data(island),
        planned=_is_planned(island),
    )
    alerts += _fuel_alerts(island, mine, viewer)
    data["alerts"] = [a.text for a in alerts]
    if mine:
        losing = losing_facilities(island, currency)
        data["losing"] = losing[:LIST_MAX]
        data["orders"] = orders_brief(island)
        unread = letters.inbox(island).filter(read_at__isnull=True)
        data["letters"] = {
            "unread": unread.count(),
            "latest_unread": [
                {"id": x.id, "from": x.sender.name, "subject": x.subject, "turn": x.turn}
                for x in unread[:5]
            ],
        }
        data["board"] = {"unread_replies": _board_unread(island)}
        alerts, data["next_steps"], data["todo"] = _owner_lists(
            island,
            alerts,
            latest,
            before,
            facilities,
            goods,
            hungry,
            data["diplomacy"],
            losing,
            data["letters"]["unread"],
            data["board"]["unread_replies"],
        )
        data["alerts"] = [a.text for a in alerts]
        # 備蓄の放出の決まり（市場 MK-39。命令 set_warehouse_release）と、いま放出している財
        released = (island.economy or {}).get("ext", {}).get("warehouse_released", [])
        data["warehouse_release"] = {
            "auto": (island.policy or {}).get("warehouse_release", True),
            "releasing": [get_game().defs.name("goods", g) for g in released],
        }
        data["warehouse_standing"] = display.warehouse_standing(island)
    return data


def orders_brief(island: m.Island) -> dict[str, Any]:
    """命令の列と行政力、直近に実行したターンの命令の結果（持ち主だけ）。"""
    d = get_game().defs
    _, stats = display.latest_stats(island)
    admin_power = stats.get("admin_power")
    if admin_power is None:
        admin_power = get_game().config.get("orders.base_admin_power")
    pending = island.orders.filter(status="pending").order_by("seq", "id")
    done = island.orders.exclude(status="pending")
    last = done.aggregate(t=Max("executed_turn"))["t"]
    labels = display.facility_labels(island)  # 施設への命令の文に種類と作る物を（W-262）
    return {
        "admin_power": int(admin_power),
        "pending": [
            {
                "id": o.id,  # 1 件ずつ消すときの番号（remove_order・DELETE .../items/<番号>）
                "kind": o.kind,
                "payload": display.order_payload(o.kind, o.payload, to_game=False),
                "cost": d.orders[o.kind]["admin_cost"] if o.kind in d.orders else 0,
                "text": display.describe_order(o.kind, o.payload, labels),
            }
            for o in pending
        ],
        "last_turn": last,
        "last_results": [
            _result(o, labels) for o in done.filter(executed_turn=last).order_by("seq")
        ]
        if last is not None
        else [],
    }


def _result(o: Order, labels: dict[tuple[int, int], str] | None = None) -> dict[str, Any]:
    return {
        "turn": o.executed_turn,
        "status": o.status,
        "text": display.describe_order(o.kind, o.payload, labels),
        "reason": display.message(o.reason, **(o.reason_args or {})) if o.reason else "",
    }


def log_range(
    island: m.Island,
    start: int,
    end: int,
    mine: bool,
    kind: str | None = None,
    facility: str | None = None,
) -> list[dict[str, Any]]:
    """ターン start〜end のターンログ（古いターンから）。軍の行は持ち主だけ（MI-55）。"""
    rows = display.visible_logs(
        TurnLog.objects.filter(island=island, turn__gte=start, turn__lte=end), mine, island
    )
    entries = display.log_entries(rows.order_by("turn", "id"))
    return [
        row
        for row in entries
        if (not kind or row["kind"] == kind) and (not facility or row.get("facility") == facility)
    ]


def changes(
    island: m.Island, since: int, mine: bool, secret_orders: Any, hidden_orders: Any
) -> dict[str, Any]:
    """ターン since より後に起きたこと（GET /api/islands/<id>/changes?since=）。

    ターンログ・主な指標の変わり方・実行した命令の結果と、持ち主には届いた公電。ターンログは
    LOG_TURNS_MAX ターン分まで（それより前は truncated を立て、log_from から返す）。
    secret_orders は伏せる命令の条件（ほかの国の人に、失敗した伏せる命令を見せないため）、
    hidden_orders は実行した後も伏せる命令の条件（部隊と艦隊への命令。ほかの国の人には見せない）。
    """
    world = island.world
    currency = _currency(island)
    log_from = max(since + 1, world.turn - LOG_TURNS_MAX + 1)
    stats_turn, latest = display.latest_stats(island)
    results = island.orders.exclude(status="pending").filter(executed_turn__gt=since)
    if not mine:
        results = results.exclude(Q(status="failed") & secret_orders)
        results = results.exclude(hidden_orders)
    labels = display.facility_labels(island)  # 施設への命令の文に種類と作る物を（W-262）
    data: dict[str, Any] = {
        "island": island.id,
        "since": since,
        "turn": world.turn,
        "date": display.turn_text(world.turn),
        "stats_turn": stats_turn,
        "log_from": log_from,
        "truncated": log_from > since + 1,
        "notable_changes": notable_changes(
            reports.with_fiscal(latest), _stats_at(island, since), currency
        ),
        "log": log_range(island, log_from, world.turn, mine),
        "indicators": [
            row
            for row in indicators(reports.with_fiscal(latest), _stats_at(island, since), currency)
            if "before" in row
        ],
        "orders": [_result(o, labels) for o in results.order_by("executed_turn", "seq")],
    }
    data["hall"] = hall.entries_since(world, since)[-LOG_TURNS_MAX:]  # 国際会議場（誰にでも）
    if mine:
        visitors = island.visitor_notes.filter(hidden=False, turn__gt=since).select_related(
            "visitor"
        )
        data["visitor_notes"] = [
            {"from": {"id": n.visitor_id, "name": n.visitor.name}, "text": n.text, "turn": n.turn}
            for n in visitors.order_by("id")[:50]
        ]
        received = letters.inbox(island).filter(turn__gt=since)
        data["letters"] = [
            {
                "id": x.id,
                "from": {"id": x.sender_id, "name": x.sender.name},
                "subject": x.subject,
                "turn": x.turn,
                "read": x.read_at is not None,
            }
            for x in received[:50]
        ]
        data["board"] = {"unread_replies": _board_unread(island)}
        data["warehouse_standing"] = display.warehouse_standing(island)
    return data
