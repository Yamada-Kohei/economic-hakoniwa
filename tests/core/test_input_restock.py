"""原料の積み増しと按分（MK-49。W-285、掲示板の按分の会議 2026-11-18）: 工場の原料の買いは、使う分
（足りない分）と在庫の目安との差の積み増しの分に分ける。使う分は今までどおり（買い負ければ買い
上がる）、積み増しは今の値段までで買い上がらず、品不足の合図にも数えない。原料の買いは家計と同じ
順位で、同じ値段で並べば注文の量の比で分ける。"""

from __future__ import annotations

from types import SimpleNamespace

from hakoniwa_core import stock
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules.market import FACILITY, Market
from hakoniwa_core.state import WorldState
from tests.core.test_finance import context
from tests.core.test_rules import add, home, make_island, run, with_economy
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）


def _plant(game):
    island = make_island(game)
    island.regime["economy"] = "mixed"
    home(game, island, 5, 6, 6000)
    plant = add(island, "building_materials_plant", 6, 6, recipe="wood")
    with_economy(game, island)
    # 木材から作る工場が木材の値段を払える建材の値段（木材の方式の原価）
    island.economy.prices["building_materials"] = initial_prices(
        game.defs, game.config, island.resources
    )["building_materials"]
    plant.status = "target"
    plant.economy.update(target=400, wanted=400)
    plant.workers = 400
    return island, plant


def _orders(game, island, plant, good="wood"):
    world = WorldState(turn=0, seed=1, islands={1: island})
    books: dict = {}
    Market()._facility_orders(island, context(game, world), books, {})
    book = books.get(good)
    return [] if book is None else [(o, p) for o, p in book.buys if p.facility is plant]


def test_the_restock_part_is_split_from_what_is_used(game):
    island, plant = _plant(game)
    ctx = SimpleNamespace(defs=game.defs, config=game.config, turn=1)
    buffers: dict[str, int] = {}
    needs = dict(Market._input_needs(ctx, plant, "electricity", buffers=buffers))
    use = 12_000 * 400 // 1000
    assert needs["wood"] == use + buffers["wood"] and buffers["wood"] > 0
    stock.add(plant.stock, "wood", use, 0, game.defs.goods)  # 使う分は手元にある: みな積み増し
    buffers = {}
    needs = dict(Market._input_needs(ctx, plant, "electricity", buffers=buffers))
    assert needs["wood"] == buffers["wood"]


def test_restock_bids_the_current_price_and_used_inputs_may_bid_more(game):
    island, plant = _plant(game)
    price = island.economy.prices["wood"]
    mine = _orders(game, island, plant)
    used = [(o, p) for o, p in mine if not p.restock]
    extra = [(o, p) for o, p in mine if p.restock]
    assert used and extra
    assert extra[0][0].limit <= price  # 積み増しは今の値段まで
    assert used[0][0].limit >= extra[0][0].limit
    # 原料の買いは家計と同じ順位（同じ値段なら量の比で分ける）
    rank = game.config.get("market.household_priority")
    assert all(o.priority == rank for o, _ in mine)
    assert all(p.kind == FACILITY for _, p in mine)


def test_same_price_buys_are_shared_by_quantity(game):
    """家計と工場の買いが同じ指値・同じ順位なら、足りない売りは注文の量の比で分ける。"""
    rank = game.config.get("market.household_priority")
    buys = [Order(300, 1100, rank), Order(100, 1100, rank)]  # 工場・家計
    result = auction.clear(buys, [Order(200, 900)], 1000, 1000)
    assert result.bought == [150, 50]


def test_unfilled_restock_is_not_a_shortage(game):
    """積み増しの買い残しは、次のターンに買い上がる合図（short の price）にしない。"""
    island, plant = _plant(game)
    ctx = SimpleNamespace(defs=game.defs, config=game.config, turn=1)
    use = 12_000 * 400 // 1000
    stock.add(plant.stock, "wood", use, 0, game.defs.goods)  # 足りないのは積み増しだけ
    assert Market._input_needs(ctx, plant, "electricity")
    run(game, island, ["market"])  # 木材の売り手がいない
    assert "wood" not in plant.economy.get("short", {})
