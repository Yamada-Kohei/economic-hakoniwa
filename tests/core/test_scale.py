"""国の大きさの倍と施設の建て増し（段6 A3、W-85）。"""

from __future__ import annotations

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import scale
from hakoniwa_core.economy import equipment
from hakoniwa_core.game import load_game
from hakoniwa_core.state import OrderState
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    world = generate_world(3, 2, game.config, game.defs)
    game.engine.run_turn(world)  # 経済のある島にする
    return world


def order(game, world, island, kind, payload):
    island.orders = [OrderState(id=1, seq=1, kind=kind, payload=payload)]
    game.engine.run_turn(world)
    return island.orders[0]


# --- 国の大きさの倍 ----------------------------------------------------------------------


def test_scale_multiplies_sizes_but_not_per_person_amounts(monkeypatch):
    base = load_game()
    monkeypatch.setenv(scale.ENV, "20")
    big = load_game()
    assert scale.people(big.config) == 20
    farm, farm20 = base.defs.facilities["farm"], big.defs.facilities["farm"]
    assert farm20["workers"] == farm["workers"] * 20
    assert farm20["recipes"]["default"]["outputs"]["grain"] == (
        farm["recipes"]["default"]["outputs"]["grain"] * 20
    )
    assert (
        big.defs.settlements["town"]["capacity"] == base.defs.settlements["town"]["capacity"] * 20
    )
    assert big.defs.army["infantry"]["soldiers"] == base.defs.army["infantry"]["soldiers"] * 20
    assert big.defs.army["corvette"]["soldiers"] == base.defs.army["corvette"]["soldiers"]
    assert big.config.get("trade.ship_capacity") == base.config.get("trade.ship_capacity") * 20
    # 1 人あたりの量と賃金は変えない
    for name in ("population.food_per_person", "labor.initial_wage", "founding.money.treasury"):
        assert big.config.get(name) == base.config.get(name)
    office = "government_office"
    assert big.defs.facilities[office]["admin_power"] * 20 == pytest.approx(
        base.defs.facilities[office]["admin_power"]
    )


def test_founding_population_follows_the_scale(monkeypatch):
    small = load_game()
    monkeypatch.setenv(scale.ENV, "20")
    big = load_game()
    count = [
        sum(t.population for t in island.tiles)
        for g in (small, big)
        for island in [generate_world(5, 1, g.config, g.defs).islands[1]]
    ]
    assert count[1] == count[0] * 20


# --- 建て増し ------------------------------------------------------------------------------


def _built(island, kind):
    return next(f for f in island.facilities if f.kind == kind and f.built)


def _state_plant(world):
    """国営の火力発電所のある島と、その発電所。"""
    for island in world.islands.values():
        for f in island.facilities:
            if f.kind == "thermal_power_plant" and f.built and f.owner == "state":
                return island, f
    raise AssertionError("国営の火力発電所がない")


def test_size_multiplies_workers_inputs_outputs_and_equipment(game, world):
    island = world.islands[1]
    plant = _built(island, "thermal_power_plant")
    workers = fac.workers_of(game.defs, plant)
    outputs = dict(fac.recipe_of(game.defs, plant)["outputs"])
    kit = dict(equipment.kit_of(game.defs, plant))
    plant.ext[fac.SIZE] = 3
    assert fac.workers_of(game.defs, plant) == workers * 3
    assert dict(fac.recipe_of(game.defs, plant)["outputs"]) == {
        g: q * 3 for g, q in outputs.items()
    }
    assert dict(equipment.kit_of(game.defs, plant)) == {g: q * 3 for g, q in kit.items()}
    assert fac.engineers_of(game.defs, plant) == fac.engineers_needed(game.defs, plant.kind) * 3


def test_build_on_the_same_facility_starts_an_expansion(game, world):
    island, plant = _state_plant(world)
    payload = {"facility": plant.kind, "x": plant.x, "y": plant.y}
    done = order(game, world, island, "build", payload)
    assert done.status == "done", done.reason
    site = next(f for f in island.facilities if fac.expansion_of(f) == plant.id)
    assert not site.built and not fac.on_map(site)
    assert fac.position(site) == (plant.x, plant.y)
    assert plant.built  # 工事の間も動く
    for _ in range(30):
        if site not in island.facilities:
            break
        game.engine.run_turn(world)
    assert site not in island.facilities
    assert fac.size_of(plant) == 2
    assert any(e.kind == "facility_expanded" for e in island.log)


def test_land_bound_facilities_cannot_expand(game, world):
    island = world.islands[1]
    farm = _built(island, "farm")
    problem = fac.placement_problem(game.defs, island, "farm", farm.x, farm.y, expand=True)
    assert problem is not None and problem[0] == "reason_cannot_expand"


