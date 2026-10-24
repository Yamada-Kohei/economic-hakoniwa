"""水力発電所の立地（W-148）: 出力は川の水の量で決まる。

- 水は周りの山と森に降った雨が集まったもので、近くの水力発電所どうしで分け合う
- 水が足りない場所には建てられない
- 気候帯の雨の多さと、年ごとの雨のぶれ（渇水の年）で出力が変わる
"""

from __future__ import annotations

import pytest

from hakoniwa_core import autoplan, nature
from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import productivity
from hakoniwa_core.game import load_game
from hakoniwa_core.modules.events import Events
from hakoniwa_core.state import Facility
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.turn.context import StageContext

KIND = "hydro_power_plant"


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def island(game):
    """全部が平地の島に、(10..14, 10..14) の山の塊を 1 つだけ置いたもの（施設はなし）。"""
    world = generate_world(7, 1, game.config, game.defs)
    isl = world.islands[1]
    isl.facilities = []
    for t in isl.tiles:
        t.terrain = "plain" if 2 <= t.x < 30 and 2 <= t.y < 30 else "sea"
    for y in range(10, 15):
        for x in range(10, 15):
            isl.tile(x, y).terrain = "mountain"
    isl.ext[nature.CLIMATE] = "temperate"
    isl.ext.pop(nature.RAIN, None)
    return isl


def spec(game):
    return game.defs.facilities[KIND]["recipes"]["default"]["water"]


def factor(game, island, x, y, weather=True):
    return nature.water_factor(island, game.defs, spec(game), x, y, weather)


def plant(island, x, y, built=True):
    f = Facility(id=island.next_facility_id(), kind=KIND, x=x, y=y, recipe="default", built=built)
    island.facilities.append(f)
    return f


def test_a_mountain_mass_gives_full_power_and_a_lone_hill_does_not(game, island):
    assert factor(game, island, 12, 12) == 1.0
    lone = island.tile(25, 25)
    lone.terrain = "mountain"
    assert factor(game, island, 25, 25) < spec(game)["min"]
    assert fac.placement_problem(game.defs, island, KIND, 12, 12) is None
    reason, args = fac.placement_problem(game.defs, island, KIND, 25, 25)
    assert reason == "reason_dry_site" and args["percent"] < args["min"]


def test_forest_collects_less_water_than_mountain(game, island):
    for x in range(20, 25):
        for y in range(20, 25):
            island.tile(x, y).terrain = "forest"
    island.tile(22, 22).terrain = "mountain"
    forest_site = factor(game, island, 22, 22)
    assert 0.2 < forest_site < 1.0
    assert forest_site < factor(game, island, 12, 12)


def test_nearby_plants_share_the_water_and_far_ones_do_not(game, island):
    first = plant(island, 12, 12)
    alone = factor(game, island, 12, 12)
    assert alone == 1.0
    plant(island, 13, 13)  # 集水域がほとんど重なる
    shared = factor(game, island, 12, 12)
    assert shared < alone
    # 工事中も水を取る。出力の合計は 1 つだけ建てたときより増えるが、2 つ分にはならない
    total = shared + factor(game, island, 13, 13)
    assert alone < total < 2 * alone
    # 離れた別の山の塊（集水域が重ならない）とは分け合わない
    for y in range(24, 29):
        for x in range(24, 29):
            island.tile(x, y).terrain = "mountain"
    assert factor(game, island, 26, 26) == 1.0
    island.facilities.remove(first)
    assert factor(game, island, 13, 13) == 1.0  # 取り壊せば水が戻る


def test_a_foreclosed_plant_does_not_keep_its_neighbours_water(game, island):
    plant(island, 12, 12)
    dead = plant(island, 13, 13)
    shared = factor(game, island, 12, 12)
    dead.economy["foreclosed"] = True  # 差し押さえ中の発電所は水を使わない
    assert factor(game, island, 12, 12) > shared
    assert factor(game, island, 12, 12) == 1.0


