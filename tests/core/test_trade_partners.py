"""貿易の相手ごとの内訳（#5455）: add_trade と同じ額が、相手ごとに分かれて統計 partner.* に
残る。相手は実際の相手の島（二国間契約・他国の船の運賃・圏の市場の約定は清算で決まった組。
#5605）か海外の商人（merchant）で、相手ごとの合計は trade.exports・trade.imports に合う
（記録を足すだけで計算は変えない）。経路ごとの内訳は partner_route.* に残り、相手ごとの
合計と一致する。
"""

from __future__ import annotations

import copy

from hakoniwa_core import stock
from hakoniwa_core.trade import contracts
from hakoniwa_core.units import QUANTITY_SCALE
from tests.core.test_trade import game, run, traded  # noqa: F401


def _partner_totals(stats: dict, side: str) -> dict[str, float]:
    """統計の partner.<相手>.<side>（品目でない合計の行）だけを相手ごとに集める。"""
    out: dict[str, float] = {}
    for key, value in stats.items():
        parts = key.split(".")
        if len(parts) == 3 and parts[0] == "partner" and parts[2] == side:
            out[parts[1]] = value
    return out


def _partner_goods(stats: dict, partner: str, side: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in stats.items():
        parts = key.split(".")
        if len(parts) >= 4 and parts[0] == "partner" and parts[1] == partner and parts[2] == side:
            out[".".join(parts[3:])] = value
    return out


def _route_totals(stats: dict, side: str) -> dict[tuple[str, str], float]:
    """統計の partner_route.<相手>.<side>.<経路> を (相手, 経路) ごとに集める。"""
    out: dict[tuple[str, str], float] = {}
    for key, value in stats.items():
        parts = key.split(".")
        if len(parts) == 4 and parts[0] == "partner_route" and parts[2] == side:
            out[(parts[1], parts[3])] = value
    return out


def test_partner_totals_add_up_to_trade_totals(game, traded):  # noqa: F811
    world = copy.deepcopy(traded[0])
    seen_bloc_route = False

    def check(w):
        nonlocal seen_bloc_route
        for island in w.islands.values():
            stats = island.stats
            for side in ("exports", "imports"):
                totals = _partner_totals(stats, side)
                assert sum(totals.values()) == stats.get(f"trade.{side}", 0)
                # 圏の市場の分は "bloc" の相手には残らず、実際の相手の島に分かれる（#5605）
                assert "bloc" not in totals
                for partner, total in totals.items():
                    assert sum(_partner_goods(stats, partner, side).values()) == total
                # 経路ごとの内訳の合計は、相手ごとの合計と一致する
                routes = _route_totals(stats, side)
                assert sum(routes.values()) == sum(totals.values())
                if any(route == "bloc" for _, route in routes):
                    seen_bloc_route = True
            # 海外の商人の行は、商人の統計から公定価格との差を引いた額（add_trade の額）に合う
            merchant_in = _partner_totals(stats, "imports").get("merchant", 0)
            assert merchant_in == stats.get("trade.merchant", 0) - stats.get(
                "trade.merchant_gap", 0
            )
            merchant_out = _partner_totals(stats, "exports").get("merchant", 0)
            assert merchant_out == stats.get("trade.merchant_sold", 0) - stats.get(
                "trade.merchant_premium", 0
            )

    run(game, world, 3, check)
    assert seen_bloc_route  # 圏の市場の約定が、経路=圏の市場で実際に記録される


def test_contract_trade_is_recorded_under_the_other_island(game, traded):  # noqa: F811
    world = copy.deepcopy(traded[0])
    seller, buyer = world.islands[1], world.islands[2]
    good = "building_materials"
    stock.add(seller.warehouse, good, 200 * QUANTITY_SCALE, world.turn, game.defs.goods)
    price = buyer.economy.prices[good]
    proposal = {
        "partner": buyer.id,
        "side": "sell",
        "good": good,
        "quantity": 2,
        "price": price,
        "currency": buyer.id,
        "turns": 6,
    }

    def offer(island):
        return [("propose_contract", proposal)] if island is seller else []

    run(game, world, 1, extra=offer)
    (c,) = [c for c in contracts.of_island(world, seller.id) if c["proposer"] == seller.id]

    def accept(island):
        return [("accept_contract", {"contract": c["id"]})] if island is buyer else []

    run(game, world, 1, extra=accept)
    snapshots = []
    run(
        game,
        world,
        6,
        lambda w: snapshots.append({i.id: dict(i.stats) for i in w.islands.values()}),
    )
    # 履行のターンは、経路=二国間契約の相手別輸出が出たターンで拾う（#5605。相手別輸出の
    # 合計には、同じ相手への圏の市場の分や運賃も混ざるので、額の一致では見ない）
    delivered_turns = [
        s
        for s in snapshots
        if s[seller.id].get(f"partner_route.{buyer.id}.exports.contract", 0) > 0
    ]
    assert delivered_turns  # 履行のターンに売り手の相手別輸出が出る
    for stats in delivered_turns:
        assert stats[seller.id][f"partner.{buyer.id}.exports"] > 0
        assert stats[seller.id][f"partner.{buyer.id}.exports.{good}"] > 0
        assert stats[buyer.id][f"partner.{seller.id}.imports"] > 0
        assert stats[buyer.id][f"partner.{seller.id}.imports.{good}"] > 0
        # 経路の内訳にも、二国間契約として買い手側にも残る（#5605）
        assert stats[buyer.id].get(f"partner_route.{seller.id}.imports.contract", 0) > 0


def test_bloc_trade_is_recorded_under_the_actual_partner(game, traded):  # noqa: F811
    """圏の市場の約定は、清算で決まった組の相手の島で両側に残る（#5605）。売り手の
    partner_route.<買い手>.exports.bloc と、買い手の partner_route.<売り手>.imports.bloc が
    同じターンに出る組が実際にある。額は各島の自国通貨建てなので、ここでは組の有無を見る
    （額の鏡の照合は為替を直して測りの側で行う）。"""
    world = copy.deepcopy(traded[0])
    seen_pair = False

    def check(w):
        nonlocal seen_pair
        for island in w.islands.values():
            stats = island.stats
            for (partner, route), amount in _route_totals(stats, "exports").items():
                if route != "bloc" or not partner.isdigit() or amount <= 0:
                    continue
                other = w.islands[int(partner)]
                back = _route_totals(other.stats, "imports").get((str(island.id), "bloc"), 0)
                if back > 0:
                    seen_pair = True

    run(game, world, 3, check)
    assert seen_pair  # 圏の市場の組が、両側とも実際の相手の島で記録される
