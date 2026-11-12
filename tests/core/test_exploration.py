"""探鉱（MI-68）と電子機器のアルミの作り方（MI-69）。W-234。"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import exploration, veins
from hakoniwa_core import facilities as fac
from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.economy.prices import initial_prices, unit_cost
from hakoniwa_core.game import load_game
from hakoniwa_core.modules.exploration import Exploration
from hakoniwa_core.orders import check_order
from hakoniwa_core.state import ORDER_DONE
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.turn.context import StageContext
from tests.core.test_gate import make_world


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture(scope="module")
def started(game):
    """石炭と鉄鉱石を選び、2 ターン回した島（お金の経済と鉱脈の印がある）。試験ごとに写す。"""
    world = make_world(game, 3, ["coal", "iron_ore"])
    for _ in range(2):
        game.engine.run_turn(world)
    return world


@pytest.fixture
def world(started):
    return copy.deepcopy(started)


def ctx_of(game, world):
    return StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.engine.registry,
        formulas={},
        turn=world.turn + 1,
        seed=world.seed,
        stage="exploration",
        before=world,
    )


def free_tile(game, island, good):
    terrains = exploration.miner_terrains(game.defs, good)
    used = {(f.x, f.y) for f in island.facilities}
    return next(
        t
        for t in island.tiles
        if t.terrain in terrains and (t.x, t.y) not in used and not t.population and not t.zone
    )


def check(game, island, payload):
    return check_order(game.defs, game.engine.registry, island, "explore", payload)


def start(game, world, good):
    """探鉱の命令を出して 1 ターン回す。置いた探鉱隊を返す。"""
    island = world.islands[1]
    tile = free_tile(game, island, good)
    island.orders.clear()
    queue_orders(island, [("explore", {"resource": good, "x": tile.x, "y": tile.y})])
    game.engine.run_turn(world)
    assert island.orders[-1].status == ORDER_DONE
    team = exploration.team(island, game.config)
    assert team is not None and team.ext[exploration.EXPLORE] == {"good": good, "progress": 0}
    return team


def advance(game, world, team, turns, rate=1000):
    """探鉱の段だけを、稼働率 rate で turns 回動かす。"""
    island = world.islands[1]
    team.built = True
    for _ in range(turns):
        if team not in island.facilities:
            return
        team.operating_rate = rate
        Exploration().run_island(island, ctx_of(game, world))


def test_the_geology_uses_the_same_number_as_the_veins(game):
    """銅は鉱脈と同じ数で決まる: 鉱脈の割合より下なら鉱脈で、探鉱では見つからない（同じ島に鉱脈と
    鉱床の両方は出ない）。鉱脈のない資源は別の数を引く。"""
    world = generate_world(5, 120, game.config, game.defs)
    share = game.config.get("world.veins")["copper_ore"]
    chance = game.config.get("explore.chance")["copper_ore"]
    found = 0
    for island in world.islands.values():
        free = copy.deepcopy(island)
        free.resources = []
        free.ext.pop(veins.KEY, None)
        vein = "copper_ore" in veins.place(free, game.config, world.seed)
        value = veins.roll(world.seed, island.id, "copper_ore")
        assert vein == (value < share)
        deposit = exploration.has_deposit(island, game.config, world.seed, "copper_ore")
        assert not (vein and deposit)
        found += deposit
    # 鉱脈の出なかった島（約 6 割）の約 4 分の 1 に、探鉱で見つかる鉱床がある
    assert 0.05 * len(world.islands) < found < 0.3 * len(world.islands) and chance < 0.3
    # 鉱脈のない資源の数は、鉱脈の数と別（同じ島で同じ数にならない）
    rolls = {veins.roll(world.seed, i, "bauxite") for i in world.island_ids()}
    assert len(rolls) == len(world.islands)
    assert veins.roll(world.seed, 1, "bauxite") != veins.roll(world.seed, 1, "copper_ore")


def test_the_order_needs_a_resource_the_island_lacks_on_the_right_land(game, world):
    island = world.islands[1]
    assert set(exploration.explorable(island, game.defs, game.config)) <= {
        "stone",
        "crude_oil",
        "bauxite",
        "copper_ore",
    }
    tile = free_tile(game, island, "stone")
    ok = check(game, island, {"resource": "stone", "x": tile.x, "y": tile.y})
    assert ok.ok, ok
    # ウラン鉱石は探せない（核への道は建国のときの鉱脈だけ）
    bad = check(game, island, {"resource": "uranium_ore", "x": tile.x, "y": tile.y})
    assert bad.reason == "reason_explore_resource"
    # 持っている資源は探せない
    bad = check(game, island, {"resource": "coal", "x": tile.x, "y": tile.y})
    assert bad.reason == "reason_explore_have"
    # 採石場の建たない平地では石材を探せない
    plain = next(
        t
        for t in island.tiles
        if t.terrain == "plain" and island.facility_at(t.x, t.y) is None and not t.zone
    )
    bad = check(game, island, {"resource": "stone", "x": plain.x, "y": plain.y})
    assert bad.reason in ("reason_explore_terrain", "reason_tile_occupied")
    # 探鉱隊は建設の命令では建てられない
    built = check_order(
        game.defs,
        game.engine.registry,
        island,
        "build",
        {"facility": "exploration_team", "x": tile.x, "y": tile.y},
    )
    assert built.reason == "reason_not_buildable"
    # 鉱脈を置く前の島（建国の後の最初の更新の前）は出せない
    island.ext[veins.KEY] = veins.VERSION - 1
    bad = check(game, island, {"resource": "stone", "x": tile.x, "y": tile.y})
    assert bad.reason == "reason_explore_not_yet"
    island.ext[veins.KEY] = veins.VERSION
    # 上限（建国で選べる数 + 1）に届いた島は出せない
    island.resources = sorted([*island.resources, "crude_oil"])
    bad = check(game, island, {"resource": "stone", "x": tile.x, "y": tile.y})
    assert bad.reason == "reason_explore_full"


def test_a_deposit_is_found_after_the_full_turns_and_the_team_leaves(game, world, monkeypatch):
    island = world.islands[1]
    monkeypatch.setitem(game.config.raw["explore"]["chance"], "bauxite", 1.0)
    mines = fac.island_limit(game.defs, island, "mine")
    team = start(game, world, "bauxite")
    pos = (team.x, team.y)
    # 1 度に 1 つだけ
    other = free_tile(game, island, "stone")
    busy = check(game, island, {"resource": "stone", "x": other.x, "y": other.y})
    assert busy.reason == "reason_explore_busy"
    turns = game.config.get("explore.turns")
    # 稼働率が半分なら、全力の 2 倍のターンがかかる
    advance(game, world, team, turns, rate=500)
    assert team in island.facilities
    assert team.ext[exploration.EXPLORE]["progress"] == turns * 500
    assert "bauxite" not in island.resources
    advance(game, world, team, turns, rate=500)
    assert team not in island.facilities and island.facility_at(*pos) is None
    assert "bauxite" in island.resources
    assert exploration.explored(island) == {"bauxite": exploration.FOUND}
    assert [e.kind for e in island.log][-1] == "explore_found"
    # 鉱山の上限も 1 つ増える（FM-66）
    assert fac.island_limit(game.defs, island, "mine") == max(mines, 3)
    # 始めの値段の基準は建国のときの資源のまま
    assert exploration.base_resources(island) == ["coal", "iron_ore"]


def test_an_empty_island_is_known_and_cannot_be_searched_again(game, world, monkeypatch):
    island = world.islands[1]
    monkeypatch.setitem(game.config.raw["explore"]["chance"], "stone", 0.000001)
    team = start(game, world, "stone")
    advance(game, world, team, game.config.get("explore.turns"))
    assert team not in island.facilities
    assert "stone" not in island.resources
    assert exploration.explored(island) == {"stone": exploration.NONE}
    assert [e.kind for e in island.log][-1] == "explore_none"
    assert "stone" not in exploration.explorable(island, game.defs, game.config)
    tile = free_tile(game, island, "stone")
    again = check(game, island, {"resource": "stone", "x": tile.x, "y": tile.y})
    assert again.reason == "reason_explore_tried"


def test_a_vein_or_a_full_island_ends_the_search_without_a_find(game, world):
    island = world.islands[1]
    team = start(game, world, "copper_ore")
    advance(game, world, team, 3)
    island.resources = sorted([*island.resources, "copper_ore"])  # 鉱脈で銅が出た
    advance(game, world, team, 1)
    assert team not in island.facilities
    assert [e.kind for e in island.log][-1] == "explore_stopped"
    assert exploration.explored(island) == {}


def test_an_occupied_or_demolished_team_makes_no_progress(game, world):
    island = world.islands[1]
    team = start(game, world, "stone")
    team.ext["occupied"] = True
    advance(game, world, team, 5)
    assert team.ext[exploration.EXPLORE]["progress"] == 0
    team.ext.pop("occupied")
    advance(game, world, team, 2)
    assert team.ext[exploration.EXPLORE]["progress"] == 2000
    island.orders.clear()
    queue_orders(island, [("demolish", {"x": team.x, "y": team.y})])
    game.engine.run_turn(world)
    assert exploration.team(island, game.config) is None
    assert exploration.explored(island) == {}  # やめても、外れたことにはならない


def test_the_team_is_paid_from_the_public_works_budget(game, world):
    """探鉱隊の賃金と仕入れは公共事業費から（学校と同じく公務の施設。国営だけ・民営にできない）。"""
    team = start(game, world, "stone")
    fdef = game.defs.facilities[team.kind]
    assert fdef["public_service"] and fdef["budget"] == "construction"
    assert fdef["private"] is False and fdef["max_per_island"] == 1
    assert not fdef["recipes"]["default"]["outputs"]


def test_the_explore_settings_cover_the_mining_goods_but_uranium(game):
    chance = game.config.get("explore.chance")
    mining = {g for g, d in game.defs.goods.items() if d.get("mining")}
    assert set(chance) == mining - {"uranium_ore"}
    assert all(0 < c < 1 for c in chance.values())
    assert chance["stone"] > chance["coal"] > chance["crude_oil"]
    assert game.config.get("explore.turns") > 12


# --- 電子機器のアルミの作り方（MI-69） ---------------------------------------------------------


def _variable_margin(game, recipe, prices):
    """全力で 1 ターン動いたときの売上 − 投入財（賃金の前の変動費の利益）。"""
    out = sum(prices[g] * q for g, q in recipe["outputs"].items())
    return out - sum(prices[g] * q for g, q in recipe["inputs"].items())


def test_electronics_can_be_made_with_aluminium_instead_of_copper(game):
    recipes = game.defs.facilities["electronics_plant"]["recipes"]
    copper, alu = recipes["default"], recipes["aluminium"]
    assert copper["outputs"] == alu["outputs"]
    assert alu["inputs"]["aluminium"] == copper["inputs"]["copper"] / 2
    prices = initial_prices(game.defs, game.config)
    # 始めの値段では銅の作り方が得で（利益は約 8 割）、電子機器の始めの値段は銅の作り方のまま
    ratio = _variable_margin(game, alu, prices) / _variable_margin(game, copper, prices)
    assert 0.75 < ratio < 0.88
    wage = game.config.get("labor.initial_wage")
    copper_cost = unit_cost(game.defs, game.config, "electronics_plant", copper, prices, wage)
    alu_cost = unit_cost(game.defs, game.config, "electronics_plant", alu, prices, wage)
    assert copper_cost < alu_cost
    # 銅が始めの値段の約 1.4 倍を超えると、アルミの作り方が得になる
    for factor, better in ((1.3, False), (1.5, True)):
        high = {**prices, "copper": prices["copper"] * factor}
        assert (_variable_margin(game, alu, high) > _variable_margin(game, copper, high)) == better


def test_an_island_without_copper_can_make_electronics_from_bauxite(game):
    assert "electronics" not in fac.producible_goods(game.defs, {"coal", "iron_ore"})
    made = fac.producible_goods(game.defs, {"coal", "iron_ore", "bauxite", "crude_oil"})
    assert "electronics" in made
    # 銅のない島の電子機器の始めの値段は、アルミの作り方の原価から（どの島でも同じ値段より高い）
    base = initial_prices(game.defs, game.config)
    island = initial_prices(game.defs, game.config, ["coal", "iron_ore", "bauxite", "crude_oil"])
    assert island["electronics"] > base["electronics"]


def test_a_wrecked_team_starts_over_and_a_finished_team_leaves_the_grid(game, world, monkeypatch):
    """攻撃や暴動で壊れて直す工事になった探鉱隊は、進みが 0 に戻る。終わった探鉱隊のマスには送電網を
    残す（まわりの施設の送電網が切れないように）。"""
    from hakoniwa_core import infra

    island = world.islands[1]
    team = start(game, world, "stone")
    advance(game, world, team, 4)
    team.ext["repair"] = True
    team.built = False
    Exploration().run_island(island, ctx_of(game, world))
    assert team.ext[exploration.EXPLORE]["progress"] == 0
    team.ext.pop("repair")
    monkeypatch.setitem(game.config.raw["explore"], "turns", 2)
    advance(game, world, team, 2)
    assert team not in island.facilities
    assert infra.level(island.tile(team.x, team.y), infra.POWER) == infra.FULL


def test_the_last_month_of_wages_is_paid_before_the_team_leaves(game, world, monkeypatch):
    """探鉱の段は賃金と市場の段の後: 終わる月に働いた人の賃金を払ってから探鉱隊を取り除く。"""
    stages = list(game.config.stages)
    assert stages.index("exploration") > stages.index("payroll")
    assert stages.index("exploration") > stages.index("market")
