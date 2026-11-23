"""所有の船だけで海運業が成り立つこと（W-281 の残り。掲示板 #1438。船の長い借りは採らなかった）。

- 運賃の資本費は寿命の前の船の傷みだけ（船の利子 FX-84 (1) は 2026-11-22 に保留）。
- 海運業が買って船団に入れる船は設備投資で、審査の営業の現金収支にその時の代金を入れず、船を保つ
  買い替えの額を毎ターン引く（FX-84 (2)）。
- 船の借り（用途 ship）は 1 件もできない。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core.economy import banks
from hakoniwa_core.game import load_game
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import shipping_of, ships
from hakoniwa_core.units import QUANTITY_SCALE, money_of
from tests.core.test_finance import open_books
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


def test_freight_capital_cost_is_the_wear_of_the_ships_before_their_life(game, base):
    """運賃の資本費は、寿命の前の船の傷み（今の値段 ÷ 寿命）だけ。船の利子（FX-84）は 2026-11-22 に
    保留した（本人「とりあえず外そうか」。利子が運賃に乗って船を手放した島が戻らなかった）。"""
    world = copy.deepcopy(base)
    island = world.islands[1]
    config = game.config
    f = shipping_of(island, config)
    price = island.economy.prices[GOOD]
    life = config.get("trade.ship_life")
    turn = world.turn
    f.economy["fleet"] = [[turn, MILLI], [turn - life // 2, MILLI], [turn - life, MILLI]]
    assert ships.fleet_wear(island, config, f, turn) == round(2 * price / life)
    assert ships._ship_wear(island, config) == pytest.approx(price / life)


def test_ship_purchase_is_not_in_the_operating_cash_flow(game, base):
    """海運業が買って船団に入れる船は設備投資で、営業の現金収支から外す。"""
    from hakoniwa_core.economy import books
    from hakoniwa_core.modules.accounts import Accounts

    world = copy.deepcopy(base)
    island = world.islands[1]
    f = shipping_of(island, game.config)
    open_books(island)
    books.stock_moved(island, f.id, GOOD, MILLI)
    price = island.economy.prices[GOOD]
    assert Accounts._ship_capex(island, game.config) == {f.id: money_of(price, MILLI)}


def test_review_cash_flow_takes_the_ship_upkeep_every_turn(game, base, monkeypatch):
    """代金の代わりに、船を保つ買い替えの額（寿命の前の船 × 今の値段 ÷ 寿命）を毎ターン営業の
    現金収支から引く（維持の設備投資。掲示板 #1563）。寿命を過ぎた船は数えない。"""
    config = game.config
    world = copy.deepcopy(base)
    island = world.islands[1]
    f = shipping_of(island, config)
    price = island.economy.prices[GOOD]
    life = config.get("trade.ship_life")
    turn = world.turn
    f.economy["fleet"] = [[turn, 2 * MILLI], [turn - life, MILLI]]
    assert ships.fleet_wear(island, config, f, turn) == round(2 * price / life)
    # 1 ターン回して、引いた版と引かない版の審査の現金収支の差が傷みと同じ
    plain = copy.deepcopy(base)
    run(game, world, 1)
    monkeypatch.setattr(ships, "fleet_wear", lambda *a: 0)
    plain_f = shipping_of(plain.islands[1], config)
    plain_f.economy["fleet"] = [[turn, 2 * MILLI], [turn - life, MILLI]]
    run(game, plain, 1)
    got = plain_f.economy["ebitda"][-1] - shipping_of(island, config).economy["ebitda"][-1]
    want = round(2 * island.economy.prices[GOOD] / life)
    assert abs(got - want) <= 1


def test_ships_are_bought_with_owned_money_and_no_ship_loan_exists(game, base):
    """船を全部失って荷を残す海運業も、船の借りなしに手元と持ち主のお金で船を買う。"""
    world = copy.deepcopy(base)
    island = world.islands[1]
    config = game.config
    f = shipping_of(island, config)
    ships.lose_ships(island, config, f, ships.fleet(f))
    f.economy["ships_short"] = [1] * config.get("trade.cost_turns")
    run(game, world, 3)
    assert not [x for x in island.loans if x.purpose == "ship"]
    assert not hasattr(banks, "SHIP")
