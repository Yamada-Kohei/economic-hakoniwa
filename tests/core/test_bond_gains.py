"""国債の評価益は売るまで損益に入れない（2026-10-14 の決定。試し遊びのターン283・383）。

銀行と中央銀行の損益には、国債を売った・償還された・切り下げられた分の実現損益だけを入れ、まだ
売っていない評価損益は入れない（法人税・配当・中央銀行の納付に回らない）。銀行の自己資本は国債を
時価で数えるので、金利が上がって自己資本がマイナスになれば、これまでどおり破綻する。
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

from hakoniwa_core import ledger
from hakoniwa_core.economy import OWNERS, banks, books, debt, household
from hakoniwa_core.modules.accounts import Accounts
from hakoniwa_core.modules.bonds import Bonds
from hakoniwa_core.modules.failures import Failures
from hakoniwa_core.units import to_basis
from tests.core.test_finance import books_ok, context, island_of, open_books
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401


def _bond_bank(game, island, turn):
    """国債をいちばん多く持つ銀行。"""
    return max(
        banks.active_banks(island), key=lambda b: banks.bond_value(island, game.config, b, turn)
    )


def _shift_yields(island, points):
    island.economy.finance["yields"] = [
        max(0, y + points) for y in island.economy.finance["yields"]
    ]


def _fresh(island):
    """直接呼ぶ段のために、前のターンの途中の記録（流れ・国債の出入り）を消し、帳簿を確かめる準備
    をする。"""
    island.scratch.clear()
    open_books(island)


def _book(island, bank):
    number = banks.bank_number(bank)
    return next(b for b in reversed(island.books) if b.facility == number)


def test_helpers_realize_the_difference_from_the_cost():
    """取得原価と受け取った額の差だけが実現損益になる（手放した割合の取得原価と比べる）。"""
    island = SimpleNamespace(economy=SimpleNamespace(finance={}))
    debt.bought(island, "bank:1", 7, 900)  # 額面 1,000 を 900 で買った
    assert debt.released(island, "bank:1", 7, 400, 1000, 380) == 20  # 原価 360 の分を 380 で売った
    assert debt.cost_of(island, "bank:1") == 540
    assert debt.released(island, "bank:1", 7, 600, 600, 600) == 60  # 残りが額面で償還された
    assert debt.cost_of(island, "bank:1") == 0
    assert island.economy.finance[debt.REALIZED] == {"bank:1": 80}
    # 切り下げは、切られた額面の取得原価がそのまま損になる
    debt.bought(island, ledger.CB, 8, 1000)
    assert debt.released(island, ledger.CB, 8, 250, 1000, 0) == -250
    assert debt.cost_of(island, ledger.CB) == 750


def test_unrealized_gains_are_not_taxed_or_paid_out(game, warmed):
    """利回りが下がって国債が値上がりしても、売るまで銀行の損益・法人税・配当にも、中央銀行の
    納付にも入らない（値上がり益がそのまま配当と法人税で国庫に入っていた）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    bank = _bond_bank(game, island, ctx.turn)
    book_value = island.economy.finance["bond_book"][bank]
    assert book_value > 0
    _shift_yields(island, -300)  # 3 ポイント下がる
    gain = banks.bond_value(island, game.config, bank, ctx.turn) - book_value
    assert gain > 0
    _fresh(island)
    treasury = ledger.money(island, ledger.GOV)
    Accounts().run_island(island, ctx)
    items = _book(island, bank).items
    assert items[books.VALUATION] == gain
    assert books.REALIZED not in items
    assert items.get(books.PROFIT, 0) < gain  # 評価益は損益に入らない
    assert books.TAX not in items and books.DIVIDEND not in items
    assert not any(e.kind == "cb_payment" for e in island.journal)
    assert ledger.money(island, ledger.GOV) == treasury
    books_ok(game, island)


def test_selling_a_bond_realizes_the_gain(game, warmed):
    """値上がりした国債を売ると、取得原価との差が実現損益として損益に入る。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    bank = _bond_bank(game, island, ctx.turn)
    _shift_yields(island, -300)
    edges = game.config.get("bonds.buckets")
    by_id = {b.id: b for b in island.bonds}
    held = Bonds._by_bucket(island, bank, ctx.turn, edges, by_id)
    bucket = max(range(len(held)), key=lambda k: held[k])
    rate = island.economy.finance["yields"][bucket]
    _fresh(island)
    cost = debt.cost_of(island, bank)
    money = banks.reserves_of(island, bank)
    Bonds._deliver(island, ctx, bank, ledger.CB, held[bucket] // 2, bucket, rate, by_id)
    received = banks.reserves_of(island, bank) - money
    gain = received - (cost - debt.cost_of(island, bank))
    assert received > 0 and gain > 0
    Accounts().run_island(island, ctx)
    items = _book(island, bank).items
    assert items[books.REALIZED] == gain
    assert items[books.PROFIT] == gain - items.get(books.DEPRECIATION, 0)  # ほかの流れは空
    books_ok(game, island)


def test_unrealized_losses_still_fail_a_bank(game, warmed):
    """評価損は損益に入れないが、自己資本は時価で数えるので、マイナスになれば破綻する。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    bank = _bond_bank(game, island, ctx.turn)
    held = banks.bond_value(island, game.config, bank, ctx.turn)
    before = list(island.economy.finance["yields"])
    top = to_basis(game.config.get("bonds.max_yield"))
    island.economy.finance["yields"] = [top] * len(before)
    loss = held - banks.bond_value(island, game.config, bank, ctx.turn)
    island.economy.finance["yields"] = before
    assert loss > 0
    # 自己資本を値下がりの半分まで減らしておく（配当で払い出す）
    worth = banks.net_worth(island, game.config, bank, ctx.turn)
    ledger.pay(island, bank, household(OWNERS), worth - loss // 2, "dividend")
    island.economy.finance["yields"] = [top] * len(before)
    assert banks.net_worth(island, game.config, bank, ctx.turn) < 0
    _fresh(island)
    Accounts().run_island(island, ctx)
    items = _book(island, bank).items
    assert items[books.VALUATION] < 0 and books.TAX not in items
    Failures().run_island(island, ctx)
    assert banks.is_failed(island, bank)


def test_old_worlds_start_the_cost_at_the_market_value(game, warmed):
    """取得原価の記録のない世界（前の版）は、前のターンの時価を取得原価にして始める（前の版で
    損益に入れた評価益を、売ったときにもう一度数えない）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    finance = island.economy.finance
    finance.pop(debt.COST)
    terms = game.config.get("bonds.bucket_terms")
    debt.start_costs(island, world.turn, terms)
    for holder, value in finance["bond_book"].items():
        assert abs(debt.cost_of(island, holder) - value) <= len(island.bonds)
    # 記録があれば作り直さない
    finance[debt.COST][ledger.CB] = {"1": 5}
    debt.start_costs(island, world.turn, terms)
    assert finance[debt.COST][ledger.CB] == {"1": 5}
