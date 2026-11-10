"""二国間契約の不足分は、それぞれの契約で受け取れる値段まで買う。"""

from __future__ import annotations

from hakoniwa_core import ledger, stock, trade
from hakoniwa_core.economy import Payer, funds
from hakoniwa_core.modules.fiscal import Budget
from hakoniwa_core.modules.market import Market
from hakoniwa_core.trade import fx, traders
from hakoniwa_core.units import BASIS, money_of
from tests.core.test_recession_round1 import context
from tests.core.test_rules import game as game  # noqa: F401
from tests.core.test_warehouse_standing import island_for


def _contract(island, price, qty=1000, currency=None, **kw):
    return {
        "side": "sell",
        "active": True,
        "good": "coal",
        "qty": qty,
        "price": price,
        "currency": island.id if currency is None else currency,
        **kw,
    }


def test_contract_supply_uses_contract_prices_and_current_exchange_rate(game):
    island = island_for(game)
    island.economy.prices["coal"] = 10000
    foreign = island.id + 1
    fx.rates(island)[str(foreign)] = BASIS * 3 // 2
    trade.island_state(island)["contracts"] = [
        _contract(island, 123),
        _contract(island, 101, currency=foreign),
        _contract(island, 999, active=False),
        _contract(island, 999, side="buy"),
    ]
    assert traders.contract_supply_plan(island) == [
        ("coal", 1000, 123),
        ("coal", 1000, 151),
    ]
    fx.rates(island)[str(foreign)] = BASIS * 2
    assert traders.contract_supply_plan(island)[1] == ("coal", 1000, 202)


def test_contract_supply_allocates_stock_once_in_fulfilment_order(game):
    island = island_for(game)
    stock.add(island.warehouse, "coal", 1500, 0, game.defs.goods)
    trade.island_state(island)["contracts"] = [
        _contract(island, 100),
        _contract(island, 200),
        _contract(island, 300),
    ]
    assert traders.contract_supply_plan(island) == [
        ("coal", 500, 200),
        ("coal", 1000, 300),
    ]
    assert stock.total(island.warehouse, "coal") == 1500


def test_market_keeps_contract_limits_separate_and_budget_reserves_actual_cost(game):
    island = island_for(game)
    island.economy.prices["coal"] = 10000
    # 貿易の仕組みを置き終えた島（市場の段が商社・契約の注文を出す）
    trade.island_state(island)["since"] = 1
    trade.island_state(island)["contracts"] = [
        _contract(island, 100, qty=2000),
        _contract(island, 300, qty=1000),
    ]
    Budget().run_island(island, context(game))
    payer = Payer(ledger.GOV, traders.CONTRACT_SUPPLY)
    estimate = money_of(100, 2000) + money_of(300, 1000)
    assert funds.fiscal_state(island)["reserved"][traders.CONTRACT_SUPPLY] == estimate
    available = funds.available(island, payer)
    books = {}
    Market()._trade_orders(island, context(game), books, {}, 0)
    orders = [o for o, p in books["coal"].buys if p.payer == payer]
    assert [(o.quantity, o.limit) for o in orders] == [(2000, 100), (1000, 300)]
    assert funds.available(island, payer) == available - estimate


def test_goods_aid_does_not_bid_above_zero(game):
    island = island_for(game)
    trade.island_state(island)["contracts"] = [_contract(island, 0)]
    assert traders.contract_supply_plan(island) == [("coal", 1000, 0)]
