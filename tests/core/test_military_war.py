"""陸の戦闘・揚陸・補給・占領・降伏・捕虜（フェーズ4-3 の①。15-military の
MI-24〜MI-30・MI-36・MI-49）。"""

from __future__ import annotations

import pytest

from hakoniwa_core import monuments, politics, stock
from hakoniwa_core.autoplan import plan_war, queue_orders
from hakoniwa_core.diplomacy import treaties, war
from hakoniwa_core.game import load_game
from hakoniwa_core.log import format_log
from hakoniwa_core.military import army, battle, forces, occupation, supply
from hakoniwa_core.modules import politics as politics_stage
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.state.model import Facility
from hakoniwa_core.units import BASIS, QUANTITY_SCALE


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    """2 島（島 2 が島 1 に攻められる）。議会のない体制にして、命令がそのまま通るようにする。"""
    world = generate_world(7, 2, game.config, game.defs)
    for island in world.islands.values():
        island.regime["politics"] = "authoritarian"
        forces.state_of(island, game.config)["morale"] = 7000
        forces.state_of(island, game.config)["training"] = 6000
    return world


def step(game, world, orders=None):
    for island_id, items in (orders or {}).items():
        queue_orders(world.islands[island_id], items)
    game.engine.run_turn(world)


def start_war(game, world):
    """島 1 が島 2 に宣戦して、戦争が始まるターンの直前まで進める。"""
    step(game, world, {1: [("declare_war", {"target": 2})]})
    assert war.status(world, 1, 2, world.turn + 1) == war.WAR


def put_unit(game, world, owner, kind, x, y, at=None, soldiers=None):
    """そろった部隊を、島の兵の数に合わせて置く。"""
    island = world.islands[owner]
    spec = game.defs.army[kind]
    n = soldiers or spec["soldiers"]
    forces.state_of(island, game.config)["volunteers"] += n
    unit = army.new_unit(world, owner, kind, x, y)
    unit["soldiers"] = n
    unit["equipment"] = {
        g: q // QUANTITY_SCALE * n // spec["soldiers"] for g, q in spec["equipment"].items()
    }
    unit["status"] = army.READY
    if at is not None:
        unit["at"] = at
        unit["stores"] = {g: q * 4 for g, q in supply.per_turn(_ctx(game, world), unit).items()}
        unit["stores"] = {g: max(q, 50_000) for g, q in unit["stores"].items()}
    return unit


def put_transport(world, owner, x, y, ships=1, goods=None):
    """そろった輸送船団（乗員は島の兵に数える）。"""
    forces.state_of(world.islands[owner])["volunteers"] += 10 * ships
    fleet = army.new_unit(world, owner, "transport", x, y, ships=ships)
    fleet["status"] = army.READY
    fleet["soldiers"], fleet["equipment"] = 10 * ships, {"transport_ship": ships}
    if goods:
        fleet["goods"] = goods
    return fleet


def _ctx(game, world):
    from hakoniwa_core.turn.context import StageContext

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


def plain_tiles(island, count, free_of=()):
    """人も施設もない平地のマス。"""
    used = {(f.x, f.y) for f in island.facilities} | set(free_of)
    out = [
        t
        for t in island.tiles
        if t.terrain == "plain" and t.population == 0 and (t.x, t.y) not in used
    ]
    return out[:count]


def feed(game, world):
    """どちらの国も、兵が飢えないように国の倉庫を満たす。"""
    for island in world.islands.values():
        for good in ("food", "ammunition", "fuel"):
            stock.add(island.warehouse, good, 100 * QUANTITY_SCALE, world.turn, game.defs.goods)
        island.policy.budget[forces.BUDGET_PERSONNEL] = 50_000_000
        island.policy.budget[forces.BUDGET_PROCUREMENT] = 50_000_000


def logs(world, island_id, kind):
    return [e for e in world.islands[island_id].log if e.kind == kind]


def test_no_battle_unless_at_war(game, world):
    a, b = plain_tiles(world.islands[2], 2)
    attacker = put_unit(game, world, 1, "infantry", a.x, a.y, at=2)
    defender = put_unit(game, world, 2, "infantry", a.x, a.y)
    attacker["entered"] = 99
    feed(game, world)
    step(game, world)
    assert attacker["soldiers"] == 100 and defender["soldiers"] == 100
    assert not logs(world, 1, "battle")