def test_expansion_stops_at_the_tile_limit(game, world):
    island, plant = _state_plant(world)
    plant.ext[fac.SIZE] = fac.max_size(game.defs, plant.kind)
    problem = fac.placement_problem(game.defs, island, plant.kind, plant.x, plant.y, expand=True)
    assert problem is not None and problem[0] == "reason_expand_full"


def test_other_facility_still_blocks_the_tile(game, world):
    island = world.islands[1]
    plant = _built(island, "thermal_power_plant")
    problem = fac.placement_problem(game.defs, island, "steel_mill", plant.x, plant.y)
    assert problem is not None and problem[0] in ("reason_tile_occupied", "reason_wrong_terrain")


def test_world_scale_comes_after_the_environment(monkeypatch):
    monkeypatch.delenv(scale.ENV, raising=False)
    raw = {"scale": {"people": 20}}
    assert scale.factor(raw) == 20
    assert scale.factor(raw, world=1) == 1  # 倍を入れる前の世界
    monkeypatch.setenv(scale.ENV, "5")
    assert scale.factor(raw, world=1) == 5


def test_searching_for_a_free_tile_skips_the_same_facility(game, world):
    """建国や民営の会社の空いたマス探しは、同じ施設のマスを空いたマスとみない。"""
    island = world.islands[1]
    plant = _built(island, "thermal_power_plant")
    problem = fac.placement_problem(game.defs, island, plant.kind, plant.x, plant.y)
    assert problem is not None and problem[0] == "reason_tile_occupied"


def test_big_cargo_ships_are_not_counted_twice(monkeypatch):
    """輸送船は 1 隻を大きくする（積める重さと燃料を倍）。造船所の作る隻数は倍 1 のまま。"""
    base = load_game()
    monkeypatch.setenv(scale.ENV, "20")
    big = load_game()
    yard, yard20 = base.defs.facilities["shipyard"], big.defs.facilities["shipyard"]
    out = yard["recipes"]["default"]["outputs"]["transport_ship"]
    assert yard20["recipes"]["default"]["outputs"]["transport_ship"] == out
    assert yard20["recipes"]["warship"]["outputs"]["warship"] == pytest.approx(
        yard["recipes"]["warship"]["outputs"]["warship"] * 20
    )
    assert big.config.get("trade.ship_fuel")["fuel"] == pytest.approx(
        base.config.get("trade.ship_fuel")["fuel"] * 20
    )
    assert big.defs.facilities["radar"]["military"]["radar"] == 3  # 見える距離は変えない
    assert big.defs.facilities["missile_base"]["military"]["missile_base"] == 12 * 20


def test_resource_and_counted_facilities_do_not_expand(game):
    for kind in ("hydro_power_plant", "nuclear_facility"):
        assert fac.max_size(game.defs, kind) == 1


def test_repairs_of_an_expanded_facility_need_the_whole_size(game, world):
    island, plant = _state_plant(world)
    one = fac.materials_of(game.defs, plant)
    plant.ext[fac.SIZE] = 3
    assert fac.materials_of(game.defs, plant) == {g: q * 3 for g, q in one.items()}


def _finish(game, world, island, site):
    for _ in range(30):
        if site not in island.facilities:
            return
        game.engine.run_turn(world)
    raise AssertionError("建て増しの工事が済まない")


def test_expansion_is_dropped_when_the_owner_changes(game, world):
    island, plant = _state_plant(world)
    payload = {"facility": plant.kind, "x": plant.x, "y": plant.y}
    assert order(game, world, island, "build", payload).status == "done"
    site = next(f for f in island.facilities if fac.expansion_of(f) == plant.id)
    site.owner = "private"  # 工事の間に持ち主が替わったのと同じ（国営の工事と民営の施設）
    _finish(game, world, island, site)
    assert fac.size_of(plant) == 1  # 工事は取り除かれ、規模は上がらない


def test_expansion_finishes_on_a_facility_under_repair(game, world):
    island, plant = _state_plant(world)
    payload = {"facility": plant.kind, "x": plant.x, "y": plant.y}
    assert order(game, world, island, "build", payload).status == "done"
    site = next(f for f in island.facilities if fac.expansion_of(f) == plant.id)
    site.progress = fac.total_progress(game.defs, site) - 1
    plant.built = False  # 戦争や暴動で壊れて直している
    plant.ext["repair"] = True
    plant.progress = 0
    _finish(game, world, island, site)
    assert fac.size_of(plant) == 2
