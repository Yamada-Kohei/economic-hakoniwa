"""帳簿の名前の索引（hakoniwa_core/state/accounts.py）が、勘定を全部なめたときと同じ答えを返すか。

索引は速さのためだけのものなので、どう書き換えても索引のない帳簿（ふつうの dict）と同じ答え・
同じ並びになることを、でたらめな書き換えと仕訳で確かめます。
"""

from __future__ import annotations

import copy
import pickle
import random
from dataclasses import replace

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import debt
from hakoniwa_core.state import Accounts, Bond, IslandState
from hakoniwa_core.state.serialize import island_from_dict, island_to_dict

HOLDERS = ("hh:general", "hh:owners", "fac:1", "fac:12", "bank:3", "bank:4", "cb", "gov")
ITEMS = ("deposit", "equity", "bond:1", "bond:11", "loan:2", "reserve")
ISSUERS = ("bank:3", "bank:4", "gov", "cb", "fac:1")


def _names(rng: random.Random) -> list[str]:
    names = [f"{h}/{i}/{s}" for h in HOLDERS for i in ITEMS for s in ISSUERS]
    names += [f"{s}/issued/{i}" for s in ISSUERS for i in ITEMS]
    names += ["nr:2:bank:1/bond:1/gov", "nr:2:gov/nr_deposit/cb", "odd", "a/b"]
    rng.shuffle(names)
    return names


def _holder(name: str) -> str:
    return name.partition("/")[0]


def _item(name: str) -> str:
    return name.partition("/")[2].partition("/")[0]


def _assert_index(accounts: Accounts) -> None:
    """索引が、表を全部なめて選んだ名前と同じ（並びも同じ）か。"""
    names = list(accounts)
    holders = {_holder(n) for n in names} | set(HOLDERS)
    items = {_item(n) for n in names} | set(ITEMS)
    for holder in holders:
        assert list(accounts.of_holder(holder)) == [n for n in names if _holder(n) == holder]
        for item in items:
            assert list(accounts.of_pair(holder, item)) == [
                n for n in names if (_holder(n), _item(n)) == (holder, item)
            ]
    for item in items:
        assert list(accounts.of_item(item)) == [n for n in names if _item(n) == item]


@pytest.mark.parametrize("seed", range(40))
def test_index_follows_every_change(seed):
    """どの書き方で勘定を足し・消しても、中身と並びはふつうの dict と同じで、索引も合う。"""
    rng = random.Random(seed)
    names = _names(rng)
    accounts, plain = Accounts(), {}
    for step in range(400):
        op = rng.randrange(10)
        name = rng.choice(names)
        value = rng.randint(-5, 5)
        if op <= 3:
            accounts[name] = plain[name] = value
        elif op == 4:
            if name in plain:
                del accounts[name], plain[name]
        elif op == 5:
            assert accounts.pop(name, None) == plain.pop(name, None)
        elif op == 6 and plain:
            assert accounts.popitem() == plain.popitem()
        elif op == 7:
            assert accounts.setdefault(name, value) == plain.setdefault(name, value)
        elif op == 8:
            batch = [(rng.choice(names), rng.randint(-5, 5)) for _ in range(rng.randint(0, 4))]
            if rng.random() < 0.5:
                accounts.update(batch)
                plain.update(batch)
            else:
                accounts |= dict(batch)
                plain |= dict(batch)
        elif op == 9 and step % 97 == 0:
            accounts.clear()
            plain.clear()
        assert list(accounts.items()) == list(plain.items())
        if step % 20 == 0:
            _assert_index(accounts)
    _assert_index(accounts)


def test_copies_keep_the_index():
    """写し（copy・deepcopy・pickle・保存の形を通したもの）も索引を持ち、元と別のものになる。"""
    accounts = Accounts(
        {"bank:3/bond:1/gov": 5, "gov/issued/bond:1": -5, "hh:general/deposit/bank:3": 2}
    )
    for other in (
        accounts.copy(),
        copy.copy(accounts),
        copy.deepcopy(accounts),
        pickle.loads(pickle.dumps(accounts)),
    ):
        assert type(other) is Accounts and other == accounts and other is not accounts
        other["cb/bond:1/gov"] = 1
        _assert_index(other)
        assert "cb/bond:1/gov" not in accounts
    _assert_index(accounts)