def test_equal_armies_lose_about_the_same_and_the_attacker_pays_for_terrain(game, world):
    start_war(game, world)
    isl2 = world.islands[2]
    tile = next(t for t in isl2.tiles if t.terrain == "mountain" and t.population == 0)
    attacker = put_unit(game, world, 1, "infantry", tile.x, tile.y, at=2)
    defender = put_unit(game, world, 2, "infantry", tile.x, tile.y)
    attacker["entered"] = world.turn + 1  # 動いて入った
    feed(game, world)
    step(game, world)
    lost_attack = 100 - attacker["soldiers"]
    lost_defend = 100 - defender["soldiers"]
    assert lost_attack > lost_defend > 0  # 山を守る側は強い
    assert attacker["role"] == "attack" and defender["role"] == "defend"
    # 戦死と負傷（1 : 3）、戦死は歴史と殺した数に残る
    h2 = monuments.history(isl2)
    h1 = monuments.history(world.islands[1])
    assert h2["dead"]["war"] > 0 and h1["dead"]["war"] > 0
    assert h1["kills"] == h2["dead"]["war"] - 0 or h1["kills"] > 0
    [battle_log] = logs(world, 1, "battle")
    assert "戦いました" in format_log(game.defs, "battle", battle_log.args)
    # 負傷した兵は数ターン後に戻る
    state2 = forces.state_of(isl2)
    assert state2["wounded"] and forces.soldiers(isl2) == defender["soldiers"]


def test_a_stronger_army_breaks_the_other_and_it_retreats_or_surrenders(game, world):
    start_war(game, world)
    isl2 = world.islands[2]
    tile = plain_tiles(isl2, 1)[0]
    attacker = put_unit(game, world, 1, "armor", tile.x, tile.y, at=2)
    weak = put_unit(game, world, 2, "infantry", tile.x, tile.y, soldiers=20)
    attacker["entered"] = world.turn + 1
    feed(game, world)
    step(game, world)
    assert weak["soldiers"] < 20
    for _ in range(4):
        feed(game, world)
        step(game, world)
        if weak not in world.ext["military"]["units"]:
            break
    # 崩れた弱い部隊は、隣へ退いたか、退く所がなく降伏して捕虜になった
    retreated = (weak["x"], weak["y"]) != (tile.x, tile.y)
    surrendered = weak not in world.ext["military"]["units"]
    assert retreated or surrendered


def test_cornered_unit_surrenders_and_its_men_become_prisoners(game, world):
    start_war(game, world)
    isl2 = world.islands[2]
    tile = plain_tiles(isl2, 1)[0]
    attacker = put_unit(game, world, 1, "armor", tile.x, tile.y, at=2)
    weak = put_unit(game, world, 2, "infantry", tile.x, tile.y, soldiers=10)
    attacker["entered"] = world.turn + 1
    ctx = _ctx(game, world)
    # 退く所がない: 隣のマスがすべて海・敵のいるマスなら降伏する
    battle._surrender(world, ctx, weak, 1)
    assert weak not in world.ext["military"]["units"]
    captor = world.islands[1]
    assert occupation.held(captor) == 10
    assert logs(world, 1, "prisoners_taken") and logs(world, 2, "unit_surrendered")
    # 捕虜は出身の島の人口に数える
    occupation.release(world, ctx)
    assert forces.state_of(isl2)["captive"] == 10


def test_prisoners_go_home_when_peace_comes_or_after_the_set_turns(game, world):
    start_war(game, world)
    captor, home = world.islands[1], world.islands[2]
    forces.state_of(captor)["prisoners"] = [[world.turn, 2, 30]]
    ctx = _ctx(game, world)
    before = sum(t.population for t in home.tiles)
    occupation.release(world, ctx)  # 戦争中で期限前: そのまま
    assert occupation.held(captor) == 30
    ctx_late = _ctx(game, world)
    ctx_late.turn = world.turn + game.config.get("military.occupation.prisoner_turns")
    occupation.release(world, ctx_late)
    assert occupation.held(captor) == 0
    assert sum(t.population for t in home.tiles) == before + 30
    assert logs(world, 2, "prisoners_returned")


