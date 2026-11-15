"""船の借り（W-281。2026-11-15。束 C1。economy/ship_loans.py）。

海運業は船の代金の loans.ship_ltv までを船を担保に loans.ship_turns ターンで借りる。審査は
買い替え loans.ship_replace_coverage・買い増し loans.coverage。枠は銀行の段で決め、お金は市場で船の
買い注文を出す直前に貸し、買えなかった分はそのターンのうちに返す。前注文の借りは引き渡しまで利子
だけ。沈んでも借りは残り、担保の不足を数える。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import banks, facility_entity, ship_loans
from hakoniwa_core.economy.start import next_payments, ship_payments
from hakoniwa_core.game import load_game
from hakoniwa_core.modules.market import Party
from hakoniwa_core.state import ACCOUNTING_SELF, OWNER_PRIVATE, OWNER_STATE, Facility
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import orders, shipping_of, ships
from hakoniwa_core.units import QUANTITY_SCALE, money_of
from tests.core.test_finance import context, open_books
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


def _setup(game, base, earnings_ships=10.0):
    """島1の海運業（国営にして大口融資の規制を外す）に、船 earnings_ships 隻分の値段の稼ぎの記録と
    買いたい船を持たせる。銀行は新しく貸せる状態にする。"""
    world = copy.deepcopy(base)
    island = world.islands[1]
    config = game.config
    f = shipping_of(island, config)
    f.owner = OWNER_STATE
    for bank in banks.bank_list(island):
        banks.facility_of(island, bank).economy.pop("crunch_until", None)
    price = island.economy.prices[GOOD]
    turns = config.get("firms.ebitda_turns")
    f.economy["ebitda"] = [int(price * earnings_ships)] * turns
    f.stock.pop(GOOD, None)
    return world, island, f


def _ctx(game, world, stage):
    return context(game, world, stage)


def _ship_loans(island, f):
    return ship_loans.ship_loans_of(island, facility_entity(f.id))


def test_ship_loan_is_repaid_over_ship_turns_in_equal_installments(game, base):
    world, island, f = _setup(game, base)
    config = game.config
    bank = banks.active_banks(island)[0]
    entity = facility_entity(f.id)
    turn = world.turn + 1
    term = config.get("loans.ship_turns")
    loan = banks.lend(island, bank, entity, 1_200_000, banks.SHIP, term, turn, 500, turn + 1)
    assert loan.term == 240
    assert loan.installment == 5_000  # 元金は毎ターン同じ額（最後にまとめて返さない）
    # 次のターンから元金を返す（審査の返済の見込みに元金が入る）
    assert banks.debt_service(island, entity, turn) >= 5_000
    # 返し始めた船の借りは、同じ銀行の船の借りに足して1件にまとめる
    again = banks.lend(island, bank, entity, 1_200_000, banks.SHIP, term, turn, 500, turn + 1)
    assert again is loan and banks.outstanding(island, loan) == 2_400_000
    assert loan.installment == 10_000


def test_ship_price_is_not_in_working_capital_only_the_down_payment(game, base):
    world, island, f = _setup(game, base)
    config = game.config
    f.economy["ships_wanted"] = 2 * MILLI
    f.economy["ships_replace"] = 2 * MILLI
    ctx = _ctx(game, world, "banking")
    other = next_payments(island, game.defs, config, f) - ship_payments(config, f)
    ship_loans.arrange(island, ctx, f, other)
    credit = f.economy[ship_loans.CREDIT]
    value = money_of(ship_loans.unit_value(island, config) // 1, 2 * MILLI)
    assert credit["qty"] == 2 * MILLI
    assert credit["loan"] == pytest.approx(value * config.get("loans.ship_ltv"), rel=1e-6)
    # 次の支払いに入る船の代金は頭金だけ（8 割の借りは運転資金の審査に入らない）
    assert ship_payments(config, f) == credit["down"] + credit["due"]
    assert credit["down"] == pytest.approx(value * (1 - config.get("loans.ship_ltv")), rel=1e-6)
    assert next_payments(island, game.defs, config, f) - other == credit["down"]


def test_replacement_failing_coverage_is_bought_with_the_owners_money(game, base):
    world, island, f = _setup(game, base, earnings_ships=0.0)
    config = game.config
    f.economy["ships_wanted"] = MILLI
    f.economy["ships_replace"] = MILLI
    ctx = _ctx(game, world, "banking")
    review = ship_loans.Review(island, ctx, f, ship_loans.unit_value(island, config))
    # 返済の見込みには、今ある借りの返済と今回の元金と利子が入る
    loan = review.loan(MILLI)
    assert not review.passes(MILLI, config.get("loans.ship_replace_coverage"), False)
    assert review.base == banks.debt_service(island, facility_entity(f.id), ctx.turn)
    assert loan > 0
    gov = ledger.money(island, ledger.GOV)
    ship_loans.arrange(island, ctx, f, 0)
    credit = f.economy[ship_loans.CREDIT]
    # 借りずに、持ち主（国）の出資で買う（国の出せる分まで）
    assert 0 < credit["qty"] <= MILLI and credit["loan"] == 0
    assert credit["down"] == credit["value"]
    assert ledger.money(island, ledger.GOV) < gov
    assert island.stats["ship_loans.refused_replace"] == 1


def test_expansion_failing_coverage_is_not_bought(game, base):
    world, island, f = _setup(game, base, earnings_ships=0.0)
    f.economy["ships_wanted"] = 2 * MILLI
    f.economy["ships_replace"] = 0
    ship_loans.arrange(island, _ctx(game, world, "banking"), f, 0)
    assert ship_loans.CREDIT not in f.economy  # 持ち主の出資で無理に買わない
    assert island.stats["ship_loans.refused_expand"] == 2
    # 稼ぎがあれば通る
    world, island, f = _setup(game, base, earnings_ships=10.0)
    f.economy["ships_wanted"] = 2 * MILLI
    f.economy["ships_replace"] = 0
    ship_loans.arrange(island, _ctx(game, world, "banking"), f, 0)
    # 頭金は手元と企業の預金からだけ（持ち主は買い増しの頭金を出さない）。手元が足りなければ減らす
    small = f.economy[ship_loans.CREDIT]["qty"]
    assert 0 < small < 2 * MILLI
    world, island, f = _setup(game, base, earnings_ships=10.0)
    f.economy["ships_wanted"] = 2 * MILLI
    f.economy["ships_replace"] = 0
    value = ship_loans.unit_value(island, game.config)
    ledger.pay(island, ledger.GOV, facility_entity(f.id), money_of(value, MILLI), "grant")
    ship_loans.arrange(island, _ctx(game, world, "banking"), f, 0)
    assert f.economy[ship_loans.CREDIT]["qty"] == 2 * MILLI


def test_unbought_ships_loan_is_returned_the_same_turn(game, base):
    world, island, f = _setup(game, base)
    config = game.config
    f.economy["ships_wanted"] = 2 * MILLI
    f.economy["ships_replace"] = 2 * MILLI
    ship_loans.arrange(island, _ctx(game, world, "banking"), f, 0)
    credit = dict(f.economy[ship_loans.CREDIT])
    open_books(island)
    ctx = _ctx(game, world, "market")
    entity = facility_entity(f.id)
    before = ledger.money(island, entity)
    ship_loans.draw(island, ctx, f, 2 * MILLI)
    lent = ledger.money(island, entity) - before
    assert lent == credit["loan"]
    # 買い注文の取り置き（借りたお金は船の代金にだけ使う）。半分だけ買えた
    party = Party("facility", entity, None, 0, 0, f)
    party.reserved = credit["value"]
    ship_loans.bid_placed(island, party)
    party.reserved = credit["value"] // 2
    ship_loans.note_spent(island)
    ship_loans.settle(island, ctx, f)
    held = sum(banks.outstanding(island, x) for x in _ship_loans(island, f))
    assert held == pytest.approx(lent / 2, abs=2)
    assert island.stats["ship_loans.returned"] == pytest.approx(lent / 2, abs=2)
    assert ship_loans.CREDIT not in f.economy  # 枠は使い切り
    assert island.stats.get("ship_loans.over_limit", 0) == 0
    assert not ledger.check(island, island.scratch["opening_accounts"])
    del config


def test_frame_is_not_lent_before_the_market(game, base):
    """枠は銀行の段では貸さない（お金を賃金などほかの支払いに使えないように。掲示板 #858）。"""
    world, island, f = _setup(game, base)
    f.economy["ships_wanted"] = MILLI
    f.economy["ships_replace"] = MILLI
    ship_loans.arrange(island, _ctx(game, world, "banking"), f, 0)
    assert f.economy[ship_loans.CREDIT]["loan"] > 0
    assert not _ship_loans(island, f)
    # 市場で買う数は枠まで
    assert ship_loans.market_qty(f, 5 * MILLI) == MILLI


