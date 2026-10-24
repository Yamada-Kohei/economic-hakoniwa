"""標準の計画の予算の見直し: 返す力の比が高いと、公共事業費に加えて教育費も減らす（W-63）。"""

from __future__ import annotations

import pytest

from hakoniwa_core import autoplan
from hakoniwa_core.game import load_game
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


def _island(game):
    world = generate_world(3, 1, game.config, game.defs)
    game.engine.run_turn(world)  # 経済のある島にする
    island = next(iter(world.islands.values()))
    island.policy.budget["construction"] = 0
    island.policy.budget["education"] = 0
    return island


def _education(game, island, burden: float) -> int:
    island.stats["bonds.burden"] = burden
    order = autoplan._budget_order(island, game.defs, game.config, autoplan.BUDGET_TURNS)
    assert order is not None
    return order[1]["education"]


def test_education_is_cut_when_the_burden_is_over_the_limit(game):
    island = _island(game)
    full = _education(game, island, 0.0)
    assert full == autoplan._education_cost(island, game.defs, game.config)
    over = _education(game, island, autoplan.FISCAL_LIMIT + 0.1)
    # 0.1 超えるごとに 20% 減らす（公共事業費と同じ率）
    assert over == pytest.approx(full * 0.8, abs=1)
    assert _education(game, island, autoplan.FISCAL_LIMIT) == full  # 限度ちょうどでは減らさない


def test_education_cut_stops_at_the_floor(game):
    island = _island(game)
    full = _education(game, island, 0.0)
    deep = _education(game, island, 5.0)
    assert deep == int(full * autoplan.EDUCATION_FLOOR)