def test_island_always_holds_an_indexed_ledger():
    """島の帳簿は、ふつうの dict を渡しても、保存の形から戻しても、索引つきになる。"""
    island = IslandState(id=1, name="x", wx=0, wy=0, size=0, tiles=[], accounts={"a/b/c": 1})
    assert type(island.accounts) is Accounts
    assert list(island.accounts.of_holder("a")) == ["a/b/c"]
    assert replace(island, name="y").accounts is island.accounts  # 写しを作らない置き換えは同じ帳簿
    island.accounts["d/e/f"] = 2
    back = island_from_dict(island_to_dict(island))
    assert type(back.accounts) is Accounts
    assert list(back.accounts.items()) == list(island.accounts.items())
    _assert_index(back.accounts)


def _island(accounts: dict[str, int]) -> IslandState:
    island = IslandState(id=1, name="x", wx=0, wy=0, size=0, tiles=[])
    island.accounts = accounts  # 索引のない帳簿（ふつうの dict）も試せるように差し替える
    island.economy.banks = [3, 4, 5]
    island.economy.deposit_bank = {"hh:general": 4, "fac:1": 3}
    return island


@pytest.mark.parametrize("seed", range(30))
def test_ledger_answers_match_a_full_scan(seed):
    """同じ仕訳を、索引つきの帳簿と索引のない帳簿に入れる。仕訳・残高・預金・持ち物・国債の
    持ち手が、並びまで同じになる。"""
    rng = random.Random(seed)
    fast, slow = _island(Accounts()), _island({})
    depositors = ("hh:general", "hh:owners", "fac:1", "fac:12")
    banks = ("bank:3", "bank:4", "bank:5")
    holders = (*banks, "cb", "nr:2:bank:1")
    bonds = [Bond(id=n, issued=0, term=24, rate=300, face=0) for n in range(1, 8)]
    for island in (fast, slow):
        island.bonds = copy.deepcopy(bonds)
    for _ in range(300):
        op = rng.randrange(6)
        bank, entity = rng.choice(banks), rng.choice(depositors)
        amount = rng.randint(1, 50)
        bond = ledger.bond_item(rng.choice(bonds).id)
        a, b = rng.sample(holders, 2)
        payer, payee = rng.sample(depositors, 2)
        owner = rng.choice(depositors)
        for island in (fast, slow):
            if op == 0:
                ledger.create(island, entity, ledger.DEPOSIT, bank, amount, "x")
            elif op == 1:
                ledger.create(island, a, bond, ledger.GOV, amount, "x")
            elif op == 2:
                held = ledger.balance(island, ledger.account(a, bond, ledger.GOV))
                if held > 0:
                    ledger.transfer(island, bond, ledger.GOV, a, b, min(held, amount), "x")
            elif op == 3:
                held = ledger.balance(island, ledger.account(a, bond, ledger.GOV))
                if held > 0:
                    ledger.cancel(island, a, bond, ledger.GOV, held, "x")
            elif op == 4:
                if ledger.money(island, payer) >= amount:
                    ledger.pay(island, payer, payee, amount, "x")
            else:
                ledger.create(island, owner, ledger.EQUITY, "fac:1", amount, "x")
        assert list(fast.accounts.items()) == list(slow.accounts.items())
        assert fast.journal == slow.journal
        for entity in depositors:
            assert ledger.money(fast, entity) == ledger.money(slow, entity)
            assert list(ledger.deposits_by_bank(fast, entity).items()) == list(
                ledger.deposits_by_bank(slow, entity).items()
            )
        for holder in (*holders, *depositors):
            for prefix in ("bond:", "deposit", "equity", ""):
                assert list(ledger.holdings(fast, holder, prefix).items()) == list(
                    ledger.holdings(slow, holder, prefix).items()
                )
        for fb, sb in zip(fast.bonds, slow.bonds, strict=True):
            assert list(debt.holders(fast, fb).items()) == list(debt.holders(slow, sb).items())
    _assert_index(fast.accounts)
