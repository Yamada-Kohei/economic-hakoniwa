"""飢饉から戻れること（2026-11-02。商人の減らし #240 の後の命令なしの国の長い飢え）。

島の漁港と農場が会社ごと倒産して食べ物が市場から消えると、働き手がみな畑に戻り、市場で買いたい
人がいなくなって、漁港も農場も建たないまま 100 ターン以上飢えが続いた。飢えている家計は、売りに
出ていない食料のどれでも、それだけ売りに出ていれば買った量まで買いたい量に出す。畑に戻った人と
その家族の分も足す。"""

from __future__ import annotations

import copy

from hakoniwa_core.economy import GENERAL, retail
from hakoniwa_core.modules.market import Market
from hakoniwa_core.modules.population import FED, jobless_family
from hakoniwa_core.units import to_milli
from tests.core.test_finance import context, island_of
from tests.core.test_finance import game as game  # noqa: F401
from tests.core.test_finance import warmed as warmed  # noqa: F401

STAPLES = ("fish", "grain")


def _wanted(game, world, island, back=0):
    """食べ物が1つも売りに出ていないときの、家計の買いたい量。"""
    ctx = context(game, world, "market")
    island.scratch.clear()
    island.scratch["freight_household"] = 0
    island.stats["labor.back_to_fields"] = back
    retail.prepare(island, game.config)
    Market()._household_orders(island, ctx, {}, 0, {})
    return island.scratch.get("wanted", {})


def test_hungry_households_want_any_staple_when_no_food_is_on_sale(game, warmed):  # noqa: F811
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    fed = _wanted(game, world, island)
    island.economy.households[GENERAL][FED] = 0  # 前のターンに何も食べられなかった
    hungry = _wanted(game, world, island)
    for good in STAPLES:
        assert hungry.get(good, 0) > fed.get(good, 0)
    # 畑に戻った人とその家族も、売りに出ていれば買う
    back = 10_000
    more = _wanted(game, world, island, back)
    per_person = game.config.get("population.food_per_person")
    extra = to_milli(back * jobless_family(game.defs, island) * per_person)
    nutrition = game.defs.goods["fish"]["food"]["nutrition"]
    assert more["fish"] - hungry["fish"] >= 0.5 * extra / nutrition
