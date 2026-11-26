"""貿易と為替の画面（フェーズ3-1）と、その JSON API の中身の組み立て。

ルールの計算はせず、世界の貿易の状態（World.ext["trade"]: 貿易圏・契約・運ぶ途中の荷）と島の
貿易の状態（Island.ext["trade"]: レート・外貨の持ち高・海外の商人）、商社・海運業・港の施設の状態、
保存した統計と勘定を、コアの読み出しの道具（hakoniwa_core/trade/）で読む。

見せる範囲（設計「フェーズ3の画面」）:
- 貿易圏（加盟国・決済通貨・圏の値段と売買量）と航路（どの島からどの島へ、何隻分の荷が向かって
  いるか）は誰にでも見せる。荷の中身（財と量）は、見ている人の島が積み出したか受け取る荷だけ。
- 島の貿易と為替の中身（契約・商社・船団・海外の商人・外貨の持ち高・非居住者の預金の内訳）は島の
  持ち主だけ。ほかの人には国全体の数（輸出・輸入・貿易収支・船の数・レート）と貿易圏の加盟だけ。
"""

from __future__ import annotations

from typing import Any

from django.db.models import Sum
from django.utils.translation import gettext

from hakoniwa_core import ledger
from hakoniwa_core.economy import is_stopped
from hakoniwa_core.modules import trade as trade_stage
from hakoniwa_core.modules.trade import contract_view
from hakoniwa_core.naming import facility_name
from hakoniwa_core.state import IslandState, WorldState
from hakoniwa_core.trade import (
    KEY,
    abroad,
    balance,
    blocs,
    contract_goods,
    currency_labels,
    food_merchant,
    is_port,
    is_trader,
    merchant,
    policy,
    port_of,
    shipping_of,
    ships,
    started,
    swap,
    tradable_goods,
    traders,
)
from hakoniwa_core.trade import contracts as contract_states
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.trade import orders as ship_orders
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, from_milli
from server.economy.models import Account
from server.game import get_game
from server.web import display, reports
from server.world import models as m
from server.world.repository import facility_from_row, island_from_row, load_money

# 貿易の画面と API に出す統計（持ち主に出す分と、ほかの人に出す国全体の数）
TRADE_STATS = [
    "trade.exports",
    "trade.imports",
    "trade.balance",
    "trade.merchant",
    "trade.merchant_gap",  # 計画経済の国で、海外の商人の値段と公定価格の差を政府が払った分
    "trade.merchant_sold",  # 海外の商人に売った額（W-186 回4）
    "trade.merchant_premium",  # 計画経済の国で、商人の買い値と公定価格の差を政府が受け取った分
    "trade.price_gap_subsidies",  # 計画経済の国で、国営の商社の輸入の価格差を政府が払った分
    "ships",
    "ships.busy",
    "freight.ship_rate",
    "port.handled",
    "trade.unpaid",
    "trade.charter_income",  # 貸した船の運賃の収入（ほかの国の荷を運んだ）
    "trade.charter_paid",  # 借りたほかの国の船に払った運賃
    "trade.tariffs",
    "trade.tariff_unpaid",
    "trade.export_subsidies",
    "trade.subsidy_unpaid",
    "aid.sent",
    "aid.received",
]
PUBLIC_TRADE_STATS = ["trade.exports", "trade.imports", "trade.balance", "ships"]
# 為替の画面と API に出す国全体の数（誰にでも）
FX_STATS = [
    "trade.exports",
    "trade.imports",
    "trade.balance",
    "trade.merchant",
    "trade.merchant_gap",
    "trade.merchant_sold",
    "fx.holdings",
    "money.nonresident",
    "fx.reserves",
    "fx.swap.borrowed",
    "fx.swap.lent",
    "fx.swap.waived",
    "bonds.foreign",
]
# 国際収支の内訳（フェーズ3-3。誰にでも。hakoniwa_core/trade/balance.py）
BOP_STATS = [
    "bop.current",
    *(f"bop.{item}" for item in balance.CURRENT_ITEMS),
    "bop.financial",
    *(f"bop.{item}" for item in balance.ASSET_ITEMS),
    *(f"bop.{item}" for item in balance.LIABILITY_ITEMS),
]
# 推移の API で返す、レート以外の系列
FX_HISTORY = [
    "trade.exports",
    "trade.imports",
    "trade.balance",
    "fx.holdings",
    "money.nonresident",
    "fx.reserves",
    "bop.current",
    "bop.financial",
]

SIDE_NAMES = {"sell": "売る", "buy": "買う"}
DELIVERY_NAMES = {
    contract_states.MARKET: "市場に出す",
    contract_states.WAREHOUSE: "国の倉庫に置く",
}
STATE_NAMES = {
    contract_states.PROPOSED: "申し込み中",
    contract_states.ACTIVE: "有効",
    contract_states.ENDED: "終了",
}
DIRECTION_NAMES = {traders.EXPORT: "輸出", traders.IMPORT: "輸入"}
# 輸入の狙う量を圏で買えなかったわけ（前のターン）
WHY_NAMES = {
    trade_stage.WHY_NO_PRICE: "圏にまだ値段がない",
    trade_stage.WHY_PRICE: "運賃と港の使用料を引くと、島の値段では引き合わない",
    trade_stage.WHY_SHIPS: "空いている輸送船が足りない",
    trade_stage.WHY_CAPACITY: "商社の1ターンに扱える重さの上限に届いた",
    trade_stage.WHY_MONEY: "払う決済通貨が足りない（為替で買えた外貨が足りない）",
    trade_stage.WHY_SELLERS: "圏の売りが足りないか、売りの値段が高い",
    trade_stage.WHY_BUYERS: "圏の買いが足りないか、買いの値段が安い",
    trade_stage.WHY_FUNDS: "払うお金（商社の預金）が足りない",
    trade_stage.WHY_QUOTA: "輸入の割り当ての上限まで買った",
    trade_stage.WHY_BLOCKADE: "封鎖で船出を断られた",
    trade_stage.WHY_PRIORITY: "外貨を、燃料・主食や設備など先に使う物に取り置いた（計画経済）",
}
SHIPMENT_KINDS = {"bloc": "貿易圏", "contract": "二国間契約"}


def _why_text(item: dict[str, Any]) -> str:
    """届かなかったわけ。注文を出す前のわけと、板寄せで約定しきらなかったわけ（W-308 案 4a）が
    違えば両方。"""
    before = WHY_NAMES.get(item.get("why"), "")
    after = WHY_NAMES.get(item.get(trade_stage.MARKET_WHY), "")
    if after and before and after != before:
        return f"{before}。出した注文も、{after}"
    return before or after


# 外貨の持ち高と非居住者の預金の持ち手（施設はまとめて「商社など」）
FACILITIES = "facilities"
HOLDER_NAMES = {
    ledger.GOV: "政府",
    ledger.CB: "中央銀行（外貨準備）",
    FACILITIES: "商社など（合計）",
}
# 資本取引の制限（フェーズ3-3）
CAPITAL_NAMES = {
    policy.FREE: "自由（誰でも外国の国債を売り買いできる）",
    policy.OFFICIAL: "政府と中央銀行だけ",
    policy.CLOSED: "禁止（外国の国債を新しく買えず、外国にも自国の国債を売らない）",
}
# 外国の国債の持ち手
BOND_HOLDER_NAMES = {ledger.GOV: "政府", ledger.CB: "中央銀行", abroad.BANKS: "市中銀行"}


# --- 世界 ------------------------------------------------------------------------------


def world_state(world: m.World) -> dict[str, list[dict[str, Any]]]:
    """世界の貿易の状態（読むだけ。まだなければ空の並び）。"""
    state = (world.ext or {}).get(KEY) or {}
    return {
        key: state.get(key, []) for key in ("blocs", "contracts", "shipments", "contract_changes")
    }


