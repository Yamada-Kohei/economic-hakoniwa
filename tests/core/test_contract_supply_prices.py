"""二国間契約の不足分の国内での買い（FX-24。2026-11-12）。

上限は値幅の上端（国内の値段が契約の値段より高くても、売りがあれば損を出して買う）。指値は
売りの安い順に要る量まで（入札）で、政府の買いだけで値段を毎ターン押し上げない。
"""

from __future__ import annotations

from hakoniwa_core import ledger, stock, trade
from hakoniwa_core.economy import Payer, funds
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules.fiscal import Budget
from hakoniwa_core.modules.market import (
    CONTRACT_PRIORITY,
    HOUSEHOLD,
    MERCHANT,
    STORE,
    Book,
    Market,
    Party,
)
from hakoniwa_core.trade import traders
from hakoniwa_core.units import money_of
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


def _book(*asks, buys=()):
    """売りの指値と量の組（と、ほかの買い手の量と指値の組）から、財の注文の板を作る。"""
    book = Book()
    for limit, qty in asks:
        book.sells.append((Order(qty, limit), Party(MERCHANT, MERCHANT, Payer(ledger.CB))))
    for qty, limit in buys:
        book.buys.append((Order(qty, limit), Party(HOUSEHOLD, "hh:general", Payer(ledger.CB))))
    return book


def _contract_orders(books, payer):
    return [(o.quantity, o.limit) for o, p in books["coal"].buys if p.payer == payer]


def test_plan_caps_at_the_band_top_whatever_the_contract_price(game):
    island = island_for(game)
    island.economy.prices["coal"] = 10000
    stock.add(island.warehouse, "coal", 500, 0, game.defs.goods)
    trade.island_state(island)["contracts"] = [
        _contract(island, 100),
        _contract(island, 300),
        _contract(island, 999, active=False),
        _contract(island, 999, side="buy"),
    ]
    # 2 つの売りの契約の量から倉庫の分を引いた量を、今の値段の値幅の上端（+10%）まで
    assert traders.contract_supply_plan(island, context(game).config) == {"coal": (1500, 11000)}


def test_tender_bids_the_ask_that_covers_the_need():
    # 売りが余っている: 安い順に要る量まで（9500 の 600 と 10500 の 600 で 1000 に届く）
    book = _book((10500, 600), (9500, 600), (10800, 2000))
    assert Market._tender_limit(book, 1000, 9000, 11000) == 10500
    # 足りない: 上端以下の売りの一番高い指値（上端より高い売りは数えない）
    book = _book((9500, 300), (10200, 300), (12000, 5000))
    assert Market._tender_limit(book, 1000, 9000, 11000) == 10200
    # 指値なしの売りは、板寄せでの付け値（前の値段の値幅の下端）と見る
    assert Market._tender_limit(_book((None, 2000)), 1000, 9000, 11000) == 9000
    # 上端以下の売りがない・売りがない: 上端（前と同じ）
    assert Market._tender_limit(_book((12000, 5000)), 1000, 9000, 11000) == 11000
    assert Market._tender_limit(Book(), 1000, 9000, 11000) == 11000
    assert Market._tender_limit(None, 1000, 9000, 11000) == 11000


def test_tender_leaves_room_for_buyers_who_bid_higher():
    """見直し役の場面: 売り A 100@100・B 100@108、家計が 100@110。政府が A だけを見て 100 で出すと、
    高く買う家計に A を取られ、上端の中に B が売れ残っていても政府は買えない。"""
    book = _book((100, 100), (108, 100), buys=[(100, 110)])
    limit = Market._tender_limit(book, 100, 90, 110)
    assert limit == 108
    sells = [o for o, _ in book.sells]
    buys = [o for o, _ in book.buys] + [Order(100, limit, CONTRACT_PRIORITY)]
    result = auction.clear(buys, sells, 100, bounds=(1, 110))
    assert result.price == 108 and result.bought == [100, 100]


def test_tender_skips_the_states_own_and_clearance_sells():
    """国の倉庫の売り（倉庫の分は足りない量からもう引いてある）と値引きの売りは数えない。"""
    book = _book((105, 1000))
    book.sells.append((Order(600, 90), Party(STORE, ledger.GOV, Payer(ledger.GOV, "warehouse"))))
    book.sells.append((Order(600, 80), Party(MERCHANT, MERCHANT, Payer(ledger.CB), clearance=True)))
    assert Market._tender_limit(book, 400, 90, 110) == 105


def test_contract_buys_when_the_home_price_is_above_the_contract_price(game):
    """掲示板 #31: 国内の値段が契約の値段を少し上回ると、売りが余っていても買えなかった。"""
    island = island_for(game)
    island.economy.prices["coal"] = 10000
    trade.island_state(island)["since"] = 1  # 貿易の仕組みを置き終えた島
    trade.island_state(island)["contracts"] = [_contract(island, 9000, qty=2000)]
    Budget().run_island(island, context(game))
    payer = Payer(ledger.GOV, traders.CONTRACT_SUPPLY)
    # 取り置きは上限（値幅の上端）で見積もる
    assert funds.fiscal_state(island)["reserved"][traders.CONTRACT_SUPPLY] == money_of(11000, 2000)
    books = {"coal": _book((10100, 5000))}
    Market()._contract_orders(island, context(game), books, {})
    assert _contract_orders(books, payer) == [(2000, 10100)]
    assert not any(e.kind == "contract_supply_short" for e in island.log)


def test_short_supply_does_not_push_the_price_to_the_band_top():
    """売りが契約の量より少ないとき、上端で買うと約定の値段が毎ターン上端で決まる（石炭が数百倍に
    なった道筋）。入札の指値なら、約定は売りの指値どまり。"""
    sells = [Order(400, 9800), Order(600, 10000)]
    at_top = auction.clear([Order(3000, 11000, CONTRACT_PRIORITY)], sells, 10000)
    assert at_top.price == 11000
    limit = Market._tender_limit(_book((9800, 400), (10000, 600)), 3000, 9000, 11000)
    tender = auction.clear([Order(3000, limit, CONTRACT_PRIORITY)], sells, 10000)
    assert tender.price == 10000 and tender.volume == 1000


def test_goods_aid_is_bought_at_home_again(game):
    """物の援助（値段 0 の契約）も、倉庫に足りない分を国内で買う（11-10 から 11-12 は指値 0 で
    買えなかった）。"""
    island = island_for(game)
    island.economy.prices["coal"] = 10000
    trade.island_state(island)["since"] = 1
    trade.island_state(island)["contracts"] = [_contract(island, 0, aid=True)]
    Budget().run_island(island, context(game))
    books = {"coal": _book((10000, 5000))}
    Market()._contract_orders(island, context(game), books, {})
    assert _contract_orders(books, Payer(ledger.GOV, traders.CONTRACT_SUPPLY)) == [(1000, 10000)]


def test_official_prices_keep_the_band_top(game):
    """見直し役の場面: 計画経済の公定価格の財は板寄せの値幅が公定価格 1 点なので、国営施設の売り
    （指値 0）から入札の指値を作ると公定価格に届かず買えない。値段は動かないので前と同じ上端で。"""
    island = island_for(game)
    island.economy.prices["coal"] = 10000
    trade.island_state(island)["since"] = 1
    trade.island_state(island)["contracts"] = [_contract(island, 9000)]
    Budget().run_island(island, context(game))
    books = {"coal": _book((0, 5000))}
    Market()._contract_orders(island, context(game), books, {"coal": 10000})
    assert _contract_orders(books, Payer(ledger.GOV, traders.CONTRACT_SUPPLY)) == [(1000, 11000)]
