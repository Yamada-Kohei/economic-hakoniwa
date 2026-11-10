"""報告 #9: 閉じた施設から引き取った在庫と国の倉庫の決まった買い（FS-30）、売りの契約で倉庫に
足りない分の買いの取り置き（FX-24・FS-09）。"""

from __future__ import annotations

import copy

from hakoniwa_core import ledger, stock, trade
from hakoniwa_core.economy import Payer, funds
from hakoniwa_core.modules.firms import take_over_stock
from hakoniwa_core.modules.fiscal import Budget
from hakoniwa_core.modules.market import Market
from hakoniwa_core.trade import traders
from tests.core.test_finance import context as world_context
from tests.core.test_finance import island_of
from tests.core.test_finance import warmed as warmed  # noqa: F401
from tests.core.test_recession_round1 import context
from tests.core.test_rules import add
from tests.core.test_rules import game as game  # noqa: F401
from tests.core.test_warehouse_standing import island_for, orders, put


def _close(game, island, good, qty):
    mine = add(island, "mine", 6, 5, recipe="coal", stock={good: qty})
    take_over_stock(island, context(game), mine)


def test_take_over_keeps_what_the_reserve_lacks_and_does_not_pause_buys(game):
    island = island_for(game)
    stock.add(island.warehouse, "coal", 3000, 0, game.defs.goods)
    item = put(game, island, "coal", "reserve", quantity=10, limit=100, release=200)
    island.economy.prices["coal"] = 100
    _close(game, island, "coal", 5000)
    assert stock.total(island.warehouse, "coal") == 8000
    assert trade.to_market(island).get("coal", 0) == 0
    book = orders(game, island)["coal"]
    assert book.buys[0][0].quantity == 2000
    assert item["last"].get("paused") is None


def test_take_over_sells_only_beyond_the_reserve_target(game):
    island = island_for(game)
    stock.add(island.warehouse, "coal", 8000, 0, game.defs.goods)
    put(game, island, "coal", "reserve", quantity=10, limit=100, release=200)
    _close(game, island, "coal", 5000)
    assert trade.to_market(island)["coal"] == 3000


def test_take_over_keeps_everything_for_a_standing_buy(game):
    island = island_for(game)
    put(game, island, "coal", "buy", quantity=1, limit=100)
    _close(game, island, "coal", 5000)
    assert trade.to_market(island).get("coal", 0) == 0


def test_take_over_without_setting_sells_everything(game):
    island = island_for(game)
    _close(game, island, "coal", 5000)
    assert trade.to_market(island)["coal"] == 5000


def test_take_over_keeps_two_turns_of_sell_contracts(game):
    island = island_for(game)
    state = trade.island_state(island)
    state["contracts"] = [{"side": "sell", "active": True, "good": "coal", "qty": 1000}]
    stock.add(island.warehouse, "coal", 500, 0, game.defs.goods)
    _close(game, island, "coal", 5000)
    assert trade.to_market(island)["coal"] == 5000 - 1500


def _contract_island(world, good, qty):
    island = island_of(world, "market")
    state = trade.island_state(island)
    state["contracts"] = [
        {
            "side": "sell",
            "active": True,
            "good": good,
            "qty": qty,
            "id": 99,
            "partner": 1,
            "price": island.economy.prices[good],
            "currency": island.id,
        }
    ]
    island.warehouse.pop(good, None)
    return island


def _contract_buy(game, world, island, good):
    books: dict = {}
    Market()._trade_orders(island, world_context(game, world), books, {}, 0)
    buys = books[good].buys
    return sum(o.quantity for o, p in buys if p.payer.item == traders.CONTRACT_SUPPLY)


def test_contract_supply_is_reserved_before_large_budgets(game, warmed):
    world = copy.deepcopy(warmed)
    good = "steel"
    island = _contract_island(world, good, 2000)
    # 年の予算を大きく決め、国庫をほぼ取り置きで埋める
    for item in island.policy.budget:
        island.policy.budget[item] = 10**15
    Budget().run_island(island, world_context(game, world))
    assert funds.available(island, Payer(ledger.GOV, traders.CONTRACT_SUPPLY)) > 0
    assert _contract_buy(game, world, island, good) == 2000
    assert not any(e.kind == "contract_supply_short" for e in island.log)


def test_contract_supply_cut_by_treasury_is_logged(game, warmed):
    world = copy.deepcopy(warmed)
    good = "steel"
    island = _contract_island(world, good, 2000)
    Budget().run_island(island, world_context(game, world))
    state = funds.fiscal_state(island)
    state["reserved"][traders.CONTRACT_SUPPLY] = 0
    spendable = funds.spendable(island)
    state["reserved"]["civil_servants"] = state["reserved"].get("civil_servants", 0) + spendable
    bought = _contract_buy(game, world, island, good)
    assert bought < 2000
    entry = next(e for e in island.log if e.kind == "contract_supply_short")
    assert entry.args["good"] == good and entry.args["amount"] == 2.0