def test_an_undefended_tile_is_occupied_and_its_facility_stops(game, world):
    start_war(game, world)
    isl2 = world.islands[2]
    farm = next(
        f
        for f in isl2.facilities
        if f.built and f.kind != "house" and not (f.x, f.y) == isl2.capital
    )
    tile = isl2.tile(farm.x, farm.y)
    stock.add(farm.stock, "food", 5 * QUANTITY_SCALE, world.turn, game.defs.goods)
    unit = put_unit(game, world, 1, "infantry", farm.x, farm.y, at=2)
    unit["stores"] = {}
    feed(game, world)
    step(game, world)
    held = tile.ext.get(occupation.OCCUPIED)
    assert held and held["by"] == 1
    assert farm.ext.get("occupied") and farm.ext["halted_until"] >= world.turn + 1
    assert logs(world, 2, "tile_occupied") and logs(world, 1, "tile_occupying")
    # 取り立て: 占領した国の部隊が、在庫を補給に取り立てる（持ち帰らず、その場で使う）
    assert unit["stores"].get("food", 0) > 0 or stock.total(farm.stock, "food") < 5 * QUANTITY_SCALE
    # 部隊が去ると占領は解ける
    army.remove(world, unit)
    feed(game, world)
    step(game, world)
    assert not tile.ext.get(occupation.OCCUPIED) and not farm.ext.get("occupied")
    assert logs(world, 2, "occupation_lifted")


def test_taking_the_capital_makes_the_country_surrender_once(game, world):
    start_war(game, world)
    isl2 = world.islands[2]
    cx, cy = isl2.capital
    # 警察の守りは、首都の戦闘で押し切られる前提にして、警察を置かずに試す
    for f in isl2.facilities:
        if game.defs.facilities[f.kind].get("police"):
            f.workers = 0
    unit = put_unit(game, world, 1, "armor", cx, cy, at=2)
    unit["entered"] = world.turn + 1
    feed(game, world)
    step(game, world)
    w = war.open_war(world, 1, 2)
    assert w["state"] == war.CEASEFIRE
    deed = treaties.find(world, w["ceasefire"])  # 降伏文書は両国の署名した停戦の条約（W-112）
    assert deed["state"] == treaties.IN_FORCE and deed["surrender"] == 2
    assert treaties.kinds(deed) == ["ceasefire"]
    assert politics.in_transition(isl2, world.turn)
    reselect = politics.state_of(isl2)["reselect"]
    assert reselect["by"] == 1
    assert logs(world, 2, "surrendered") and logs(world, 1, "enemy_surrendered")
    # もう 1 回は降伏しない（占領は続く）
    feed(game, world)
    step(game, world)
    assert not logs(
        world, 2, "surrendered"
    )  # ターンのログは毎ターン入れ替わる。もう降伏の行は出ない
    # 体制を選び直す（混乱期間は始め直さず、決めた軸だけ変わる）
    until = politics.state_of(isl2)["transition"]["until"]
    step(game, world, {2: [("choose_regime", {"politics": "totalitarian"})]})
    assert isl2.regime["politics"] == "totalitarian"
    assert "reselect" not in politics.state_of(isl2)
    assert politics.state_of(isl2)["transition"]["until"] == until


def test_a_surrender_in_a_transition_can_still_choose_again(game, world):
    """体制を変えた混乱期間の中に首都を取られても、選び直せる（W-183）。"""
    start_war(game, world)
    isl2 = world.islands[2]
    state = politics.state_of(isl2)
    state["transition"] = {"since": world.turn, "until": world.turn + 12, "kind": "economy"}
    cx, cy = isl2.capital
    for f in isl2.facilities:
        if game.defs.facilities[f.kind].get("police"):
            f.workers = 0
    unit = put_unit(game, world, 1, "armor", cx, cy, at=2)
    unit["entered"] = world.turn + 1
    feed(game, world)
    step(game, world)
    assert treaties.find(world, war.open_war(world, 1, 2)["ceasefire"])["surrender"] == 2
    assert state["reselect"]["by"] == 1 and state["transition"]["kind"] == "economy"
    step(game, world, {2: [("choose_regime", {"politics": "totalitarian"})]})
    assert isl2.regime["politics"] == "totalitarian"