def test_a_second_plant_cannot_be_built_on_what_is_left(game, island):
    for x, y in ((11, 11), (13, 13), (11, 13), (13, 11)):
        plant(island, x, y)
    problem = fac.placement_problem(game.defs, island, KIND, 12, 12)
    assert problem is not None and problem[0] == "reason_dry_site"


def test_productivity_follows_the_water(game, island):
    f = plant(island, 12, 12)
    island.economy.started = 0  # has_economy
    full = productivity.productivity(
        island, game.defs, game.config, fac.recipe_of(game.defs, f), 12, 12
    )
    plant(island, 13, 13)
    shared = productivity.productivity(
        island, game.defs, game.config, fac.recipe_of(game.defs, f), 12, 12
    )
    assert shared < full


def test_climate_and_the_year_change_the_output(game, island):
    plant(island, 11, 11)
    plant(island, 13, 13)
    plant(island, 12, 12)
    base = factor(game, island, 12, 12)
    assert 0.3 < base < 1.0
    island.ext[nature.CLIMATE] = "tropical"
    assert factor(game, island, 12, 12) > base
    island.ext[nature.CLIMATE] = "cold"
    assert factor(game, island, 12, 12) < base
    island.ext[nature.CLIMATE] = "temperate"
    island.ext[nature.RAIN] = {"factor": 600, "turn": 12}  # 渇水の年
    assert factor(game, island, 12, 12) == pytest.approx(base * 0.6)
    assert (
        factor(game, island, 12, 12, weather=False) == base
    )  # 建てられるかの判断は年のぶれを見ない


def _events_context(game, world, turn, seed):
    return StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.registry,
        formulas=game.engine.formulas,
        turn=turn,
        seed=seed,
        stage="events",
        before=world,
    )


def test_the_rain_of_the_next_year_is_drawn_in_december_and_drought_is_reported(game):
    world = generate_world(7, 2, game.config, game.defs)
    island = world.islands[1]
    island.facilities.append(Facility(id=999, kind=KIND, x=1, y=1, recipe="default", built=True))
    drawn = []
    for seed in range(60):  # 渇水の年が出る種を探す
        island.ext.pop(nature.RAIN, None)
        island.log.clear()
        Events().run_island(island, _events_context(game, world, 12, seed))
        value = island.ext[nature.RAIN]["factor"] / 1000
        rain = game.config.get("climate.rain")
        assert rain["min"] <= value <= rain["max"]
        drawn.append(value)
        assert any(e.kind == "rain_dry" for e in island.log) == (value < 0.8)
    assert min(drawn) < 0.8 < max(drawn)  # 渇水の年もふつうの年もある
    # 水力発電所のない島には知らせず、12月以外には引かない
    island.facilities.pop()
    island.ext.pop(nature.RAIN, None)
    island.log.clear()
    Events().run_island(island, _events_context(game, world, 12, 5))
    assert nature.RAIN in island.ext and not any(e.kind == "rain_dry" for e in island.log)
    island.ext.pop(nature.RAIN, None)
    Events().run_island(island, _events_context(game, world, 7, 5))
    assert nature.RAIN not in island.ext


def test_the_standard_plan_counts_only_the_water_it_will_get(game, island):
    island.economy.started = 0
    plant(island, 11, 11)
    plant(island, 13, 13)
    plant(island, 12, 12)
    supply, _ = autoplan._flows(island, game.defs, game.config)
    power = game.config.get("power.good")
    nominal = 3 * game.defs.facilities[KIND]["recipes"]["default"]["outputs"][power]
    assert 0 < supply[power] < nominal
    # 水の少ない場所には、標準の計画は水力発電所を建てない（建てられる下限より厳しい）
    lone = island.tile(25, 25)
    lone.terrain = "mountain"
    for y in range(24, 27):
        for x in range(24, 27):
            island.tile(x, y).terrain = "mountain"
    level = factor(game, island, 25, 25, weather=False)
    assert spec(game)["min"] <= level < autoplan.WATER_MIN
    site = autoplan._site_for(island, game.defs, game.config, KIND, "default")
    assert site is None or factor(game, island, *site, weather=False) >= autoplan.WATER_MIN
