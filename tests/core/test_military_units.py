"""部隊と基地と動き（フェーズ4-2 の③。設計書 15-military の MI-18〜21・MI-52・MI-54）。"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import stock
from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.game import load_game
from hakoniwa_core.military import army, forces
from hakoniwa_core.state import Facility
from hakoniwa_core.units import QUANTITY_SCALE
from tests.core.test_gate import make_world, run_with_plan


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture(scope="module")
def settled(game):
    world = make_world(game, 3, ["coal", "iron_ore"])
    run_with_plan(game, world, 60)
    return world


@pytest.fixture
def world(settled):
    return copy.deepcopy(settled)


def free_plain(island, near=None, skip=()):
    used = {(f.x, f.y) for f in island.facilities}
    tiles = [
        t
        for t in island.tiles
        if t.terrain == "plain" and (t.x, t.y) not in used and (t.x, t.y) not in skip
    ]
    if near:
        tiles.sort(key=lambda t: abs(t.x - near[0]) + abs(t.y - near[1]))
    return tiles[0]


def put_barracks(island, near=None):
    tile = free_plain(island, near)
    fid = max((f.id for f in island.facilities), default=0) + 1
    island.facilities.append(Facility(id=fid, kind="barracks", x=tile.x, y=tile.y, built=True))
    return tile


def fund(island):
    """兵の賃金が遅れて士気が崩れないよう、国防費を多めに置く。"""
    island.policy.budget[forces.BUDGET_PERSONNEL] = 50_000_000
    island.policy.budget[forces.BUDGET_PROCUREMENT] = 50_000_000


def give(island, good, count, game, turn=0):
    stock.add(island.warehouse, good, count * QUANTITY_SCALE, turn, game.defs.goods)


def step(game, world):
    """1 ターン進める。国の倉庫の食べ物は市場や人口の段で減るので、兵が飢えないよう毎回足す。"""
    island = world.islands[1]
    fund(island)
    give(island, "food", 1, game, world.turn)
    give(island, "ammunition", 1, game, world.turn)
    game.engine.run_turn(world)


def order(game, world, *orders):
    island = world.islands[1]
    queue_orders(island, list(orders))
    for _ in range(6):
        step(game, world)
        if not any(o.status == "pending" for o in island.orders):
            break
    return island


def unit_of(world, island=1):
    return army.units_of(world, island)[0]


def test_a_unit_forms_in_a_barracks_and_gets_ready_when_soldiers_and_arms_arrive(game, world):
    island = world.islands[1]
    base = put_barracks(island)
    order(game, world, ("form_unit", {"army": "infantry", "x": base.x, "y": base.y}))
    unit = unit_of(world)
    assert unit["status"] == army.FORMING and unit["soldiers"] == 0
    forces.state_of(island, game.config)
    forces.recruit(island, game.config, 100, 1000)
    fund(island)
    give(island, "food", 20, game)
    step(game, world)
    assert unit["soldiers"] == 100 and unit["status"] == army.FORMING  # 武器がまだ
    give(island, "weapons", 100, game)
    step(game, world)
    assert unit["status"] == army.READY and unit["equipment"] == {"weapons": 100}
    assert any(e.kind == "unit_ready" for e in island.log)
    assert island.ext["military"]["assigned"] == 100


def test_forming_is_refused_without_a_barracks_or_room(game, world):
    island = world.islands[1]
    tile = free_plain(island)
    order(game, world, ("form_unit", {"army": "infantry", "x": tile.x, "y": tile.y}))
    assert army.units_of(world, 1) == []
    assert any(
        e.kind in ("unit_order_failed", "order_failed")  # 命令の確かめで断られる
        and e.args["reason"] == "reason_no_barracks"
        for e in island.log
    )
    base = put_barracks(island)
    for _ in range(5):  # 兵舎の定員は 500 人
        order(game, world, ("form_unit", {"army": "infantry", "x": base.x, "y": base.y}))
    assert len(army.units_of(world, 1)) == 5
    order(game, world, ("form_unit", {"army": "infantry", "x": base.x, "y": base.y}))
    assert len(army.units_of(world, 1)) == 5


def ready_unit(game, world, kind="infantry", at=None):
    island = world.islands[1]
    base = put_barracks(island, at)
    forces.state_of(island, game.config)
    forces.recruit(island, game.config, 200, 1000)
    fund(island)
    give(island, "food", 50, game)
    give(island, "ammunition", 5, game)
    spec = game.defs.army[kind]
    for good, qty in spec["equipment"].items():
        give(island, good, qty // QUANTITY_SCALE, game)
    order(game, world, ("form_unit", {"army": kind, "x": base.x, "y": base.y}))
    step(game, world)
    unit = unit_of(world)
    assert unit["status"] == army.READY
    return island, unit, base


def test_a_ready_unit_walks_to_its_goal_within_its_speed(game, world):
    island, unit, base = ready_unit(game, world)
    goal = free_plain(island, near=(base.x + 12, base.y), skip={(base.x, base.y)})
    route = army.path(island, game.defs, game.config, (unit["x"], unit["y"]), (goal.x, goal.y))
    assert route and route[-1] == (goal.x, goal.y)
    order(game, world, ("move_unit", {"unit": unit["id"], "x": goal.x, "y": goal.y}))
    moved = abs(unit["x"] - base.x) + abs(unit["y"] - base.y)
    assert 1 <= moved <= 8 * 2  # 1 ターンの動き（速さ 8 マス、道路で増える）を超えない
    for _ in range(20):
        if unit["order"] is None:
            break
        step(game, world)
    assert (unit["x"], unit["y"]) == (goal.x, goal.y) and unit["order"] is None
    assert any(e.kind == "unit_arrived" for e in island.log)


def test_the_path_avoids_the_sea_and_is_deterministic(game, world):
    island = world.islands[1]
    sea = next(t for t in island.tiles if t.terrain == "sea")
    land = free_plain(island)
    assert army.path(island, game.defs, game.config, (land.x, land.y), (sea.x, sea.y)) is None
    a = free_plain(island)
    b = free_plain(island, near=(a.x + 5, a.y + 5), skip={(a.x, a.y)})
    one = army.path(island, game.defs, game.config, (a.x, a.y), (b.x, b.y))
    two = army.path(island, game.defs, game.config, (a.x, a.y), (b.x, b.y))
    assert one == two
    assert all(island.tile(x, y).terrain != "sea" for x, y in one or [])


def test_mountains_and_forests_cost_more_and_roads_less(game, world):
    island = world.islands[1]
    plain = next(t for t in island.tiles if t.terrain == "plain")
    cost_plain = army.enter_cost(plain, game.defs, game.config)
    forest = next((t for t in island.tiles if t.terrain == "forest"), None)
    if forest is not None:
        assert army.enter_cost(forest, game.defs, game.config) > cost_plain
    plain.infra["road"] = [1000, 1000]
    assert army.enter_cost(plain, game.defs, game.config) < cost_plain


def test_armor_needs_fuel_to_move(game, world):
    island, unit, base = ready_unit(game, world, kind="armor")
    goal = free_plain(island, near=(base.x + 6, base.y), skip={(base.x, base.y)})
    stock.take(island.warehouse, "fuel", 10**12)
    order(game, world, ("move_unit", {"unit": unit["id"], "x": goal.x, "y": goal.y}))
    assert (unit["x"], unit["y"]) == (base.x, base.y)
    assert any(e.kind == "unit_no_fuel" for e in island.log)
    give(island, "fuel", 5, game)
    step(game, world)
    assert (unit["x"], unit["y"]) != (base.x, base.y)
    assert stock.total(island.warehouse, "fuel") < 5 * QUANTITY_SCALE


def test_a_unit_digs_a_trench_with_soldiers_and_materials(game, world):
    island, unit, base = ready_unit(game, world)
    give(island, "building_materials", 100, game)  # 道路などの手入れも倉庫の建材を使うので多めに
    order(game, world, ("dig_trench", {"unit": unit["id"]}))
    assert unit["order"]["type"] == "dig" and not army.has_trench(island.tile(base.x, base.y))
    step(game, world)
    step(game, world)
    assert army.has_trench(island.tile(base.x, base.y)) and unit["order"] is None
    order(game, world, ("dig_trench", {"unit": unit["id"]}))  # 2 つ目は掘れない
    assert any(e.args.get("reason") == "reason_trench_exists" for e in island.log)


def test_disband_returns_arms_and_leaves_the_soldiers_in_the_pool(game, world):
    island, unit, base = ready_unit(game, world)
    before = stock.total(island.warehouse, "weapons")
    total = forces.soldiers(island)
    order(game, world, ("disband_unit", {"unit": unit["id"]}))
    assert army.units_of(world, 1) == []
    assert stock.total(island.warehouse, "weapons") == before + 100 * QUANTITY_SCALE
    assert forces.soldiers(island) == total and island.ext["military"]["assigned"] == 0


def test_only_free_soldiers_can_be_discharged(game, world):
    island, unit, base = ready_unit(game, world)
    free = forces.soldiers(island) - 100
    order(game, world, ("discharge", {"count": 10**6}))
    assert forces.soldiers(island) == 100 and free > 0
    order(game, world, ("discharge", {"count": 1}))
    assert any(e.args.get("reason") == "reason_soldiers_in_units" for e in island.log)


def test_unit_soldiers_follow_the_island_total_when_soldiers_die(game, world):
    island, unit, base = ready_unit(game, world)
    forces.lose(island, forces.soldiers(island) - 60)
    step(game, world)
    assert unit["soldiers"] <= forces.soldiers(island) and unit["soldiers"] == 60


def test_missing_arms_are_bought_for_the_unit_from_the_procurement_budget(game, world):
    island = world.islands[1]
    base = put_barracks(island)
    forces.state_of(island, game.config)
    forces.recruit(island, game.config, 100, 1000)
    order(
        game,
        world,
        ("set_defense", {"annual": 5_000_000, "personnel_share": 50}),
        ("form_unit", {"army": "infantry", "x": base.x, "y": base.y}),
    )
    step(game, world)
    assert island.ext["military"]["wanted"].get("weapons") == 100 * QUANTITY_SCALE


def test_a_trench_uses_materials_only_when_it_is_finished(game, world):
    island, unit, base = ready_unit(game, world)
    order(game, world, ("dig_trench", {"unit": unit["id"]}))
    order(game, world, ("dig_trench", {"unit": unit["id"]}))  # 重ねても何もしない
    assert unit["order"]["type"] == "dig"
    step(game, world)
    step(game, world)
    assert not army.has_trench(island.tile(base.x, base.y))  # 建材がないので待つ
    assert unit["order"] is not None
    give(island, "building_materials", 100, game)  # 道路などの手入れも倉庫の建材を使うので多めに
    step(game, world)
    assert army.has_trench(island.tile(base.x, base.y)) and unit["order"] is None
