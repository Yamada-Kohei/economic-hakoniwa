"""所有の船だけで海運業が成り立つこと（W-281 の残り。掲示板 #1438。船の長い借りは採らなかった）。

- 運賃の資本費に船の利子を入れる（FX-83）。
- 海運業が買って船団に入れる船は設備投資で、営業の現金収支に入れない。
- 海運業がなくなった島に開き直す。会社がいない間も島で積み残しの記録を取り続けて引き継ぐ（FX-84）。
- 船の借り（用途 ship）は 1 件もできない。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.economy import banks
from hakoniwa_core.game import load_game
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import island_state, setup, shipping_of, ships, traders, traders_of
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


def test_freight_capital_cost_includes_the_interest_on_the_ship(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    config = game.config
    price = island.economy.prices[GOOD]
    rate = config.get("market.initial_prices.capital_rate") / TURNS_PER_YEAR
    want = price / config.get("trade.ship_life") + price * rate
    assert ships._ship_wear(island, config) == pytest.approx(want)
    assert ships._ship_wear(island, config) > price / config.get("trade.ship_life")


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


def test_shipping_reopens_after_entry_turns_and_inherits_the_island_short_record(game, base):
    """海運業がなくなった島に、商社の輸入の見込みがあれば trade.entry_turns ターン後に海運業を開き
    直し、会社がいない間に島で取り続けた積み残しの記録を引き継ぐ。荷を残していれば最初の 1 隻を
    買いに出る（船の借りではなく、開業の資金と持ち主のお金で）。"""
    world = copy.deepcopy(base)
    island = world.islands[1]
    config = game.config
    defs = game.defs
    island.facilities.remove(shipping_of(island, config))
    for t in traders_of(island, config, world.turn):
        traders.state_of(t)[traders.IMPORT_NEED] = {"grain": MILLI}
    keep = config.get("trade.cost_turns")
    island_state(island)[ships.SHORT_KEPT] = {
        "ships_short": [1] * keep,
        ships.SHORT_LOAD: [0] * keep,
    }
    turn = world.turn
    wait = config.get("trade.entry_turns")
    assert setup.reopen_shipping(island, defs, config, turn) is None  # 待ち始め
    assert setup.reopen_shipping(island, defs, config, turn + wait - 1) is None
    opened = setup.reopen_shipping(island, defs, config, turn + 2 * wait)
    assert opened is not None and shipping_of(island, config) is opened
    assert opened.economy["ships_short"] == [1] * keep
    assert ships.ships_wanted(config, opened, turn + 2 * wait) >= MILLI
    assert setup.reopen_shipping(island, defs, config, turn + 3 * wait) is None  # いれば開かない


def test_island_keeps_the_short_record_while_there_is_no_shipping(game, base):
    """海運業がいない間も、貿易の段は島に積み残しの記録（同じ定義）を取り続ける。"""
    world = copy.deepcopy(base)
    island = world.islands[1]
    f = shipping_of(island, game.config)
    island_state(island)[ships.SHORT_KEPT] = {"ships_short": [1, 1], ships.SHORT_LOAD: [5, 5]}
    island.facilities.remove(f)
    run(game, world, 1)
    kept = island_state(island)[ships.SHORT_KEPT]
    assert len(kept["ships_short"]) == 3 and kept["ships_short"][:2] == [1, 1]
    assert len(kept[ships.SHORT_LOAD]) == 3


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