def test_a_surrender_ceasefire_can_be_denounced_back_to_war(game, world):
    """降伏文書はふつうの停戦と同じく、予告つきで破棄すると戦争に戻る（W-112）。"""
    start_war(game, world)
    step(game, world)
    isl2 = world.islands[2]
    occupation.surrender(world, _ctx(game, world), isl2, world.islands[1])
    w = war.open_war(world, 1, 2)
    deed = treaties.find(world, w["ceasefire"])
    assert w["state"] == war.CEASEFIRE and deed["surrender"] == 2
    assert treaties.denounce(world, game.config, isl2, deed, world.turn) is None
    treaties.upkeep(world, game.config, deed["notice"]["until"])
    assert deed["state"] == treaties.ENDED and w["state"] == war.WAR


def test_an_old_surrender_ceasefire_becomes_a_deed_that_can_be_denounced(game, world):
    """W-112 の前のセーブの降伏の停戦（ceasefire が -1）は、次の外交の見張りで降伏文書に移る。"""
    start_war(game, world)
    step(game, world)
    w = war.open_war(world, 1, 2)
    w["state"], w["ceasefire"] = war.CEASEFIRE, occupation.SURRENDER
    treaties.upkeep(world, game.config, world.turn + 1)
    deed = treaties.find(world, w["ceasefire"])
    assert w["state"] == war.CEASEFIRE and deed["state"] == treaties.IN_FORCE
    assert treaties.denounce(world, game.config, world.islands[1], deed, world.turn) is None


def test_a_reselect_left_after_the_transition_closes(game, world):
    """混乱期間がもう終わっている前のセーブの選び直し待ちも、次のターンに閉じる（W-112）。"""
    isl2 = world.islands[2]
    state = politics.state_of(isl2)
    state.pop("transition", None)
    state["reselect"] = {"since": world.turn - 30, "by": 1}
    step(game, world)
    assert "reselect" not in state and logs(world, 2, "regime_kept")


def test_an_unchosen_regime_stays_when_the_transition_ends(game, world):
    """体制を選び直さないまま混乱期間が終わると、今の体制のまま固まり、選び直し待ちは閉じる（W-112）。
    持ち主のいない島や AI の島に選び直し待ちが残り続けない。"""
    isl2 = world.islands[2]
    state = politics.state_of(isl2)
    state["transition"] = {"since": world.turn - 10, "until": world.turn + 1, "kind": "surrender"}
    state["reselect"] = {"since": world.turn - 10, "by": 1}
    before = dict(isl2.regime)
    step(game, world)
    assert "reselect" not in state and "transition" not in state
    assert isl2.regime == before
    assert logs(world, 2, "regime_kept")


def test_captor_without_soldiers_still_feeds_its_prisoners(game, world):
    """兵が 0 の国も、捕らえている捕虜には国の倉庫の食べ物を食べさせ、足りなければ捕虜が亡くなる
    （W-113。前は兵が 0 だと段が早く戻り、捕虜が何も食べなかった）。"""
    captor = world.islands[1]
    forces.state_of(captor, game.config)["prisoners"] = [[world.turn, 2, 1000]]
    assert forces.soldiers(captor) == 0
    stock.add(captor.warehouse, "food", 10 * QUANTITY_SCALE, world.turn, game.defs.goods)
    before = stock.total(captor.warehouse, "food")
    step(game, world)
    assert stock.total(captor.warehouse, "food") < before  # 食べた
    assert occupation.held(captor) == 1000
    # 食べ物がなく買えもしなければ、病気と飢えで亡くなる
    captor.warehouse.clear()
    captor.policy.budget[forces.BUDGET_PERSONNEL] = 0
    step(game, world)
    assert 0 < occupation.held(captor) < 1000
    assert logs(world, 1, "prisoners_starved")


