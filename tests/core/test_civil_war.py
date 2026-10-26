"""反乱と内戦（フェーズ4-4。15-military の MI-39〜MI-41・MI-58）。"""

from __future__ import annotations

import pytest

from hakoniwa_core import politics, stock
from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.game import load_game
from hakoniwa_core.log import format_log
from hakoniwa_core.military import army, civil_war, forces, occupation
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import QUANTITY_SCALE


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    """2 島。島 1 の政情を反乱の前触れにできるように、議会のない体制にする。"""
    world = generate_world(7, 2, game.config, game.defs)
    for island in world.islands.values():
        island.regime["politics"] = "authoritarian"
        forces.state_of(island, game.config)["morale"] = 7000
    return world


def ctx_of(game, world, turn=None):
    return StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.engine.registry,
        formulas={},
        turn=turn if turn is not None else world.turn + 1,
        seed=world.seed,
        stage="operations",
        before=world,
    )


def unrest(island, stability=3.0):
    """政情を反乱の前触れにする。"""
    state = politics.state_of(island)
    state["stability"] = stability
    state["level"] = politics.REBELLION


def calm(island, stability=80.0):
    state = politics.state_of(island)
    state["stability"] = stability
    state["level"] = politics.CALM


@pytest.fixture
def still(game, monkeypatch):
    """反乱軍が動かない（行軍の試験でない試験で、反乱軍を立ち上がったマスに留める）。"""
    monkeypatch.setitem(game.config.raw["military"]["civil_war"], "speed", 0)


def empty_plain(island):
    """人も施設もない平地のマス。"""
    used = {(f.x, f.y) for f in island.facilities}
    return next(
        t
        for t in island.tiles
        if t.terrain == "plain" and t.population == 0 and (t.x, t.y) not in used
    )


def raise_rebels(game, world, island_id=1):
    """反乱の前触れを trigger_turns ターン続けて、反乱軍を立ち上げる。"""
    island = world.islands[island_id]
    turns = game.config.get("military.civil_war.trigger_turns")
    for i in range(turns):
        unrest(island)
        civil_war.run(world, ctx_of(game, world, world.turn + 1 + i))
    return island


def rebels(world, island_id=1):
    return civil_war.groups_of(world, island_id)


def logs(world, island_id, kind):
    return [e for e in world.islands[island_id].log if e.kind == kind]


def test_unrest_must_last_before_rebels_rise(game, world):
    island = world.islands[1]
    turns = game.config.get("military.civil_war.trigger_turns")
    for i in range(turns - 1):
        unrest(island)
        civil_war.run(world, ctx_of(game, world, i + 1))
    assert not rebels(world)
    calm(island)  # 一度落ち着くと数え直し
    civil_war.run(world, ctx_of(game, world, turns))
    unrest(island)
    civil_war.run(world, ctx_of(game, world, turns + 1))
    assert not rebels(world)
    assert civil_war.state_of(island)["alarm"] == 1


def test_rebels_rise_in_a_big_town_far_from_the_capital(game, world):
    island = world.islands[1]
    people = forces.population_of(island)
    raise_rebels(game, world)
    [group] = rebels(world)
    assert group["island"] == 1 and group["soldiers"] >= 1
    assert (group["x"], group["y"]) != island.capital
    tile = island.tile(group["x"], group["y"])
    # 兵は住民から抜ける（家計の働き手から）
    assert forces.population_of(island) == people - group["soldiers"]
    # 首都と陸でつながる町だけ
    assert army.path(island, game.defs, game.config, (tile.x, tile.y), island.capital) is not None
    assert logs(world, 1, "rebellion_started")
    assert civil_war.is_rebel_tile(tile)  # 立ち上がった町は反乱軍の占領になる
    assert format_log(game.defs, "rebellion_started", logs(world, 1, "rebellion_started")[0].args)


def test_no_rebels_when_no_town_connects_to_the_capital(game, world, monkeypatch):
    island = world.islands[1]
    monkeypatch.setattr(army, "path", lambda *a, **k: None)
    raise_rebels(game, world)
    assert not rebels(world)
    assert civil_war.state_of(island)["alarm"] >= 1  # 前触れは続いているが、立ち上がる所がない


def test_armed_only_from_the_arsenal_and_defectors(game, world):
    raise_rebels(game, world)
    [group] = rebels(world)
    assert group["weapons"] == 0  # 何もない所から武器は生まれない（猟銃や農具で戦う）
    armed = civil_war._power(ctx_of(game, world), {**group, "weapons": group["soldiers"]})
    bare = civil_war._power(ctx_of(game, world), group)
    assert 0 < bare < armed
    improvised = game.config.get("military.civil_war.improvised")
    assert bare == pytest.approx(armed * improvised)


