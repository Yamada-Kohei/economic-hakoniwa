"""兵と国防費と軍の維持（フェーズ4-2 の②。設計書 15-military の MI-07〜MI-14・MI-53）。"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import stock
from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.game import load_game
from hakoniwa_core.military import forces
from hakoniwa_core.modules import military as mil
from tests.core.test_gate import make_world, run_with_plan


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture(scope="module")
def settled(game):
    """経済が回り始めた 1 島（60 ターン）。試験ごとに写しを使う。"""
    world = make_world(game, 3, ["coal", "iron_ore"])
    run_with_plan(game, world, 60)
    return world


@pytest.fixture
def world(settled):
    return copy.deepcopy(settled)


def people(island):
    return sum(t.population for t in island.tiles) + forces.soldiers(island)


def run_orders(game, world, orders):
    """命令を並べ、行政力の都合で先送りされても、全部済むまで（最大 6 ターン）進める。"""
    island = world.islands[1]
    queue_orders(island, list(orders))
    for _ in range(6):
        game.engine.run_turn(world)
        if not any(o.status == "pending" for o in island.orders):
            break
    return island


def play(game, world, turns, orders=()):
    island = world.islands[1]
    if orders:
        run_orders(game, world, orders)
        turns = max(0, turns - 1)
    run_with_plan(game, world, turns)
    return island


def test_recruit_takes_people_from_the_tiles_and_keeps_the_total(game, world):
    island = world.islands[1]
    before = people(island)
    run_orders(game, world, [("recruit", {"count": 50})])
    assert forces.soldiers(island) == 50
    state = island.ext["military"]
    assert state["volunteers"] == 50 and state["conscripts"] == 0
    # 兵は人口の統計に数え、マスの人口からは抜ける（MI-53）。1 ターンの増減の分だけ動く
    assert island.stats["population"] >= sum(t.population for t in island.tiles) + 50
    assert abs(people(island) - before) < 0.02 * before


def test_take_people_is_proportional_and_exact(world):
    island = world.islands[1]
    populated = [t for t in island.tiles if t.population > 5]
    before = sum(t.population for t in island.tiles)
    got = forces.take_people(island, 777, keep=1)
    assert got == 777
    assert sum(t.population for t in island.tiles) == before - 777
    assert all(t.population >= 1 for t in populated)
    assert forces.return_people(island, 777) == 777
    assert sum(t.population for t in island.tiles) == before


def test_soldiers_are_paid_from_the_defense_budget_and_fed_from_the_warehouse(game, world):
    island = world.islands[1]
    stock.add(island.warehouse, "ammunition", 5000, world.turn, game.defs.goods)  # 訓練の弾薬
    play(game, world, 1, [("set_defense", {"annual": 3_000_000}), ("recruit", {"count": 50})])
    play(game, world, 3)
    state = island.ext["military"]
    assert island.stats["military.wages"] > 0 and island.stats["military.wages_unpaid"] == 0
    assert state["late"] == 0 and not state["hungry"]
    assert state["training"] > 0 and state["morale"] >= 6500
    assert stock.total(island.warehouse, "food") > 0
    # 入ったターンは家で食べるので、飢えない
    assert "military.starved" not in island.stats


def test_set_defense_splits_the_budget_and_wage(game, world):
    island = world.islands[1]
    run_orders(
        game,
        world,
        [("set_defense", {"annual": 1_000_000, "personnel_share": 70, "volunteer_wage": 150})],
    )
    assert island.policy.budget[forces.BUDGET_PERSONNEL] == 700_000
    assert island.policy.budget[forces.BUDGET_PROCUREMENT] == 300_000
    assert island.ext["military"]["volunteer_wage"] == 15000


def test_unpaid_wages_lower_morale_and_soldiers_desert_back_to_households(game, world):
    island = world.islands[1]
    play(game, world, 1, [("recruit", {"count": 80})])  # 国防費なし
    total = people(island)
    for _ in range(8):
        play(game, world, 1)
    state = island.ext["military"]
    assert island.stats["military.wages_unpaid"] > 0 and state["late"] >= 5
    assert state["morale"] < 5000
    assert forces.soldiers(island) < 80  # 士気が崩れた兵は家計に戻る
    assert abs(people(island) - total) < 0.03 * total  # 人は消えず、飢えた分だけ少ない
    assert any(e.kind == "military_wages_late" for e in island.log) or state["late"] > 0


def test_a_small_budget_leaves_wages_late_or_food_short(game, world):
    island = world.islands[1]
    play(game, world, 1, [("set_defense", {"annual": 700_000}), ("recruit", {"count": 60})])
    short = 0
    for _ in range(6):
        play(game, world, 1)
        state = island.ext["military"]
        short += state["hungry"] or state["late"] > 0
    assert short > 0 and state["morale"] < 7000


def test_volunteers_come_from_the_unemployed_unless_the_wage_is_high(game, world):
    island = world.islands[1]
    config = game.config
    state = forces.state_of(island, config)
    state["volunteer_wage"] = 5000  # 平均賃金の半分
    low = forces.volunteer_limit(island, config, unemployed=10, wage_ratio=state["volunteer_wage"])
    assert low == 10
    high = forces.volunteer_limit(island, config, unemployed=10, wage_ratio=10000)
    assert high > low


def test_conscription_is_capped_by_the_population(game, world):
    island = world.islands[1]
    population = forces.population_of(island)
    run_orders(game, world, [("conscript", {"count": 10**9})])
    state = island.ext["military"]
    cap = int(population * game.config.get("military.conscript_share"))
    assert 0 < state["conscripts"] <= cap * 1.05 and state["volunteers"] == 0
    assert any(e.kind == "conscripted" for e in island.log)


def test_discharge_sends_conscripts_home_first(game, world):
    island = world.islands[1]
    forces.state_of(island, game.config)
    forces.recruit(island, game.config, 20, 100)
    forces.conscript(island, game.config, 30)
    home = sum(t.population for t in island.tiles)
    assert forces.discharge(island, 35) == 35
    state = island.ext["military"]
    assert state["conscripts"] == 0 and state["volunteers"] == 15
    assert sum(t.population for t in island.tiles) == home + 35


def test_the_army_cap_follows_the_population(game, world):
    island = world.islands[1]
    cap = int(people(island) * game.config.get("military.max_share"))
    forces.conscript(island, game.config, 10**9)
    forces.recruit(island, game.config, 10**9, 10**9)
    assert forces.soldiers(island) <= cap


def test_budget_estimate_reserves_wages_and_food(game, world):
    island = world.islands[1]
    assert mil.planned(island, game.config) == {}
    forces.recruit(island, game.config, 40, 100)
    estimate = mil.planned(island, game.config)
    assert estimate[forces.BUDGET_PERSONNEL] >= mil.wage_bill(island) > 0


def test_orders_without_soldiers_or_economy_are_refused(game, world):
    from hakoniwa_core.registry import OrderFailed

    handler = game.registry.orders["discharge"]
    with pytest.raises(OrderFailed):
        handler.check(world.islands[1], {"count": 1}, game.defs)


def test_conscripts_lower_the_stability_target(game, world):
    from types import SimpleNamespace

    from hakoniwa_core.modules.politics import events_bonus

    island = world.islands[1]
    ctx = SimpleNamespace(config=game.config, defs=game.defs, turn=world.turn)
    assert "conscription" not in events_bonus(island, ctx)
    forces.conscript(island, game.config, 500)
    penalty = events_bonus(island, ctx)["conscription"]
    assert -game.config.get("military.conscription")["max"] <= penalty < 0


def test_the_intake_limits_are_per_turn_not_per_order(game, world):
    island = world.islands[1]
    config = game.config
    cap = int(forces.population_of(island) * config.get("military.conscript_share"))
    first = forces.conscript(island, config, 10**9)
    second = forces.conscript(island, config, 10**9)
    assert first <= cap and second <= max(0, cap - first)
    assert forces.recruit(island, config, 10**9, 10) == 0 or forces.soldiers(island) <= cap + 10


def test_nobody_leaves_the_army_when_no_tile_can_take_them_back(game, world):
    island = world.islands[1]
    forces.recruit(island, game.config, 40, 100)
    for t in island.tiles:
        t.population = 0
    before = forces.soldiers(island)
    assert forces.discharge(island, 10) == 0
    assert forces.soldiers(island) == before


def test_set_defense_changes_only_what_it_names(game, world):
    island = world.islands[1]
    run_orders(game, world, [("set_defense", {"annual": 1_000_000, "personnel_share": 60})])
    run_orders(game, world, [("set_defense", {"annual": 2_000_000})])
    assert island.policy.budget[forces.BUDGET_PERSONNEL] == 1_200_000
    run_orders(game, world, [("set_defense", {"personnel_share": 50})])
    assert island.policy.budget[forces.BUDGET_PERSONNEL] == 1_000_000
    assert island.policy.budget[forces.BUDGET_PROCUREMENT] == 1_000_000
