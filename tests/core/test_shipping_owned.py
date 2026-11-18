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


def test_freight_capital_cost_includes_the_interest_on_the_book_value_of_the_ship(game, base):
    """運賃の資本費の船の利子は、償却した後の帳簿価額（新品の値段 ×（1 − 年 ÷ 寿命））にかかる。
    寿命を過ぎた船は数えない。年がわからない見積もりは一生の平均（新品の半分）で見る（FX-83）。"""
    world = copy.deepcopy(base)
    island = world.islands[1]
    config = game.config
    f = shipping_of(island, config)
    price = island.economy.prices[GOOD]
    life = config.get("trade.ship_life")
    rate = config.get("market.initial_prices.capital_rate") / TURNS_PER_YEAR
    turn = world.turn
    f.economy["fleet"] = [[turn, MILLI]]
    new = price / life + price * rate
    assert ships.fleet_capital(island, config, f, turn) == pytest.approx(new)
    f.economy["fleet"] = [[turn - life // 2, MILLI]]
    half = price / life + price * rate * (1 - (life // 2) / life)
    assert ships.fleet_capital(island, config, f, turn) == pytest.approx(half)
    f.economy["fleet"] = [[turn - life, MILLI]]
    assert ships.fleet_capital(island, config, f, turn) == 0
    want = price / life + price / 2 * rate
    assert ships._ship_wear(island, config) == pytest.approx(want)


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


def test_shipping_reopens_after_entry_turns_and_inherits_the_island_short_record(
    game, base, monkeypatch
):
    """海運業がなくなった島に、商社の輸入の見込みがあれば trade.entry_turns ターン後に海運業を開き
    直し、会社がいない間に島で取り続けた積み残しの記録を引き継ぐ。荷を残していれば最初の 1 隻を
    買いに出る（船の借りではなく、開業の資金と持ち主のお金で）。"""
    monkeypatch.setattr(setup, "_owner_spare", lambda *a: 10**15)  # 持ち主は出せる
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


def test_shipping_reopens_only_when_its_owner_can_fund_a_ship_and_its_running(
    game, base, monkeypatch
):
    """開き直すのは、持ち主が船 1 隻とその船を trade.shipping_capital_turns ターン走らせる運航費と
    利子を出せるときだけ。出せなければ国営の待ちを過ぎても開かない。開けば、その額を出資する（FX-84）。"""
    from hakoniwa_core import ledger
    from hakoniwa_core.economy import facility_entity

    world = copy.deepcopy(base)
    island = world.islands[1]
    config = game.config
    defs = game.defs
    island.facilities.remove(shipping_of(island, config))
    for t in traders_of(island, config, world.turn):
        traders.state_of(t)[traders.IMPORT_NEED] = {"grain": MILLI}
    need = setup.shipping_capital(island, config)
    price = island.economy.prices[GOOD]
    run_cost = ships._ship_run_cost(island, config)
    assert need > price + run_cost * config.get("trade.shipping_capital_turns")
    turn = world.turn
    wait = config.get("trade.entry_turns")
    real = setup._owner_spare
    monkeypatch.setattr(setup, "_owner_spare", lambda *a: need - 1)
    assert setup.reopen_shipping(island, defs, config, turn) is None
    assert setup.reopen_shipping(island, defs, config, turn + 3 * wait) is None
    monkeypatch.setattr(setup, "_owner_spare", lambda *a: need)
    before = {o: real(island, defs, config, o) for o in (setup.OWNER_PRIVATE, setup.OWNER_STATE)}
    opened = setup.reopen_shipping(island, defs, config, turn + 3 * wait)
    assert opened is not None
    assert ledger.money(island, facility_entity(opened.id)) == min(need, before[opened.owner])


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