def directory(world: m.World) -> dict[int, dict[str, Any]]:
    """世界の島の番号 → 国名・通貨の名前と記号・世界マップの位置・休止中か。"""
    out = {}
    for row in world.islands.order_by("id").values("id", "name", "names", "wx", "wy", "inactive"):
        names = row["names"] or {}
        out[row["id"]] = {
            "name": row["name"],
            "currency": names.get("currency", row["name"]),
            "symbol": names.get("currency_symbol", ""),
            "wx": row["wx"],
            "wy": row["wy"],
            "inactive": row["inactive"],
        }
    # 同じ名前の通貨がほかの島にもあれば、国名を前に付ける
    labels = currency_labels({i: (e["name"], e["currency"]) for i, e in out.items()})
    for island_id, entry in out.items():
        entry["currency"] = labels[island_id]
    return out


def _percent(value: float, base: float, digits: int) -> float:
    """value が base より何 % 高いか（-0.0 は 0.0 にする）。"""
    return round((value / base - 1) * 100, digits) or 0.0


def _name(islands: dict[int, dict[str, Any]], island_id: int) -> str:
    entry = islands.get(island_id)
    return entry["name"] if entry else f"島 {island_id}"


def _currency(islands: dict[int, dict[str, Any]], island_id: int) -> str:
    entry = islands.get(island_id)
    return entry["currency"] if entry else f"通貨 {island_id}"


def trading_islands(world: m.World) -> dict[int, IslandState]:
    """貿易の仕組みを置き終えた島（番号 → マスと施設を読まない島の状態）。"""
    out = {}
    for row in world.islands.order_by("id"):
        state = island_from_row(row)
        if started(state):
            out[row.id] = state
    return out


def port_islands(world: m.World) -> set[int]:
    """動いている港（建っていて、閉鎖中・差し押さえ中でない）のある島。"""
    config = get_game().config
    rows = m.Facility.objects.filter(
        island__world=world, kind=config.get("trade.port_facility"), built=True
    )
    return {row.island_id for row in rows if not is_stopped(facility_from_row(row))}


def bloc_goods(bloc: dict[str, Any]) -> list[dict[str, Any]]:
    """圏の市場の財ごとの値段（決済通貨）・前のターンからの変わり方・約定した量・買い・売り。"""
    defs = get_game().defs
    rows = []
    for good in display.goods_in_order(defs):
        price = bloc.get("prices", {}).get(good)
        if not price:
            continue
        row: dict[str, Any] = {
            "good": good,
            "name": defs.name("goods", good),
            "unit": defs.unit_name(good),
            "price": display.price(good, price),
            "volume": display.amount(bloc.get("volume", {}).get(good, 0), good),
            "demand": display.amount(bloc.get("demand", {}).get(good, 0), good),
            "supply": display.amount(bloc.get("supply", {}).get(good, 0), good),
        }
        history = bloc.get("history", {}).get(good, [])
        if len(history) >= 2 and history[-2]:
            row["change"] = _percent(history[-1], history[-2], 1)
        rows.append(row)
    return rows


def bloc_view(
    bloc: dict[str, Any], islands: dict[int, dict[str, Any]], goods: bool = True
) -> dict[str, Any]:
    """誰にでも見せる貿易圏の様子（加盟国・代表・決済通貨。goods なら圏の市場の財ごとの数も）。"""
    out: dict[str, Any] = {
        "id": bloc["id"],
        "name": bloc["name"],
        "rep": bloc["rep"],
        "rep_name": _name(islands, bloc["rep"]),
        "members": [{"id": i, "name": _name(islands, i)} for i in bloc["members"]],
        "open": bloc["open"],
        "currency": bloc["currency"],
        "currency_name": _currency(islands, bloc["currency"]),
        "currency_symbol": islands.get(bloc["currency"], {}).get("symbol", ""),
        "created": bloc.get("created"),
        # 圏の決まり（フェーズ3-3）: 圏内の関税をなくすか、財ごとの固定価格（決済通貨）
        "no_tariff": blocs.no_tariff(bloc),
        "fixed": _fixed_rows(bloc),
    }
    if goods:
        out["goods"] = bloc_goods(bloc)
    return out


def _fixed_rows(bloc: dict[str, Any]) -> list[dict[str, Any]]:
    defs = get_game().defs
    return [
        {
            "good": good,
            "name": defs.name("goods", good),
            "unit": defs.unit_name(good),
            "price": display.price(good, p),
        }
        for good, p in sorted(bloc.get("fixed", {}).items())
    ]


def routes(
    shipments: list[dict[str, Any]], islands: dict[int, dict[str, Any]], own: set[int]
) -> list[dict[str, Any]]:
    """運ぶ途中の荷を、積み出した島と受け取る島の組ごとにまとめた航路（船の数は積んだ重さから）。

    荷の中身（財ごとの量と、一番早く着くターン）は、own の島が積み出したか受け取る航路だけに付ける。
    """
    game = get_game()
    defs, config = game.defs, game.config
    groups: dict[tuple[int, int], dict[str, Any]] = {}
    for s in sorted(shipments, key=lambda s: s["id"]):
        key = (s["from"], s["to"])
        group = groups.setdefault(key, {"load": 0, "count": 0, "cargo": {}})
        group["load"] += s["load"]
        group["count"] += 1
        if s["from"] in own or s["to"] in own:
            cargo = group["cargo"].setdefault(s["good"], {"qty": 0, "arrive": s["arrive"]})
            cargo["qty"] += sum(q for _, q in s["batches"])
            cargo["arrive"] = min(cargo["arrive"], s["arrive"])
    out = []
    for (source, target), group in sorted(groups.items()):
        row: dict[str, Any] = {
            "from": source,
            "from_name": _name(islands, source),
            "to": target,
            "to_name": _name(islands, target),
            "shipments": group["count"],
            "load": display.tons(group["load"]),  # 見せるトン
            "ships": round(from_milli(ships.ships_of_load(config, group["load"])), 2),
        }
        if source in own or target in own:
            row["mine"] = True
            row["cargo"] = [
                {
                    "good": good,
                    "name": defs.name("goods", good),
                    "unit": defs.unit_name(good),
                    "amount": display.amount(c["qty"], good),
                    "arrive": c["arrive"],
                }
                for good, c in sorted(group["cargo"].items())
            ]
        out.append(row)
    return out


def world_trade(world: m.World, own: set[int]) -> dict[str, Any]:
    """世界マップの貿易: 貿易圏、島ごとの加盟と港、航路（荷の中身は own の島の分だけ）。"""
    state = world_state(world)
    islands = directory(world)
    ports = port_islands(world)
    member: dict[int, list[int]] = {}
    for bloc in state["blocs"]:
        for island_id in bloc["members"]:
            member.setdefault(island_id, []).append(bloc["id"])
    return {
        "turn": world.turn,
        "blocs": [bloc_view(b, islands) for b in state["blocs"]],
        "islands": [
            {
                "id": island_id,
                "name": entry["name"],
                "wx": entry["wx"],
                "wy": entry["wy"],
                "port": island_id in ports,
                "blocs": member.get(island_id, []),
            }
            for island_id, entry in islands.items()
        ],
        "routes": routes(state["shipments"], islands, own),
    }


# --- 島の貿易 --------------------------------------------------------------------------