def _yard(game, island):
    used = {(x.x, x.y) for x in island.facilities}
    x, y = next(
        (t.x, t.y)
        for t in island.tiles
        if (t.x, t.y) not in used and t.terrain in game.defs.facilities["shipyard"]["terrain"]
    )
    yard = Facility(
        id=max(x.id for x in island.facilities) + 1,
        kind="shipyard",
        x=x,
        y=y,
        owner=OWNER_PRIVATE,
        accounting=ACCOUNTING_SELF,
        built=True,
    )
    island.facilities.append(yard)
    return yard


def test_preorder_loan_pays_interest_only_until_delivery(game, base):
    world, island, f = _setup(game, base)
    config = game.config
    yard = _yard(game, island)
    turns = config.get("trade.cost_turns")
    f.economy["ships_short"] = [1] * turns
    f.economy[orders.SHORT_LOAD] = [config.get("trade.ship_capacity") * 1500] * turns
    price = island.economy.prices[GOOD]
    ledger.pay(island, ledger.GOV, facility_entity(f.id), money_of(price, MILLI), "grant")
    turn = world.turn + 1
    ctx = _ctx(game, world, "market")
    assert orders.place(island, game.defs, config, f, turn, ctx) == 2 * MILLI
    (order,) = yard.economy[orders.ORDERS]
    value = money_of(order["price"], order["qty"])
    assert order["financed"] == pytest.approx(value * config.get("loans.ship_ltv"), rel=1e-6)
    assert order["paid"] == pytest.approx(value - order["financed"], abs=2)  # 手付けは自分で
    assert not _ship_loans(island, f)
    # 合算の上限: 前注文の借りも船の借りの合計に入る
    assert ship_loans.ship_debt(island, f) == order["financed"]
    # 造船所が作り上げたら、残りの代金は銀行が貸して払う。引き渡しまで元金は返さない
    from hakoniwa_core import stock

    stock.add(yard.stock, GOOD, MILLI, turn, game.defs.goods)
    world.turn += 1
    orders.run(island, game.defs, config, turn + 1, _ctx(game, world, "market"))
    (loan,) = _ship_loans(island, f)
    assert loan.repay_from == ship_loans.FAR
    assert banks.debt_service(island, facility_entity(f.id), turn + 1) == ledger.interest(
        banks.outstanding(island, loan), loan.rate
    )
    stock.add(yard.stock, GOOD, MILLI, turn + 1, game.defs.goods)
    world.turn += 1
    orders.run(island, game.defs, config, turn + 2, _ctx(game, world, "market"))
    assert orders.ORDERS not in yard.economy  # 引き渡した
    assert loan.repay_from == turn + 2  # 引き渡しのターンから元金を返す
    assert banks.outstanding(island, loan) == order["financed"]
    assert island.stats.get("ship_loans.over_limit", 0) == 0


