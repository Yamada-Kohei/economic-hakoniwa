"""核兵器（フェーズ4-3 の③b。設計書 15-military の MI-46・MI-47・MI-60）。"""

from __future__ import annotations

import random

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import nature, stock
from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.diplomacy import arms, credit, war
from hakoniwa_core.game import load_game
from hakoniwa_core.military import missiles, nuclear
from hakoniwa_core.modules.production import Production
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


def step(game, world, orders):
    for island_id, items in orders.items():
        queue_orders(world.islands[island_id], items)
    game.engine.run_turn(world)


def start_war(game, world):
    step(game, world, {1: [("declare_war", {"target": 2})]})
    assert war.status(world, 1, 2, world.turn + 1) == war.WAR


def free_tiles(island, count):
    used = {(f.x, f.y) for f in island.facilities}
    return [t for t in island.tiles if t.terrain == "plain" and (t.x, t.y) not in used][:count]


def build(game, island, kind, tile):
    f = Facility(id=max([f.id for f in island.facilities] + [0]) + 1, kind=kind, x=tile.x, y=tile.y)
    f.built = True
    island.facilities.append(f)
    return f


def load(game, f, good, count):
    stock.add(f.stock, good, count * QUANTITY_SCALE, 1, game.defs.goods)


def test_the_chain_needs_uranium_ore(game):
    base = {"iron_ore", "coal", "crude_oil", "copper_ore"}
    assert "nuclear_warhead" not in fac.producible_goods(game.defs, base)
    assert "nuclear_warhead" in fac.producible_goods(game.defs, base | {"uranium_ore"})


def test_a_nuclear_launch_needs_a_warhead_and_uses_one_with_each_missile(game, world):
    start_war(game, world)
    isl = world.islands[1]
    base = build(game, isl, "missile_base", free_tiles(isl, 1)[0])
    load(game, base, "missile", 5)
    ctx = ctx_of(game, world)
    req = {"base": base.id, "island": 2, "x": 1, "y": 1, "count": 2, "nuclear": True}
    assert missiles.launch(world, ctx, isl, req) == "reason_no_warheads"
    load(game, base, "nuclear_warhead", 1)
    assert missiles.launch(world, ctx, isl, req) is None  # 弾頭は 1 発なので 1 発だけ撃つ
    assert stock.total(base.stock, "nuclear_warhead") == 0
    assert stock.total(base.stock, "missile") == 4 * QUANTITY_SCALE
    assert (
        missiles.strikes(world)[-1]["nuclear"] is True and missiles.strikes(world)[-1]["count"] == 1
    )


def test_warheads_fill_the_bases_only_for_a_country_with_a_nuclear_facility(game, world):
    isl = world.islands[1]
    base = build(game, isl, "missile_base", free_tiles(isl, 1)[0])
    stock.add(isl.warehouse, "nuclear_warhead", 3 * QUANTITY_SCALE, 1, game.defs.goods)
    turn = world.turn + 1
    assert "nuclear_warhead" not in missiles.wanted(isl, game.defs, turn)
    build(game, isl, "nuclear_facility", free_tiles(isl, 2)[1])
    assert missiles.wanted(isl, game.defs, turn)["nuclear_warhead"] == 2 * QUANTITY_SCALE
    missiles.restock(world, ctx_of(game, world))
    assert stock.total(base.stock, "nuclear_warhead") == 2 * QUANTITY_SCALE  # 基地の上限は 2 発
    assert stock.total(isl.warehouse, "nuclear_warhead") == QUANTITY_SCALE


def test_a_nuclear_hit_kills_by_ring_ruins_facilities_and_leaves_fallout(game, world):
    start_war(game, world)
    attacker, loc = world.islands[1], world.islands[2]
    cx, cy = loc.size // 2, loc.size // 2
    for tile in loc.tiles:
        tile.population = 1000
    plant = build(game, loc, "arms_plant", loc.tile(cx + 1, cy))
    far = build(game, loc, "arms_plant", loc.tile(cx + 3, cy))
    total = sum(t.population for t in loc.tiles)
    cfg = game.config.get("military.nuclear")
    expected = sum(
        round(t.population * cfg["dead"][nuclear.ring(t.x, t.y, cx, cy)])
        for t in loc.tiles
        if nuclear.ring(t.x, t.y, cx, cy) < len(cfg["dead"])
    )
    ctx = ctx_of(game, world)
    killed = nuclear.detonate(world, ctx, loc, cx, cy, attacker)
    assert killed == expected and killed > 0
    assert (
        sum(t.population for t in loc.tiles) == total - killed
    )  # 逃げた人は同じ島の別のマスへ移る
    assert not plant.built and far.built  # 2 周の中の施設は壊れ、外は壊れない
    assert loc.tile(cx, cy).ext[nature.FALLOUT] == 8000
    assert loc.tile(cx + 1, cy).ext[nature.FALLOUT] == 5000
    assert loc.tile(cx + 2, cy).ext[nature.FALLOUT] == 3000
    assert nature.FALLOUT not in loc.tile(cx + 3, cy).ext


