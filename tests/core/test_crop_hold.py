"""作物の売り惜しみ（MK-48。W-285 の 3b の作り直し、掲示板 #1459）: 農場は、持ち手に見える見込みの
値段を今に割り引いた値が今の値段より高ければ、倉に入る分を売らずに持つ。飢えか値の跳ねが見えたら
放出する。"""

from __future__ import annotations

from types import SimpleNamespace

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.economy import crop_hold
from hakoniwa_core.economy.prices import expected_path
from hakoniwa_core.modules.market import Market, operating_target
from hakoniwa_core.state import ACCOUNTING_SELF, OWNER_PRIVATE
from tests.core.test_rules import add, make_island, with_economy
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）

GOOD = "grain"
DRAINING = [3000, 2700, 2400, 2100, 1800, 1500]  # 来る量 700、買いたい量 1000（在庫が減る）
GLUT = [5000, 5500, 6000, 6500, 7000, 7500]  # 売れ残りが目安を超えて積み上がる


def _island(game, supply, price=1000):
    island = with_economy(game, make_island(game))
    island.regime["economy"] = "market"  # 計画経済の島は公定価格で、売り惜しみしない
    island.economy.prices[GOOD] = price
    island.economy.price_history[GOOD] = [price] * 6
    island.economy.finance.update(
        {"demand": {GOOD: [1000] * 6}, "supply": {GOOD: list(supply)}, "sold": {GOOD: [1000] * 6}}
    )
    island.stats["food.ratio"] = 1.0
    return island


def _farm(game, island, x=6, y=6, have=0):
    farm = add(island, "farm", x, y, recipe="default", owner=OWNER_PRIVATE)
    farm.accounting = ACCOUNTING_SELF
    farm.status = "target"
    farm.economy.update(target=1000, wanted=0, expected={GOOD: 1000})
    if have:
        stock.add(farm.stock, GOOD, have, 1, game.defs.goods)
    return farm


def _ctx(game, turn=1):
    return SimpleNamespace(defs=game.defs, config=game.config, turn=turn)


def test_the_floor_is_the_best_discounted_outlook():
    path = [1100, 1250, 1300]
    rate = 0.01
    best = max(p / (1 + rate) ** t for t, p in enumerate(path, start=1))
    assert crop_hold.value(path, rate) == int(best)
    assert crop_hold.value([], rate) == 0


def test_the_horizon_is_the_growing_time_within_the_shelf_life(game):
    grown = crop_hold.crops(game.defs)
    assert grown["grain"] == 5 and "food" not in grown
    life = game.defs.goods["grain"].get("shelf_life")
    assert crop_hold.horizon(game.defs, "grain", grown) == min(5, life - 1)


def test_holds_only_when_the_outlook_beats_the_price(game):
    island = _island(game, DRAINING)
    up = [1100, 1200, 1300]
    kept, floor = crop_hold.plan(island, game.config, GOOD, 5000, 950, 1100, up, 3000)
    assert kept == 3000  # 倉の大きさまで
    assert floor is None  # 下限が値幅の上端以上: 売りに出さない
    kept, floor = crop_hold.plan(island, game.config, GOOD, 5000, 950, 2000, up, 9000)
    assert kept == 5000 and 1000 < floor < 2000  # 上端より下: 下限の指値で出す
    # 見込みが平ら（利子の分だけ今より低い）なら、ふだんの指値が値段より低くても持たない
    assert crop_hold.plan(island, game.config, GOOD, 5000, 900, 1100, [1000] * 3, 9000) == (0, None)


def test_hunger_or_a_price_jump_releases_the_stock(game):
    island = _island(game, DRAINING)
    up = [1100, 1200, 1300]
    island.stats["food.ratio"] = 0.95
    assert crop_hold.released(island, GOOD, game.config) == "hunger"
    assert crop_hold.plan(island, game.config, GOOD, 5000, 950, 1100, up, 3000) == (0, None)
    island.stats["food.ratio"] = 1.0
    band = game.config.get("market.band")
    island.economy.price_history[GOOD] = [1000, round(1000 * (1 + band))]
    assert crop_hold.released(island, GOOD, game.config) == "jump"
    island.economy.price_history[GOOD] = [1000, 1000]
    assert crop_hold.released(island, GOOD, game.config) is None


def test_a_farm_sees_only_the_fields_near_it(game):
    """見込みは持ち手に見える物だけ: 通勤圏の外の畑の作り替えは数えない。"""
    island = _island(game, [1500] * 6)
    farm = _farm(game, island, 3, 3)
    far = _farm(game, island, 13, 13)
    # 遠い畑が穀物をやめて綿に替えると決めた（先の穀物が減る）
    far.ext[fac.RECIPE_NEXT] = "cotton"
    ctx = _ctx(game)
    radius = fac.commute_radius(game.defs, game.config, "farm")
    assert max(abs(far.x - farm.x), abs(far.y - farm.y)) > radius
    seen = crop_hold.farm_path(island, ctx, farm, GOOD, 5)
    everyone = expected_path(island, GOOD, 5, game.defs, game.config, turn=ctx.turn)
    assert everyone[-1] > seen[-1]  # 島じゅうを見れば値上がりを見込むが、農場には見えない
    near = _farm(game, island, 4, 4)
    near.ext[fac.RECIPE_NEXT] = "cotton"
    assert crop_hold.farm_path(island, ctx, farm, GOOD, 5)[-1] > seen[-1]


def _orders(game, island, farm):
    from hakoniwa_core.state import WorldState
    from tests.core.test_finance import context

    world = WorldState(turn=0, seed=1, islands={1: island})
    books = {}
    Market()._facility_orders(island, context(game, world), books, {})
    book = books.get(GOOD)
    mine = [] if book is None else [(o, p) for o, p in book.sells if p.facility is farm]
    return mine, context(game, world)


def test_the_farm_withholds_in_a_draining_market_and_sells_in_a_glut(game):
    island = _island(game, DRAINING)
    farm = _farm(game, island, have=4000)
    mine, ctx = _orders(game, island, farm)
    row = island.economy.ext.get(crop_hold.EXT, {}).get(GOOD)
    assert row is not None and row["kept"] > 0
    kept = farm.economy[crop_hold.WITHHELD][GOOD]
    regular = sum(o.quantity for o, p in mine if not p.withheld and not p.clearance)
    floored = [o for o, p in mine if p.withheld]
    assert regular + kept <= 4000 and kept > 0
    if row["withheld"]:
        assert not floored  # 下限が上端以上: 持つ分は売りに出さない
    else:
        assert floored and floored[0].limit > island.economy.prices[GOOD]
    # 持った分は操業目標の在庫に数えない（持つ間も作り続ける）
    before = operating_target(island, farm, ctx)
    farm.economy.pop(crop_hold.WITHHELD)
    assert operating_target(island, farm, ctx) <= before

    island = _island(game, GLUT)
    farm = _farm(game, island, have=4000)
    mine, _ = _orders(game, island, farm)
    assert crop_hold.EXT not in island.economy.ext or not island.economy.ext[crop_hold.EXT].get(
        GOOD, {}
    ).get("kept")
    assert sum(o.quantity for o, p in mine) >= 4000 - 1  # みな売りに出す


def test_hungry_islands_do_not_withhold(game):
    island = _island(game, DRAINING)
    island.stats["food.ratio"] = 0.9
    farm = _farm(game, island, have=4000)
    mine, _ = _orders(game, island, farm)
    assert island.economy.ext[crop_hold.EXT][GOOD].get("released") == "hunger"
    assert crop_hold.WITHHELD not in farm.economy
    assert not any(p.withheld for _, p in mine)