def test_sunk_ships_keep_the_loan_and_record_the_shortfall(game, base):
    world, island, f = _setup(game, base)
    config = game.config
    bank = banks.active_banks(island)[0]
    entity = facility_entity(f.id)
    turn = world.turn + 1
    amount = int(banks.book_capital(f) * config.get("loans.ship_ltv"))
    loan = banks.lend(
        island,
        bank,
        entity,
        amount,
        banks.SHIP,
        config.get("loans.ship_turns"),
        turn,
        500,
        turn + 1,
    )
    ships.lose_ships(island, config, f, ships.fleet(f) // 2)
    assert banks.outstanding(island, loan) == amount  # 沈んでも借りは残る
    assert island.stats["ship_loans.shortfall"] > 0


def _lost_every_ship(game, f, short=True):
    """f の航海に出ていた船を全部沈め、直近の積み残しの記録を持たせる。"""
    config = game.config
    f.economy["used"] = [config.get("trade.ship_capacity") * 2]  # 2 隻が航海に出ていた
    turns = config.get("trade.cost_turns")
    f.economy["ships_short"] = [1 if short else 0] * turns


def test_first_ship_after_losing_every_ship_is_a_replacement(game, base):
    """航海に出ていた船を沈没で全部失い、積み残しが続く海運業の最初の 1 隻は買い替えの扱い（注文と
    在庫を含めて 1 隻まで）。新しい海運業・自分で解体した海運業・積み残しのない海運業は買い増し。"""
    world, island, f = _setup(game, base)
    config = game.config
    turn = world.turn
    _lost_every_ship(game, f)
    ships.lose_ships(island, config, f, ships.fleet(f))
    assert ships.fleet(f) == 0
    assert f.economy[ships.LOST] == {"why": "sunk", "used": ships.used_ships(config, f)}
    assert ships.replacement(config, f, turn) == MILLI
    assert ships.replacement(config, f, turn, MILLI // 4) == MILLI * 3 // 4  # 注文を含めて 1 隻
    assert ships.replacement(config, f, turn, MILLI) == 0
    f.economy["ships_short"] = [0] * config.get("trade.cost_turns")  # 積み残しがない
    assert ships.replacement(config, f, turn) == 0
    _lost_every_ship(game, f)
    f.economy[ships.LOST] = {"why": "scrapped"}  # 使わない船を自分で解体した
    assert ships.replacement(config, f, turn) == 0
    f.economy.pop(ships.LOST)  # 新しく作った海運業（船を持ったことがない）
    assert ships.replacement(config, f, turn) == 0
    # 船を船団に入れたら印は消える（2 隻目からは買い増し）
    f.economy[ships.LOST] = {"why": "sunk", "used": MILLI}
    stock.add(f.stock, GOOD, MILLI // 2, turn, game.defs.goods)
    ships.commission(island, game.defs, config, turn)
    assert ships.LOST not in f.economy


def test_lost_ships_with_no_earnings_and_no_owner_money_wait(game, base):
    """船を全部失い稼ぎの記録が 0 で、持ち主も出せないなら、買い替えの扱いでも買えない（銀行は返す力
    のない借りを貸さない。持ち主が出せるようになるまで待つ）。"""
    world, island, f = _setup(game, base, earnings_ships=0.0)
    f.owner = OWNER_PRIVATE
    f.accounting = ACCOUNTING_SELF
    entity = facility_entity(f.id)
    owner = ship_loans.owner_entity(f)
    ledger.pay(island, owner, ledger.GOV, max(0, ledger.money(island, owner)), "grant")
    ledger.pay(island, entity, ledger.GOV, max(0, ledger.money(island, entity)), "grant")
    f.economy["ships_wanted"] = MILLI
    f.economy["ships_replace"] = MILLI
    ctx = _ctx(game, world, "banking")
    ship_loans.arrange(island, ctx, f, 0)
    credit = f.economy.get(ship_loans.CREDIT)
    assert credit is None or (credit["qty"] < MILLI and credit["loan"] == 0)
    assert island.stats["ship_loans.refused_replace"] == 1


def test_review_counts_earnings_over_service(game, base):
    """審査の「稼ぎ ÷ 返済」を数える（数えるだけ。審査には使わない）。"""
    world, island, f = _setup(game, base, earnings_ships=0.01)
    f.economy["ships_wanted"] = MILLI
    f.economy["ships_replace"] = 0
    ship_loans.arrange(island, _ctx(game, world, "banking"), f, 0)
    stats = island.stats
    assert stats["ship_loans.reviews"] == 1
    assert stats["ship_loans.review_service"] > 0
    ratio = stats["ship_loans.review_earnings"] / stats["ship_loans.review_service"]
    assert stats.get("ship_loans.review_below_1", 0) == (1 if ratio < 1.0 else 0)
    assert stats.get("ship_loans.review_below_1_2", 0) == (1 if ratio < 1.2 else 0)


def test_turns_run_with_ship_loans_and_the_books_balance(game, base):
    """船の借りを含めてターンが回り、帳簿の確かめ（記録の段）が通る。"""
    world, island, f = _setup(game, base)
    config = game.config
    f.economy["ships_wanted"] = MILLI
    f.economy["ships_replace"] = MILLI
    run(game, world, 3)
    f = shipping_of(island, config)
    assert ship_loans.CREDIT not in f.economy or f.economy[ship_loans.CREDIT]["qty"] >= 0
    assert island.stats.get("ship_loans.over_limit", 0) == 0
