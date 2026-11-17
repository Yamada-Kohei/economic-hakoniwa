"""作物の買い置き（MK-48。W-285 の 3b）: 持ち手の決まりは「t ターン先の見込みの値段 − 持つ費用 >
今の値段」の 1 つの比べ。在庫が減り続ける島では買い置き、余る島ではふだんの分を超えた在庫を売りに
出す。"""

from __future__ import annotations

from types import SimpleNamespace

from hakoniwa_core import stock
from hakoniwa_core.economy import crop_stock
from hakoniwa_core.economy.prices import expected_path, expected_price
from hakoniwa_core.state import ACCOUNTING_SELF, OWNER_PRIVATE
from tests.core.test_rules import add, make_island, run, with_economy
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）

GOOD = "grain"
DRAINING = [3000, 2700, 2400, 2100, 1800, 1500]  # 来る量 700、買いたい量 1000（在庫が減る）
GLUT = [5000, 5500, 6000, 6500, 7000, 7500]  # 売れ残りが目安を超えて積み上がる


def _island(game, supply, price=1000):
    island = with_economy(game, make_island(game))
    island.economy.prices[GOOD] = price
    island.economy.price_history[GOOD] = [price] * 6
    island.economy.finance.update(
        {"demand": {GOOD: [1000] * 6}, "supply": {GOOD: list(supply)}, "sold": {GOOD: [1000] * 6}}
    )
    return island


def _ctx(game):
    return SimpleNamespace(defs=game.defs, config=game.config, turn=1)


def test_the_path_ends_at_the_expected_price(game):
    island = _island(game, DRAINING)
    path = expected_path(island, GOOD, 8, game.defs, game.config)
    assert len(path) == 8
    for ahead in range(1, 9):
        assert path[ahead - 1] == expected_price(island, GOOD, ahead, game.defs, game.config)


def test_crops_are_the_goods_of_recipes_that_grow(game):
    grown = crop_stock.crops(game.defs)
    assert grown["grain"] == 5 and grown["cotton"] == 6
    assert "food" not in grown and "wood" not in grown


def test_a_draining_stock_is_held(game):
    """在庫が尽きた後に値段が上がる見込みなら、持つ費用を引いても今より高いので持つ（t* > 0）。"""
    view = crop_stock.outlook(_island(game, DRAINING), _ctx(game), GOOD)
    assert view["t"] > 1 and view["sell"] is None
    assert view["limit"] > view["p"]
    assert view["ratio"] < 1  # 見通しの比 p ÷ p*(H): 値上がりを見込む


def test_a_glut_is_sold_below_the_price(game):
    view = crop_stock.outlook(_island(game, GLUT), _ctx(game), GOOD)
    assert view["t"] == 0
    assert view["sell"] is not None and view["sell"] < view["p"]
    assert view["ratio"] > 1


def test_a_balanced_market_is_not_held_beyond_the_usual_stock(game):
    """流れが釣り合っていれば見込みは今の値段のままで、持つ費用の分だけ損なので持たない。"""
    island = _island(game, [1500] * 6)
    island.policy.deposit_rate = 300
    view = crop_stock.outlook(island, _ctx(game), GOOD)
    assert view["t"] == 0 and view["sell"] < view["p"]


def test_the_extra_goal_is_what_the_usual_stock_does_not_cover(game):
    # u = 100、S* = 150 で t* = 5 なら、5 ターンの間に使う残り 400 のうち 150 はふだんの分で持つ
    assert crop_stock.extra_goal(100, 150, 5) == 250
    assert crop_stock.extra_goal(100, 150, 1) == 0
    assert crop_stock.extra_goal(100, 500, 3) == 0


def _factory(game, supply, have=0):
    island = _island(game, supply)
    island.regime["economy"] = "market"  # 計画経済の島は公定価格で、買い置きしない
    plant = add(island, "food_factory", 6, 6, recipe="default", owner=OWNER_PRIVATE)
    plant.accounting = ACCOUNTING_SELF
    plant.status = "target"
    plant.economy.update(target=1000, wanted=0)
    plant.workers = plant.economy.get("wanted", 0)
    if have:
        stock.add(plant.stock, GOOD, have, 1, game.defs.goods)
    return island, plant


def test_the_factory_holds_and_sells_by_the_outlook(game):
    """減り続ける島では、ふだんの分の上に買い置きの注文を出し、余る島では、ふだんの分を超えた
    在庫を売りに出す。買い置きと売りは別の数で残る。"""
    from hakoniwa_core.modules.market import Market

    island, plant = _factory(game, DRAINING)
    ctx = _ctx(game)
    grown = crop_stock.crops(game.defs)
    recipe = {"inputs": {GOOD: 12}, "outputs": {"food": 10}}
    held = Market._crop_goods(island, ctx, plant, recipe, grown, {})
    view, use, normal, have = held[GOOD]
    assert view["t"] > 1 and use > 0 and normal > use and have == 0
    assert crop_stock.extra_goal(use, normal - use, view["t"]) > 0

    island, plant = _factory(game, GLUT, have=10**6)
    held = Market._crop_goods(island, ctx, plant, recipe, grown, {})
    view, use, normal, have = held[GOOD]
    assert view["sell"] is not None and have > normal
    # 公定価格の財は持たない
    assert Market._crop_goods(island, ctx, plant, recipe, grown, {GOOD: 1}) == {}


def test_the_market_stage_records_the_hold_orders(game):
    from hakoniwa_core import ledger
    from hakoniwa_core.economy import facility_entity

    island, plant = _factory(game, DRAINING)
    ledger.pay(island, ledger.GOV, facility_entity(plant.id), 10**9, "capital_injection")
    run(game, island, ["market"])
    row = island.economy.ext.get(crop_stock.EXT, {}).get(GOOD)
    assert row is not None and row["t"] > 1
    assert row["buy"] > 0 and row["sell"] == 0
