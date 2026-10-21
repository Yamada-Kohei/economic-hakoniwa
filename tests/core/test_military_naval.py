"""海の戦い・護衛・通商破壊・封鎖（フェーズ4-3 の②。15-military の MI-31〜MI-35・MI-48・MI-56）。"""

from __future__ import annotations

import pytest

from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.diplomacy import war
from hakoniwa_core.game import load_game
from hakoniwa_core.military import army, forces, naval
from hakoniwa_core.modules.trade import _Round
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import world_state as trade_state
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
        stage="trade",
        before=world,
    )


def start_war(game, world):
    queue_orders(world.islands[1], [("declare_war", {"target": 2})])
    game.engine.run_turn(world)
    assert war.status(world, 1, 2, world.turn + 1) == war.WAR


def put_fleet(world, game, owner, kind, ships, x, y, duty=None):
    spec = game.defs.army[kind]
    forces.state_of(world.islands[owner], game.config)["volunteers"] += spec["soldiers"] * ships
    fleet = army.new_unit(world, owner, kind, x, y, ships=ships)
    fleet["status"] = army.READY
    fleet["soldiers"] = spec["soldiers"] * ships
    fleet["equipment"] = {g: q // QUANTITY_SCALE * ships for g, q in spec["equipment"].items()}
    if duty:
        fleet["duty"] = duty
    return fleet


def cell(world, island_id):
    island = world.islands[island_id]
    return island.wx, island.wy


def logs(world, island_id, kind):
    return [e for e in world.islands[island_id].log if e.kind == kind]


def test_no_sea_battle_in_peace(game, world):
    x, y = cell(world, 2)
    a = put_fleet(world, game, 1, "frigate", 2, x, y)
    b = put_fleet(world, game, 2, "corvette", 2, x, y)
    naval.battle(world, game.defs, game.config, world.turn + 1)
    assert a["ships"] == 2 and b["ships"] == 2


def test_the_side_that_sees_the_other_shoots_first_and_sinks_ships(game, world):
    start_war(game, world)
    x, y = cell(world, 2)
    strong = put_fleet(world, game, 1, "frigate", 4, x, y)
    weak = put_fleet(world, game, 2, "corvette", 2, x, y)
    naval.battle(world, game.defs, game.config, world.turn + 1)
    assert weak["ships"] < 2 or weak not in world.ext["military"]["units"]
    assert strong["ships"] >= 3
    assert logs(world, 1, "sea_battle") and logs(world, 2, "sea_battle")


def test_a_sunk_fleet_takes_its_cargo_down(game, world):
    start_war(game, world)
    x, y = cell(world, 2)
    transport = put_fleet(world, game, 2, "transport", 1, x, y)
    unit = army.new_unit(world, 2, "infantry", 0, 0)
    unit["soldiers"], unit["status"], unit["aboard"] = 100, army.READY, transport["id"]
    transport["cargo"].append(unit["id"])
    dead = naval.sink_ships(world, game.defs, transport, 1, world.turn + 1, killer=1)
    assert dead == 110  # 乗員 10 人と載せていた 100 人
    assert transport not in world.ext["military"]["units"]
    assert unit not in world.ext["military"]["units"]
    assert logs(world, 2, "fleet_sunk")


def test_fleet_duty_needs_a_warship_and_a_foreign_island_to_blockade(game, world):
    start_war(game, world)
    x, y = cell(world, 2)
    frigate = put_fleet(world, game, 1, "frigate", 1, x, y)
    transport = put_fleet(world, game, 1, "transport", 1, x, y)
    home = put_fleet(world, game, 1, "corvette", 1, *cell(world, 1))
    queue_orders(
        world.islands[1],
        [
            ("fleet_duty", {"fleet": frigate["id"], "duty": "blockade"}),
            ("fleet_duty", {"fleet": transport["id"], "duty": "watch"}),
            ("fleet_duty", {"fleet": home["id"], "duty": "blockade"}),
        ],
    )
    game.engine.run_turn(world)
    assert frigate.get("duty") == "blockade"
    assert "duty" not in transport and "duty" not in home
    reasons = [e.args["reason"] for e in logs(world, 1, "unit_order_failed")]
    assert reasons.count("reason_fleet_not_warship") == 1
    assert reasons.count("reason_no_blockade_target") == 1


def test_a_blockade_stops_ships_to_and_from_the_island_only_in_war(game, world):
    x, y = cell(world, 2)
    put_fleet(world, game, 1, "corvette", 2, x, y, duty="blockade")
    turn = world.turn + 1
    assert not naval.blocked(world, game.defs, game.config, turn, 2, 1, "food")  # 平時
    start_war(game, world)
    turn = world.turn + 1
    assert naval.blocked(world, game.defs, game.config, turn, 2, 1, "food")
    assert not naval.blocked(world, game.defs, game.config, turn, 1, 1, "food")  # 封鎖する国の船


def test_one_ship_blocks_half_of_the_sailings(game, world):
    start_war(game, world)
    put_fleet(world, game, 1, "corvette", 1, *cell(world, 2), duty="blockade")
    turn = world.turn + 1
    blocked = sum(
        naval.blocked(world, game.defs, game.config, turn, 2, 1, f"good{i}") for i in range(200)
    )
    assert 60 < blocked < 140


def shipment(world, load=40_000, oneway=3, loaded=1, ship=True, qty=10_000):
    state = trade_state(world)
    state["seq"]["shipment"] += 1
    s = {
        "id": state["seq"]["shipment"],
        "kind": "bloc",
        "ref": 1,
        "from": 1,
        "to": 2,
        "facility": None,
        "good": "food",
        "batches": [[loaded, qty]],
        "load": load,
        "loaded": loaded,
        "arrive": loaded + 2 * oneway,
        "ship": ship,
        "oneway": oneway,
    }
    state["shipments"].append(s)
    cargo = state.setdefault("cargo", {"loaded": {}, "unloaded": {}})
    cargo["loaded"]["food"] = cargo["loaded"].get("food", 0) + qty
    return s


def test_cargo_is_aboard_only_on_the_way_back(game, world):
    s = shipment(world, loaded=1, oneway=3)
    out = naval.position(world, s, 3)
    back = naval.position(world, s, 6)
    assert out is not None and out[1] is False
    assert back is not None and back[1] is True
    assert back[0] == cell(world, 1) or back[0] != out[0]
    assert naval.position(world, {**s, "ship": False}, 3) is None


def test_a_watched_cell_sinks_the_enemy_ship_with_its_cargo_on_the_way_back(game, world):
    start_war(game, world)
    s = shipment(world, loaded=world.turn - 3, oneway=3)  # 帰りの途中
    where = naval.position(world, s, world.turn + 1)
    assert where is not None and where[1] is True
    put_fleet(world, game, 1, "corvette", 12, *where[0], duty="watch")
    round_ = _Round(world, ctx_of(game, world))
    round_._intercept()
    assert s not in trade_state(world)["shipments"]
    cargo = trade_state(world)["cargo"]
    assert cargo["unloaded"]["food"] == 10_000  # 沈んで失った荷として突き合わせに入る
    assert logs(world, 2, "ship_sunk_cargo")
    assert world.islands[2].stats["trade.cargo_sunk"] == 10_000


def test_a_ship_sunk_on_the_way_out_loses_only_the_ship_and_is_delayed(game, world):
    start_war(game, world)
    s = shipment(world, loaded=world.turn, oneway=3)  # 行きの途中
    where = naval.position(world, s, world.turn + 1)
    assert where is not None and where[1] is False
    put_fleet(world, game, 1, "corvette", 12, *where[0], duty="watch")
    arrive = s["arrive"]
    round_ = _Round(world, ctx_of(game, world))
    round_._intercept()
    assert s in trade_state(world)["shipments"]
    assert s["arrive"] == arrive + 3 and s["delay"] == 3
    assert logs(world, 2, "ship_sunk_out")


def test_a_neutral_ship_is_turned_back_at_a_blockade_and_an_enemy_ship_is_sunk(game):
    world = generate_world(7, 3, game.config, game.defs)
    for island in world.islands.values():
        island.regime["politics"] = "authoritarian"
    start_war(game, world)  # 島 1 が島 2 に宣戦（島 3 は中立）
    put_fleet(world, game, 1, "corvette", 2, *cell(world, 2), duty="blockade")
    for buyer, outcome in ((3, naval.TURNED), (2, naval.SUNK)):
        s = shipment(world, loaded=world.turn, oneway=1)
        s["from"], s["to"] = (2, 3) if buyer == 3 else (3, 2)
        if buyer == 2:  # 封鎖される国の船が、中立の島から荷を積んで戻る
            s["loaded"] = world.turn - 1
        turn = world.turn + 1
        groups = naval.blockades(world, game.defs, game.config, turn)
        results = {
            naval.shipment_outcome(
                world, game.defs, game.config, turn, {**s, "id": s["id"] + n}, [], groups
            )
            for n in range(40)
        }
        assert outcome in results and results <= {outcome, None}


def test_escorts_cut_the_chance_of_being_found(game, world):
    start_war(game, world)
    x, y = cell(world, 1)
    put_fleet(world, game, 1, "corvette", 3, x, y, duty="watch")
    path = naval.route_cells(world, 2, 1)
    turn = world.turn + 1
    bare = naval.risk(world, game.defs, game.config, turn, 2, path)
    put_fleet(world, game, 2, "frigate", 4, 0, 0, duty="escort")
    escorted = naval.risk(world, game.defs, game.config, turn, 2, path)
    assert bare > 0 and escorted < bare
    peace = generate_world(7, 2, game.config, game.defs)
    assert naval.risk(peace, game.defs, game.config, 1, 2, path) == 0


def test_the_war_risk_raises_the_freight_not_the_peaceful_route(game, world):
    start_war(game, world)
    put_fleet(world, game, 1, "corvette", 3, *cell(world, 1), duty="watch")
    round_ = _Round(world, ctx_of(game, world))
    buyer, seller = world.islands[2], world.islands[1]
    assert round_._war_risk(1000, buyer, seller) > 1000
    assert round_._war_risk(1000, seller, buyer) == 1000  # 買い手 1 の船は自国の見張りに襲われない