def trade_data(
    island: m.Island, mine: bool, currency: str, stats: dict[str, float] | None = None
) -> dict[str, Any]:
    """島の貿易の API の中身。だれにでも国全体の数と貿易圏の加盟を、持ち主には貿易圏の値段と自国の
    値段の比べ・契約・商社・船団と港・運ぶ途中の荷・海外の商人と、命令の入力に使う一覧も返す。"""
    config = get_game().config
    world = island.world
    turn, latest = display.latest_stats(island)
    stats = latest if stats is None else stats
    islands = directory(world)
    state = world_state(world)
    here = display.island_for_naming(island)
    member = [b for b in state["blocs"] if island.id in b["members"]]
    data: dict[str, Any] = {
        "island": island.id,
        "mine": mine,
        "turn": turn,
        "world_turn": world.turn,
        "currency": currency,
        "currency_name": _currency(islands, island.id),
        "started": started(here),
        "port": port_of(here, config) is not None,
        **reports.stats_part(stats, TRADE_STATS if mine else PUBLIC_TRADE_STATS),
        "blocs": [bloc_view(b, islands, goods=False) for b in member],
    }
    data["partner_stats"] = partner_rows(island, islands, currency)
    if not mine:
        data["overview"] = overview(data, stats, currency)
        return data
    data["blocs"] = [_member_bloc(b, here, islands) for b in member]
    data["other_blocs"] = [
        {
            "id": b["id"],
            "name": b["name"],
            "open": b["open"],
            "rep_name": _name(islands, b["rep"]),
            "members": len(b["members"]),
            "currency_name": _currency(islands, b["currency"]),
            "applied": island.id in b.get("applicants", []),
        }
        for b in state["blocs"]
        if island.id not in b["members"]
    ]
    data["contracts"] = contract_rows(
        state["contracts"],
        island.id,
        world.turn,
        islands,
        state["shipments"],
        state["contract_changes"],
    )
    data["traders"] = trader_rows(here, islands, state["blocs"])
    data["fleet"] = fleet_view(island, here, stats)
    data["shipments"] = shipment_rows(state["shipments"], island.id, islands, state["blocs"])
    data["merchant"] = merchant_view(here, world.turn)
    data["partners"] = [
        {"id": i, "name": _name(islands, i), "currency_name": _currency(islands, i)}
        for i in trading_islands(world)
        if i != island.id
    ]
    defs = get_game().defs
    allowed = set(contract_goods(defs))
    data["goods"] = [
        {
            "good": g,
            "name": defs.name("goods", g),
            "unit": defs.unit_name(g),
            "home_price": _price_or_none(g, here.economy.prices.get(g)),
        }
        for g in display.goods_in_order(defs)
        if g in allowed
    ]
    data["cancel_turns"] = config.get("trade.contract_cancel_turns")
    data["policy"] = policy_view(world, here, islands)
    data["overview"] = overview(data, stats, currency)
    data["orders"] = trade_orders(island)
    # いますべきこと（ホームの今月すべきことの貿易の行。overview は todo を通してこのモジュールを
    # 読むので、ここで読む）
    from server.web.overview import trade_todo

    data["todo"] = trade_todo(island)
    return data


# 貿易の概要に出す統計（誰にでも。W-247 (5)）
OVERVIEW_STATS = ["trade.exports", "trade.imports", "trade.balance"]
# 貿易の命令（概要の打てる手の箱に、出ている命令と前のターンの結果を出す種類）
TRADE_ORDERS = (
    "propose_contract",
    "accept_contract",
    "cancel_contract",
    "set_contract_delivery",
    "propose_contract_change",
    "accept_contract_change",
    "create_bloc",
    "join_bloc",
    "leave_bloc",
    "bloc_settings",
    "transfer_bloc",
    "take_over_bloc",
    "set_tariff",
    "set_export_subsidy",
    "set_import_quota",
    "set_embargo",
    "send_aid",
    "set_ship_lending",
)


def overview(data: dict[str, Any], stats: dict[str, float], currency: str) -> dict[str, Any]:
    """貿易の概要（W-247 (5)）: 入っている貿易圏・輸出と輸入と貿易収支と、持ち主には運ぶ途中の荷の
    重さと契約の数。trade_data の値を数えただけで、新しい計算はしない。"""
    out: dict[str, Any] = {
        "blocs": [
            {"id": b["id"], "name": b["name"], "is_rep": b["rep"] == data["island"]}
            for b in data["blocs"]
        ],
        "rows": reports.stat_rows(stats, OVERVIEW_STATS, currency),
    }
    if not data["mine"]:
        return out
    contracts = data["contracts"]
    proposed = [c for c in contracts if c["state"] == contract_states.PROPOSED]
    active = [c for c in contracts if c["state"] == contract_states.ACTIVE]
    moving = [s for s in data["shipments"] if s["direction"] != "lent"]
    out["contracts"] = {
        "active": len(active),
        "offered": sum(1 for c in proposed if c["can_accept"]),  # 相手からの申し込み
        "waiting": sum(1 for c in proposed if c["proposed_by_me"]),  # 相手の返事待ち
        "short": sum(1 for c in active if c["short_any"]),  # 直近に届けきれなかった契約
    }
    out["shipments"] = {
        "count": len(moving),
        "export_tons": round(sum(s["load"] for s in moving if s["direction"] == traders.EXPORT), 1),
        "import_tons": round(sum(s["load"] for s in moving if s["direction"] == traders.IMPORT), 1),
    }
    return out


# 貿易の相手ごとの統計の、島でない相手の表示名（#5455）。bloc は #5605 より前の記録にだけ
# 残る（新しい記録では圏の市場の分も実際の相手の国ごとに出る。12 ターンで消える）
PARTNER_LABELS = {"bloc": "圏の市場（相手は混ざる）", "merchant": "海外の商人"}
# 経路の表示名（#5605）
ROUTE_LABELS = {"bloc": "圏の市場", "contract": "二国間契約", "merchant": "海外の商人"}


def _partner_route_parts(name: str) -> tuple[str, str, str]:
    """統計名 partner_route.<相手>.<輸出入>.<経路> を (相手, 輸出入, 経路) に分ける。形の違う
    名前は記録側（books.add_partner_trade）の不具合なので、黙って捨てずに ValueError にして
    試験で気づけるようにする（#6188）。"""
    parts = name.split(".")
    if len(parts) != 4:
        raise ValueError(
            gettext("partner_route の統計名の形が違います: {name}").format(name=repr(name))
        )
    return parts[1], parts[2], parts[3]


