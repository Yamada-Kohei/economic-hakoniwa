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

from hakoniwa_core import ledger
from hakoniwa_core.economy import is_stopped
from hakoniwa_core.modules import trade as trade_stage
from hakoniwa_core.modules.trade import contract_view
from hakoniwa_core.naming import facility_name
from hakoniwa_core.state import IslandState, WorldState
from hakoniwa_core.trade import (
    KEY,
    currency_labels,
    is_port,
    is_trader,
    merchant,
    port_of,
    shipping_of,
    ships,
    started,
    tradable_goods,
    traders,
)
from hakoniwa_core.trade import contracts as contract_states
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney
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
    "ships",
    "ships.busy",
    "freight.ship_rate",
    "port.handled",
    "trade.unpaid",
]
PUBLIC_TRADE_STATS = ["trade.exports", "trade.imports", "trade.balance", "ships"]
# 為替の画面と API に出す国全体の数（誰にでも）
FX_STATS = [
    "trade.exports",
    "trade.imports",
    "trade.balance",
    "trade.merchant",
    "fx.holdings",
    "money.nonresident",
]
# 推移の API で返す、レート以外の系列
FX_HISTORY = ["trade.exports", "trade.imports", "trade.balance", "fx.holdings", "money.nonresident"]

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
}
SHIPMENT_KINDS = {"bloc": "貿易圏", "contract": "二国間契約"}
# 外貨の持ち高と非居住者の預金の持ち手（施設はまとめて「商社など」）
FACILITIES = "facilities"
HOLDER_NAMES = {
    ledger.GOV: "政府",
    ledger.CB: "中央銀行（外貨準備）",
    FACILITIES: "商社など（合計）",
}


# --- 世界 ------------------------------------------------------------------------------


