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

from hakoniwa_core import facilities as fac
from hakoniwa_core import politics as core_politics
from hakoniwa_core.economy import books
from hakoniwa_core.naming import facility_name
from hakoniwa_core.state import ACCOUNTING_SELF
from server.accounts import keys
from server.economy.models import FacilityBook
from server.game import get_game
from server.letters import services as letters
from server.orders.models import Order
from server.turns.models import TurnLog
from server.web import display, rankings, reports
from server.web import politics as politics_views
from server.world import models as m
from server.world.repository import facility_from_row

LIST_MAX = 30  # 要約の一覧（止まった施設・工事・赤字の施設）に出す数
BONDS_DUE_TURNS = 12  # このターン数のうちに満期になる国債を出す
LOG_TURNS_MAX = 24  # 差分とターンログで1回に返すターン数


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
    }


def _facility_rows(island: m.Island) -> list[m.Facility]:
    housing = fac.housing_kind(get_game().config)
    return list(island.facilities.exclude(kind=housing).order_by("number"))


def facility_state(island: m.Island) -> dict[str, Any]:
    """止まった施設、満たない稼働のわけごとの数、工事中の施設（地図の施設の状態と同じ。誰にでも）。"""
    game = get_game()
    state = display.island_for_naming(island)
    stopped, works = [], []
    reasons: dict[str, int] = defaultdict(int)
    rows = _facility_rows(island)
    for row in rows:
        f = facility_from_row(row)
        text = display.status_text(f)
        if not row.built:
            works.append(
                {
                    "kind_name": game.defs.name("facilities", row.kind),
                    "x": row.x,
                    "y": row.y,
                    "progress": round(row.progress / 1000, 1),
                    "turns": game.defs.facilities.get(row.kind, {})
                    .get("construction", {})
                    .get("turns"),
                    "status_text": text,
                }
            )
            continue
        if row.operating_rate <= 0 and row.status:  # 地図の止まった印（赤い点）と同じ
            stopped.append(
                {
                    "number": row.number,
                    "name": facility_name(game.defs, game.config, state, f),
                    "kind_name": game.defs.name("facilities", row.kind),
                    "x": row.x,
                    "y": row.y,
                    "status_text": text,
                }
            )
        elif row.operating_rate < fac.FULL and text:
            reasons[text] += 1
    infra = (island.economy or {}).get("infra", {})
    return {
        "facilities": len(rows),
        "stopped_count": len(stopped),
        "stopped": stopped[:LIST_MAX],
        "partial": [
            {"status_text": t, "count": n} for t, n in sorted(reasons.items(), key=lambda r: -r[1])
        ],
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
        out.append(
            {
                "number": number,
                "name": facility_name(game.defs, game.config, state, f),
                "x": row.x,
                "y": row.y,
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


def _goods(island: m.Island, stats: dict[str, float], currency: str) -> list[dict[str, Any]]:
    """財ごとの値段・売買量・買えなかった量・生産量・在庫（市場の画面の表と同じ）。"""
    keep = ("good", "name", "unit", "price", "change", "volume", "unmet", "production", "stock")
    return [
        {k: r[k] for k in keep if k in r} for r in reports.market_table(island, stats, currency)
    ]


FOOD_SHORT_TEXTS = {
    "money": "お金が足りず買えません",
    "goods": "市場の売りが足りず買えません",
}


def hunger(island: m.Island, stats: dict[str, float], currency: str) -> list[dict[str, Any]]:
    """飢えている家計の層（食べられた割合が 1 未満か、飢えの蓄えが残っている層）。層の名前、市場で
    食料を買う人の数、食べられた割合、買えなかったわけ（お金がないのか、売りが足りないのか）、
    1人あたりの所得、飢えの蓄え（栄養の不足の月数）、亡くなる人が出始める蓄え、いまの蓄えで亡くなる
    割合、このターンに飢えで亡くなった人。値は人口の段が層の状態と統計に残したもの
    （modules/population）。10-17 のカード「知らせて回7」。家族や村の支え合いは回7 で入れる。"""
    config = get_game().config
    settings = config.get("population.hunger")
    out = []
    for name, h in sorted((island.economy or {}).get("households", {}).items()):
        fed = h.get("fed")
        months = h.get("hunger", 0.0)
        if fed is None or (fed >= fac.FULL and months <= 0):
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
        text += f"食べられたのは要る量の {row['fed'] * 100:.0f}%"
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
            text += f"（いまの不足では {row['at_risk'] * 100:.1f}% が持ちこたえられません）"
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
) -> list[str]:
    """気をつけたほうがよいこと（画面の値を言葉にしただけ。先の見込みは入れない）。"""
    out = []
    if politics.get("level") and politics["level"] != core_politics.CALM:
        out.append(f"政情は「{politics['level_name']}」です")
    food = stats.get("food.ratio")
    if food is not None and food < 1:
        # 99.9% を 100% と出さない（切り捨て。Codex の試し遊び）
        out.append(f"食料が足りていません（充足率 {math.floor(food * 1000) / 10:.1f}%）")
    out += [hunger_text(row) for row in hungry]
    shortage = stats.get("power.shortage")
    if shortage:
        out.append(f"電力が足りていません（不足率 {math.ceil(shortage * 1000) / 10:.1f}%）")
    if facilities["stopped_count"]:
        out.append(f"止まっている施設が {facilities['stopped_count']} か所あります")
    unmet = [g["name"] for g in goods if g.get("unmet")]
    if unmet:
        out.append("買いたい量に売りが届かなかった財: " + "・".join(unmet))
    due = [b for b in bonds if b["maturity"] <= turn + 1]
    if due:
        out.append(f"次のターンに満期になる国債が {len(due)} 本あります")
    if politics.get("transition"):
        out.append(f"体制を変えた後の混乱期間です（ターン {politics['transition']['until']} まで）")
    return out


def _mine_alerts(island: m.Island, losing: list[dict[str, Any]], unread: int) -> list[str]:
    """持ち主にだけ出す注意（赤字の施設・未読の公電）。"""
    out = []
    if losing:
        turns = display.BOOK_TURNS
        out.append(f"直近 {turns} ターンの損益が赤字の施設が {len(losing)} か所あります")
    if unread:
        out.append(f"読んでいない公電が {unread} 通あります")
    return out


def island_alerts(island: m.Island, mine: bool) -> list[str]:
    """島の画面の「注意」（国の要約の alerts と同じ文）。"""
    world = island.world
    currency = _currency(island)
    _, latest = display.latest_stats(island)
    stats = reports.with_fiscal(latest)
    out = _alerts(
        stats,
        _politics(island, mine),
        facility_state(island),
        _goods(island, latest, currency),
        _bonds_due(island, world.turn, currency),
        hunger(island, latest, currency),
        world.turn,
    )
    if mine:
        unread = letters.inbox(island).filter(read_at__isnull=True).count()
        out += _mine_alerts(island, losing_facilities(island, currency), unread)
    return out


POPULATION_CHANGES = [
    ("births", "population.births", "生まれた人"),
    ("deaths", "population.deaths", "亡くなった人（飢えのほか）"),
    ("starved", "population.starved", "飢えで亡くなった人"),
    ("arrived", "migration.arrived", "ほかの国から移り住んできた人（帰ってきた人も）"),
    ("left", "migration.left", "ほかの国へ移り住んだ人"),
    ("fled_to_villages", "population.fled_to_villages", "食べ物を買えず町から村へ移った人"),
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


def summary(island: m.Island, mine: bool) -> dict[str, Any]:
    """国の要約（GET /api/islands/<id>/summary）。"""
    world = island.world
    currency = _currency(island)
    stats_turn, latest = display.latest_stats(island)
    stats = reports.with_fiscal(latest)
    before = _stats_at(island, stats_turn - 1) if stats_turn is not None else {}
    facilities = facility_state(island)
    goods = _goods(island, latest, currency)
    bonds = _bonds_due(island, world.turn, currency)
    politics = _politics(island, mine)
    hungry = hunger(island, latest, currency)
    logs = TurnLog.objects.filter(island=island)
    log_turn = logs.aggregate(t=Max("turn"))["t"]
    data: dict[str, Any] = {
        "island": island_head(island),
        "mine": mine,
        "turn": world.turn,
        "date": display.turn_text(world.turn),
        "next_turn_at": world.next_turn_at.isoformat() if world.next_turn_at else None,
        "stats_turn": stats_turn,
        "indicators": indicators(stats, before, currency),
        "politics": politics,
        "strengths": rankings.island_strengths(island, latest),
        "facilities": facilities,
        "goods": goods,
        "services": services_brief(latest, currency),
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
        "alerts": _alerts(stats, politics, facilities, goods, bonds, hungry, world.turn),
    }
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
        data["alerts"] += _mine_alerts(island, losing, data["letters"]["unread"])
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
    return {
        "admin_power": int(admin_power),
        "pending": [
            {
                "kind": o.kind,
                "payload": o.payload,
                "cost": d.orders[o.kind]["admin_cost"] if o.kind in d.orders else 0,
                "text": display.describe_order(o.kind, o.payload),
            }
            for o in pending
        ],
        "last_turn": last,
        "last_results": [_result(o) for o in done.filter(executed_turn=last).order_by("seq")]
        if last is not None
        else [],
    }


def _result(o: Order) -> dict[str, Any]:
    return {
        "turn": o.executed_turn,
        "status": o.status,
        "text": display.describe_order(o.kind, o.payload),
        "reason": display.message(o.reason, **(o.reason_args or {})) if o.reason else "",
    }


def log_range(island: m.Island, start: int, end: int) -> list[dict[str, Any]]:
    """ターン start〜end のターンログ（古いターンから）。"""
    rows = TurnLog.objects.filter(island=island, turn__gte=start, turn__lte=end)
    return display.log_entries(rows.order_by("turn", "id"))


def changes(island: m.Island, since: int, mine: bool, secret_orders: Any) -> dict[str, Any]:
    """ターン since より後に起きたこと（GET /api/islands/<id>/changes?since=）。

    ターンログ・主な指標の変わり方・実行した命令の結果と、持ち主には届いた公電。ターンログは
    LOG_TURNS_MAX ターン分まで（それより前は truncated を立て、log_from から返す）。
    secret_orders は伏せる命令の条件（ほかの国の人に、失敗した伏せる命令を見せないため）。
    """
    world = island.world
    currency = _currency(island)
    log_from = max(since + 1, world.turn - LOG_TURNS_MAX + 1)
    stats_turn, latest = display.latest_stats(island)
    results = island.orders.exclude(status="pending").filter(executed_turn__gt=since)
    if not mine:
        results = results.exclude(Q(status="failed") & secret_orders)
    data: dict[str, Any] = {
        "island": island.id,
        "since": since,
        "turn": world.turn,
        "date": display.turn_text(world.turn),
        "stats_turn": stats_turn,
        "log_from": log_from,
        "truncated": log_from > since + 1,
        "log": log_range(island, log_from, world.turn),
        "indicators": [
            row
            for row in indicators(reports.with_fiscal(latest), _stats_at(island, since), currency)
            if "before" in row
        ],
        "orders": [_result(o) for o in results.order_by("executed_turn", "seq")],
    }
    if mine:
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
    return data
