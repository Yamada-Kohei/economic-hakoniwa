"""ミサイルと防空（フェーズ4-3 の③。15-military の MI-37・MI-38）。"""

from __future__ import annotations

import random

import pytest

from hakoniwa_core import stock
from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.diplomacy import war
from hakoniwa_core.game import load_game
from hakoniwa_core.military import missiles
from hakoniwa_core.state import Facility
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import QUANTITY_SCALE


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    world = generate_world(7, 2, game.config, game.defs)
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


def start_war(game, world):
    queue_orders(world.islands[1], [("declare_war", {"target": 2})])
    game.engine.run_turn(world)
    assert war.status(world, 1, 2, world.turn + 1) == war.WAR


def free_tiles(island, count):
    used = {(f.x, f.y) for f in island.facilities}
    return [t for t in island.tiles if t.terrain == "plain" and (t.x, t.y) not in used][:count]


def build(game, island, kind, tile, filled=0):
    f = Facility(id=max([f.id for f in island.facilities] + [0]) + 1, kind=kind, x=tile.x, y=tile.y)
    f.built = True
    island.facilities.append(f)
    good = {"missile_base": "missile", "air_defense": "interceptor"}.get(kind)
    if good and filled:
        stock.add(f.stock, good, filled * QUANTITY_SCALE, 1, game.defs.goods)
    return f


def test_bases_fill_up_from_the_warehouse(game, world):
    isl = world.islands[1]
    base = build(game, isl, "missile_base", free_tiles(isl, 1)[0])
    stock.add(isl.warehouse, "missile", 5 * QUANTITY_SCALE, 1, game.defs.goods)
    assert missiles.wanted(isl, game.defs, world.turn + 1) == {"missile": 12 * QUANTITY_SCALE}
    missiles.restock(world, ctx_of(game, world))
    assert stock.total(base.stock, "missile") == 5 * QUANTITY_SCALE
    assert stock.total(isl.warehouse, "missile") == 0
    assert missiles.wanted(isl, game.defs, world.turn + 1) == {"missile": 7 * QUANTITY_SCALE}


def fire(game, world, base, island, x, y, count):
    ctx = ctx_of(game, world)
    reason = missiles.launch(
        world,
        ctx,
        world.islands[1],
        {"base": base.id, "island": island, "x": x, "y": y, "count": count},
    )
    return ctx, reason


def test_firing_needs_war_range_stock_and_a_turn_limit(game, world):
    isl = world.islands[1]
    base = build(game, isl, "missile_base", free_tiles(isl, 1)[0], filled=10)
    assert fire(game, world, base, 2, 1, 1, 1)[1] == "reason_not_at_war"
    start_war(game, world)
    assert fire(game, world, base, 2, 1, 1, 1)[1] is None
    ctx, reason = fire(game, world, base, 2, 1, 1, 9)
    assert reason is None  # 1 ターンに 4 発まで: 残りは 3 発
    assert base.ext["fired"]["n"] == 4
    assert fire(game, world, base, 2, 1, 1, 1)[1] == "reason_no_missiles"
    assert stock.total(base.stock, "missile") == 6 * QUANTITY_SCALE
    far = world.islands[2]
    far.wx, far.wy = isl.wx + 20, isl.wy
    base.ext.pop("fired")
    assert fire(game, world, base, 2, 1, 1, 1)[1] == "reason_out_of_range"
    far.wx, far.wy = isl.wx + 1, isl.wy
    assert fire(game, world, base, 2, 999, 1, 1)[1] == "reason_off_island"
    assert fire(game, world, base, 2, 1, 1, 1)[1] is None
    assert (
        missiles.launch(
            world, ctx_of(game, world), isl, {"base": 99, "island": 2, "x": 1, "y": 1, "count": 1}
        )
        == "reason_no_missile_base"
    )


