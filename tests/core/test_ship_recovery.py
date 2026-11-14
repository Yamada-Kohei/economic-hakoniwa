"""船が寿命で減って戻らない弱さの直し（2026-11-13。設計書タブ 9 FX-78〜FX-80）。

- 寿命を過ぎた船は、代わりの船が足りるまで使い続ける（FX-79。trade/ships.py の scrap）
- 目標の船団（直近に使った船 ÷ 目標の混み具合）に足りない分を買う（FX-80。ships.renewal）
- 標準の計画は、圏の船の寿命に圏の造船所が間に合わなければ造船所を足す（FX-78。
  autoplan.shipyard_islands）

圏のほかの島の造船所への前注文（最初の FX-78）は、外貨の払いが通貨安と品切れを生み、外すと崩れが
main 並みに戻ったので取りやめた（2026-11-14 の X870 の切り分け）。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core.autoplan import shipyard_islands
from hakoniwa_core.game import load_game
from hakoniwa_core.state import ACCOUNTING_SELF, OWNER_PRIVATE, Facility
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import blocs, orders, shipping_of, ships
from hakoniwa_core.units import QUANTITY_SCALE
from tests.core.test_trade import run

MILLI = QUANTITY_SCALE
GOOD = "transport_ship"


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """貿易の始まった2島（2ターン回した）。試験ごとに写す。"""
    world = generate_world(7, 2, game.config, game.defs)
    run(game, world, 2)
    return world


def _yard_on(game, island):
    used = {(x.x, x.y) for x in island.facilities}
    x, y = next(
        (t.x, t.y)
        for t in island.tiles
        if (t.x, t.y) not in used and t.terrain in game.defs.facilities["shipyard"]["terrain"]
    )
    yard = Facility(
        id=max(f.id for f in island.facilities) + 1,
        kind="shipyard",
        x=x,
        y=y,
        owner=OWNER_PRIVATE,
        accounting=ACCOUNTING_SELF,
        built=True,
    )
    island.facilities.append(yard)
    return yard


def _setup(game, base):
    """島2（決済通貨の島）が作った圏に島1が入り、島2に民営の造船所、島1の海運業は船が足りない
    記録（1.5 隻分）を持つ。"""
    world = copy.deepcopy(base)
    config = game.config
    buyer_island, yard_island = world.islands[1], world.islands[2]
    blocs.create(world, yard_island, "test", True, world.turn)
    blocs.join(world, buyer_island, blocs.blocs(world)[0])
    yard = _yard_on(game, yard_island)
    f = shipping_of(buyer_island, config)
    turns = config.get("trade.cost_turns")
    f.economy["ships_short"] = [1] * turns
    f.economy[orders.SHORT_LOAD] = [config.get("trade.ship_capacity") * 1500] * turns
    f.stock.pop(GOOD, None)
    return world, buyer_island, yard_island, f, yard


# --- 寿命の延長（FX-79） ---------------------------------------------------------------


def test_retired_ships_sail_on_until_replacements_cover_the_use(game, base):
    world = copy.deepcopy(base)
    config = game.config
    island = world.islands[1]
    f = shipping_of(island, config)
    life = config.get("trade.ship_life")
    turn = world.turn
    load = config.get("trade.ship_capacity") * 2000  # 2隻分を使っている
    f.economy["used"] = [load]
    f.economy["fleet"] = [[turn - life - 3, 2000], [turn, 1000]]  # 2隻は寿命を過ぎた
    assert ships.scrap(island, config, turn) == MILLI  # 足りない1隻分だけ使い続ける
    assert ships.fleet(f) == 2 * MILLI and f.economy["overdue"] == MILLI
    f.economy["fleet"].append([turn, 1000])  # 代わりが届いた
    assert ships.scrap(island, config, turn) == MILLI
    assert "overdue" not in f.economy
    # 寿命の決めた倍を過ぎた船は、代わりがなくても解体する
    limit = round(life * config.get("trade.ship_life_limit"))
    f.economy["fleet"] = [[turn - limit, 2000]]
    assert ships.scrap(island, config, turn) == 2 * MILLI


def test_ships_past_their_life_add_no_wear_to_the_freight(game, base):
    world = copy.deepcopy(base)
    config = game.config
    island = world.islands[1]
    f = shipping_of(island, config)
    f.economy["fleet"] = [[world.turn, 2000]]
    f.economy["overdue"] = MILLI
    ships.close_turn(island, config, 0, 0)
    wear = island.economy.prices[GOOD] / config.get("trade.ship_life")
    assert abs(f.economy["capital_costs"][-1] - wear) <= 1  # 1隻分だけ


# --- 標準の計画の造船所（FX-78） -------------------------------------------------------


def test_plan_adds_a_yard_when_the_bloc_ships_retire_before_its_yards_can_replace_them(game, base):
    world, buyer_island, yard_island, f, yard = _setup(game, base)
    config = game.config
    life = config.get("trade.ship_life")
    turn = world.turn + 1
    yard_island.facilities.remove(yard)
    for island in (buyer_island, yard_island):
        shipping_of(island, config).economy["fleet"] = [[turn, 2000]]
    assert shipyard_islands(world, game.defs, config, turn) == set()  # 寿命はまだ先
    for island in (buyer_island, yard_island):
        shipping_of(island, config).economy["fleet"] = [[turn - life + 30, 2000]]
    chosen = shipyard_islands(world, game.defs, config, turn)
    assert len(chosen) <= 1  # 圏で1島ずつ
    # 圏の造船所が間に合えば足さない（造船所は全力で 1 ターンに 1/4 隻。60 ターンで 15 隻）
    yard_island.facilities.append(yard)
    assert shipyard_islands(world, game.defs, config, turn) == set()


# --- 目標の船団（FX-80） ---------------------------------------------------------------


def test_left_cargo_keeps_the_target_fleet_above_zero_without_ships(game, base):
    """船が 0 隻で航海の記録もなくても、載せられなかった荷があれば目標の船団は 0 にならない。"""
    world, buyer_island, yard_island, f, yard = _setup(game, base)
    config = game.config
    turns = config.get("trade.cost_turns")
    f.economy["fleet"] = []
    f.economy["used"] = [0] * turns
    # 1.5 隻分の荷が毎ターン残る（_setup）。目標の混み具合で割って 2 隻
    assert ships.left_ships(config, f) == 1500
    assert ships.fleet_target(config, f) == -(-1500 * 4 // 3)
    assert ships.renewal(config, f, world.turn + 1) == ships.fleet_target(config, f)
    f.economy[orders.SHORT_LOAD] = [0] * turns
    assert ships.fleet_target(config, f) == 0