def test_people_return_to_the_capital_of_an_emptied_island(game, world):
    """住民のいなくなった島へ帰る兵・捕虜は首都のマスに戻る（W-113。前は消えていた）。"""
    home = world.islands[2]
    for t in home.tiles:
        t.population = 0
    assert forces.return_people(home, 40) == 40
    cx, cy = home.capital
    assert home.tile(cx, cy).population == 40


def test_prisoners_from_a_vanished_country_stay_with_the_captor(game, world):
    captor = world.islands[1]
    forces.state_of(captor, game.config)["prisoners"] = [[world.turn, 99, 30]]
    before = sum(t.population for t in captor.tiles)
    occupation.release(world, _ctx(game, world))
    assert occupation.held(captor) == 0
    assert sum(t.population for t in captor.tiles) == before + 30


def test_occupied_facility_does_not_sell_in_the_home_market(game, world):
    """占領されたマスの施設の在庫は、元の国の市場に売りに出ない（MI-36、W-112）。"""
    from hakoniwa_core.modules.market import Market

    isl2 = world.islands[2]
    farm = next(
        f
        for f in isl2.facilities
        if f.built and fac_outputs(game, f) and not (f.x, f.y) == isl2.capital
    )
    good = sorted(fac_outputs(game, farm))[0]
    stock.add(farm.stock, good, 5 * QUANTITY_SCALE, world.turn, game.defs.goods)
    ctx = _ctx(game, world)
    books: dict = {}
    Market()._facility_orders(isl2, ctx, books, {})
    assert any(p.facility is farm for _, p in books[good].sells)
    farm.ext["occupied"] = True
    books = {}
    Market()._facility_orders(isl2, ctx, books, {})
    assert good not in books or not any(p.facility is farm for _, p in books[good].sells)


def fac_outputs(game, f):
    from hakoniwa_core import facilities as fac

    power = game.config.get("power.good")
    return [g for g in fac.recipe_of(game.defs, f)["outputs"] if g != power]


def test_choose_regime_only_after_a_surrender(game, world):
    step(game, world, {2: [("choose_regime", {"politics": "totalitarian"})]})
    assert world.islands[2].orders[-1].status == "failed"
    assert world.islands[2].regime["politics"] == "authoritarian"


def test_war_weariness_lowers_stability_with_the_dead(game, world):
    isl = world.islands[1]
    ctx = _ctx(game, world)
    assert "war_weariness" not in politics_stage.war_bonus(isl, ctx)
    monuments.add_dead(isl, monuments.WAR_DEAD, 200)
    small = politics_stage.war_bonus(isl, ctx)["war_weariness"]
    monuments.add_dead(isl, monuments.WAR_DEAD, 2000)
    big = politics_stage.war_bonus(isl, ctx)["war_weariness"]
    assert 0 > small > big  # 戦死者が増えるほど下がり、増え方はだんだん緩やか
    monuments.history(isl)["war"] = {"turn": ctx.turn, "country": "x", "id": 1}
    assert politics_stage.war_bonus(isl, ctx)["war_rally"] > 0


