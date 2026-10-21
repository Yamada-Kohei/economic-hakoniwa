"""隠した施設（フェーズ4-3 の③c。設計書 15-military の MI-62）。"""

from __future__ import annotations

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.diplomacy import war
from hakoniwa_core.game import load_game
from hakoniwa_core.military import army, hidden, missiles, occupation
from hakoniwa_core.modules.construction import check_build
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.state import Facility
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import QUANTITY_SCALE


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    world = generate_world(7, 3, game.config, game.defs)
    for island in world.islands.values():
        island.regime["politics"] = "authoritarian"
    return world


def ctx_of(game, world):
    return StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.engine.registry,
        formulas={},
        turn=world.turn + 1,
        seed=world.seed,
        stage="operations",
        before=world,
    )


def step(game, world, orders):
    for island_id, items in orders.items():
        queue_orders(world.islands[island_id], items)
    game.engine.run_turn(world)


def free_tiles(island, count):
    used = {(f.x, f.y) for f in island.facilities}
    return [t for t in island.tiles if t.terrain == "plain" and (t.x, t.y) not in used][:count]


def secret_base(island, kind="missile_base", built=True):
    tile = free_tiles(island, 1)[0]
    f = Facility(id=max([f.id for f in island.facilities] + [0]) + 1, kind=kind, x=tile.x, y=tile.y)
    f.built = built
    f.ext["hidden"] = True
    island.facilities.append(f)
    return f


def test_only_a_few_facilities_can_be_hidden(game, world):
    isl = world.islands[1]
    tile = free_tiles(isl, 1)[0]
    ok = {"facility": "missile_base", "x": tile.x, "y": tile.y, "hidden": True}
    check_build(isl, ok, game.defs)  # 通る
    with pytest.raises(OrderFailed) as why:
        check_build(isl, {**ok, "facility": "barracks"}, game.defs)
    assert why.value.reason == "reason_cannot_hide"


def test_a_hidden_site_takes_longer_and_more_materials(game, world):
    isl = world.islands[1]
    tile = free_tiles(isl, 1)[0]
    step(
        game,
        world,
        {1: [("build", {"facility": "missile_base", "x": tile.x, "y": tile.y, "hidden": True})]},
    )
    f = isl.facility_at(tile.x, tile.y)
    assert f is not None and f.ext["hidden"] is True
    assert fac.total_progress(game.defs, f) == 8 * fac.FULL
    plain = game.defs.facilities["missile_base"]["construction"]["materials"]
    assert fac.materials_of(game.defs, f) == {g: q * 2 for g, q in plain.items()}


def test_a_foreign_unit_next_to_it_finds_it_and_so_does_an_ally(game, world):
    a, b = world.islands[1], world.islands[2]
    f = secret_base(b)
    assert not hidden.visible_to(f, 2, 1) and hidden.visible_to(f, 2, 2)
    unit = army.new_unit(world, 1, "infantry", f.x + 1, f.y)
    unit["status"] = army.READY
    unit["at"] = 2  # 外国の島にいる
    assert hidden.discover(world) == [(2, 1, f.id)]
    assert hidden.visible_to(f, 2, 1) and not hidden.visible_to(f, 2, 3)
    assert hidden.discover(world) == []  # 見つかったのは 1 度だけ
    assert a.log[-1].kind == "hidden_found" and b.log[-1].kind == "hidden_found_by"


def test_an_occupied_tile_gives_the_facility_away(game, world):
    b = world.islands[2]
    f = secret_base(b)
    b.tile(f.x, f.y).ext[occupation.OCCUPIED] = {"by": 3, "turn": 1}
    assert hidden.discover(world) == [(2, 3, f.id)]


def test_firing_from_a_hidden_base_gives_its_place_away(game, world):
    step(game, world, {1: [("declare_war", {"target": 2})]})
    assert war.status(world, 1, 2, world.turn + 1) == war.WAR
    a = world.islands[1]
    base = secret_base(a)
    stock.add(base.stock, "missile", 3 * QUANTITY_SCALE, 1, game.defs.goods)
    ctx = ctx_of(game, world)
    req = {"base": base.id, "island": 2, "x": 1, "y": 1, "count": 1}
    assert missiles.launch(world, ctx, a, req) is None
    assert hidden.visible_to(base, 1, 2) and not hidden.visible_to(base, 1, 3)