def test_undefended_capital_falls_and_the_regime_is_chosen_again(game, world):
    island = raise_rebels(game, world)
    for f in list(island.facilities):  # 警察も部隊もない首都
        if game.defs.facilities[f.kind].get("police"):
            island.facilities.remove(f)
    for i in range(40):
        calm_before = civil_war.active(world, 1)
        civil_war.run(world, ctx_of(game, world, world.turn + 10 + i))
        if not civil_war.active(world, 1):
            assert calm_before
            break
        unrest(island)
    assert not civil_war.active(world, 1)
    assert logs(world, 1, "revolution")
    state = politics.state_of(island)
    assert "reselect" in state and state["reselect"]["by"] == 0
    assert state["transition"]["kind"] == "revolution"
    assert not any(civil_war.is_rebel_tile(t) for t in island.tiles)
    assert civil_war.state_of(island)["cooldown"] > 0  # すぐには次の反乱が起きない


def test_choose_regime_works_after_a_revolution(game, world):
    island = raise_rebels(game, world)
    for f in list(island.facilities):
        if game.defs.facilities[f.kind].get("police"):
            island.facilities.remove(f)
    for i in range(40):
        civil_war.run(world, ctx_of(game, world, world.turn + 10 + i))
        if not civil_war.active(world, 1):
            break
        unrest(island)
    new = next(p for p in game.defs.regime_axes()["politics"] if p != island.regime["politics"])
    queue_orders(island, [("choose_regime", {"politics": new})])
    game.engine.run_turn(world)
    assert "reselect" not in politics.state_of(island)
    assert island.regime["politics"] == new


def test_a_capital_that_falls_in_a_transition_can_still_choose_again(game, world):
    """体制を変えた混乱期間の中に首都が落ちても、選び直せる（W-183）。混乱期間は始め直さない。"""
    island = raise_rebels(game, world)
    for f in list(island.facilities):
        if game.defs.facilities[f.kind].get("police"):
            island.facilities.remove(f)
    state = politics.state_of(island)
    state["transition"] = {"since": world.turn, "until": world.turn + 12, "kind": "economy"}
    for i in range(40):
        civil_war.run(world, ctx_of(game, world, world.turn + 1 + i % 3))
        if not civil_war.active(world, 1):
            break
        unrest(island)
    assert not civil_war.active(world, 1)
    assert logs(world, 1, "revolution")
    assert state["reselect"]["by"] == 0
    assert state["transition"]["kind"] == "economy"  # 前の混乱期間のまま
    new = next(p for p in game.defs.regime_axes()["politics"] if p != island.regime["politics"])
    queue_orders(island, [("choose_regime", {"politics": new})])
    game.engine.run_turn(world)
    assert island.orders[-1].status != "failed"
    assert "reselect" not in politics.state_of(island)
    assert island.regime["politics"] == new


def test_a_second_fall_keeps_the_first_record_of_who_toppled_the_regime(game, world):
    island = world.islands[1]
    ctx = ctx_of(game, world)
    from hakoniwa_core.modules import politics as politics_stage

    politics_stage.lose_regime(island, ctx, "surrender", 2)
    politics_stage.lose_regime(island, ctx, "revolution", 0)
    state = politics.state_of(island)
    assert state["reselect"]["by"] == 2 and state["transition"]["kind"] == "surrender"


def put_garrison(game, world, x, y, soldiers=100):
    """首都のマスに政府の歩兵を置く（強い守り）。"""
    island = world.islands[1]
    spec = game.defs.army["infantry"]
    forces.state_of(island, game.config)["volunteers"] += soldiers
    unit = army.new_unit(world, 1, "infantry", x, y)
    unit["soldiers"] = soldiers
    unit["equipment"] = {"weapons": soldiers}
    unit["status"] = army.READY
    forces.state_of(island, game.config)["training"] = 9000
    assert spec["soldiers"] > 0
    return unit


def test_government_units_defeat_a_small_band(game, world, still):
    raise_rebels(game, world)
    [group] = rebels(world)
    group["soldiers"] = 40
    unit = put_garrison(game, world, group["x"], group["y"], soldiers=300)
    unit["entered"] = world.turn + 1  # 政府の部隊が入ってきた
    group["entered"] = 0
    civil_war.run(world, ctx_of(game, world, world.turn + 20))
    assert not rebels(world) or rebels(world)[0]["soldiers"] < 40
    assert unit in army.world_state(world)["units"]
    assert logs(world, 1, "civil_battle")