def test_landing_needs_war_a_shore_and_a_weight_limit(game, world):
    isl1, isl2 = world.islands[1], world.islands[2]
    units = [put_unit(game, world, 1, "armor" if i else "infantry", 1, 1) for i in range(4)]
    transport = put_transport(world, 1, isl2.wx, isl2.wy, ships=1)
    shore = next(
        t
        for t in isl2.tiles
        if t.terrain == "plain"
        and any(
            not (0 <= t.x + dx < isl2.size and 0 <= t.y + dy < isl2.size)
            or not game.defs.terrain[isl2.tile(t.x + dx, t.y + dy).terrain]["land"]
            for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0))
        )
    )
    for u in units:
        u["aboard"] = transport["id"]
        transport["cargo"].append(u["id"])

    def unload(u, island_id=2, x=shore.x, y=shore.y):
        step(
            game,
            world,
            {
                1: [
                    (
                        "unload_unit",
                        {
                            "unit": u["id"],
                            "fleet": transport["id"],
                            "x": x,
                            "y": y,
                            "island": island_id,
                        },
                    )
                ]
            },
        )
        return [e.args["reason"] for e in isl1.log if e.kind == "unit_order_failed"]

    feed(game, world)
    assert unload(units[0]) == ["reason_not_at_war"]  # 平時の相手の島には降ろせない
    start_war(game, world)
    isl1.log.clear()
    inland = next(
        t
        for t in isl2.tiles
        if t.terrain == "plain"
        and (t.x, t.y) != (shore.x, shore.y)
        and not any(
            not (0 <= t.x + dx < isl2.size and 0 <= t.y + dy < isl2.size)
            for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0))
        )
        and all(
            game.defs.terrain[isl2.tile(t.x + dx, t.y + dy).terrain]["land"]
            for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0))
        )
    )
    assert unload(units[0], x=inland.x, y=inland.y) == ["reason_not_landing_site"]
    isl1.log.clear()
    assert unload(units[0]) == []
    assert units[0]["at"] == 2 and (units[0]["x"], units[0]["y"]) == (shore.x, shore.y)
    assert units[0]["landed"] == world.turn
    # 港でない所に 1 ターンに下ろせる重さには上限がある（輸送船 1 隻分 × 隻数）
    step(
        game,
        world,
        {
            1: [
                (
                    "unload_unit",
                    {
                        "unit": u["id"],
                        "fleet": transport["id"],
                        "x": shore.x,
                        "y": shore.y,
                        "island": 2,
                    },
                )
                for u in units[1:]
            ]
        },
    )
    failed = [e.args["reason"] for e in isl1.log if e.kind == "unit_order_failed"]
    assert "reason_landing_limit" in failed
    assert any("aboard" in u for u in units[1:]) and any("aboard" not in u for u in units[1:])
    # 揚陸したターンは弱い（戦闘に入ると強さが半分）
    ctx = _ctx(game, world)
    ctx.turn = units[0]["landed"]
    weak = battle.unit_power(world, ctx, units[0])
    units[0]["landed"] = -1
    assert weak == pytest.approx(
        battle.unit_power(world, ctx, units[0]) * game.config.get("military.combat.landing_weak")
    )


def test_units_abroad_eat_their_stores_and_starve_without_supply(game, world):
    start_war(game, world)
    isl2 = world.islands[2]
    tile = plain_tiles(isl2, 1)[0]
    unit = put_unit(game, world, 1, "infantry", tile.x, tile.y, at=2)
    unit["stores"] = {}
    feed(game, world)
    step(game, world)
    assert unit.get("hungry") is True and unit["soldiers"] < 100
    assert forces.state_of(world.islands[1])["abroad"] == unit["soldiers"]
    # 国の倉庫の食べ物は、外国の島の部隊の分を使わない（MI-64）
    from hakoniwa_core.modules.military import food_need

    assert food_need(world.islands[1], game.config) == 0 or forces.soldiers(world.islands[1]) > 0


def test_a_supply_fleet_feeds_units_on_the_island_and_a_port_helps(game, world):
    start_war(game, world)
    isl2 = world.islands[2]
    tile = plain_tiles(isl2, 1)[0]
    unit = put_unit(game, world, 1, "infantry", tile.x, tile.y, at=2)
    unit["stores"] = {}
    fleet = put_transport(
        world,
        1,
        isl2.wx,
        isl2.wy,
        ships=2,
        goods={"food": 20_000, "ammunition": 20_000, "fuel": 5_000},
    )
    feed(game, world)
    step(game, world)
    assert not unit.get("hungry") and unit["soldiers"] == 100
    assert unit["stores"]["food"] > 0
    assert fleet["goods"]["food"] < 20_000


def test_supplies_can_be_loaded_onto_a_docked_transport(game, world):
    isl1 = world.islands[1]
    fleet = put_transport(world, 1, isl1.wx, isl1.wy)
    feed(game, world)
    step(
        game,
        world,
        {1: [("load_supplies", {"fleet": fleet["id"], "good": "food", "quantity": 12})]},
    )
    assert fleet["goods"]["food"] == 12 * QUANTITY_SCALE
    step(
        game,
        world,
        {1: [("load_supplies", {"fleet": fleet["id"], "good": "food", "quantity": 100})]},
    )
    assert supply.goods_weight(fleet) <= game.defs.army["transport"]["capacity"]


