"""帳簿の確かめ（ledger.check / created_by_kind / aggregates）の同値性の試験。

速くする変更（C3 W-236）のための試験で、返す文の並びと中身・集計の表が前と1文字も変わらない
ことを、前の書き方をここに写した見本（``_check_old`` など）と比べる。重い試験にしない
（1島・仕訳は手で組み立てる）。
"""

from __future__ import annotations

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.game import load_game
from hakoniwa_core.ledger import (
    DEPOSIT,
    GOV,
    ISSUED,
    MONEY_ITEMS,
    NONRESIDENT,
    TREASURY,
    instrument,
    is_issued,
    parse,
)
from hakoniwa_core.state import JournalEntry
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def books(game):
    """経済の始まった島と、確かめの基準にする残高（ここから仕訳を足していく）。"""
    world = generate_world(1, 1, game.config, game.defs)
    island = next(iter(world.islands.values()))
    island.journal.clear()
    return island, dict(island.accounts), game.defs.journal_kinds()


# --- 前の書き方（見本。変えた書き方と比べるためのもので、ここでは直さない） ----------


def _aggregates_old(accounts: dict[str, int]) -> dict[str, int]:
    totals = dict.fromkeys(MONEY_ITEMS, 0)
    for name, value in accounts.items():
        holder, item, _ = parse(name)
        if holder == GOV and item == DEPOSIT:
            continue
        for key, items in MONEY_ITEMS.items():
            if item in items:
                totals[key] += value
    return totals


def _created_by_kind_old(journal) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {key: {} for key in MONEY_ITEMS}
    for e in journal:
        for name, sign in ((e.debit, 1), (e.credit, -1)):
            holder, item, _ = parse(name)
            if item == ISSUED or (holder == GOV and item == DEPOSIT):
                continue
            for key, items in MONEY_ITEMS.items():
                if item in items and is_issued(e.credit if sign == 1 else e.debit):
                    by_kind = out[key]
                    by_kind[e.kind] = by_kind.get(e.kind, 0) + sign * e.amount
    return out


def _check_old(island, opening, kinds=None) -> list[str]:
    problems: list[str] = []
    known = set(kinds) if kinds is not None else None
    replay = dict(opening)
    for e in island.journal:
        if e.amount <= 0 or instrument(e.debit) != instrument(e.credit):
            problems.append(f"形の正しくない仕訳: {e}")
        if known is not None and e.kind not in known:
            problems.append(f"定義されていない仕訳の種類: {e.kind}")
        replay[e.debit] = replay.get(e.debit, 0) + e.amount
        replay[e.credit] = replay.get(e.credit, 0) - e.amount
    closing = island.accounts
    wrong = [name for name in replay if replay[name] != closing.get(name, 0)]
    wrong += [name for name in closing if name not in replay and closing[name] != 0]
    for name in sorted(wrong):
        if replay.get(name, 0) != closing.get(name, 0):
            problems.append(
                f"勘定 {name} の残高 {closing.get(name, 0)} が、前の残高と仕訳から求めた"
                f" {replay.get(name, 0)} と違います"
            )
    before, after = _aggregates_old(opening), _aggregates_old(closing)
    created = _created_by_kind_old(island.journal)
    for key in MONEY_ITEMS:
        if after[key] - before[key] != sum(created[key].values()):
            problems.append(
                f"{key} の増減 {after[key] - before[key]} が、生まれた・消えた額"
                f" {sum(created[key].values())} と一致しません"
            )
    rows: dict[tuple[str, str], int] = {}
    for name, value in closing.items():
        key = instrument(name)
        rows[key] = rows.get(key, 0) + value
        holder, item, _ = parse(name)
        if item in (DEPOSIT, TREASURY, NONRESIDENT) and value < 0:
            problems.append(f"勘定 {name} がマイナスです（{value}）")
    for key, total in sorted(rows.items()):
        if total != 0:
            problems.append(f"持ち物 {key} の残高の合計が 0 になりません（{total}）")
    return problems


# --- 仕訳の組み立て ---------------------------------------------------------------


