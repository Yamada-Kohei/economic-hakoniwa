from __future__ import annotations

import pytest

from hakoniwa_core.config import default_config_path, load_config
from hakoniwa_core.defs import load_defs
from hakoniwa_core.modules import build_registry
from hakoniwa_core.registry import IslandStage, WorldStage
from hakoniwa_core.simulate import world_digest
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.turn import Engine, EngineError


class Drift(IslandStage):
    """乱数で人口を動かすだけの試験用の段。"""

    name = "drift"

    def run_island(self, island, ctx):
        rng = ctx.rng(island.id)
        tile = rng.choice(island.tiles)
        tile.population += rng.randint(0, 10)


class CopyNeighbor(IslandStage):
    """隣の番号の島の、段の前の人口の合計を書く（他の島を読む段）。"""

    name = "copy_neighbor"
    reads_other_islands = True

    def run_island(self, island, ctx):
        other_id = island.id % len(ctx.before.islands) + 1
        other = ctx.other_island(other_id)
        island.ext["neighbor_pop"] = sum(t.population for t in other.tiles)
        island.tiles[0].population += 1  # 自分を書き換えても、他の島からは段の前の値が見える


class Lottery(WorldStage):
    """全島から注文を集めて、一度に当選を決める試験用の段。"""

    name = "lottery"

    def collect(self, island, ctx):
        return sum(t.population for t in island.tiles)

    def resolve(self, requests, ctx):
        winner = ctx.rng().choice(sorted(requests))
        return {i: i == winner for i in requests}

    def apply(self, island, result, ctx):
        island.ext["won"] = island.ext.get("won", 0) + int(result)


def make_engine(stages):
    config = load_config(default_config_path())
    defs = load_defs(config)
    registry = build_registry(defs)
    for stage in (Drift(), CopyNeighbor(), Lottery()):
        registry.add_stage(stage)
    object.__setattr__(config, "stages", tuple(stages))
    return Engine(defs, config, registry), config


def run(seed, n_islands, turns, stages=("execute_orders", "drift", "copy_neighbor", "lottery")):
    engine, config = make_engine(stages)
    world = generate_world(seed, n_islands, config)
    for _ in range(turns):
        engine.run_turn(world)
    return world


def test_empty_stages_300_turns_same_seed_same_result():
    a = run(7, 5, 300, stages=("execute_orders",))
    b = run(7, 5, 300, stages=("execute_orders",))
    assert a.turn == 300
    assert world_digest(a) == world_digest(b)


def test_random_stages_are_deterministic():
    assert world_digest(run(3, 6, 50)) == world_digest(run(3, 6, 50))
    assert world_digest(run(3, 6, 50)) != world_digest(run(4, 6, 50))


def test_island_order_does_not_matter(monkeypatch):
    forward = run(5, 6, 20)
    from hakoniwa_core.state.model import WorldState

    monkeypatch.setattr(WorldState, "island_ids", lambda self: sorted(self.islands, reverse=True))
    backward = run(5, 6, 20)
    assert world_digest(forward) == world_digest(backward)


def test_island_rng_does_not_depend_on_island_count():
    small = run(9, 3, 30, stages=("drift",))
    large = run(9, 6, 30, stages=("drift",))
    # 地形は世界の作り方で変わるが、島1の乱数の結果（人口の動き）は島の数によらない
    assert [t.population for t in small.islands[1].tiles] == [
        t.population for t in large.islands[1].tiles
    ]


def test_other_island_requires_flag():
    engine, config = make_engine(("drift",))
    world = generate_world(1, 2, config)

    class Peek(IslandStage):
        name = "peek"

        def run_island(self, island, ctx):
            ctx.other_island(1)

    engine.stages = [Peek()]
    with pytest.raises(RuntimeError, match="reads_other_islands"):
        engine.run_turn(world)


def test_unknown_stage_in_config():
    with pytest.raises(EngineError, match="nothing_here"):
        make_engine(("nothing_here",))


def test_formula_override(game_dir):
    path = game_dir(extra_yaml='\nformulas:\n  admin_power: "tests.core.test_engine:tiny_power"\n')
    from hakoniwa_core.game import load_game

    game = load_game(path / "game.yaml")
    assert game.engine.formulas["admin_power"] is tiny_power


def tiny_power(island, ctx):
    return 0


def test_pack_module_adds_stage(game_dir):
    from hakoniwa_core.game import load_game
    from tests.conftest import write

    path = game_dir(packs=["example"])
    write(path / "packs/example/pack.yaml", "name: example\nversion: 1\n")
    write(
        path / "packs/example/modules/growth/__init__.py",
        """\
        from hakoniwa_core.registry import IslandStage, Registry


        class Growth(IslandStage):
            name = "growth"

            def run_island(self, island, ctx):
                rng = ctx.rng(island.id)
                rate = ctx.config.get("growth.rate")
                for tile in island.tiles:
                    if tile.population and rng.random() < rate:
                        tile.population += 1


        def register(registry: Registry) -> None:
            registry.add_stage(Growth())
        """,
    )
    config = (path / "game.yaml").read_text(encoding="utf-8")
    config = config.replace("  - execute_orders\n", "  - execute_orders\n  - growth\n")
    (path / "game.yaml").write_text(config + "\ngrowth:\n  rate: 1.0\n", encoding="utf-8")

    game = load_game(path / "game.yaml")
    world = generate_world(1, 1, game.config)
    world.islands[1].tiles[0].population = 5
    game.engine.run_turn(world)
    assert world.islands[1].tiles[0].population == 6