def test_units_past_the_withdrawal_deadline_are_sent_home(game, world):
    start_war(game, world)
    isl1, isl2 = world.islands[1], world.islands[2]
    tile = plain_tiles(isl2, 1)[0]
    unit = put_unit(game, world, 1, "infantry", tile.x, tile.y, at=2)
    w = war.open_war(world, 1, 2)
    war.end(w, 1, world.turn, 2, True)
    ctx = _ctx(game, world)
    supply.recall(world, ctx)
    assert unit in world.ext["military"]["units"]  # 期限の中
    ctx.turn = world.turn + 5
    before = sum(t.population for t in isl1.tiles)
    supply.recall(world, ctx)
    assert unit not in world.ext["military"]["units"]
    assert sum(t.population for t in isl1.tiles) == before + 100
    assert logs(world, 1, "unit_demobilized_abroad")


def _ended_war_with_a_unit_abroad(game, world):
    start_war(game, world)
    isl2 = world.islands[2]
    tile = plain_tiles(isl2, 1)[0]
    unit = put_unit(game, world, 1, "infantry", tile.x, tile.y, at=2)
    war.end(war.open_war(world, 1, 2), 1, world.turn, 2, True)
    ctx = _ctx(game, world)
    ctx.turn = world.turn + 5  # 期限（2 ターン）を過ぎた
    return unit, ctx


def test_units_past_the_deadline_sail_home_when_a_transport_has_room(game, world):
    """引き揚げの期限が来たとき、空きのある輸送船団があれば部隊は帰国する（W-192）。"""
    from tests.core.test_military_fleets import put_port

    isl1 = world.islands[1]
    port = put_port(isl1)
    unit, ctx = _ended_war_with_a_unit_abroad(game, world)
    forces.state_of(isl1)["abroad"] = unit["soldiers"]
    stores = dict(unit["stores"])
    fleet = put_transport(world, 1, isl1.wx, isl1.wy, ships=2)
    before = (sum(t.population for t in isl1.tiles), stock.total(isl1.warehouse, "food"))
    supply.recall(world, ctx)
    assert unit in world.ext["military"]["units"] and not army.abroad(unit)
    assert "at" not in unit and (unit["x"], unit["y"]) == (port.x, port.y)
    assert unit["soldiers"] == 100 and unit["status"] == army.READY
    assert "stores" not in unit  # 外国で持っていた補給は国の倉庫へ
    assert forces.state_of(isl1).get("abroad", 0) == 0  # 島の外にいる兵の数から引く
    assert stock.total(isl1.warehouse, "food") == before[1] + stores["food"]
    assert sum(t.population for t in isl1.tiles) == before[0]  # 家計には戻らない
    [entry] = logs(world, 1, "unit_returned_home")
    assert entry.args["fleet"] == fleet["id"] and not logs(world, 1, "unit_demobilized_abroad")
    assert format_log(game.defs, "unit_returned_home", entry.args)


def test_units_with_no_room_on_the_transports_are_discharged_where_they_stand(game, world):
    from tests.core.test_military_fleets import put_port

    isl1 = world.islands[1]
    put_port(isl1)
    unit, ctx = _ended_war_with_a_unit_abroad(game, world)
    fleet = put_transport(world, 1, isl1.wx, isl1.wy)
    spec = game.defs.army["transport"]
    # 補給で船いっぱいにして、部隊 1 つ分の重さの空きもない
    fleet["goods"] = {"food": int(spec["capacity"]) * QUANTITY_SCALE}
    before = sum(t.population for t in isl1.tiles)
    supply.recall(world, ctx)
    assert unit not in world.ext["military"]["units"]
    assert sum(t.population for t in isl1.tiles) == before + 100
    assert logs(world, 1, "unit_demobilized_abroad") and not logs(world, 1, "unit_returned_home")


def test_a_transport_away_from_its_home_port_cannot_carry_units_home(game, world):
    from tests.core.test_military_fleets import put_port

    isl1 = world.islands[1]
    put_port(isl1)
    unit, ctx = _ended_war_with_a_unit_abroad(game, world)
    isl2 = world.islands[2]
    put_transport(world, 1, isl2.wx, isl2.wy, ships=2)  # 母港の区画にいない
    supply.recall(world, ctx)
    assert unit not in world.ext["military"]["units"]
    assert logs(world, 1, "unit_demobilized_abroad")