def partner_rows(
    island: m.Island, islands: dict[int, dict[str, Any]], currency: str
) -> list[dict[str, Any]]:
    """貿易の相手ごとの輸出・輸入（#5455）: 統計 partner.* を集計ターンと直近 1 年（12 ターン）
    で足し上げた表。相手は島の番号か海外の商人（merchant）。圏の市場の分も、清算で決まった
    実際の相手の国ごとに分かれる（#5605）。各行には経路（圏の市場・二国間契約・海外の商人）
    ごとの直近 1 年の内訳 routes を添える。表では経路の注を金額の欄の 2 行目に小さく出す
    ので、輸出・輸入の片側ずつの route_exports・route_imports も添える。主な品目は直近
    1 年の額の大きい順に 3 つまで。
    記録が無い相手は行に出ない。"""
    turn, _ = display.latest_stats(island)
    if turn is None:
        return []
    rows = island.stats.filter(turn__gt=turn - 12, name__startswith="partner.").values_list(
        "turn", "name", "value"
    )
    totals: dict[str, dict[str, float]] = {}
    goods: dict[str, dict[str, float]] = {}
    for row_turn, name, value in rows:
        parts = name.split(".")
        if len(parts) < 3:
            continue
        partner, side = parts[1], parts[2]
        entry = totals.setdefault(partner, {"exports": 0.0, "imports": 0.0})
        if len(parts) == 3:
            entry[side] += value
            if row_turn == turn:
                entry[f"{side}_turn"] = entry.get(f"{side}_turn", 0.0) + value
        else:
            good = ".".join(parts[3:])
            by_good = goods.setdefault(partner, {})
            by_good[good] = by_good.get(good, 0.0) + value
    route_rows = island.stats.filter(
        turn__gt=turn - 12, name__startswith="partner_route."
    ).values_list("name", "value")
    routes: dict[str, dict[str, dict[str, float]]] = {}
    for name, value in route_rows:
        partner, side, route = _partner_route_parts(name)
        by_route = routes.setdefault(partner, {}).setdefault(
            route, {"exports": 0.0, "imports": 0.0}
        )
        by_route[side] += value
    defs = get_game().defs

    def good_name(good: str) -> str:
        return "運賃" if good == "freight" else defs.name("goods", good)

    def route_entries(partner: str) -> list[dict[str, Any]]:
        entries = [
            {
                "route": route,
                "label": ROUTE_LABELS.get(route, route),
                "exports": display.stat_text(
                    "trade.exports", amounts["exports"], "money", currency
                ),
                "imports": display.stat_text(
                    "trade.imports", amounts["imports"], "money", currency
                ),
                "_total": amounts["exports"] + amounts["imports"],
            }
            for route, amounts in routes.get(partner, {}).items()
            if amounts["exports"] or amounts["imports"]
        ]
        entries.sort(key=lambda entry: -entry["_total"])
        for entry in entries:
            del entry["_total"]
        return entries

    def route_side_entries(partner: str, side: str) -> list[dict[str, Any]]:
        """経路の内訳を輸出・輸入の片側だけに絞ったもの。相手国の表では、経路の注を
        直近 1 年の金額の欄の 2 行目に小さく出すので、欄ごとにその側の額だけを並べる。"""
        entries = [
            {
                "label": ROUTE_LABELS.get(route, route),
                "amount": display.stat_text(f"trade.{side}", amounts[side], "money", currency),
                "_total": amounts[side],
            }
            for route, amounts in routes.get(partner, {}).items()
            if amounts[side]
        ]
        entries.sort(key=lambda entry: -entry["_total"])
        for entry in entries:
            del entry["_total"]
        return entries

    out = []
    for partner, entry in totals.items():
        top = sorted(goods.get(partner, {}).items(), key=lambda kv: -kv[1])[:3]
        out.append(
            {
                "partner": partner,
                "name": PARTNER_LABELS.get(partner)
                or (_name(islands, int(partner)) if partner.isdigit() else partner),
                "exports_turn": display.stat_text(
                    "trade.exports", entry.get("exports_turn", 0.0), "money", currency
                ),
                "imports_turn": display.stat_text(
                    "trade.imports", entry.get("imports_turn", 0.0), "money", currency
                ),
                "exports_year": display.stat_text(
                    "trade.exports", entry["exports"], "money", currency
                ),
                "imports_year": display.stat_text(
                    "trade.imports", entry["imports"], "money", currency
                ),
                "goods": [{"good": g, "name": good_name(g)} for g, _ in top],
                "routes": route_entries(partner),
                "route_exports": route_side_entries(partner, "exports"),
                "route_imports": route_side_entries(partner, "imports"),
                "_total": entry["exports"] + entry["imports"],
            }
        )
    out.sort(key=lambda row: -row["_total"])
    for row in out:
        del row["_total"]
    return out


def trade_orders(island: m.Island) -> dict[str, Any]:
    """貿易の命令のうち、待っている列にあるものと、直近に実行したターンの結果（持ち主だけ。品目の
    ページの打てる手の箱と同じ形）。"""
    pending = [
        display.describe_order(o.kind, o.payload)
        for o in island.orders.filter(status="pending", kind__in=TRADE_ORDERS).order_by("seq", "id")
    ]
    done = island.orders.exclude(status="pending").filter(kind__in=TRADE_ORDERS)
    last = done.order_by("-executed_turn").values_list("executed_turn", flat=True).first()
    recent = [
        {
            "text": display.describe_order(o.kind, o.payload),
            "status": o.status,
            "reason": display.message(o.reason, **(o.reason_args or {})) if o.reason else "",
        }
        for o in done.filter(executed_turn=last).order_by("seq")
    ]
    return {"pending": pending, "recent": recent, "recent_turn": last if recent else None}


def _amount(good: str, milli: int) -> float:
    """量を見せる量に（丸めない。0.001 トンの未達が「不足なし」に見えないように）。"""
    return round(get_game().defs.shown_amount(good, from_milli(milli)), 3)


def _price_or_none(good: str, price: int | None) -> float | None:
    return None if price is None else display.price(good, price)