def _mixed_journal(island):
    """いろいろな形の仕訳を並べた仕訳帳（正しいものと、わざと合わないものを混ぜる）。

    - 預金の支払い（出し手の勘定を使わない）
    - 預金の発行（貸し方が出し手の勘定）と消却（借り方が出し手の勘定）
    - 国債の売り買い（お金の量に数えない持ち物）
    - 非居住者の預金（外国の主体が持つこの島の通貨）
    - 政府の預金（お金の量に数えない）
    - 金額 0 の仕訳（形の確かめにかかる）
    - 持ち物の違う勘定の間の仕訳（形の確かめにかかる）
    - 出し手の勘定と無関係の勘定の間の仕訳（形の確かめにかかる）
    - 両方とも出し手の勘定の仕訳（形の確かめにかかる）
    """
    ledger.pay(island, "gov", "hh:general", 1000, "wage")
    ledger.pay(island, "hh:general", "gov", 300, "tax_income")
    ledger.create(island, "hh:general", DEPOSIT, "bank:1", 500, "wage")
    ledger.cancel(island, "hh:general", DEPOSIT, "bank:1", 100, "wage")
    ledger.transfer(island, "bond:1", "gov", "bank:1", "cb", 100, "bond_trade")
    extra = [
        JournalEntry(
            kind="wage", debit="nr:2:gov/nr_deposit/cb", credit="cb/issued/nr_deposit", amount=50
        ),
        JournalEntry(
            kind="wage", debit="gov/deposit/bank:1", credit="bank:1/issued/deposit", amount=200
        ),
        JournalEntry(kind="wage", debit="gov/treasury/cb", credit="cb/issued/treasury", amount=0),
        JournalEntry(
            kind="wage", debit="gov/treasury/cb", credit="hh:general/deposit/bank:1", amount=500
        ),
        JournalEntry(
            kind="wage", debit="cb/issued/treasury", credit="hh:general/deposit/bank:1", amount=300
        ),
        JournalEntry(
            kind="wage", debit="cb/issued/treasury", credit="cb/issued/treasury", amount=100
        ),
    ]
    island.journal.extend(extra)
    return island.journal


# --- 同値性 -----------------------------------------------------------------------


def test_created_by_kind_matches_old_writing(books):
    """生まれた・消えた額の表が、前の書き方と1文字も変わらない。"""
    island, _, _ = books
    journal = _mixed_journal(island)
    assert ledger.created_by_kind(journal) == _created_by_kind_old(journal)


def test_aggregates_matches_old_writing():
    """お金の量の合計が、前の書き方と同じ。"""
    accounts = {
        "hh:general/deposit/bank:1": 1000,
        "gov/deposit/bank:1": 500,  # 政府の預金はお金の量に数えない
        "gov/treasury/cb": 2000,
        "bank:1/reserve/cb": 300,
        "nr:2:gov/nr_deposit/cb": 70,
        "bank:1/issued/deposit": -1500,  # 勘定自体が出し手のもので、持ち物は数えない
        "bank:1/bond:1/gov": 400,  # 国債はお金の量に数えない
    }
    assert ledger.aggregates(accounts) == _aggregates_old(accounts)


def _broken_books(books, break_it):
    island, opening, kinds = books
    break_it(island, opening)
    return island, opening, kinds


def test_check_matches_old_writing_when_balance_shifted(books):
    """1. 残高をずらす（仕訳なしに残高だけ変える）と、前と同じ文が同じ順で出る。"""

    def break_it(island, opening):
        name = ledger.deposit(island, "hh:general")
        island.accounts[name] = island.accounts.get(name, 0) + 1

    island, opening, kinds = _broken_books(books, break_it)
    assert ledger.check(island, opening, kinds) == _check_old(island, opening, kinds)


def test_check_matches_old_writing_for_zero_amount_entry(books):
    """金額 0 の仕訳があると、前と同じ文が同じ順で出る。"""

    def break_it(island, opening):
        island.journal.append(
            JournalEntry(
                kind="wage", debit="gov/treasury/cb", credit="cb/issued/treasury", amount=0
            )
        )

    island, opening, kinds = _broken_books(books, break_it)
    assert ledger.check(island, opening, kinds) == _check_old(island, opening, kinds)


def test_check_matches_old_writing_for_negative_deposit(books):
    """4. 預金がマイナスになると、前と同じ文が同じ順で出る。"""

    def break_it(island, opening):
        held = ledger.money(island, "hh:general")
        ledger.pay(island, "hh:general", "gov", held + 1, "tax_consumption")

    island, opening, kinds = _broken_books(books, break_it)
    assert ledger.check(island, opening, kinds) == _check_old(island, opening, kinds)


def test_check_matches_old_writing_for_unknown_kind(books):
    """定義されていない仕訳の種類があると、前と同じ文が同じ順で出る。"""

    def break_it(island, opening):
        ledger.pay(island, "gov", "hh:general", 10, "no_such_kind")

    island, opening, kinds = _broken_books(books, break_it)
    assert ledger.check(island, opening, kinds) == _check_old(island, opening, kinds)


def test_check_matches_old_writing_for_combined_broken_books(books):
    """合わないところをいくつも混ぜても、前と同じ文が同じ順で出る。"""

    def break_it(island, opening):
        _mixed_journal(island)
        ledger.pay(island, "gov", "hh:general", 10, "no_such_kind")
        name = ledger.deposit(island, "hh:general")
        island.accounts[name] = island.accounts.get(name, 0) + 1
        held = ledger.money(island, "hh:general")
        ledger.pay(island, "hh:general", "gov", held + 1, "tax_consumption")

    island, opening, kinds = _broken_books(books, break_it)
    assert ledger.check(island, opening, kinds) == _check_old(island, opening, kinds)
