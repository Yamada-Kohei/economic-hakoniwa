"""銅の鉱脈と銅・電子機器・兵器の品目と施設（フェーズ4-2 ①。設計書 15-military の MI-15 など）。"""

from __future__ import annotations

import copy

from hakoniwa_core import facilities as fac
from hakoniwa_core.military import veins
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import tradable, tradable_goods
from tests.core.test_phase3_3 import game as game  # noqa: F401  （試験の部品を使い回す）

ARMS = ("weapons", "ammunition", "military_vehicle", "warship", "frigate", "submarine")


def test_arms_move_only_by_contract_and_aid_not_in_the_bloc_market(game):
    goods = set(tradable_goods(game.defs))
    for good in ARMS:
        assert tradable(game.defs, good)  # 契約と物の援助には使える
        assert good not in goods  # 貿易圏の市場と商社には出ない
    assert {"copper_ore", "copper", "electronics"} <= goods


def test_the_chain_from_copper_ore_to_electronics_and_warships(game):
    base = {"iron_ore", "coal", "crude_oil"}
    without = fac.producible_goods(game.defs, base)
    assert not {"copper", "electronics", "warship"} & without
    made = fac.producible_goods(game.defs, base | {"copper_ore"})
    assert {"copper_ore", "copper", "electronics", "weapons", "ammunition"} <= made
    assert {"warship", "frigate", "submarine", "military_vehicle"} <= made


def test_veins_are_placed_once_by_seed_and_island_number_only(game):
    world = generate_world(5, 50, game.config, game.defs)
    first = copy.deepcopy(world)
    for island in world.islands.values():
        island.resources = [r for r in island.resources if r != "copper_ore"]
        island.ext.pop(veins.KEY, None)
        first.islands[island.id].resources = list(island.resources)
        first.islands[island.id].ext.pop(veins.KEY, None)
    placed = {i: veins.place(world.islands[i], game.config, world.seed) for i in world.island_ids()}
    # 島の順番を逆にしても同じ
    again = {
        i: veins.place(first.islands[i], game.config, first.seed)
        for i in reversed(first.island_ids())
    }
    assert placed == again
    count = sum(1 for p in placed.values() if "copper_ore" in p)
    share = game.config.get("military.veins")["copper_ore"]
    assert 0.2 * len(placed) < count < 0.6 * len(placed) and 0.2 < share < 0.6
    # 2 度目は何もしない
    for island in world.islands.values():
        before = list(island.resources)
        assert veins.place(island, game.config, world.seed) == []
        assert island.resources == before
    # 鉱業資源の上限（建国で選べる数）の外に置く
    limit = game.config.get("founding.max_resources")
    assert any(len(i.resources) > limit for i in world.islands.values())


def test_the_standard_plan_does_not_dig_the_vein_resources(game):
    """買い手が軍だけの間は、標準の計画は銅を掘らない（買い手のいない精錬所と鉱山が建ち、鉱山の
    上限で鉄鉱石の鉱山が建たなくなるため）。"""
    from tests.core.test_gate import make_world, run_with_plan

    world = make_world(game, 7, ["coal", "iron_ore"])
    island = world.islands[1]
    island.resources = sorted([*island.resources, "copper_ore"])
    run_with_plan(game, world, 80)
    kinds = [f.kind for f in island.facilities]
    assert "copper_smelter" not in kinds
    assert any(f.kind == "mine" and f.recipe == "iron_ore" for f in island.facilities)


def test_uranium_veins_come_with_version_two_and_leave_the_copper_alone(game):
    world = generate_world(5, 50, game.config, game.defs)
    old = copy.deepcopy(world)
    for island in world.islands.values():
        island.ext[veins.KEY] = 1  # 4-2 の世界（銅だけ置いた版）
        old.islands[island.id].ext[veins.KEY] = 1
    placed = {i: veins.place(world.islands[i], game.config, world.seed) for i in world.island_ids()}
    assert all(set(p) <= {"uranium_ore"} for p in placed.values())  # 銅は動かさない
    count = sum(1 for p in placed.values() if p)
    share = game.config.get("military.veins")["uranium_ore"]
    assert 0 < count < 0.4 * len(placed) and share < 0.3
    for island in world.islands.values():
        assert island.ext[veins.KEY] == veins.VERSION
        assert veins.place(island, game.config, world.seed) == []