def policy_view(
    world: m.World, here: IslandState, islands: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    """産業政策（フェーズ3-3）: 財ごとの関税・輸出補助・輸入の割り当て、禁輸している国と、この国に
    禁輸している国（持ち主向け）。"""
    defs = get_game().defs
    rules = here.policy
    goods = []
    tradable = set(tradable_goods(defs))
    for good in (g for g in display.goods_in_order(defs) if g in tradable):
        tariff = policy.tariff_rate(here, good)
        subsidy = policy.export_subsidy(here, good)
        quota = policy.quota(here, good)
        if not tariff and not subsidy and quota is None:
            continue
        goods.append(
            {
                "good": good,
                "name": defs.name("goods", good),
                "unit": defs.unit_name(good),
                "tariff": tariff * 100 / BASIS if tariff else None,  # %
                "subsidy": display.price(good, subsidy) if subsidy else None,  # 見せる1単位あたり
                "quota": display.amount(quota, good) if quota is not None else None,
            }
        )
    embargoes = [
        {
            "partner": int(key),
            "partner_name": _name(islands, int(key)),
            "goods": [defs.name("goods", g) for g in sorted(items)],
        }
        for key, items in sorted(rules.embargoes.items(), key=lambda kv: int(kv[0]))
    ]
    against = []
    for row in world.islands.exclude(pk=here.id).order_by("id").values("id", "policy"):
        items = ((row["policy"] or {}).get("embargoes") or {}).get(str(here.id))
        if items is not None:
            against.append(
                {
                    "partner": row["id"],
                    "partner_name": _name(islands, row["id"]),
                    "goods": [defs.name("goods", g) for g in sorted(items)],
                }
            )
    return {"goods": goods, "embargoes": embargoes, "embargoed_by": against}


def _member_bloc(
    bloc: dict[str, Any], here: IslandState, islands: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    """加盟している圏の様子（持ち主向け）: 圏の値段を今のレートで自国通貨に直したものと、島の値段の
    比べ。代表なら申し込みの一覧も。"""
    view = bloc_view(bloc, islands)
    e = fxrates.rate(here, bloc["currency"])
    prices = here.economy.prices
    for row in view["goods"]:
        good = row["good"]
        own = bloc["prices"][good] * e // BASIS
        home = prices.get(good)
        row["price_own"] = display.price(good, own)
        row["home_price"] = _price_or_none(good, home)
        if home:
            row["gap"] = _percent(own, home, 1)  # 圏が島より何 % 高いか
    rep = islands.get(bloc["rep"], {})
    view.update(
        rate=e / BASIS,
        is_rep=bloc["rep"] == here.id,
        rep_inactive=bool(rep.get("inactive")) and bloc["rep"] != here.id,
        leaving=here.id in bloc.get("leaving", []),
        applicants=[{"id": i, "name": _name(islands, i)} for i in bloc.get("applicants", [])],
    )
    return view


def contract_rows(
    contracts: list[dict[str, Any]],
    island_id: int,
    turn: int,
    islands: dict[int, dict[str, Any]],
    shipments: list[dict[str, Any]] | None = None,
    contract_changes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """島が当事者の二国間契約（申し込み中・有効・終わったもの）。turn は世界の今のターン。

    shipments は運ぶ途中の荷。契約の荷のうち、まだ海の上の量と、いちばん早く着く見込みのターンを
    出す（送り出した量は、積んで代金を払った時点で足す。届くのは船が着いてから。W-226）。
    """
    defs = get_game().defs
    wait = get_game().config.get("trade.contract_cancel_turns")
    sailing: dict[int, list[int]] = {}  # 契約の番号 → [海の上の量, 早く着くターン, 遅く着くターン]
    for s in shipments or []:
        if s.get("kind") != "contract":
            continue
        qty = sum(q for _, q in s["batches"])
        item = sailing.setdefault(s["ref"], [0, s["arrive"], s["arrive"]])
        item[0] += qty
        item[1] = min(item[1], s["arrive"])
        item[2] = max(item[2], s["arrive"])
    rows = []
    for c in contracts:
        if island_id not in (c["seller"], c["buyer"]):
            continue
        view = contract_view(c, island_id, turn)
        at_sea, first_arrive, last_arrive = sailing.get(c["id"], [0, None, None])
        # 着く予定のターンを過ぎた荷は、買う国の港の荷下ろしの空きを待っている（次のターン以降）
        waiting = first_arrive is not None and first_arrive <= turn
        if waiting:
            first_arrive = max(first_arrive, turn + 1)
            last_arrive = max(last_arrive, turn + 1)
        state = view["state"]
        proposed = state == contract_states.PROPOSED
        rows.append(
            {
                "id": view["id"],
                "state": state,
                "state_name": STATE_NAMES.get(state, state),
                "side": view["side"],
                "side_name": SIDE_NAMES[view["side"]],
                "partner": view["partner"],
                "partner_name": _name(islands, view["partner"]),
                "proposed_by_me": view["proposer"] == island_id,
                "aid": bool(c.get("aid")),  # 物の援助（値段 0 の片道の契約。フェーズ3-3）
                "good": view["good"],
                "good_name": defs.name("goods", view["good"]),
                "unit": defs.unit_name(view["good"]),
                "quantity": _amount(view["good"], view["qty"]),  # 1ターンの量
                "price": display.price(view["good"], view["price"]),
                "currency": view["currency"],
                "currency_name": _currency(islands, view["currency"]),
                "currency_symbol": islands.get(view["currency"], {}).get("symbol", ""),
                "turns": c.get("turns"),
                "indefinite": view["indefinite"],  # 無期限の契約（FX-68）
                "start": view["start"] or None,
                "end": view["end"] or None,
                "active": view["active"],
                # 送り出した量と不足は丸めない（0.001 トンの未達が「不足なし」に見えた。Codex の
                # 報告）。送り出した量は積んで代金を払った量の合計で、まだ海の上の分を含む（W-226）
                "shipped": _amount(view["good"], view["delivered"]),
                "at_sea": _amount(view["good"], at_sea),  # まだ海の上の量
                "arrive_first": first_arrive,  # 海の上の荷が着く見込みのターン（早いもの）
                "arrive_last": last_arrive,  # 同じく遅いもの（積んだターンが違う荷があるとき）
                "port_wait": waiting,  # 港の荷下ろしの空きを待っている荷がある
                "paid": c.get("paid", 0),
                "short": _amount(view["good"], view["short"]),
                "short_any": view["short"] > 0,
                "short_why": view["short_why"],  # goods・money・fx・blockade・ships・port
                # 外貨が足りずに届かなかった直近のターンに、払う外貨をどこからいくら用意できたか
                # （買う側だけ。契約の通貨の額。turn・need・had・reserve・market・short）
                "fx": view["fx"],
                # 届いた物の行き先（買う側だけが決める。決める前は市場）
                "delivery": view["delivery"] if view["side"] == "buy" else "",
                "delivery_name": DELIVERY_NAMES.get(view["delivery"], "")
                if view["side"] == "buy"
                else "",
                "can_accept": proposed and view["proposer"] != island_id,
                # 有効な契約は、やめる命令を出してもまだ先に終わる予定のときだけ
                "can_cancel": proposed
                or (
                    state == contract_states.ACTIVE
                    and (view["end"] is None or view["end"] > turn + 1 + wait)
                ),
                # 受け取り方の切り替え（FX-69）: 買う側だけ・有効な契約だけ（条約の契約は除く）
                "can_set_delivery": state == contract_states.ACTIVE
                and view["side"] == "buy"
                and not c.get("treaty"),
                # 条件の変更の申し込み（FX-70）: 有効な契約だけ。物の援助・条約の契約と、
                # やめる命令を出した契約は変えられない（core の propose_change の判定に合わせる）
                "can_propose_change": state == contract_states.ACTIVE
                and not c.get("aid")
                and not c.get("treaty")
                and c.get("cancelled_by") is None,
                # 申し込み中の条件の変更（FX-70。相手のものだけ受けられる）
                "changes": [
                    {
                        "id": ch["id"],
                        "mine": ch["proposer"] == island_id,
                        "proposer_name": _name(islands, ch["proposer"]),
                        "quantity": _amount(view["good"], ch["qty"])
                        if ch.get("qty") is not None
                        else None,
                        "price": display.price(view["good"], ch["price"])
                        if ch.get("price") is not None
                        else None,
                        "turns": ch.get("turns"),
                        "indefinite": bool(ch.get("indefinite")),
                    }
                    for ch in contract_changes or []
                    if ch["contract"] == c["id"] and ch.get("accepted") is None
                ],
            }
        )
    order = {contract_states.PROPOSED: 0, contract_states.ACTIVE: 1, contract_states.ENDED: 2}
    return sorted(rows, key=lambda r: (order.get(r["state"], 3), r["id"]))


def trader_rows(
    here: IslandState, islands: dict[int, dict[str, Any]], blocs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """島の商社（工事中・閉鎖中を含む）と、財ごとの向き・狙う量・持つ量・手元の荷・運ぶ途中の荷。"""
    game = get_game()
    defs, config = game.defs, game.config
    names = {b["id"]: b["name"] for b in blocs}
    out = []
    for f in sorted(here.facilities, key=lambda f: f.id):
        if not is_trader(config, f):
            continue
        goods = []
        for good, item in sorted(traders.goods_of(f).items()):
            direction = item.get("dir")
            held, cost = item.get("held", [0, 0])
            transit = item.get("transit", 0)
            bloc = item.get("bx") if direction == traders.EXPORT else item.get("bm")
            target = item.get("x", 0) if direction == traders.EXPORT else item.get("m", 0)
            goods.append(
                {
                    "good": good,
                    "name": defs.name("goods", good),
                    "unit": defs.unit_name(good),
                    "direction": direction or "",
                    "direction_name": DIRECTION_NAMES.get(direction, "休み"),
                    "target": display.amount(target if direction else 0, good),  # 1ターンに狙う量
                    "held": display.amount(max(0, held - transit), good),  # 港の倉庫にある荷
                    "transit": display.amount(transit, good),  # 船で運ぶ途中の荷
                    # 輸入の荷の持つ量（手元と運ぶ途中を合わせて。FX-66）
                    "hold": display.amount(item.get("hold", 0), good)
                    if direction == traders.IMPORT
                    else None,
                    "unit_cost": display.price(good, cost * QUANTITY_SCALE // held)
                    if held > 0
                    else None,
                    "age": item.get("age", 0),  # 売れ残ったターン数
                    "bloc": bloc,
                    "bloc_name": names.get(bloc, "") if bloc is not None else "",
                    "why": _why_text(item) if direction else "",
                }
            )
        out.append(
            {
                "number": f.id,
                "name": facility_name(defs, config, here, f),
                "x": f.x,
                "y": f.y,
                "owner_name": display.message(f"owner_{f.owner}"),
                "built": f.built,
                "status_text": display.status_text(f),
                "workers": f.workers,
                "goods": goods,
            }
        )
    return out


def fleet_view(island: m.Island, here: IslandState, stats: dict[str, float]) -> dict[str, Any]:
    """海運業の船団（船の数・積める量・航海に使っている量・運賃・燃料）と港（使用料・積み下ろしの
    量と上限）。"""
    game = get_game()
    defs, config = game.defs, game.config
    capacity = config.get("trade.ship_capacity")
    weight = defs.weight_scale()  # 荷の重さは見せるトンで（W-30）
    out: dict[str, Any] = {
        "ship_capacity": capacity * weight,
        "shipping": None,
        "port": None,
        # 空いている船を、圏の市場のほかの国の荷のために貸すか（命令 set_ship_lending）
        "lend_ships": here.policy.lend_ships,
    }
    f = shipping_of(here, config)
    if f is not None:
        total = ships.capacity(config, f)
        busy = ships.busy(f)
        need = ships.fuel_per_turn(config, total)
        have = dict(
            m.Stock.objects.filter(island=island, facility__number=f.id)
            .values("good")
            .annotate(qty=Sum("quantity"))
            .values_list("good", "qty")
        )
        out["shipping"] = {
            "number": f.id,
            "name": facility_name(defs, config, here, f),
            "ships": from_milli(ships.fleet(f)),
            "busy": round(ships.busy(f) / QUANTITY_SCALE / capacity, 2),
            "free": round(ships.free(config, f) / QUANTITY_SCALE / capacity, 2),
            "capacity": display.tons(total),  # 全部の船で1航海に運べるトン
            "capacity_tons": display.tons(total),
            "busy_tons": display.tons(busy),
            "free_tons": display.tons(ships.free(config, f)),
            "busy_share": busy / total if total else 0,
            "freight_rate": display.weight_price(ships.freight_rate(here, config, f)),
            "workers": f.workers,
            # 直近の、船が足りずに輸入を減らしたターンの数（数えたターンのうち）
            "short_turns": sum(f.economy.get("ships_short", [])),
            "short_of": len(f.economy.get("ships_short", [])),
            # 島の造船所に注文して、まだ届いていない輸送船（船の前注文。FX-60）
            "on_order": from_milli(ship_orders.on_order(here, f)),
            # 圏の荷を運ぶ船の貸し借り（1ターンの運賃）
            "charter_income": stats.get("trade.charter_income", 0),
            "charter_paid": stats.get("trade.charter_paid", 0),
            "fuel": [
                {
                    "good": good,
                    "name": defs.name("goods", good),
                    "unit": defs.unit_name(good),
                    "amount": display.amount(have.get(good, 0), good),
                    # 船を全部出したときに何ターン持つか
                    "turns": have.get(good, 0) // per_turn if per_turn > 0 else None,
                }
                for good, per_turn in sorted(need.items())
            ],
        }
    port = port_of(here, config)
    if port is not None:
        out["port"] = {
            "number": port.id,
            "name": facility_name(defs, config, here, port),
            "x": port.x,
            "y": port.y,
            "fee": display.weight_price(ships.port_fee(here, config)),  # 荷 1 トンあたり
            "capacity": ships.port_capacity(config, port) * weight,  # 1ターンに積み下ろせるトン
            "handled": display.stat_value("port.handled", stats.get("port.handled"), " トン"),
            "workers": port.workers,
        }
    elif any(is_port(config, p) for p in here.facilities):
        out["port_stopped"] = True  # 港はあるが工事中か止まっている
    return out


def shipment_rows(
    shipments: list[dict[str, Any]],
    island_id: int,
    islands: dict[int, dict[str, Any]],
    blocs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """島が積み出したか受け取る、運ぶ途中の荷と、ほかの国に貸した船で運んでいる荷（着く順）。圏の
    荷は、どの圏で約定したかと、ほかの国から借りた船ならその国も出す。"""
    defs = get_game().defs
    names = {b["id"]: b["name"] for b in blocs or []}
    rows = []
    for s in shipments:
        carrier = s.get("carrier", s["to"])
        if island_id not in (s["from"], s["to"], carrier):
            continue
        incoming = s["to"] == island_id
        lent = island_id not in (s["from"], s["to"])
        partner = s["to"] if lent else s["from"] if incoming else s["to"]
        if lent:
            direction, direction_name = "lent", "貸した船"
        else:
            direction = traders.IMPORT if incoming else traders.EXPORT
            direction_name = "輸入" if incoming else "輸出"
        rows.append(
            {
                "id": s["id"],
                "direction": direction,
                "direction_name": direction_name,
                "partner": partner,
                "partner_name": _name(islands, partner),
                # ほかの国から借りた船で運ぶ荷は、その船の国（貸した船の行と自分の船の荷は空）
                "carrier": carrier if carrier not in (s["to"], island_id) else None,
                "carrier_name": _name(islands, carrier)
                if carrier not in (s["to"], island_id)
                else "",
                "kind": s["kind"],
                "kind_name": SHIPMENT_KINDS.get(s["kind"], s["kind"]),
                "bloc_name": names.get(s.get("ref"), "") if s["kind"] == "bloc" else "",
                "good": s["good"],
                "name": defs.name("goods", s["good"]),
                "unit": defs.unit_name(s["good"]),
                "amount": display.amount(sum(q for _, q in s["batches"]), s["good"]),
                "load": display.tons(s["load"], s["good"]),
                "loaded": s["loaded"],
                "arrive": s["arrive"],
            }
        )
    return sorted(rows, key=lambda r: (r["arrive"], r["id"]))


def merchant_view(here: IslandState, turn: int) -> dict[str, Any]:
    """海外の商人: 次のターンに来るか、あと何ターン来るか、量を減らし始めるターンと今の量の割合、
    値段の倍率（島の物価を含む）と、売り買いする物（財・1ターンの上限・売る値段・買う値段と島の
    値段）。turn は世界の今のターン（商人は次のターンの市場で売り買いする）。"""
    game = get_game()
    defs, config = game.defs, game.config
    settings = config.get("foreign_merchant")
    coming = turn + 1
    left = merchant.turns_left(here, config, coming)
    share = merchant.taper(here, config, coming)
    since = here.ext.get(KEY, {}).get("merchant_from")
    prices = here.economy.prices
    bids = {good: price for good, _, price in merchant.bids(here, defs, config, coming)}
    return {
        "enabled": bool(settings.get("enabled")),
        "active": merchant.active(here, config, coming),
        "turns_left": left,
        "since": since,
        "until": since + settings["turns"] - 1 if since is not None else None,
        "turns": settings["turns"],
        "taper_from": settings["taper_from"],
        # 持ってくる量を減らし始めるターン（この次のターンから減る）と、今の量の割合（%）
        "taper_start": since + settings["taper_from"] if since is not None else None,
        "tapering": 0 < share < 1,
        "share": round(share * 100),
        "multiplier": round(merchant.multiplier(here, config, coming), 2),
        "buy_multiplier": settings.get("buy_multiplier", 0),
        "blockaded": bool(here.ext.get(KEY, {}).get(merchant.BLOCKADE)),
        "goods": [
            {
                "good": good,
                "name": defs.name("goods", good),
                "unit": defs.unit_name(good),
                "quantity": _amount(good, qty),  # 1ターンの上限
                "price": display.price(good, price),
                "buy_price": display.price(good, bids[good]) if good in bids else None,
                "home_price": _price_or_none(good, prices.get(good)),
            }
            for good, qty, price in merchant.offers(here, defs, config, coming)
        ],
        "food": food_merchant_view(here),
    }


def food_merchant_view(here: IslandState) -> dict[str, Any] | None:
    """物がないときの海外の食料商人（FX-73。W-239）: このターンに来ていれば、来始めたターン・売りに
    出した量・売れた量・値段・計画経済の差額の予算で量を削ったか。来ていなければ None。"""
    defs = get_game().defs
    state = here.ext.get(KEY, {}).get(food_merchant.KEY, {})
    last = state.get("last")
    if not state.get("active") or not last:
        return None
    good = get_game().config.get("food_reserve.good")
    return {
        "good": good,
        "name": defs.name("goods", good),
        "unit": defs.unit_name(good),
        "since": state.get("since"),
        "quantity": _amount(good, last["qty"]),
        "sold": _amount(good, last["sold"]),
        "price": display.price(good, last["price"]),
        "cut": bool(last.get("cut")),
    }


# --- 為替 ------------------------------------------------------------------------------


def _uses(state: dict[str, list[dict[str, Any]]], island_id: int) -> dict[int, list[str]]:
    """通貨 → その島がその通貨を使うわけ（加盟している圏の決済通貨・契約の通貨）。"""
    out: dict[int, list[str]] = {}
    for b in state["blocs"]:
        if island_id in b["members"]:
            out.setdefault(b["currency"], []).append(f"貿易圏「{b['name']}」の決済通貨")
    for c in state["contracts"]:
        if island_id in (c["seller"], c["buyer"]) and c["state"] != contract_states.ENDED:
            out.setdefault(c["currency"], []).append(f"契約（{c['id']} 番）の通貨")
    return out


def _previous_rates(island: m.Island, turn: int | None) -> dict[str, float]:
    """前のターンの統計のレート（fx.<通貨> → 値）。"""
    if turn is None:
        return {}
    rows = island.stats.filter(turn=turn - 1, name__startswith="fx.").values_list("name", "value")
    return {name: value for name, value in rows if name.removeprefix("fx.").isdigit()}


def rate_names(island: m.Island) -> list[str]:
    """統計に残っているレートの名前（fx.<通貨>。通貨の番号の順）。"""
    names = island.stats.filter(name__startswith="fx.").values_list("name", flat=True).distinct()
    found = {n for n in names if n.removeprefix("fx.").isdigit()}
    return sorted(found, key=lambda n: int(n.removeprefix("fx.")))


def fx_data(
    island: m.Island,
    mine: bool,
    currency: str,
    start: Any = None,
    end: Any = None,
    history: bool = True,
) -> dict[str, Any]:
    """為替と国際収支の API の中身。だれにでも使っている通貨のレート（と、ほかの通貨の目安の
    レート）、国全体の数、レートと貿易収支の推移を、持ち主には外貨の持ち高（政府・中央銀行・商社
    など）と、自国通貨を持つ外国の主体（非居住者の預金）の内訳も返す。

    start・end は推移のターンの範囲（reports.turn_range と同じ。決めなければ直近の 120 ターン）。
    history が偽なら推移を省く（画面はグラフを統計の系列の API で読むので）。"""
    config = get_game().config
    world = island.world
    turn, stats = display.latest_stats(island)
    islands = directory(world)
    state = world_state(world)
    trading = trading_islands(world)
    here = trading.get(island.id) or island_from_row(island)
    uses = _uses(state, island.id)
    previous = _previous_rates(island, turn)
    rates = []
    for key, e in sorted(fxrates.rates(here).items(), key=lambda kv: int(kv[0])):
        other = int(key)
        row: dict[str, Any] = {
            "currency": other,
            "name": _currency(islands, other),
            "symbol": islands.get(other, {}).get("symbol", ""),
            "island_name": _name(islands, other),
            "rate": e / BASIS,  # 相手の通貨 1 が自国通貨でいくらか
            "inverse": BASIS / e if e else None,  # 自国通貨 1 が相手の通貨でいくらか
            "uses": uses.get(other, []),
        }
        before = previous.get(f"fx.{key}")
        if before:
            row["change"] = _percent(e / BASIS, before, 2)
        # 前のターンに市場で売りと買いに出ていた外貨の量（中央銀行の注文を除く。UI-80。記録の
        # 前のターンは None）
        recorded = 0 if "fx.book" in stats else None  # 0 は書かないので、記録したターンなら 0
        row["offered"] = stats.get(f"fx.offered.{key}", recorded)
        row["wanted"] = stats.get(f"fx.wanted.{key}", recorded)
        rates.append(row)
    world_view = WorldState(
        turn=world.turn,
        seed=world.seed,
        islands={**trading, island.id: here},
        ext={KEY: {"blocs": state["blocs"]}},
    )
    cross = []
    for other in sorted(trading):
        if other == island.id or fxrates.has_rate(here, other):
            continue
        e = fxrates.cross(world_view, here, other)
        cross.append(
            {
                "currency": other,
                "name": _currency(islands, other),
                "symbol": islands.get(other, {}).get("symbol", ""),
                "island_name": _name(islands, other),
                "rate": e / BASIS if e else None,
            }
        )
    data: dict[str, Any] = {
        "island": island.id,
        "mine": mine,
        "turn": turn,
        "currency": currency,
        "currency_name": _currency(islands, island.id),
        "band": config.get("fx.band"),  # 1ターンに動ける幅（割合）
        "rates": rates,
        "cross": cross,
        **reports.stats_part(stats, FX_STATS),
        "bop": reports.stats_part(stats, BOP_STATS),
    }
    if history:
        first, last = reports.turn_range(island, start, end)
        names = rate_names(island) + FX_HISTORY
        data["history"] = reports.series(island, names, first, last, mine)
    if mine:
        data["holdings"] = holding_rows(here, islands)
        data["nonresident"] = nonresident_rows(island, here, islands)
        data["swaps"] = swap_view(world_view, here, islands, _previous_swaps(island, turn, stats))
        data["policy"] = fx_policy_view(here, islands, trading, rates, data["swaps"]["room"])
        data["foreign_bonds"] = foreign_bond_rows(here, islands)
    return data


def _previous_swaps(
    island: m.Island, turn: int | None, stats: dict[str, float]
) -> dict[str, float | None]:
    """融通で借りている額と貸している額の、1 ターンの増加（自国通貨。前のターンの記録がなければ
    None）。"""
    names = ("fx.swap.borrowed", "fx.swap.lent")
    if turn is None:
        return dict.fromkeys(names)
    rows = dict(island.stats.filter(turn=turn - 1, name__in=names).values_list("name", "value"))
    return {
        name: stats[name] - rows[name] if name in rows and name in stats else None for name in names
    }


def fx_policy_view(
    here: IslandState,
    islands: dict[int, dict[str, Any]],
    trading: dict[int, IslandState],
    rates: list[dict[str, Any]] | None = None,
    room: int | None = None,
) -> dict[str, Any]:
    """為替と資本取引の政策（フェーズ3-3）: 資本取引の制限、中央銀行の介入と固定相場、外国の国債の
    売り買いの命令。命令の入力に使う一覧（相手の通貨と、国債を出している国）も返す。通貨の一覧には
    前のターンに市場で売りと買いに出ていた量（rates の offered・wanted）と、融通で借りられる残り
    （room。自国通貨）をその通貨に直した額を添える（介入の欄の目安と確かめ。UI-80）。"""
    market = {r["currency"]: r for r in rates or []}

    def currency_row(key: str) -> dict[str, Any]:
        cid = int(key)
        row = market.get(cid, {})
        e = fxrates.rate(here, cid)
        return {
            "id": cid,
            "name": _currency(islands, cid),
            "offered": row.get("offered"),
            "wanted": row.get("wanted"),
            "swap_room": room * BASIS // e if room is not None and e > 0 else None,
        }

    defs = get_game().defs
    config = get_game().config
    rules = here.policy
    regime = here.regime.get("economy", "")
    mode = policy.capital_controls(here, defs)
    peg_band = config.get("fx.peg_band")
    return {
        "capital_controls": mode,
        "capital_name": CAPITAL_NAMES.get(mode, mode),
        "capital_default": not rules.capital_controls,
        "regime_name": defs.name("regimes", regime) if regime else "",
        "cb_independent": rules.cb_independent,
        "interventions": [
            {
                "currency": int(key),
                "name": _currency(islands, int(key)),
                "amount": abs(amount),
                "side": "買う" if amount > 0 else "売る",
            }
            for key, amount in sorted(rules.fx_intervention.items(), key=lambda kv: int(kv[0]))
        ],
        "pegs": [
            {
                "currency": int(key),
                "name": _currency(islands, int(key)),
                "rate": rate / BASIS,
                "low": rate * (1 - peg_band) / BASIS,
                "high": rate * (1 + peg_band) / BASIS,
            }
            for key, rate in sorted(rules.fx_peg.items(), key=lambda kv: int(kv[0]))
        ],
        "peg_band": peg_band * 100,  # 固定相場の幅（%）
        # 介入の効き方の注意（W-255。命令の定義の caution と同じ）
        "intervention_caution": display.ORDER_CAUTIONS["set_fx_intervention"],
        "bond_orders": [
            {
                "holder": holder,
                "holder_name": BOND_HOLDER_NAMES.get(holder, holder),
                "issuer": int(key),
                "issuer_name": _name(islands, int(key)),
                "currency_name": _currency(islands, int(key)),
                "amount": abs(amount),
                "side": "買う" if amount > 0 else "売る",
            }
            for holder, issuers in sorted(rules.foreign_bonds.items())
            for key, amount in sorted(issuers.items(), key=lambda kv: int(kv[0]))
        ],
        "currencies": [
            currency_row(key) for key in sorted(fxrates.rates(here), key=int) if int(key) != here.id
        ],
        "issuers": [
            {"id": i, "name": _name(islands, i), "currency_name": _currency(islands, i)}
            for i in sorted(trading)
            if i != here.id
        ],
    }


def foreign_bond_rows(
    here: IslandState, islands: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """島の政府・中央銀行・市中銀行が持つ外国の国債（発行した国ごと。額面と時価はその国の通貨、
    値打ちは今のレートで自国通貨に直した額）。"""
    out = []
    for row in abroad.view(here):
        holder = row["holder"]
        key = holder if holder in (ledger.GOV, ledger.CB) else abroad.BANKS
        out.append(
            {
                "holder": key,
                "holder_name": BOND_HOLDER_NAMES[key],
                "issuer": row["issuer"],
                "issuer_name": _name(islands, row["issuer"]),
                "symbol": islands.get(row["issuer"], {}).get("symbol", ""),
                "face": row["face"],
                "value": row["value"],
                "own": row["own"],
            }
        )
    merged: dict[tuple[str, int], dict[str, Any]] = {}
    for r in out:  # 市中銀行はまとめる
        slot = merged.setdefault((r["holder"], r["issuer"]), {**r, "face": 0, "value": 0, "own": 0})
        for k in ("face", "value", "own"):
            slot[k] += r[k]
    order = {ledger.GOV: 0, ledger.CB: 1, abroad.BANKS: 2}
    return sorted(merged.values(), key=lambda r: (order[r["holder"]], r["issuer"]))


def swap_view(
    world_view: WorldState,
    here: IslandState,
    islands: dict[int, dict[str, Any]],
    changes: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    """中央銀行どうしの融通（フェーズ3-3 の続き。hakoniwa_core/trade/swap.py）: 借りている相手と
    貸している相手ごとの額（その通貨と、自国通貨に直した額）と金利（年率 %）、まだ借りられる額と、
    決済通貨を発行した国なら、まだ新しく作って貸せる額。上限の割合は %。為替のタブの頭に出す
    借りの限度額（limit）と貸している額（lent）、1 ターンの増加（changes。UI-80）も。"""
    config = get_game().config
    view = swap.view(world_view, here, config)
    for field, rows in (("lender", view["owed"]), ("borrower", view["lent"])):
        for row in rows:
            other = row[field]
            row["name"] = _currency(islands, row["currency"])
            row["symbol"] = islands.get(row["currency"], {}).get("symbol", "")
            row["other_name"] = _name(islands, other)
            # 発行した国が新しく作って貸した分（借りた側では貸し手が、貸した側では通貨が自国）
            maker = other if field == "lender" else here.id
            row["issued"] = row["currency"] == maker
            row["rate"] = row["rate"] * 100 / BASIS
    view["quota"] = config.get("fx.swap_quota") * 100
    view["issuer_share"] = config.get("fx.swap_issuer_share") * 100
    view["limit"] = view["borrowed"] + view["room"] if view["room"] is not None else None
    view["lent_total"] = sum(row["own"] for row in view["lent"])
    changes = changes or {}
    view["borrowed_change"] = changes.get("fx.swap.borrowed")
    view["lent_change"] = changes.get("fx.swap.lent")
    return view


def holding_rows(here: IslandState, islands: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """外貨の持ち高を、政府・中央銀行・商社など（施設の合計）に分けて通貨ごとに。量は外貨の額、
    帳簿の値打ち（手に入れたときの自国通貨）と今の値打ち（今のレートで自国通貨に直した額）。"""
    rates = fxrates.rates(here)
    groups: dict[str, dict[str, list[int]]] = {}
    for entity, currencies in sorted(fxmoney.holdings(here).items()):
        holder = entity if entity in (ledger.GOV, ledger.CB) else FACILITIES
        for key, (amount, book) in currencies.items():
            slot = groups.setdefault(holder, {}).setdefault(key, [0, 0])
            slot[0] += amount
            slot[1] += book
    out = []
    for holder in (ledger.GOV, ledger.CB, FACILITIES):
        if holder not in groups:
            continue
        rows = []
        for key, (amount, book) in sorted(groups[holder].items(), key=lambda kv: int(kv[0])):
            value = amount * rates.get(key, BASIS) // BASIS
            rows.append(
                {
                    "currency": int(key),
                    "name": _currency(islands, int(key)),
                    "symbol": islands.get(int(key), {}).get("symbol", ""),
                    "amount": amount,
                    "book_value": book,
                    "value": value,
                    "gain": value - book,
                }
            )
        out.append(
            {
                "holder": holder,
                "name": HOLDER_NAMES[holder],
                "currencies": rows,
                "value": sum(r["value"] for r in rows),
            }
        )
    return out


def nonresident_rows(
    island: m.Island, here: IslandState, islands: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """自国通貨を持つ外国の主体（中央銀行に置いた非居住者の預金）を、持ち主の島ごとに。"""
    load_money(
        {island.id: here},
        Account.objects.filter(island=island, name__startswith="nr:"),
    )
    out = []
    for owner, entities in sorted(fxmoney.nonresident_deposits(here).items()):
        parts = {ledger.GOV: 0, ledger.CB: 0, FACILITIES: 0}
        for entity, amount in entities.items():
            parts[entity if entity in parts else FACILITIES] += amount
        out.append(
            {
                "island": owner,
                "name": _name(islands, owner),
                "gov": parts[ledger.GOV],
                "cb": parts[ledger.CB],
                "facilities": parts[FACILITIES],
                "total": sum(parts.values()),
            }
        )
    return out