def test_fallout_halves_in_the_half_life_and_adds_to_the_pollution(game, world):
    isl = world.islands[1]
    tile = isl.tiles[0]
    tile.ext[nature.FALLOUT] = 8000
    ctx = ctx_of(game, world)
    Production._spread_pollution(isl, ctx)
    assert tile.pollution == 8 - 0  # 濃さ（点）が汚染にそのまま足される
    turns = game.config.get("military.nuclear.half_life")
    for _ in range(turns - 1):
        nature.decay_fallout(tile, game.config)
    assert 3800 <= tile.ext[nature.FALLOUT] <= 4200
    for _ in range(turns * 8):
        nature.decay_fallout(tile, game.config)
    assert nature.FALLOUT not in tile.ext  # 薄まりきったら消える


def test_using_a_nuclear_weapon_is_a_big_violation_and_a_broken_promise(game, world):
    start_war(game, world)
    attacker, loc = world.islands[1], world.islands[2]
    ctx = ctx_of(game, world)
    nuclear.condemn(world, ctx, attacker, loc)
    [row] = attacker.ext["diplomacy"]["violations"]
    assert row["reason"] == "nuclear_used" and row["weight"] == 5
    assert credit.premium(attacker, ctx.turn, game.config) > 0


def promise(game, world):
    step(
        game, world, {1: [("propose_treaty", {"partner": 2, "clauses": [{"type": "no_nuclear"}]})]}
    )
    from hakoniwa_core.diplomacy import treaties

    t = treaties.treaties(world)[-1]
    step(game, world, {2: [("sign_treaty", {"treaty": t["id"]})]})
    assert t["state"] == treaties.IN_FORCE
    return t


def test_the_no_nuclear_promise_flags_holding_once_and_using(game, world):
    t = promise(game, world)
    assert arms.holds(world.islands[1]) is False
    build(game, world.islands[1], "nuclear_facility", free_tiles(world.islands[1], 1)[0])
    arms.watch(world, game.config, world.turn + 1)
    arms.watch(world, game.config, world.turn + 2)  # 持ち続けても記録は 1 回
    assert [(v["by"], v["reason"]) for v in t["violations"]] == [(1, "nuclear_held")]
    assert world.islands[2].ext.get("diplomacy", {}).get("violations", []) == []
    arms.used(world, game.config, 2, world.turn + 3)
    assert [v["reason"] for v in t["violations"]] == ["nuclear_held", "nuclear_used"]


def test_the_promise_cannot_be_made_in_wartime(game, world):
    start_war(game, world)
    step(
        game, world, {1: [("propose_treaty", {"partner": 2, "clauses": [{"type": "no_nuclear"}]})]}
    )
    failed = [e for e in world.islands[1].log if e.kind == "diplomacy_request_failed"]
    assert failed and failed[-1].args["reason"] == "reason_clause_wartime"


def test_an_interceptor_is_less_likely_to_stop_a_warhead(game, world):
    loc = world.islands[2]
    shield = build(game, loc, "air_defense", free_tiles(loc, 1)[0])
    load(game, shield, "interceptor", 12)
    ctx = ctx_of(game, world)
    rng = random.Random(5)
    chance = game.config.get("military.nuclear.intercept")
    assert chance < game.config.get("military.missiles.intercept")
    hits = sum(
        missiles._intercepted(world, ctx, loc, chance, rng, shield.x, shield.y) for _ in range(12)
    )
    assert 0 < hits < 12


def test_a_bank_in_the_blast_is_halted_not_ruined_and_refugees_stay_out_of_the_rings(game, world):
    start_war(game, world)
    attacker, loc = world.islands[1], world.islands[2]
    cx, cy = loc.size // 2, loc.size // 2
    for tile in loc.tiles:
        tile.population = 100
    bank = build(game, loc, "bank", loc.tile(cx + 1, cy))
    ctx = ctx_of(game, world)
    nuclear.detonate(world, ctx, loc, cx, cy, attacker)
    assert bank.built and bank.ext["halted_until"] > ctx.turn
    cfg = game.config.get("military.nuclear")
    for tile in loc.tiles:
        r = nuclear.ring(tile.x, tile.y, cx, cy)
        if r < len(cfg["dead"]):  # 逃げた人は輪の外へ行き、輪の中に戻らない
            assert tile.population <= round(100 * (1 - cfg["dead"][r]) * (1 - cfg["flee"])) + 1


def test_nuclear_use_is_recorded_once_per_turn_even_when_intercepted(game, world):
    start_war(game, world)
    attacker, loc = world.islands[1], world.islands[2]
    shield = build(game, loc, "air_defense", free_tiles(loc, 1)[0])
    load(game, shield, "interceptor", 12)
    ctx = ctx_of(game, world)
    for n in (1, 2):
        missiles.strikes(world).append(
            {
                "id": n,
                "owner": 1,
                "island": 2,
                "x": shield.x,
                "y": shield.y,
                "count": 2,
                "nuclear": True,
            }
        )
    missiles.run(world, ctx)
    rows = [v for v in attacker.ext["diplomacy"]["violations"] if v["reason"] == "nuclear_used"]
    assert len(rows) == 1


def test_uranium_cannot_be_chosen_at_founding(game):
    assert "uranium_ore" not in game.defs.mining_resources()
    assert "copper_ore" in game.defs.mining_resources()