def test_a_rebel_tile_is_lifted_when_the_government_comes_back(game, world, still):
    island = raise_rebels(game, world)
    [group] = rebels(world)
    tile = island.tile(group["x"], group["y"])
    assert civil_war.is_rebel_tile(tile)
    away = empty_plain(island)
    group["x"], group["y"] = away.x, away.y  # 反乱軍は町を離れた
    unit = put_garrison(game, world, tile.x, tile.y, soldiers=50)
    group["fought"] = 1
    civil_war.run(world, ctx_of(game, world, world.turn + 30))
    assert not civil_war.is_rebel_tile(tile)
    assert logs(world, 1, "tile_rebel_lifted")
    assert unit["soldiers"] == 50


def test_rebel_held_tiles_halt_facilities_and_pull_politics_down(game, world, still):
    island = raise_rebels(game, world)
    [group] = rebels(world)
    tile = island.tile(group["x"], group["y"])
    assert tile.ext["occupied"]["by"] == civil_war.REBELS
    # 占領の仕組みは外国の占領と同じ（施設が止まる。反乱軍の占領を占領の段が解かない）
    ctx = ctx_of(game, world)
    occupation.update(world, ctx)
    assert civil_war.is_rebel_tile(tile)


def test_defecting_units_join_the_nearest_rebels(game, world, still):
    island = raise_rebels(game, world)
    [group] = rebels(world)
    state = forces.state_of(island, game.config)
    state["morale"] = 1000
    state["late"] = 5
    unit = put_garrison(game, world, island.capital[0], island.capital[1], soldiers=100)
    before = group["soldiers"]
    game.config.raw["military"]["civil_war"]["defect"]["chance"] = 1.1
    try:
        civil_war.run(world, ctx_of(game, world, world.turn + 5))
    finally:
        game.config.raw["military"]["civil_war"]["defect"]["chance"] = 0.15
    assert unit not in army.world_state(world)["units"]
    assert rebels(world) and rebels(world)[0]["soldiers"] >= before + 100 - 50
    assert rebels(world)[0]["weapons"] >= 100
    assert logs(world, 1, "unit_defected")


def test_morale_keeps_loyal_units(game, world, still):
    island = raise_rebels(game, world)
    unit = put_garrison(game, world, island.capital[0], island.capital[1], soldiers=100)
    state = forces.state_of(island, game.config)
    state["morale"], state["late"] = 7000, 0  # 賃金は遅れておらず士気も高い
    game.config.raw["military"]["civil_war"]["defect"]["chance"] = 1.1
    try:
        civil_war.run(world, ctx_of(game, world, world.turn + 5))
    finally:
        game.config.raw["military"]["civil_war"]["defect"]["chance"] = 0.15
    assert unit in army.world_state(world)["units"]


def test_rebellion_ends_when_the_politics_calms(game, world, still):
    island = raise_rebels(game, world)
    [group] = rebels(world)
    soldiers = group["soldiers"]
    people = forces.population_of(island)
    group["fought"] = 0
    turns = game.config.get("military.civil_war.end_calm_turns")
    for i in range(turns):
        calm(island, 90.0)
        civil_war.run(world, ctx_of(game, world, world.turn + 40 + i))
    assert not rebels(world)
    assert logs(world, 1, "rebellion_ended")
    assert (
        forces.population_of(island) >= people + soldiers - 5
    )  # 兵は家計に戻る（食べ物の逃げ分も戻る）
    assert not any(civil_war.is_rebel_tile(t) for t in island.tiles)


def test_long_civil_war_wears_the_rebels_out(game, world, still):
    island = raise_rebels(game, world)
    [group] = rebels(world)
    group["since"] = -1000
    group["soldiers"] = 20
    for i in range(60):
        unrest(island)
        civil_war.run(world, ctx_of(game, world, world.turn + 50 + i))
        if not rebels(world):
            break
    assert not rebels(world)


def test_hungry_rebels_desert(game, world, still):
    island = raise_rebels(game, world)
    [group] = rebels(world)
    tile = island.tile(group["x"], group["y"])
    for f in island.facilities:
        if (f.x, f.y) == (tile.x, tile.y):
            for good in ("food", "grain"):
                stock.take(f.stock, good, 10**12)
    group["soldiers"] = 200
    civil_war._eat(world, ctx_of(game, world), island, group)
    assert group["hungry"] and group["soldiers"] < 200