def world_state(world: m.World) -> dict[str, list[dict[str, Any]]]:
    """世界の貿易の状態（読むだけ。まだなければ空の並び）。"""
    state = (world.ext or {}).get(KEY) or {}
    return {key: state.get(key, []) for key in ("blocs", "contracts", "shipments")}


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
    for good in defs.goods:
        price = bloc.get("prices", {}).get(good)
        if not price:
            continue
        row: dict[str, Any] = {
            "good": good,
            "name": defs.name("goods", good),
            "unit": defs.unit_name(good),
            "price": price,
            "volume": display.amount(bloc.get("volume", {}).get(good, 0)),
            "demand": display.amount(bloc.get("demand", {}).get(good, 0)),
            "supply": display.amount(bloc.get("supply", {}).get(good, 0)),
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
    }
    if goods:
        out["goods"] = bloc_goods(bloc)
    return out


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
            "load": round(from_milli(group["load"]), 1),  # トン
            "ships": round(from_milli(ships.ships_of_load(config, group["load"])), 2),
        }
        if source in own or target in own:
            row["mine"] = True
            row["cargo"] = [
                {
                    "good": good,
                    "name": defs.name("goods", good),
                    "unit": defs.unit_name(good),
                    "amount": display.amount(c["qty"]),
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


def _stats_part(stats: dict[str, float], names: list[str]) -> dict[str, Any]:
    """統計のうち names にあるものと、その表示名と単位（API の形）。"""
    shown = [n for n in names if n in stats]
    return {
        "stats": {n: stats[n] for n in shown},
        "labels": {n: reports.stat_label(n)[0] for n in shown},
        "units": {n: reports.unit_of(reports.stat_label(n)[1]) for n in shown},
    }


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
        **_stats_part(stats, TRADE_STATS if mine else PUBLIC_TRADE_STATS),
        "blocs": [bloc_view(b, islands, goods=False) for b in member],
    }
    if not mine:
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
    data["contracts"] = contract_rows(state["contracts"], island.id, world.turn, islands)
    data["traders"] = trader_rows(here, islands, state["blocs"])
    data["fleet"] = fleet_view(island, here, stats)
    data["shipments"] = shipment_rows(state["shipments"], island.id, islands)
    data["merchant"] = merchant_view(here, world.turn)
    data["partners"] = [
        {"id": i, "name": _name(islands, i), "currency_name": _currency(islands, i)}
        for i in trading_islands(world)
        if i != island.id
    ]
    defs = get_game().defs
    data["goods"] = [
        {
            "good": g,
            "name": defs.name("goods", g),
            "unit": defs.unit_name(g),
            "home_price": here.economy.prices.get(g),
        }
        for g in tradable_goods(defs)
    ]
    data["cancel_turns"] = config.get("trade.contract_cancel_turns")
    return data


def _member_bloc(
    bloc: dict[str, Any], here: IslandState, islands: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    """加盟している圏の様子（持ち主向け）: 圏の値段を今のレートで自国通貨に直したものと、島の値段の
    比べ。代表なら申し込みの一覧も。"""
    view = bloc_view(bloc, islands)
    e = fxrates.rate(here, bloc["currency"])
    prices = here.economy.prices
    for row in view["goods"]:
        own = row["price"] * e // BASIS
        home = prices.get(row["good"])
        row["price_own"] = own
        row["home_price"] = home
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
    contracts: list[dict[str, Any]], island_id: int, turn: int, islands: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """島が当事者の二国間契約（申し込み中・有効・終わったもの）。turn は世界の今のターン。"""
    defs = get_game().defs
    wait = get_game().config.get("trade.contract_cancel_turns")
    rows = []
    for c in contracts:
        if island_id not in (c["seller"], c["buyer"]):
            continue
        view = contract_view(c, island_id, turn)
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
                "good": view["good"],
                "good_name": defs.name("goods", view["good"]),
                "unit": defs.unit_name(view["good"]),
                "quantity": from_milli(view["qty"]),  # 1ターンの量
                "price": view["price"],
                "currency": view["currency"],
                "currency_name": _currency(islands, view["currency"]),
                "currency_symbol": islands.get(view["currency"], {}).get("symbol", ""),
                "turns": c.get("turns"),
                "start": view["start"] or None,
                "end": view["end"] or None,
                "active": view["active"],
                "delivered": display.amount(view["delivered"]),
                "paid": c.get("paid", 0),
                "short": display.amount(view["short"]),
                # 届いた物の行き先（買う側だけが決める。決める前は市場）
                "delivery": view["delivery"] if view["side"] == "buy" else "",
                "delivery_name": DELIVERY_NAMES.get(view["delivery"], "")
                if view["side"] == "buy"
                else "",
                "can_accept": proposed and view["proposer"] != island_id,
                # 有効な契約は、やめる命令を出してもまだ先に終わる予定のときだけ
                "can_cancel": proposed
                or (state == contract_states.ACTIVE and view["end"] > turn + 1 + wait),
            }
        )
    order = {contract_states.PROPOSED: 0, contract_states.ACTIVE: 1, contract_states.ENDED: 2}
    return sorted(rows, key=lambda r: (order.get(r["state"], 3), r["id"]))


