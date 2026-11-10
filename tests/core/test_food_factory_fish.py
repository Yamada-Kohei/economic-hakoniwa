"""食品工場の魚の作り方（FM-62。2026-11-10、W-249）。"""

from __future__ import annotations

from hakoniwa_core.economy.prices import variable_profit
from hakoniwa_core.state import Facility
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）


def test_food_factory_can_make_food_from_fish(game):
    """栄養の量は加工で変わらず、魚が安ければ魚の作り方の方が儲かる（FM-62）。"""
    goods = game.defs.goods
    recipes = game.defs.facilities["food_factory"]["recipes"]
    fish = recipes["fish"]

    def nutrition(side):
        return sum(goods[g]["food"]["nutrition"] * q for g, q in side.items() if "food" in goods[g])

    assert nutrition(fish["inputs"]) == nutrition(fish["outputs"])
    prices = {"grain": 2000, "fish": 500, "food": 3000, "electricity": 100}
    profits = {}
    for name in ("default", "fish"):
        f = Facility(id=1, kind="food_factory", x=0, y=0, recipe=name, built=True)
        profits[name] = variable_profit(game.defs, game.config, f, prices, 1000, 0)
    assert profits["fish"] > profits["default"]