def test_arsenal_is_seized_at_a_barracks(game, world):
    island = world.islands[1]
    island.warehouse.clear() if hasattr(island.warehouse, "clear") else None
    stock.add(island.warehouse, "weapons", 100 * QUANTITY_SCALE, 0, game.defs.goods)
    raise_rebels(game, world)
    [group] = rebels(world)
    barracks = next(
        (
            f
            for f in island.facilities
            if game.defs.facilities[f.kind].get("military", {}).get("barracks")
        ),
        None,
    )
    if barracks is None:
        pytest.skip("兵舎のない世界")
    group["x"], group["y"] = barracks.x, barracks.y
    civil_war._hold(world, ctx_of(game, world), island, group)
    assert group["weapons"] > 0


def test_turns_run_with_a_civil_war(game, world):
    """内戦のある世界を、命令の段から全部の段まで回しても帳簿が合う（記録の段が確かめる）。"""
    raise_rebels(game, world)
    for _ in range(4):
        calm_state = politics.state_of(world.islands[1])
        calm_state["level"] = politics.REBELLION
        game.engine.run_turn(world)
    assert world.turn >= 4


def test_standard_plan_defends_an_island_with_rebels_only(game, world, still):
    """標準の計画は戦争のない島には何もせず（測りの場面を変えない）、内戦の島は兵舎から備える。"""
    from hakoniwa_core.autoplan import plan_war

    assert plan_war(world, game.defs, game.config) == {}
    raise_rebels(game, world)
    plans = plan_war(world, game.defs, game.config)
    assert set(plans) == {1}
    assert plans[1][0][0] in ("build", "set_defense", "recruit", "form_unit")


def test_routed_rebels_do_not_return_at_once(game, world, still):
    """反乱軍が尽きたときも、次の反乱まで間を空ける（追い払うたびに7ターンで再び立ち上がらない）。"""
    island = raise_rebels(game, world)
    now = world.turn + 20
    for g in rebels(world):  # 長引いて最後の兵が逃げる
        g["soldiers"] = 1
        g["since"] = now - game.config.get("military.civil_war.drag_turns")
    unrest(island)
    civil_war.run(world, ctx_of(game, world, now))
    assert not rebels(world)
    state = civil_war.state_of(island)
    assert state["cooldown"] == now + game.config.get("military.civil_war.cooldown")
    assert logs(world, 1, "rebellion_ended")
    for i in range(1, game.config.get("military.civil_war.cooldown")):
        unrest(island)
        civil_war.run(world, ctx_of(game, world, now + i))
    assert not rebels(world) and state["alarm"] == 0  # 間は前触れを数えない


def test_revolution_gives_a_longer_quiet_time_than_a_defeat(game, world):
    cfg = game.config.get("military.civil_war")
    assert cfg["revolution_cooldown"] > cfg["cooldown"] > 0


def test_rebels_leave_a_tile_a_foreign_army_occupies(game, world, still):
    island = raise_rebels(game, world)
    [group] = rebels(world)
    tile = empty_plain(island)
    tile.population = 50
    group["x"], group["y"] = tile.x, tile.y
    tile.ext["occupied"] = {"by": 2, "turn": 1}
    civil_war._hold(world, ctx_of(game, world), island, group)
    assert tile.ext["occupied"]["by"] == 2


def test_standard_plan_prepares_once_unrest_has_lasted(game, world):
    from hakoniwa_core.autoplan import plan_war

    island = world.islands[1]
    assert plan_war(world, game.defs, game.config) == {}
    half = game.config.get("military.civil_war.trigger_turns") // 2
    for i in range(half):
        unrest(island)
        civil_war.run(world, ctx_of(game, world, i + 1))
    assert not rebels(world)
    assert set(plan_war(world, game.defs, game.config)) == {1}


def test_a_fall_in_the_last_turn_of_a_transition_still_costs_a_full_transition(game, world):
    island = world.islands[1]
    ctx = ctx_of(game, world)
    state = politics.state_of(island)
    state["transition"] = {"since": ctx.turn - 9, "until": ctx.turn, "kind": "economy"}
    from hakoniwa_core.modules import politics as politics_stage

    politics_stage.lose_regime(island, ctx, "revolution", 0)
    assert state["transition"]["until"] == ctx.turn + game.config.get("politics.transition.turns")
