"""W-186（市場経済の国の不況）の直し 回4: 海外の商人の買い（輸出）と、役所の決まった事業
（公共事業費の執行）。"""

from __future__ import annotations

import copy

from hakoniwa_core import infra
from hakoniwa_core.economy import Payer, funds
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.ledger import CB
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules import infrastructure as works
from hakoniwa_core.modules.market import MERCHANT, Book, Market, Party
from hakoniwa_core.trade import island_state, merchant
from hakoniwa_core.units import QUANTITY_SCALE, scale_price
from tests.core.test_finance import context
from tests.core.test_phase3_3 import base as base  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_phase3_3 import game as game  # noqa: F401
from tests.core.test_phase3_3 import run_checked


def _trading(island, since):
    island_state(island)["merchant_from"] = since
    return since


def test_merchant_bids_for_the_same_goods_below_the_selling_price(game, base):
    island = copy.deepcopy(base.islands[1])
    turn = _trading(island, base.turn)
    config, defs = game.config, game.defs
    prices = initial_prices(defs, config)
    bids = {g: (q, p) for g, q, p in merchant.bids(island, defs, config, turn)}
    offers = {g: (q, p) for g, q, p in merchant.offers(island, defs, config, turn)}
    assert set(bids) == set(game.config.get("foreign_merchant.goods"))
    assert "grain" not in bids  # 上限が島の食べる量の半分を超えるので入れない
    factor = config.get("foreign_merchant.buy_multiplier")
    for good, (qty, price) in bids.items():
        assert price == scale_price(prices[good], factor)
        assert price < offers[good][1]
        assert qty == offers[good][0] == merchant.cap_of(defs, good)
    # 来ている間だけ
    assert merchant.bids(island, defs, config, turn + config.get("foreign_merchant.turns")) == []


def test_blockaded_port_gets_fewer_merchants(game, base):
    island = copy.deepcopy(base.islands[1])
    turn = _trading(island, base.turn)
    full = {g: q for g, q, _ in merchant.bids(island, game.defs, game.config, turn)}
    island_state(island)[merchant.BLOCKADE] = 5000  # 半分の強さの封鎖
    half = {g: q for g, q, _ in merchant.bids(island, game.defs, game.config, turn)}
    sold = {g: q for g, q, _ in merchant.offers(island, game.defs, game.config, turn)}
    for good, qty in full.items():
        assert half[good] == qty // 2
        assert sold[good] == qty // 2


def test_merchant_bids_below_the_price_are_not_island_demand():
    """指値が約定価格より安くて買えなかった商人の買いは、島の需要（品切れ・値上げの見方）に入れない。"""
    book = Book()
    cheap = Party(MERCHANT, MERCHANT, Payer(CB))
    book.buys.append((Order(5 * QUANTITY_SCALE, 70), cheap))
    book.buys.append((Order(3 * QUANTITY_SCALE, 200), Party("household", "hh:general", Payer(CB))))
    result = auction.Result(100, 0, 8 * QUANTITY_SCALE, 0, 1, 200, [0, 0], [])
    assert Market._out_of_money(book, result) == 5 * QUANTITY_SCALE
    # 指値が約定価格以上なら、買えなかった分も需要（輸出の値段まで下がった島）
    result = auction.Result(60, 0, 8 * QUANTITY_SCALE, 0, 1, 200, [0, 0], [])
    assert Market._out_of_money(book, result) == 0


def test_islands_sell_to_merchants_with_balanced_books(game, base):
    """商人が島の物を買うと、代金は島の外から入り（輸出）、国際収支と帳簿が釣り合う（run_checked が
    毎ターン確かめる）。"""
    world = copy.deepcopy(base)
    sold = []

    def keep(w):
        sold.append(sum(i.stats.get("trade.merchant_sold", 0) for i in w.islands.values()))

    run_checked(
        game, world, 14, keep
    )  # 商人の買いはとぎれとぎれ（買わない間が 6 ターン続くことがある）
    assert sum(sold) > 0
    for island in world.islands.values():
        s = island.stats
        if s.get("trade.merchant_sold"):
            assert s["bop.assets.merchant"] == s["trade.merchant_sold"]


def _empty_queue(island):
    infra.queue(island).clear()
    works.works_state(island).pop(works.AUTO, None)


def test_ministry_queues_one_site_of_works_within_the_budget(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    ctx = context(game, world)
    _empty_queue(island)
    # 上下水道の足りない町を1つ作る
    housing = next(f for f in island.facilities if f.kind == game.config.get("housing.facility"))
    housing.ext["water_short"] = True
    infra.set_level(island.tile(housing.x, housing.y), "water", 0)
    island.scratch[works.WORKS] = {"wage": 100}
    state = funds.fiscal_state(island)
    state["caps"] = {"construction": 10**12}
    state["used"] = {"construction": 0}
    works._auto_works(island, ctx)
    assert ["water", housing.x, housing.y] in infra.queue(island)
    assert works.works_state(island)[works.AUTO] == infra.queue(island)
    # 1ターンに1か所分だけ
    assert len(infra.queue(island)) == 1
    # 月割りの残りがなければ並べない
    _empty_queue(island)
    state["used"] = {"construction": 10**12}
    works._auto_works(island, ctx)
    assert infra.queue(island) == []
    # 命令で止めていれば並べない
    state["used"] = {"construction": 0}
    works.set_public_works(island, {"auto": False}, ctx)
    works._auto_works(island, ctx)
    assert infra.queue(island) == []
    works.set_public_works(island, {"auto": True}, ctx)
    works._auto_works(island, ctx)
    assert infra.queue(island)


def test_ordered_works_go_before_the_ministrys(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    ctx = context(game, world)
    _empty_queue(island)
    land = [
        (t.x, t.y)
        for t in island.tiles
        if t.terrain == "plain" and infra.level(t, "road") < infra.FULL
    ][:2]
    (ax, ay), (ox, oy) = land
    infra.enqueue(island, "road", [(ax, ay)])
    works.works_state(island)[works.AUTO] = [["road", ax, ay]]
    infra.enqueue(island, "road", [(ox, oy)])  # 遊ぶ人の命令（後から並んだ）
    plan = works.Works(island, ctx)
    assert [(i.x, i.y) for i in plan.build()] == [(ox, oy), (ax, ay)]
    assert [(i.x, i.y) for i in plan.build(ordered_only=True)] == [(ox, oy)]
    # 国の倉庫から工事の資材に移すのは、命令の工事の分だけ
    assert set(works.wants(island, ctx, False, ordered_only=True)) <= set(
        works.wants(island, ctx, False)
    )
