"""工事の資材をその場で作る（W-289。森の伐採場が切った木で建てる）。"""

from types import SimpleNamespace

import pytest

from hakoniwa_core import autoplan
from hakoniwa_core import facilities as fac
from hakoniwa_core.economy.prices import construction_cost
from hakoniwa_core.game import load_game
from hakoniwa_core.modules.construction import build
from hakoniwa_core.state import MarketRecord
from tests.core.test_rules import make_island, with_economy


@pytest.fixture(scope="module")
def game():
    return load_game()


def _forest_island(game):
    return with_economy(game, make_island(game, terrain={(10, 10): "forest"}))


def test_only_material_the_recipe_makes(game):
    defs = game.defs
    assert set(fac.own_materials(defs, "lumber_camp")) == {"wood"}
    assert fac.own_materials(defs, "farm") == {}  # 農場は木材を産まない
    qty = fac.own_materials(defs, "lumber_camp")["wood"]
    recipe = defs.facilities["lumber_camp"]["recipes"]["default"]
    expected = -(
        -qty * fac.workers_needed(defs, "lumber_camp", "default") // recipe["outputs"]["wood"]
    )
    assert fac.own_material_labor(defs, "lumber_camp", "default", "wood", qty) == expected
    # 生産性が半分なら倍の人手
    assert (
        fac.own_material_labor(defs, "lumber_camp", "default", "wood", qty, fac.FULL // 2)
        == 2 * expected
    )


def test_lumber_camp_cuts_its_own_wood_when_none_is_on_sale(game):
    island = _forest_island(game)
    x, y = 10, 10
    ctx = SimpleNamespace(defs=game.defs, config=game.config, turn=1)
    build(island, {"facility": "lumber_camp", "x": x, "y": y}, ctx)
    f = island.facilities[-1]
    own = f.ext[fac.OWN_MATERIALS]
    assert set(own["materials"]) == {"wood"}
    assert "wood" not in fac.materials_of(game.defs, f)  # 木材は買わない
    assert own["labor"] > 0 and fac.site_labor(f) == own["labor"]
    assert (
        fac.construction_labor(game.defs, f)
        > game.defs.facilities["lumber_camp"]["construction"]["labor"]
    )


def test_buys_wood_when_on_sale_and_cheaper(game):
    island = _forest_island(game)
    x, y = 10, 10
    island.economy.prices["wood"] = 1
    island.economy.average_wage = 10**6  # 人手が高い
    island.markets.append(MarketRecord("wood", 1, 0, 0, 10**6, "trade"))
    ctx = SimpleNamespace(defs=game.defs, config=game.config, turn=1)
    build(island, {"facility": "lumber_camp", "x": x, "y": y}, ctx)
    f = island.facilities[-1]
    assert fac.OWN_MATERIALS not in f.ext
    assert "wood" in fac.materials_of(game.defs, f)


def test_cost_takes_the_cheaper_way(game):
    defs = game.defs
    dear = construction_cost(defs, "lumber_camp", {"wood": 10**9}, 100.0)
    labor = defs.facilities["lumber_camp"]["construction"]
    qty = fac.own_materials(defs, "lumber_camp")["wood"]
    cut = fac.own_material_labor(defs, "lumber_camp", "default", "wood", qty) * 100.0
    assert dear == pytest.approx(cut + labor["labor"] * labor["turns"] * 100.0)


def test_state_plan_does_not_wait_for_wood_to_build_a_lumber_camp(game):
    assert "wood" not in autoplan._bought_materials(game.defs, "lumber_camp")
    assert "wood" in autoplan._bought_materials(game.defs, "farm")