def trader_rows(
    here: IslandState, islands: dict[int, dict[str, Any]], blocs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """島の商社（工事中・閉鎖中を含む）と、財ごとの向き・狙う量・手元の荷・運ぶ途中の荷。"""
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
                    "target": display.amount(target if direction else 0),  # 1ターンに狙う量
                    "held": display.amount(max(0, held - transit)),  # 港の倉庫にある荷
                    "transit": display.amount(transit),  # 船で運ぶ途中の荷
                    "unit_cost": cost * QUANTITY_SCALE // held if held > 0 else None,
                    "age": item.get("age", 0),  # 売れ残ったターン数
                    "bloc": bloc,
                    "bloc_name": names.get(bloc, "") if bloc is not None else "",
                    "why": WHY_NAMES.get(item.get("why"), "")
                    if direction == traders.IMPORT
                    else "",
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
    """海運業の船団（船の数・航海に出ている船・空いている船・運賃・燃料）と港（使用料・積み下ろしの
    量と上限）。"""
    game = get_game()
    defs, config = game.defs, game.config
    capacity = config.get("trade.ship_capacity")
    out: dict[str, Any] = {"ship_capacity": capacity, "shipping": None, "port": None}
    f = shipping_of(here, config)
    if f is not None:
        need = ships.fuel_per_turn(config, ships.capacity(config, f))
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
            "capacity": from_milli(ships.capacity(config, f)),  # 全部の船で1航海に運べるトン
            "freight_rate": ships.freight_rate(here, config, f),
            "workers": f.workers,
            # 直近の、船が足りずに輸入を減らしたターンの数（数えたターンのうち）
            "short_turns": sum(f.economy.get("ships_short", [])),
            "short_of": len(f.economy.get("ships_short", [])),
            "fuel": [
                {
                    "good": good,
                    "name": defs.name("goods", good),
                    "unit": defs.unit_name(good),
                    "amount": display.amount(have.get(good, 0)),
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
            "fee": ships.port_fee(here, config),  # 荷 1 トンあたり
            "capacity": config.get("trade.port_capacity"),  # 1ターンに積み下ろせるトン
            "handled": stats.get("port.handled"),
            "workers": port.workers,
        }
    elif any(is_port(config, p) for p in here.facilities):
        out["port_stopped"] = True  # 港はあるが工事中か止まっている
    return out


def shipment_rows(
    shipments: list[dict[str, Any]], island_id: int, islands: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """島が積み出したか受け取る、運ぶ途中の荷（着く順）。"""
    defs = get_game().defs
    rows = []
    for s in shipments:
        if island_id not in (s["from"], s["to"]):
            continue
        incoming = s["to"] == island_id
        partner = s["from"] if incoming else s["to"]
        rows.append(
            {
                "id": s["id"],
                "direction": traders.IMPORT if incoming else traders.EXPORT,
                "direction_name": "輸入" if incoming else "輸出",
                "partner": partner,
                "partner_name": _name(islands, partner),
                "kind": s["kind"],
                "kind_name": SHIPMENT_KINDS.get(s["kind"], s["kind"]),
                "good": s["good"],
                "name": defs.name("goods", s["good"]),
                "unit": defs.unit_name(s["good"]),
                "amount": display.amount(sum(q for _, q in s["batches"])),
                "load": round(from_milli(s["load"]), 1),
                "loaded": s["loaded"],
                "arrive": s["arrive"],
            }
        )
    return sorted(rows, key=lambda r: (r["arrive"], r["id"]))


def merchant_view(here: IslandState, turn: int) -> dict[str, Any]:
    """海外の商人: 次のターンに来るか、あと何ターン来るか、値段の倍率と、売る物（財・1ターンの上限・
    値段と島の値段）。turn は世界の今のターン（商人は次のターンの市場で売る）。"""
    game = get_game()
    defs, config = game.defs, game.config
    settings = config.get("foreign_merchant")
    coming = turn + 1
    left = merchant.turns_left(here, config, coming)
    since = here.ext.get(KEY, {}).get("merchant_from")
    prices = here.economy.prices
    return {
        "enabled": bool(settings.get("enabled")),
        "active": merchant.active(here, config, coming),
        "turns_left": left,
        "since": since,
        "until": since + settings["turns"] - 1 if since is not None else None,
        "turns": settings["turns"],
        "last_turns": settings["last_turns"],
        "rising": 0 < left <= settings["last_turns"],
        "multiplier": round(merchant.multiplier(here, config, coming), 2),
        "last_multiplier": settings["last_multiplier"],
        "goods": [
            {
                "good": good,
                "name": defs.name("goods", good),
                "unit": defs.unit_name(good),
                "quantity": from_milli(qty),  # 1ターンの上限
                "price": price,
                "home_price": prices.get(good),
            }
            for good, qty, price in merchant.offers(here, defs, config, coming)
        ],
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
        rates.append(row)
    world_view = WorldState(turn=world.turn, seed=world.seed, islands={**trading, island.id: here})
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
        **_stats_part(stats, FX_STATS),
    }
    if history:
        first, last = reports.turn_range(island, start, end)
        names = rate_names(island) + FX_HISTORY
        data["history"] = reports.series(island, names, first, last, mine)
    if mine:
        data["holdings"] = holding_rows(here, islands)
        data["nonresident"] = nonresident_rows(island, here, islands)
    return data


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