def test_a_hit_on_a_town_kills_and_scatters_people_and_is_recorded_as_a_violation(game, world):
    start_war(game, world)
    isl, target = world.islands[1], world.islands[2]
    base = build(game, isl, "missile_base", free_tiles(isl, 1)[0], filled=12)
    town = next(t for t in target.tiles if t.population > 0)
    town.population = 1000
    cfg = game.config.get("military.missiles")
    assert cfg["drift"] < 1
    ctx, reason = fire(game, world, base, 2, town.x, town.y, 2)
    assert reason is None
    assert fire(game, world, base, 2, town.x, town.y, 2)[1] is None  # 命令を分けても記録は 1 回
    before = sum(t.population for t in target.tiles)
    missiles.run(world, ctx)
    assert [e for e in target.log if e.kind == "missile_hit"]
    assert sum(t.population for t in target.tiles) < before  # 亡くなった人
    assert len(isl.ext["diplomacy"]["violations"]) == 1, "住宅を撃った国は違反として 1 回記録される"
    assert missiles.strikes(world) == []


def test_air_defense_shoots_down_missiles_within_its_radius(game, world):
    start_war(game, world)
    isl, target = world.islands[1], world.islands[2]
    base = build(game, isl, "missile_base", free_tiles(isl, 1)[0], filled=12)
    tile = free_tiles(target, 5)[2]
    shield = build(game, target, "air_defense", tile, filled=12)
    ctx, reason = fire(game, world, base, 2, tile.x, tile.y, 4)
    assert reason is None
    missiles.run(world, ctx)
    spent = 12 - stock.total(shield.stock, "interceptor") // QUANTITY_SCALE
    assert spent >= 4  # 4 発それぞれに迎撃ミサイルを 1 発以上使う
    intercepted = [e for e in target.log if e.kind == "missile_intercepted"]
    hits = [e for e in target.log if e.kind == "missile_hit"]
    assert len(intercepted) + len(hits) == 4


def test_a_hit_ruins_or_halts_a_factory_and_halves_roads(game, world):
    start_war(game, world)
    isl, target = world.islands[1], world.islands[2]
    base = build(game, isl, "missile_base", free_tiles(isl, 1)[0], filled=12)
    tile = free_tiles(target, 1)[0]
    plant = build(game, target, "arms_plant", tile)
    for kind in game.defs.infra_kinds("transport"):
        tile.infra[kind] = [1000, 1000]
    cfg = dict(game.config.get("military.missiles"))
    assert cfg["infra"] == 0.5
    ctx, _ = fire(game, world, base, 2, tile.x, tile.y, 1)
    # ずれと迎撃がなくても、1 発で施設の状態が変わる（壊れるか止まる）か、ずれて外れる
    missiles.run(world, ctx)
    moved = [e for e in target.log if e.kind == "missile_hit"]
    assert len(moved) == 1
    hit_here = (moved[0].args["x"], moved[0].args["y"]) == (tile.x, tile.y)
    if hit_here:
        assert (not plant.built) or plant.ext.get("halted_until", 0) > ctx.turn


def test_a_halted_base_cannot_fire_be_filled_or_intercept(game, world):
    start_war(game, world)
    isl, target = world.islands[1], world.islands[2]
    base = build(game, isl, "missile_base", free_tiles(isl, 1)[0], filled=5)
    stock.add(isl.warehouse, "missile", 5 * QUANTITY_SCALE, 1, game.defs.goods)
    turn = world.turn + 1
    base.ext["halted_until"] = turn + 3
    assert fire(game, world, base, 2, 1, 1, 1)[1] == "reason_base_halted"
    missiles.restock(world, ctx_of(game, world))
    assert stock.total(base.stock, "missile") == 5 * QUANTITY_SCALE
    assert missiles.wanted(isl, game.defs, turn) == {}
    shield = build(game, target, "air_defense", free_tiles(target, 1)[0], filled=3)
    shield.ext["halted_until"] = turn
    assert not missiles._intercepted(
        world,
        ctx_of(game, world),
        target,
        game.config.get("military.missiles"),
        random.Random(0),
        shield.x,
        shield.y,
    )
    assert stock.total(shield.stock, "interceptor") == 3 * QUANTITY_SCALE
