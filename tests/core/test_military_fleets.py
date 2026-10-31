"""艦隊と海の動き・載せ降ろし・見える範囲（フェーズ4-2 の③'。15-military の MI-22・23・26）。"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import stock
from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.diplomacy import clauses as cl
from hakoniwa_core.facilities import is_land
from hakoniwa_core.game import load_game
from hakoniwa_core.geo import is_coastal
from hakoniwa_core.military import PRIVATE_LOGS, army, forces, sea, vision
from hakoniwa_core.state import Facility
from hakoniwa_core.units import QUANTITY_SCALE
from tests.core.test_gate import make_world, run_with_plan
from tests.core.test_military_units import fund, give, put_barracks, step


@pytest.fixture(scope="module")
def game():
    return load_game()


game_defs = load_game().defs


@pytest.fixture(scope="module")
def settled(game):
    world = make_world(game, 3, ["coal", "iron_ore"])
    run_with_plan(game, world, 60)
    return world


@pytest.fixture
def world(settled):
    return copy.deepcopy(settled)


def put_port(island):
    """海に接する平地に軍港を置く。"""
    used = {(f.x, f.y) for f in island.facilities}
    for tile in island.tiles:
        if tile.terrain != "plain" or (tile.x, tile.y) in used:
            continue
        if is_coastal(island, (tile.x, tile.y), lambda t: is_land(game_defs, t)):
            fid = max((f.id for f in island.facilities), default=0) + 1
            island.facilities.append(
                Facility(id=fid, kind="naval_base", x=tile.x, y=tile.y, built=True)
            )
            return tile
    raise AssertionError("coast not found")


def order(game, world, *orders, island_id=1):
    island = world.islands[island_id]
    queue_orders(island, list(orders))
    for _ in range(6):
        step(game, world)
        if not any(o.status == "pending" for o in island.orders):
            break
    return island


def refused(game, world, *orders):
    """命令を処理して、断られたわけ（reason の名前）を返す。"""
    island = world.islands[1]
    queue_orders(island, list(orders))
    reasons: set[str] = set()
    for _ in range(3):
        step(game, world)
        reasons |= {
            e.args["reason"]
            for e in island.log
            if e.kind in ("unit_order_failed", "order_failed")  # 命令の確かめで断られた分も
        }
    return reasons


def ready_fleet(game, world, kind="corvette", ships=2, fuel=20):
    island = world.islands[1]
    put_port(island)
    forces.state_of(island, game.config)
    spec = game.defs.army[kind]
    forces.recruit(island, game.config, spec["soldiers"] * ships, 1000)
    fund(island)
    give(island, "food", 50, game)
    give(island, "ammunition", 5, game)
    give(island, "fuel", fuel, game)
    for good, qty in spec["equipment"].items():
        give(island, good, qty // QUANTITY_SCALE * ships, game)
    order(game, world, ("form_fleet", {"army": kind, "ships": ships}))
    step(game, world)
    fleet = army.fleets_of(world, 1)[0]
    assert fleet["status"] == army.READY, fleet
    return island, fleet


def test_a_fleet_forms_at_a_naval_base_and_gets_ready_with_crew_and_ships(game, world):
    island = world.islands[1]
    assert refused(game, world, ("form_fleet", {"army": "corvette", "ships": 2})) == {
        "reason_no_naval_base"
    }
    assert army.fleets_of(world, 1) == []
    put_port(island)
    order(game, world, ("form_fleet", {"army": "corvette", "ships": 2}))
    fleet = army.fleets_of(world, 1)[0]
    assert fleet["status"] == army.FORMING and (fleet["x"], fleet["y"]) == sea.home(island)
    assert any(e.kind == "fleet_forming" for e in island.log)
    forces.state_of(island, game.config)
    forces.recruit(island, game.config, 200, 1000)
    give(island, "food", 30, game)
    give(island, "warship", 2, game)
    step(game, world)
    assert fleet["status"] == army.READY and fleet["soldiers"] == 120
    assert fleet["equipment"] == {"warship": 2}
    assert any(e.kind == "fleet_ready" for e in island.log)


def test_fleet_kind_and_size_are_checked(game, world):
    island = world.islands[1]
    put_port(island)
    refused(game, world, ("form_fleet", {"army": "infantry", "ships": 1}))  # 受けつけの段で断る
    assert island.orders[-1].status == "failed"
    assert refused(game, world, ("form_fleet", {"army": "corvette", "ships": 99})) == {
        "reason_fleet_ships"
    }
    assert army.fleets_of(world, 1) == []


def test_a_fleet_sails_along_the_line_by_its_speed(game, world):
    island, fleet = ready_fleet(game, world)
    start = (fleet["x"], fleet["y"])
    goal = (start[0] + 9, start[1] + 3)
    route = sea.line(start, goal)
    assert route[-1] == goal and len(route) == 9
    order(game, world, ("sail_fleet", {"fleet": fleet["id"], "x": goal[0], "y": goal[1]}))
    speed = sea.speed(game.defs.army["corvette"])
    assert (fleet["x"], fleet["y"]) == route[speed - 1]
    for _ in range(5):
        if fleet["order"] is None:
            break
        step(game, world)
    assert (fleet["x"], fleet["y"]) == goal and fleet["order"] is None
    assert any(e.kind == "fleet_arrived" for e in island.log)
    assert stock.total(island.warehouse, "fuel") < 20 * QUANTITY_SCALE


def test_the_line_is_deterministic_and_stays_between_the_ends():
    assert sea.line((2, 2), (2, 2)) == []
    assert sea.line((0, 0), (3, 0)) == [(1, 0), (2, 0), (3, 0)]
    diagonal = sea.line((0, 0), (3, 3))
    assert diagonal == [(1, 1), (2, 2), (3, 3)]
    assert sea.line((0, 0), (5, 2)) == sea.line((0, 0), (5, 2))


def test_a_fleet_cannot_sail_without_fuel_nor_off_the_map(game, world):
    island, fleet = ready_fleet(game, world, fuel=0)
    stock.take(island.warehouse, "fuel", 10**12)
    start = (fleet["x"], fleet["y"])
    order(game, world, ("sail_fleet", {"fleet": fleet["id"], "x": start[0] + 4, "y": start[1]}))
    assert (fleet["x"], fleet["y"]) == start
    assert any(e.kind == "fleet_no_fuel" for e in island.log)
    give(island, "fuel", 10, game)
    step(game, world)
    assert (fleet["x"], fleet["y"]) != start
    assert refused(game, world, ("sail_fleet", {"fleet": fleet["id"], "x": 40, "y": 0})) == {
        "reason_off_map"
    }


def test_a_docked_fleet_burns_only_a_share_of_the_fuel(game, world):
    island, fleet = ready_fleet(game, world, kind="frigate", ships=1, fuel=20)
    before = stock.total(island.warehouse, "fuel")
    spec = game.defs.army["frigate"]
    step(game, world)
    used = before - stock.total(island.warehouse, "fuel")
    assert 0 < used <= sea.fuel_need(spec, fleet, game.config, port=False)


def test_a_fleet_at_sea_or_with_cargo_cannot_be_disbanded(game, world):
    island, fleet = ready_fleet(game, world)
    start = (fleet["x"], fleet["y"])
    order(game, world, ("sail_fleet", {"fleet": fleet["id"], "x": start[0] + 9, "y": start[1]}))
    assert (fleet["x"], fleet["y"]) != start
    assert refused(game, world, ("disband_unit", {"unit": fleet["id"]})) == {"reason_fleet_at_sea"}
    assert army.find(world, fleet["id"]) is not None


def transport_with_unit(game, world):
    island, fleet = ready_fleet(game, world, kind="transport", ships=1)
    port = next(f for f in island.facilities if f.kind == "naval_base")
    forces.recruit(island, game.config, 200, 1000)
    give(island, "weapons", 100, game)
    barracks = put_barracks(island, (port.x, port.y))
    order(game, world, ("form_unit", {"army": "infantry", "x": barracks.x, "y": barracks.y}))
    for _ in range(3):
        step(game, world)
    unit = army.land_units_of(world, 1)[0]
    assert unit["status"] == army.READY
    return island, fleet, unit, port


def test_a_transport_loads_and_unloads_units_only_at_the_port(game, world):
    island, fleet, unit, port = transport_with_unit(game, world)
    order(game, world, ("load_unit", {"unit": unit["id"], "fleet": fleet["id"]}))
    assert "aboard" not in unit  # 部隊が軍港のマスにいない
    unit["x"], unit["y"] = port.x, port.y
    order(game, world, ("load_unit", {"unit": unit["id"], "fleet": fleet["id"]}))
    assert unit["aboard"] == fleet["id"] and fleet["cargo"] == [unit["id"]]
    assert army.housed(world, game.defs, 1, port.x, port.y) == 0
    move = ("move_unit", {"unit": unit["id"], "x": port.x + 1, "y": port.y})
    assert refused(game, world, move) == {"reason_unit_aboard"}
    assert refused(game, world, ("disband_unit", {"unit": unit["id"]})) == {"reason_unit_aboard"}
    assert army.find(world, unit["id"]) is not None
    order(
        game,
        world,
        ("unload_unit", {"unit": unit["id"], "fleet": fleet["id"], "x": port.x, "y": port.y}),
    )
    assert "aboard" not in unit and fleet["cargo"] == []


def test_transport_capacity_matches_the_merchant_ship(game):
    assert game.defs.army["transport"]["capacity"] == game.config.get("trade.ship_capacity")


def test_every_military_log_kind_is_private():
    import re
    from pathlib import Path

    kinds = set()
    root = Path(__file__).resolve().parents[2] / "hakoniwa_core"  # 試験を回す場所によらない
    for path in root.rglob("*.py"):
        name = path.relative_to(root).as_posix()
        if "military" in name or "operations" in name:
            kinds |= set(
                re.findall(
                    r'LogEntry\(\s*"((?:unit|fleet|trench)_\w+)"', path.read_text(encoding="utf-8")
                )
            )
    assert kinds
    assert kinds <= set(PRIVATE_LOGS)


# --- 見える範囲 ---------------------------------------------------------------------------


def add_islands(world, *places):
    """島 1 を写して、世界マップの区画 places に島 2, 3, ... を足す。"""
    for number, (wx, wy) in enumerate(places, start=2):
        copied = copy.deepcopy(world.islands[1])
        copied.id, copied.wx, copied.wy = number, wx, wy
        copied.facilities = []
        world.islands[number] = copied


def alliance(world, a, b):
    world.ext.setdefault("diplomacy", {}).setdefault("treaties", []).append(
        {"id": 1, "parties": [a, b], "state": "in_force", "clauses": [{"type": cl.ALLIANCE}]}
    )


def test_own_fleet_is_always_visible_and_others_by_reach(game, world):
    island, fleet = ready_fleet(game, world, kind="frigate", ships=1)
    add_islands(world, (5, 0))
    fleet["x"], fleet["y"] = 4, 0  # 隣の島の隣の区画
    assert vision.can_see(world, game.defs, 1, fleet)
    assert not vision.can_see(world, game.defs, 2, fleet)  # 島の目の広さは 0
    fleet["x"], fleet["y"] = 5, 0
    assert vision.can_see(world, game.defs, 2, fleet)  # 島の区画に入れば見える


def test_a_radar_sees_around_the_island_and_the_last_seen_place_is_remembered(game, world):
    island, fleet = ready_fleet(game, world, kind="frigate", ships=1)
    add_islands(world, (5, 5))
    other = world.islands[2]
    tile = next(t for t in other.tiles if t.terrain == "plain")
    other.facilities.append(Facility(id=1, kind="radar", x=tile.x, y=tile.y, built=True))
    fleet["x"], fleet["y"] = 7, 5  # レーダーの広さ（3）の内
    vision.update(world, game.defs, 7)
    seen = world.ext["military"]["seen"]["2"][str(fleet["id"])]
    assert seen == {"owner": 1, "kind": "frigate", "x": 7, "y": 5, "turn": 7}
    fleet["x"], fleet["y"] = 9, 5  # 見えなくなった
    vision.update(world, game.defs, 8)
    seen = world.ext["military"]["seen"]["2"][str(fleet["id"])]
    assert (seen["x"], seen["turn"]) == (7, 7)  # 前に見た場所のまま
    army.remove(world, fleet)
    vision.update(world, game.defs, 9)
    assert str(fleet["id"]) not in world.ext["military"]["seen"]["2"]


def test_allies_share_what_they_see(game, world):
    island, fleet = ready_fleet(game, world, kind="frigate", ships=1)
    add_islands(world, (5, 5), (9, 9))
    fleet["x"], fleet["y"] = 9, 9  # 島 3 の区画
    assert not vision.can_see(world, game.defs, 2, fleet)
    alliance(world, 2, 3)
    assert vision.can_see(world, game.defs, 2, fleet)  # 同盟国の目で見える
    assert not vision.can_see(
        world, game.defs, 3, army.new_unit(world, 2, "frigate", 0, 9, ships=1)
    )


def test_a_submarine_is_found_only_by_a_warship_in_the_same_cell(game, world):
    island, sub = ready_fleet(game, world, kind="submarine", ships=1)
    add_islands(world, (5, 5))
    sub["x"], sub["y"] = 5, 5
    assert not vision.can_see(world, game.defs, 2, sub)  # 島の目は潜水艦を見つけない
    hunter = army.new_unit(world, 2, "frigate", 5, 5, ships=1)
    hunter["status"] = army.READY
    found = []
    for turn in range(40):
        world.turn = turn
        found.append(vision.can_see(world, game.defs, 2, sub))
    assert any(found) and not all(found)  # 見込み（フリゲートの detect）で決まる
    world.turn = 5
    assert vision.can_see(world, game.defs, 2, sub) == vision.can_see(world, game.defs, 2, sub)
    hunter["x"] = 11  # 区画が違えば見つからない
    assert not any(vision.can_see(world, game.defs, 2, sub) for world.turn in range(40))


def test_disbanding_a_transport_unloads_its_supplies_to_the_warehouse(game, world):
    """母港で解散した輸送船団の積んでいた補給は、国の倉庫に戻る（W-113。前は消えていた）。"""
    island, fleet = ready_fleet(game, world, kind="transport", ships=1)
    give(island, "ammunition", 3, game)
    load = {"fleet": fleet["id"], "good": "ammunition", "quantity": 2}
    order(game, world, ("load_supplies", load))
    loaded = fleet.get("goods", {}).get("ammunition", 0)
    assert loaded > 0
    before = stock.total(island.warehouse, "ammunition")
    order(game, world, ("disband_unit", {"unit": fleet["id"]}))
    assert army.find(world, fleet["id"]) is None
    # 解散のターンの訓練で使う弾薬の分だけ少なくなりうる
    assert stock.total(island.warehouse, "ammunition") > before + loaded // 2
