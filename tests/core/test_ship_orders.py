"""船の前注文（FX-60。2026-11-10、W-244）: 島に造船所があれば、海運業は船を市場で買う代わりに
造船所に前もって注文し、手付け・建造の間・引き渡しに分けて払う。届かない注文は取り消して前払いを
返し、海運業が倒産したら前払いは返らない（hakoniwa_core/trade/orders.py）。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import SEIZED, arrears, facility_entity
from hakoniwa_core.game import load_game
from hakoniwa_core.modules.failures import bankrupt
from hakoniwa_core.state import ACCOUNTING_SELF, OWNER_PRIVATE, Facility
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import orders, shipping_of, ships
from hakoniwa_core.units import QUANTITY_SCALE, money_of
from tests.core.test_finance import context
from tests.core.test_trade import run

MILLI = QUANTITY_SCALE


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """貿易の始まった2島（2ターン回した）。試験ごとに写す。"""
    world = generate_world(7, 2, game.config, game.defs)
    run(game, world, 2)
    return world


def _setup(game, base):
    """島1に民営の造船所を足し、海運業に船が足りない記録（1.5 隻分）とお金を持たせる。"""
    world = copy.deepcopy(base)
    island = world.islands[1]
    config = game.config
    f = shipping_of(island, config)
    used = {(x.x, x.y) for x in island.facilities}
    x, y = next(
        (t.x, t.y)
        for t in island.tiles
        if (t.x, t.y) not in used and t.terrain in game.defs.facilities["shipyard"]["terrain"]
    )
    number = max(x.id for x in island.facilities) + 1
    yard = Facility(
        id=number,
        kind="shipyard",
        x=x,
        y=y,
        owner=OWNER_PRIVATE,
        accounting=ACCOUNTING_SELF,
        built=True,
    )
    island.facilities.append(yard)
    turns = config.get("trade.cost_turns")
    f.economy["ships_short"] = [1] * turns
    f.economy[orders.SHORT_LOAD] = [config.get("trade.ship_capacity") * 1500] * turns
    f.stock.pop(config.get("trade.ship_good"), None)
    price = island.economy.prices[config.get("trade.ship_good")]
    ledger.pay(
        island, ledger.GOV, facility_entity(f.id), money_of(price, 3 * MILLI), "capital_injection"
    )
    return world, island, f, yard


def _money(island, f):
    return ledger.money(island, facility_entity(f.id))


def test_shipping_orders_whole_ships_from_the_island_yard_and_pays_a_deposit(game, base):
    world, island, f, yard = _setup(game, base)
    config = game.config
    price = island.economy.prices[config.get("trade.ship_good")]
    before = _money(island, f)
    placed = orders.place(island, game.defs, config, f, world.turn + 1)
    assert placed == 2 * MILLI  # 1.5 隻分の不足を1隻単位に切り上げる
    (order,) = yard.economy[orders.ORDERS]
    value = money_of(price, 2 * MILLI)
    assert order["paid"] == round(value * config.get("trade.order_deposit"))
    assert before - _money(island, f) == order["paid"]
    # 全力で1ターンに 1/4 隻なので、2隻は8ターン後に届く見込み
    assert order["due"] == world.turn + 1 + 8
    # 注文済みの船は二重に頼まない
    assert orders.place(island, game.defs, config, f, world.turn + 1) == 0
    assert orders.on_order(island, f) == 2 * MILLI


def test_without_a_yard_shipping_buys_on_the_market(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    f = shipping_of(island, game.config)
    assert not orders.yards(island, game.defs, game.config)
    assert orders.place(island, game.defs, game.config, f, world.turn + 1) == -1


def test_yard_sets_ships_aside_takes_progress_payments_and_delivers(game, base):
    world, island, f, yard = _setup(game, base)
    config = game.config
    good = config.get("trade.ship_good")
    turn = world.turn + 1
    orders.place(island, game.defs, config, f, turn)
    (order,) = yard.economy[orders.ORDERS]
    value = money_of(order["price"], order["qty"])
    stock.add(yard.stock, good, MILLI, turn, game.defs.goods)  # 1隻作った
    orders.run(island, game.defs, config, turn + 1)
    assert order["built"] == MILLI
    share = config.get("trade.order_deposit") + config.get("trade.order_progress") / 2
    assert order["paid"] == round(value * share)
    assert orders.reserved(config, yard, good) == MILLI  # 取り置いた船は売りに出さない
    # 次のターンに払う見込み（造船所が全力で作ったときの建造の間の代金）を次の支払いに入れる
    assert f.economy[orders.DUE] > 0
    stock.add(yard.stock, good, MILLI, turn + 1, game.defs.goods)
    orders.run(island, game.defs, config, turn + 2)
    assert orders.ORDERS not in yard.economy  # 引き渡した
    assert stock.total(f.stock, good) == 2 * MILLI
    assert stock.total(yard.stock, good) == 0
    assert "ships_short" not in f.economy  # 足りなかった記録は数え直す


def test_late_order_is_cancelled_and_the_prepayment_is_refunded(game, base):
    world, island, f, yard = _setup(game, base)
    config = game.config
    turn = world.turn + 1
    orders.place(island, game.defs, config, f, turn)
    (order,) = yard.economy[orders.ORDERS]
    paid = order["paid"]
    late = order["due"] + config.get("trade.order_late_turns") + 1
    # 造船所の預金で返しきれない分は造船所の借りに残り、倒産すると請求（貸し手と同じ順）になる
    yard_entity = facility_entity(yard.id)
    held = ledger.money(island, yard_entity)
    ledger.pay(island, yard_entity, ledger.GOV, held - paid // 2, "equity_return")
    before = _money(island, f)
    orders.run(island, game.defs, config, late)
    assert orders.ORDERS not in yard.economy
    assert _money(island, f) - before == paid // 2
    assert yard.economy[orders.REFUNDS] == {facility_entity(f.id): paid - paid // 2}
    bankrupt(island, context(game, world), yard)
    claims = yard.economy[SEIZED]["claims"]
    assert claims[arrears.ORDERS] == {facility_entity(f.id): paid - paid // 2}
    assert orders.REFUNDS not in yard.economy
    # 設備が売れたときの代金は、貸し手と同じ順で額の割合に分けて返す
    left = arrears.pay_claims(
        island,
        ledger.GOV,
        paid,
        {arrears.LOANS: {}, arrears.ORDERS: dict(claims[arrears.ORDERS])},
        lambda _bank, _value: None,
    )
    assert left == paid - (paid - paid // 2)


def test_order_of_a_bankrupt_shipping_line_is_cancelled_without_refund(game, base):
    world, island, f, yard = _setup(game, base)
    config = game.config
    good = config.get("trade.ship_good")
    turn = world.turn + 1
    orders.place(island, game.defs, config, f, turn)
    stock.add(yard.stock, good, MILLI, turn, game.defs.goods)
    orders.run(island, game.defs, config, turn + 1)
    assert orders.reserved(config, yard, good) == MILLI
    yard_money = ledger.money(island, facility_entity(yard.id))
    f.economy[SEIZED] = {"bank": None, "since": turn + 1, "claims": {}}
    orders.run(island, game.defs, config, turn + 2)
    assert orders.ORDERS not in yard.economy
    assert orders.reserved(config, yard, good) == 0  # 作った船は造船所が市場で売る
    assert ledger.money(island, facility_entity(yard.id)) == yard_money  # 前払いは返らない


def test_yard_adds_the_order_backlog_to_its_operating_target(game, base):
    world, island, f, yard = _setup(game, base)
    config = game.config
    orders.place(island, game.defs, config, f, world.turn + 1)
    limit = round(config.get("market.max_target_change") * fac.FULL)
    goal = orders.production_goal(game.defs, config, yard, 0, 0)
    assert goal == min(fac.FULL, limit)
    assert orders.production_goal(game.defs, config, yard, 300, fac.FULL) == fac.FULL


def test_turns_run_with_orders_and_the_books_balance(game, base):
    """前注文の代金・取り置き・引き渡しを含めてターンが回り、帳簿の確かめ（記録の段）が通る。"""
    world, island, f, yard = _setup(game, base)
    config = game.config
    good = config.get("trade.ship_good")
    run(game, world, 1)
    f = shipping_of(island, config)
    yard = island.facility(yard.id)
    assert f.economy.get("ships_wanted", 0) == 0  # 市場では買わない
    assert yard.economy.get(orders.ORDERS)
    qty = sum(o["qty"] for o in yard.economy[orders.ORDERS])
    stock.add(yard.stock, good, qty, world.turn, game.defs.goods)
    fleet = ships.fleet(f)
    run(game, world, 2)
    f = shipping_of(island, config)
    assert ships.fleet(f) >= fleet + qty - MILLI  # 届いた船は船団に入る（寿命で消えた船を除く）
