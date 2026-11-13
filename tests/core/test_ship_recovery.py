"""船が寿命で減って戻らない弱さの直し（2026-11-13。設計書タブ 9 FX-78〜FX-80）。

- 島に造船所がない海運業は、圏のほかの島の造船所に決済通貨で前注文する（FX-78。trade/orders.py）
- 寿命を過ぎた船は、代わりの船が足りるまで使い続ける（FX-79。trade/ships.py の scrap）
- 目標の船団（直近に使った船 ÷ 目標の混み具合）に足りない分を買う（FX-80。ships.renewal）
- 標準の計画は、圏の船の寿命に圏の造船所が間に合わなければ造船所を足す（autoplan.shipyard_islands）
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger, stock
from hakoniwa_core.autoplan import shipyard_islands
from hakoniwa_core.economy import facility_entity
from hakoniwa_core.game import load_game
from hakoniwa_core.state import ACCOUNTING_SELF, OWNER_PRIVATE, Facility
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import balance, blocs, money, orders, shipping_of, ships
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.units import QUANTITY_SCALE, money_of
from tests.core.test_phase3_3 import run_checked
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


def _setup(game, base, holdings=3):
    """島2（決済通貨の島）が作った圏に島1が入り、島2に民営の造船所、島1の海運業は船が足りない
    記録（1.5 隻分）と、決済通貨（島2の通貨）を船 holdings 隻分持つ。"""
    world = copy.deepcopy(base)
    config = game.config
    buyer_island, yard_island = world.islands[1], world.islands[2]
    blocs.create(world, yard_island, "test", True, world.turn)
    blocs.join(world, buyer_island, blocs.blocs(world)[0])
    fxrates.ensure(world, buyer_island, yard_island.id)
    fxrates.ensure(world, yard_island, buyer_island.id)
    yard = _yard_on(game, yard_island)
    f = shipping_of(buyer_island, config)
    turns = config.get("trade.cost_turns")
    f.economy["ships_short"] = [1] * turns
    f.economy[orders.SHORT_LOAD] = [config.get("trade.ship_capacity") * 1500] * turns
    f.stock.pop(GOOD, None)
    if holdings:
        amount = money_of(yard_island.economy.prices[GOOD], holdings * MILLI)
        entity = facility_entity(f.id)
        ledger.pay(yard_island, ledger.GOV, ledger.nonresident(1, entity), amount, "grant")
        money._add(buyer_island, entity, yard_island.id, amount, amount)
    return world, buyer_island, yard_island, f, yard


def _step(game, world, turn):
    plan = orders.plan_abroad(world, game.defs, game.config, turn, [1, 2])
    orders.run_abroad(world, game.defs, game.config, turn, [1, 2], plan)
    return plan


# --- 圏の造船所への前注文（FX-78） -----------------------------------------------------


def test_shipping_without_a_yard_orders_from_a_bloc_yard_in_the_settlement_currency(game, base):
    world, buyer_island, yard_island, f, yard = _setup(game, base)
    config = game.config
    turn = world.turn + 1
    plan = orders.plan_abroad(world, game.defs, config, turn, [1, 2])
    entity = facility_entity(f.id)
    price = yard_island.economy.prices[GOOD]
    deposit = round(money_of(price, 2 * MILLI) * config.get("trade.order_deposit"))
    # 為替の段で買う外貨の見込み（手付け）
    needs: dict = {}
    orders.fx_needs(buyer_island, plan, needs, {})
    assert needs == {entity: {yard_island.id: [(deposit, None)]}}
    assert f.economy[ships.REACH]  # 圏の造船所に頼めるので、市場では買わない
    assert ships.ships_wanted(config, f, turn) == 0
    held = money.holding(buyer_island, entity, yard_island.id)
    orders.run_abroad(world, game.defs, config, turn, [1, 2], plan)
    (o,) = yard.economy[orders.ORDERS]
    assert orders.is_abroad(o) and o["qty"] == 2 * MILLI and o["currency"] == yard_island.id
    assert o["paid"] == deposit
    assert held - money.holding(buyer_island, entity, yard_island.id) == deposit
    assert f.economy[ships.ABROAD] == 2 * MILLI
    assert money.reconcile(world) == []
    # 前払いは金融の動き（経常収支は動かない）
    for island in (buyer_island, yard_island):
        assert balance.totals(island.scratch["bop"]) == (0, 0)
    assert buyer_island.scratch["bop"]["assets.orders"] == deposit
    # 注文済みの船は二重に頼まない
    assert orders.order_wanted(buyer_island, config, f, turn) == 0
    assert orders.plan_abroad(world, game.defs, config, turn, [1, 2])["new"] == {}


def test_bloc_yard_order_is_paid_delivered_and_booked_as_an_export(game, base):
    world, buyer_island, yard_island, f, yard = _setup(game, base)
    config, defs = game.config, game.defs
    turn = world.turn + 1
    _step(game, world, turn)
    (o,) = yard.economy[orders.ORDERS]
    value = money_of(o["price"], o["qty"])
    stock.add(yard.stock, GOOD, 2 * MILLI, turn, defs.goods)  # 2隻作った
    orders.run(yard_island, defs, config, turn + 1)  # 市場の段: 取り置き
    assert o["built"] == 2 * MILLI and orders.reserved(config, yard, GOOD) == 2 * MILLI
    for island in (buyer_island, yard_island):
        island.scratch.pop("bop", None)
    _step(game, world, turn + 1)
    assert orders.ORDERS not in yard.economy
    assert stock.total(f.stock, GOOD) == 2 * MILLI and stock.total(yard.stock, GOOD) == 0
    assert f.economy[ships.PAID] == [2 * MILLI, value]
    assert money.reconcile(world) == []
    # 引き渡しで財の輸出入に振り替える（経常収支 = 金融収支）
    assert yard_island.scratch["bop"]["goods"] == value
    assert buyer_island.scratch["bop"]["goods"] == -value
    for island in (buyer_island, yard_island):
        current, financial = balance.totals(island.scratch["bop"])
        assert current == financial
    # 届いた船の設備の額は払った額
    capital = f.economy.get("capital", 0)
    ships.commission(buyer_island, defs, config, turn + 2)
    assert f.economy["capital"] - capital == value
    assert ships.PAID not in f.economy


def test_without_foreign_money_the_order_waits_and_the_deposit_is_counted_as_due(game, base):
    world, buyer_island, yard_island, f, yard = _setup(game, base, holdings=0)
    _step(game, world, world.turn + 1)
    assert orders.ORDERS not in yard.economy
    price = yard_island.economy.prices[GOOD]
    deposit = round(money_of(price, 2 * MILLI) * game.config.get("trade.order_deposit"))
    expected = fxrates.to_own(buyer_island, yard_island.id, deposit)
    assert abs(f.economy[orders.DUE] - expected) <= 2  # 運転資金を借りる見込みに入れる


def test_embargo_cancels_the_bloc_order_and_the_refund_reaches_the_buyer_next_turn(game, base):
    world, buyer_island, yard_island, f, yard = _setup(game, base)
    turn = world.turn + 1
    _step(game, world, turn)
    (o,) = yard.economy[orders.ORDERS]
    paid = o["paid"]
    entity = facility_entity(f.id)
    held = money.holding(buyer_island, entity, yard_island.id)
    buyer_island.policy.embargoes[str(yard_island.id)] = [GOOD]
    _step(game, world, turn + 1)
    assert orders.ORDERS not in yard.economy
    # 返金は造船所の島の通貨で非居住者の預金へ。持ち高に入るまでは世界の確かめで数える
    assert money.reconcile(world) == []
    assert money.holding(buyer_island, entity, yard_island.id) == held
    money.settle_pending(world)
    assert money.holding(buyer_island, entity, yard_island.id) == held + paid
    assert money.reconcile(world) == []
    # 禁輸の間は新しい注文を出さない
    assert _step(game, world, turn + 2)["new"] == {}
    assert not f.economy.get(ships.REACH)


def test_yard_builds_island_orders_before_bloc_orders(game, base):
    world, buyer_island, yard_island, f, yard = _setup(game, base)
    config, defs = game.config, game.defs
    turn = world.turn + 1
    _step(game, world, turn)
    home = shipping_of(yard_island, config)
    yard.economy[orders.ORDERS].append(
        {
            "buyer": home.id,
            "payer": "fac:999999",  # 払うお金のない買い手（引き渡さずに取り置きだけ見る）
            "item": "",
            "qty": MILLI,
            "built": 0,
            "price": 10**9,
            "paid": 0,
            "placed": turn,
            "due": turn + 4,
        }
    )
    stock.add(yard.stock, GOOD, MILLI, turn, defs.goods)
    orders.run(yard_island, defs, config, turn + 1)
    abroad, mine = yard.economy[orders.ORDERS]
    assert mine["built"] == MILLI and abroad["built"] == 0


def test_turns_run_with_a_bloc_order_and_the_books_balance(game, base):
    """圏の造船所への注文・払い・引き渡しを含めてターンが回り、帳簿と島をまたぐお金の突き合わせ、
    国際収支の釣り合いが毎ターン通る。"""
    world, buyer_island, yard_island, f, yard = _setup(game, base)
    run_checked(game, world, 1)
    yard = yard_island.facility(yard.id)
    placed = [o for o in yard.economy.get(orders.ORDERS, []) if orders.is_abroad(o)]
    assert placed
    stock.add(yard.stock, GOOD, sum(o["qty"] for o in placed), world.turn, game.defs.goods)
    run_checked(game, world, 3)
    f = shipping_of(buyer_island, game.config)
    assert not any(orders.is_abroad(o) for o in yard.economy.get(orders.ORDERS, []))
    assert ships.fleet(f) >= sum(o["qty"] for o in placed)


# --- 寿命を過ぎた船（FX-79） ----------------------------------------------------------


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
