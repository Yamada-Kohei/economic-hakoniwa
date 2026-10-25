"""標準の計画の治安と国防、測りの戦争の場面（段6 A2。15-military の MI-44・MI-59）。"""

from __future__ import annotations

import pytest

from hakoniwa_core import autoplan, measure, stock, warscene
from hakoniwa_core.diplomacy import war
from hakoniwa_core.game import load_game
from hakoniwa_core.military import forces
from hakoniwa_core.state import ORDER_PENDING, Facility, OrderState
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    world = generate_world(7, 4, game.config, game.defs)
    game.engine.run_turn(world)  # 経済のある島にする
    return world


def add(island, kind, built=True):
    island.facilities.append(
        Facility(id=900 + len(island.facilities), kind=kind, x=0, y=0, built=built)
    )


def arm(island):
    """兵舎と武器工場と食料工場が建った島にする。"""
    for kind in ("barracks", "arms_plant", "food_factory"):
        add(island, kind)


def kinds(found):
    return [k for k, _ in found]


# --- 平時の警察署・兵舎・武器工場 ----------------------------------------------------------


def test_police_waits_for_the_security_turn(game, world):
    island = world.islands[1]
    early = list(autoplan._security(island, game.defs, game.config, autoplan.SECURITY_FROM - 1))
    assert early == []
    found = list(autoplan._security(island, game.defs, game.config, autoplan.SECURITY_FROM))
    assert autoplan.POLICE in kinds(found)


def test_one_police_station_is_enough(game, world):
    island = world.islands[1]
    add(island, autoplan.POLICE, built=False)  # 工事中でも数える
    found = list(autoplan._security(island, game.defs, game.config, autoplan.SECURITY_FROM))
    assert autoplan.POLICE not in kinds(found)


def test_barracks_wait_for_a_weapons_source(game, world, monkeypatch):
    """武器の原料が手に入らない島に兵舎を建てない（武器は貿易で買えず、部隊は編成中のままになる）。"""
    island = world.islands[1]
    add(island, autoplan.POLICE)
    monkeypatch.setattr(autoplan, "_feasible", lambda *a, **k: False)
    found = list(autoplan._security(island, game.defs, game.config, autoplan.SECURITY_FROM))
    assert found == []
    monkeypatch.setattr(autoplan, "_feasible", lambda *a, **k: True)
    found = list(autoplan._security(island, game.defs, game.config, autoplan.SECURITY_FROM))
    assert autoplan.BARRACKS in kinds(found)


def test_arms_plant_follows_the_barracks(game, world, monkeypatch):
    island = world.islands[1]
    add(island, autoplan.POLICE)
    monkeypatch.setattr(autoplan, "_feasible", lambda *a, **k: True)
    add(island, autoplan.BARRACKS, built=False)
    found = list(autoplan._security(island, game.defs, game.config, autoplan.SECURITY_FROM))
    assert found == []  # 兵舎ができるまで待つ
    island.facilities[-1].built = True
    found = list(autoplan._security(island, game.defs, game.config, autoplan.SECURITY_FROM))
    assert autoplan.ARMS_PLANT in kinds(found)


def test_security_budget_follows_the_police_staff(game, world):
    island = world.islands[1]
    assert autoplan._security_cost(island, game.defs, game.config) == 0
    order = autoplan._budget_order(island, game.defs, game.config, autoplan.BUDGET_TURNS)
    assert order is None or "security" not in order[1]  # 警察署がなければ予算は足さない
    add(island, autoplan.POLICE)
    cost = autoplan._security_cost(island, game.defs, game.config)
    assert cost > 0
    island.policy.budget["security"] = 0
    order = autoplan._budget_order(island, game.defs, game.config, autoplan.BUDGET_TURNS)
    assert order is not None and order[1]["security"] == cost


def test_warehouse_keeps_the_soldiers_food(game, world):
    island = world.islands[1]
    assert autoplan._military_keep(island, game.config) == {}
    forces.state_of(island, game.config)["reserve"] = {"food": 5}
    assert autoplan._military_keep(island, game.config)["food"] >= 5


# --- 平時の兵と戦時の兵 --------------------------------------------------------------------


def test_peacetime_garrison_needs_barracks_arms_and_food(game, world):
    island = world.islands[1]
    assert autoplan.plan_war(world, game.defs, game.config) == {}
    add(island, "barracks")
    add(island, "arms_plant")
    assert autoplan.plan_war(world, game.defs, game.config) == {}  # 兵の食べ物がない
    add(island, "food_factory")
    assert autoplan.fed(island, game.defs, game.config)
    plans = autoplan.plan_war(world, game.defs, game.config)
    assert set(plans) == {1}  # ほかの島は備えない