def test_a_transport_carries_only_what_fits_and_the_rest_are_discharged(game, world):
    """1 隻の空きで載せられるのは部隊の重さの分だけ。載り切らない部隊はその場で除隊する。"""
    from tests.core.test_military_fleets import put_port

    isl1 = world.islands[1]
    put_port(isl1)
    unit, ctx = _ended_war_with_a_unit_abroad(game, world)
    tile = plain_tiles(world.islands[2], 2)[1]
    second = put_unit(game, world, 1, "infantry", tile.x, tile.y, at=2)
    weight = game.defs.army["infantry"]["weight"]
    fleet = put_transport(world, 1, isl1.wx, isl1.wy)
    fleet["goods"] = {
        "food": int(game.defs.army["transport"]["capacity"] - weight * 1.5) * QUANTITY_SCALE
    }
    supply.recall(world, ctx)
    kept = [u for u in (unit, second) if u in world.ext["military"]["units"]]
    assert len(kept) == 1 and kept[0] is unit  # 番号の若い部隊が先に載る
    assert len(logs(world, 1, "unit_returned_home")) == 1
    assert len(logs(world, 1, "unit_demobilized_abroad")) == 1


def test_an_old_war_does_not_send_units_home_from_a_new_war(game, world):
    start_war(game, world)
    isl2 = world.islands[2]
    tile = plain_tiles(isl2, 1)[0]
    unit = put_unit(game, world, 1, "infantry", tile.x, tile.y, at=2)
    old = war.open_war(world, 1, 2)
    war.end(old, 1, world.turn, 2, True)
    start_war(game, world)  # 同じ相手とまた戦う
    assert war.open_war(world, 1, 2) is not None
    ctx = _ctx(game, world)
    ctx.turn = world.turn + 5
    supply.recall(world, ctx)
    assert unit in world.ext["military"]["units"]


def test_police_hold_a_town_until_overwhelmed(game, world):
    start_war(game, world)
    isl2 = world.islands[2]
    ctx = _ctx(game, world)
    if battle.police_unit(world, ctx, isl2) is None:  # 作った世界に警察がなければ置く（飛ばさない）
        used = {(f.x, f.y) for f in isl2.facilities}
        tile = next(t for t in isl2.tiles if t.terrain == "plain" and (t.x, t.y) not in used)
        isl2.facilities.append(
            Facility(id=isl2.next_facility_id(), kind="police", x=tile.x, y=tile.y, built=True)
        )
        isl2.facilities[-1].workers = 120
    police = battle.police_unit(world, ctx, isl2)
    assert police is not None and police["soldiers"] > 0
    assert battle.police_power(ctx, police) == pytest.approx(0.3 * police["soldiers"] / 100)
    assert BASIS == 10_000


def test_standard_plan_defends_when_attacked_and_signs_a_ceasefire(game, world):
    isl2 = world.islands[2]
    # 戦争のない間は、標準の計画は軍に何もしない
    assert plan_war(world, game.defs, game.config) == {}
    start_war(game, world)
    plans = plan_war(world, game.defs, game.config)
    assert plans[2][0][0] == "build" and plans[2][0][1]["facility"] == "barracks"
    for _ in range(20):
        for island_id, orders in plan_war(world, game.defs, game.config).items():
            queue_orders(world.islands[island_id], orders)
        feed(game, world)
        step(game, world)
        for island in world.islands.values():  # 試験では建設の人手と資材を待たない
            for f in island.facilities:
                if f.kind == "barracks" and not f.built:
                    f.built = True
    assert any(f.kind == "barracks" and f.built for f in isl2.facilities)
    assert forces.soldiers(isl2) > 0 and army.land_units_of(world, 2)
    # 相手が出した停戦に署名する
    step(game, world, {1: [("propose_treaty", {"partner": 2, "clauses": [{"type": "ceasefire"}]})]})
    plans = plan_war(world, game.defs, game.config)
    assert plans[2][0][0] == "sign_treaty"
    queue_orders(isl2, plans[2])
    step(game, world)
    assert war.open_war(world, 1, 2)["state"] == war.CEASEFIRE
