"""国の要約と、前のターンからの差分（AI が遊ぶための入り口。GET /api/islands/<id>/summary と
changes）。

画面を何枚も読まなくても、国の今の状態と、あるターンより後に起きたことを1回で読めるようにする。
中身は画面と同じ値を集めて短くしただけで、新しい計算や先の見込みは出さない。見せる範囲も画面と同じ
（施設の帳簿・命令の列・公電は島の持ち主だけ。伏せる命令はほかの国の人に見せない）。
"""

from __future__ import annotations

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
from server.web import display, reports
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


def _politics(island: m.Island) -> dict[str, Any]:
    data = politics_views.politics_data(island, mine=False)
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
    return {k: data.get(k) for k in keep}


def _goods(island: m.Island, stats: dict[str, float], currency: str) -> list[dict[str, Any]]:
    """財ごとの値段・売買量・買えなかった量・生産量・在庫（市場の画面の表と同じ）。"""
    keep = ("good", "name", "unit", "price", "change", "volume", "unmet", "production", "stock")
    return [
        {k: r[k] for k in keep if k in r} for r in reports.market_table(island, stats, currency)
    ]


def _alerts(
    stats: dict[str, float],
    politics: dict[str, Any],
    facilities: dict[str, Any],
    goods: list[dict[str, Any]],
    bonds: list[dict[str, Any]],
    turn: int,
) -> list[str]:
    """気をつけたほうがよいこと（画面の値を言葉にしただけ。先の見込みは入れない）。"""
    out = []
    if politics.get("level") and politics["level"] != core_politics.CALM:
        out.append(f"政情は「{politics['level_name']}」です")
    food = stats.get("food.ratio")
    if food is not None and food < 1:
        out.append(f"食料が足りていません（充足率 {food * 100:.0f}%）")
    shortage = stats.get("power.shortage")
    if shortage:
        out.append(f"電力が足りていません（不足率 {shortage * 100:.0f}%）")
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
    politics = _politics(island)
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
        "facilities": facilities,
        "goods": goods,
        "bonds_due": bonds,
        "events_turn": log_turn,
        "events": [
            {k: v for k, v in e.items() if k != "turn"}
            for e in display.log_entries(logs.filter(turn=log_turn).order_by("id"))
        ]
        if log_turn is not None
        else [],
        "alerts": _alerts(stats, politics, facilities, goods, bonds, world.turn),
    }
    if mine:
        losing = losing_facilities(island, currency)
        data["losing"] = losing[:LIST_MAX]
        if losing:
            data["alerts"].append(
                f"直近 {display.BOOK_TURNS} ターンの損益が赤字の施設が {len(losing)} か所あります"
            )
        data["orders"] = orders_brief(island)
        unread = letters.inbox(island).filter(read_at__isnull=True)
        data["letters"] = {
            "unread": unread.count(),
            "latest_unread": [
                {"id": x.id, "from": x.sender.name, "subject": x.subject, "turn": x.turn}
                for x in unread[:5]
            ],
        }
        if data["letters"]["unread"]:
            data["alerts"].append(f"読んでいない公電が {data['letters']['unread']} 通あります")
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