def test_garrison_steps_are_budget_then_recruit_then_unit(game, world):
    island = world.islands[1]
    arm(island)
    kind, payload = autoplan.defense_order(island, world, game.defs, game.config, 100)
    assert kind == "set_defense" and payload["annual"] > 0
    island.policy.budget[forces.BUDGET_PERSONNEL] = payload["annual"] * 70 // 100
    island.policy.budget[forces.BUDGET_PROCUREMENT] = payload["annual"] * 30 // 100
    kind, payload = autoplan.defense_order(island, world, game.defs, game.config, 100)
    assert (kind, payload) == ("recruit", {"count": 100})


def test_defense_budget_is_raised_when_the_target_grows(game, world):
    island = world.islands[1]
    arm(island)
    _, small = autoplan.defense_order(island, world, game.defs, game.config, 100)
    island.policy.budget[forces.BUDGET_PERSONNEL] = small["annual"]
    island.policy.budget[forces.BUDGET_PROCUREMENT] = 0
    kind, big = autoplan.defense_order(island, world, game.defs, game.config, 1000)
    assert kind == "set_defense" and big["annual"] > small["annual"]


def test_defense_order_waits_for_a_barracks_under_construction(game, world):
    island = world.islands[1]
    add(island, "barracks", built=False)
    island.orders.append(OrderState(id=1, seq=1, kind="build", payload={}, status=ORDER_PENDING))
    assert autoplan.defense_order(island, world, game.defs, game.config, 100) is None


def test_war_raises_the_target_above_the_garrison(game, world):
    """宣戦された島は、住民の DEFENSE_SHARE まで兵を集める（平時は GARRISON まで）。"""
    island = world.islands[1]
    arm(island)
    island.tiles[0].population = int(10 * autoplan.GARRISON / autoplan.DEFENSE_SHARE)
    island.policy.budget[forces.BUDGET_PERSONNEL] = 10**12
    island.policy.budget[forces.BUDGET_PROCUREMENT] = 10**12
    peace = autoplan.plan_war(world, game.defs, game.config)[1][0]
    assert peace == ("recruit", {"count": autoplan.GARRISON})
    war.declare(world, 2, 1, world.turn + 1)
    wartime = autoplan.plan_war(world, game.defs, game.config)[1][0]
    assert wartime[0] == "recruit" and wartime[1]["count"] > autoplan.GARRISON


# --- 測りの戦争の場面 ----------------------------------------------------------------------


def test_scene_picks_pairs_once_and_keeps_them(game, world):
    world.turn = warscene.WAR_FROM - warscene.WAR_PREP - 1
    for island in world.islands.values():
        add(island, "food_factory")
    first = warscene.pairs(world, game.defs, game.config)
    assert first and len(first) <= warscene.WAR_PAIRS
    attackers = {a for a, _ in first}
    defenders = {d for _, d in first}
    assert not attackers & defenders  # 1つの島が2つの組に入らない
    for island in world.islands.values():
        island.facilities = [f for f in island.facilities if f.kind != "food_factory"]
    assert warscene.pairs(world, game.defs, game.config) == first  # 一度決めたら変えない
    assert world.ext[warscene.KEY] == [list(p) for p in first]


def test_scene_skips_attackers_that_cannot_feed_soldiers(game, world):
    world.turn = warscene.WAR_FROM - warscene.WAR_PREP - 1
    assert warscene.pairs(world, game.defs, game.config) == []  # どの島も兵の食べ物がない


def test_scene_is_silent_before_the_preparation(game, world):
    world.turn = warscene.WAR_FROM - warscene.WAR_PREP - 2
    assert warscene.orders(world, game.defs, game.config) == ({}, set())


def test_scene_grants_weapons_then_declares_war(game, world):
    for island in world.islands.values():
        add(island, "food_factory")
    world.turn = warscene.WAR_FROM - warscene.WAR_PREP - 1
    (attacker, defender), *_ = warscene.pairs(world, game.defs, game.config)
    plans, preparing = warscene.orders(world, game.defs, game.config)
    assert attacker in preparing  # 備えの間、標準の計画の守りは止める
    warehouse = world.islands[attacker].warehouse
    for good in warscene.GRANT:  # 武器と弾薬を国の倉庫に足した（仮）
        assert stock.total(warehouse, good) > 0
    world.turn = warscene.WAR_FROM - 1
    plans, preparing = warscene.orders(world, game.defs, game.config)
    assert preparing == set()
    assert ("declare_war", {"target": defender}) in plans[attacker]


def test_war_scenario_is_measured(game):
    scenario = measure.SCENARIOS["war"]
    assert scenario.war and scenario.orders == "plan"
    world = generate_world(7, 2, game.config, game.defs)
    meter = measure.Measure(world, game.defs, game.config, war=True)
    for _ in range(3):
        measure.queue_scenario_orders(world, scenario, game.defs, game.config)
        game.engine.run_turn(world)
        meter.record(world)
    result = meter.watch.result()
    assert set(result) >= {"events", "pairs", "wars"}
    assert any(name == "war" for name, _, _ in measure.SETS["quick"])
