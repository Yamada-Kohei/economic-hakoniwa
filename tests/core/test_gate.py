"""フェーズ1の関門: 標準の計画を与えた島を 300 ターン回す。

- 鉄鉱石から鋼鉄、鋼鉄から機械、機械で新しい工場という生産の連鎖が回る
- 倉庫と施設の在庫がマイナス（や 0 の組）にならない
- 人口が爆発も消滅もしない
"""

from __future__ import annotations

import random

import pytest

from hakoniwa_core.autoplan import plan_turn, queue_orders
from hakoniwa_core.founding import found_island
from hakoniwa_core.game import load_game
from hakoniwa_core.simulate import world_digest
from hakoniwa_core.state import WorldState
from hakoniwa_core.state.generate import generate_land, random_plan
from hakoniwa_core.units import from_milli

TURNS = 300


@pytest.fixture(scope="module")
def game():
    return load_game()


def make_world(game, seed: int, resources: list[str]) -> WorldState:
    config = game.config
    rng = random.Random(seed)
    land = generate_land(
        rng, config.get("island.size"), config.get("island.land_min"), config.get("island.land_max")
    )
    plan = random_plan(rng, "試験島", config, game.defs, land)
    plan.resources = resources
    island = found_island(game.defs, config, plan, 1, 0, 0)
    return WorldState(turn=0, seed=seed, islands={1: island})


def run_with_plan(game, world: WorldState, turns: int, each_turn=None) -> None:
    island = world.islands[1]
    for _ in range(turns):
        queue_orders(island, plan_turn(island, game.defs, game.config, world.turn + 1))
        game.engine.run_turn(world)
        if each_turn is not None:
            each_turn(island)


@pytest.mark.parametrize("seed", [1, 2])
def test_supply_chain_runs_for_300_turns(game, seed):
    world = make_world(game, seed, ["coal", "iron_ore"])
    island = world.islands[1]
    machinery_at_start = from_milli(sum(q for _, q in island.warehouse.get("machinery", [])))
    start_population = sum(t.population for t in island.tiles)
    produced: dict[str, float] = {}
    machinery_built_in = 0.0
    populations = [start_population]
    bad_stock = []

    def check(island):
        for key, value in island.stats.items():
            if key.startswith("production."):
                good = key.removeprefix("production.")
                produced[good] = produced.get(good, 0) + value
        nonlocal machinery_built_in
        for entry in island.log:
            if entry.kind == "construction_done":
                materials = game.defs.facilities[entry.args["facility"]]["construction"]
                machinery_built_in += from_milli(materials.get("materials", {}).get("machinery", 0))
        for good, batches in island.warehouse.items():
            bad_stock.extend((good, q) for _, q in batches if q <= 0)
        for f in island.facilities:
            bad_stock.extend((f.kind, g, q) for g, q in f.stock.items() if q <= 0)
        populations.append(island.stats["population"])

    run_with_plan(game, world, TURNS, check)

    assert world.turn == TURNS
    # 生産の連鎖: 鉄鉱石 → 鋼鉄 → 機械 → 機械を使った新しい工場
    assert produced.get("iron_ore", 0) > 0
    assert produced.get("steel", 0) > 0
    assert produced.get("machinery", 0) > 0
    assert machinery_built_in > machinery_at_start, "島で作った機械で工場が建っていない"
    # 在庫はマイナスにならない（0 になった組は消える）
    assert bad_stock == []
    # 人口は爆発も消滅もしない
    assert min(populations) >= start_population * 0.5
    assert populations[-1] <= start_population * 15
    jumps = [abs(b - a) / a for a, b in zip(populations, populations[1:], strict=False)]
    assert max(jumps) < 0.05
    # 食料は足りている
    assert island.stats["food.ratio"] == 1.0


def test_same_seed_same_result(game):
    a = make_world(game, 3, ["coal", "iron_ore"])
    b = make_world(game, 3, ["coal", "iron_ore"])
    run_with_plan(game, a, 60)
    run_with_plan(game, b, 60)
    assert world_digest(a) == world_digest(b)
