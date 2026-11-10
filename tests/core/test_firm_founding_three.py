"""資本家の会社づくりの3つの直し（2026-11-03）: 創業融資、株を担保にした借入、資本家の消費の下げ。

設計書 5 施設と企業「民営工場の参入」「企業の仕組み」、7 お金と銀行「融資」、4 労働と家計
「使う額」。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import GENERAL, OWNERS, Payer, banks, funds, household, layers
from hakoniwa_core.economy import companies as co
from hakoniwa_core.economy.households import investable, wealth_propensity
from hakoniwa_core.modules.banking import Banking
from hakoniwa_core.modules.firms import ENTRY_PLEDGE, EntryExit
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.units import round_money
from tests.core.test_balance_round7 import market_island, private_members
from tests.core.test_finance import books_ok, context, open_books
from tests.core.test_phase3_3 import run_checked


@pytest.fixture(scope="module")
def game():
    from hakoniwa_core.game import load_game

    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """標準の計画で 24 ターン回した6島。試験ごとに写す。"""
    world = generate_world(1, 6, game.config, game.defs)
    run_checked(game, world, 24)
    return world


def _setup(game, base):
    world = copy.deepcopy(base)
    island = market_island(world)
    ctx = context(game, world)
    f = private_members(game, island)[0]
    number = co.firm_of(f)
    kinds = {m.kind for x in island.economy.firms for m in co.members(island, int(x))}
    other = next(
        k
        for k in sorted(game.defs.facilities)
        if k not in kinds and co.eligible(game.defs, game.config, f)
    )
    return world, island, ctx, f, number, other


def _pledged(island):
    """資本家階級の株を担保にした借入の残高。"""
    return sum(
        banks.outstanding(island, x)
        for x in banks.loans_by_borrower(island, household(OWNERS))
        if x.purpose == banks.PLEDGE
    )


def _set_owners(island, amount):
    """資本家階級の預金を amount にする（政府とのやり取りで）。"""
    owners = household(OWNERS)
    have = ledger.money(island, owners)
    if have > amount:
        ledger.pay(island, owners, ledger.GOV, have - amount, "subsidy")
    elif have < amount:
        ledger.pay(island, ledger.GOV, owners, amount - have, "grant")


def test_capitalists_spend_less_of_their_income_and_wealth(game):
    """資本家階級は所得の5割ほどを使い、財産から年3〜5%を取り崩す。財産は所得の10〜12年分ほどで
    釣り合う（現実の上位1%）。働く層は今までどおり。"""
    config = game.config
    owners, general = layers(config)[OWNERS], layers(config)[GENERAL]
    assert wealth_propensity(config, OWNERS) == owners["c2"] < config.get("household.c2")
    assert 0.03 <= wealth_propensity(config, OWNERS) * 12 <= 0.05
    assert wealth_propensity(config, GENERAL) == config.get("household.c2")
    assert 0.3 <= owners["c1"] <= 0.5 < general["c1"]
    months = (1 - owners["c1"]) / owners["c2"]
    assert 10 * 12 <= months <= 12 * 12


def test_capitalists_draw_on_their_shares_too(game, base):
    """資本家階級が取り崩すもとは、預金と持ち株の値打ちから株を担保にした借入を引いた分。働く層は
    預金だけ。"""
    from hakoniwa_core.economy.households import SHARES_WORTH, wealth

    world, island, ctx, f, number, other = _setup(game, base)
    config = game.config
    owners = household(OWNERS)
    island.scratch.pop(SHARES_WORTH, None)  # 写した世界に前のターンの値が残っている
    shares = co.owners_collateral(island) - _pledged(island)
    assert shares > 0
    assert wealth(island, config, OWNERS) == ledger.money(island, owners) + shares
    assert wealth(island, config, GENERAL) == ledger.money(island, household(GENERAL))
    # 株を担保に借りた分は財産から引く（株の値打ちはターンに一度だけ数える）
    island.scratch.pop(SHARES_WORTH, None)
    open_books(island)
    bank = banks.active_banks(island)[0]
    banks.lend(island, bank, owners, 1_000_000, banks.PLEDGE, 60, ctx.turn, 300)
    assert wealth(island, config, OWNERS) == ledger.money(island, owners) + shares - 1_000_000


def test_firms_without_a_track_record(game, base):
    """建国のときからある企業と、できて firms.track_record_turns ターンたった企業は実績がある。"""
    world, island, ctx, f, number, _ = _setup(game, base)
    config = game.config
    rec = co.record(island, number)
    assert rec[co.FOUNDED] <= 1 and co.established(island, config, number, ctx.turn)
    rec[co.FOUNDED] = ctx.turn - 1
    assert not co.established(island, config, number, ctx.turn)
    rec[co.FOUNDED] = ctx.turn - config.get("firms.track_record_turns")
    assert co.established(island, config, number, ctx.turn)
    # 途中のターンで建国した島（サーバーの世界）は、建国の次のターンまでにできた企業が実績あり
    island.ext["founded_turn"] = ctx.turn - 3
    rec[co.FOUNDED] = ctx.turn - 2
    assert co.established(island, config, number, ctx.turn)
    rec[co.FOUNDED] = ctx.turn - 1
    assert not co.established(island, config, number, ctx.turn)


def test_new_firms_put_up_more_equity_than_firms_with_a_track_record(game, base):
    """資本家階級が新しく起こす会社は建設費の firms.startup_equity_share を出資し、実績のある会社は
    firms.equity_share でよい（銀行が新しい会社に貸すのは少なめ。創業融資）。"""
    world, island, ctx, f, number, other = _setup(game, base)
    config = game.config
    amount = 2_000_000
    firm = co.entity(number)
    # 資本家階級は新しい会社の出資を出せるが、同業の企業のほうが預金が多い
    startup_equity = round_money(amount * config.get("firms.startup_equity_share"))
    _set_owners(island, startup_equity * 2)
    ledger.pay(island, ledger.GOV, firm, startup_equity * 4, "subsidy")
    plan = EntryExit()._finance(island, ctx, amount, 10**12, other)
    assert isinstance(plan, list)
    assert island.scratch["entry_investor"] == (household(OWNERS), None)
    loan = sum(p for _, p, _ in plan)
    assert loan == amount - round_money(amount * config.get("firms.startup_equity_share"))
    plan = EntryExit()._finance(island, ctx, amount, 10**12, f.kind)
    assert island.scratch["entry_investor"] == (firm, number)
    loan = sum(p for _, p, _ in plan)
    assert loan == amount - round_money(amount * config.get("firms.equity_share"))
    # できたばかりの企業は、同業の増設でも実績がない
    co.record(island, number)[co.FOUNDED] = ctx.turn - 1
    plan = EntryExit()._finance(island, ctx, amount, 10**12, f.kind)
    assert island.scratch["entry_investor"] == (firm, number)
    assert sum(p for _, p, _ in plan) == amount - round_money(
        amount * config.get("firms.startup_equity_share")
    )


def test_capitalists_borrow_on_their_shares_to_found_a_firm(game, base):
    """資本家階級の預金で足りなければ、持っている会社の株を担保に借りて新しい会社を起こす。借りるのは
    足りない分だけで、帳簿は合う。"""
    world, island, ctx, f, number, other = _setup(game, base)
    config = game.config
    amount = 2_000_000
    equity = round_money(amount * config.get("firms.startup_equity_share"))
    _set_owners(island, equity // 4)
    firm = co.entity(number)
    ledger.pay(island, firm, ledger.GOV, co.spare(island, number), "subsidy")
    assert co.owners_collateral(island) * config.get("firms.pledge_ratio") > equity
    before = _pledged(island)
    open_books(island)
    plan = EntryExit()._finance(island, ctx, amount, 10**12, other)
    assert isinstance(plan, list)
    assert island.scratch["entry_investor"] == (household(OWNERS), None)
    pledge = island.scratch[ENTRY_PLEDGE]
    # 出資に回せる預金は、次のターンの暮らしの分を除いた額（households.investable）
    have = investable(island, config)
    living = funds.available(island, Payer(household(OWNERS))) - have
    need = amount - sum(p for _, p, _ in plan)
    keep = EntryExit._pledge_keep(island, ctx, need, pledge)
    assert sum(p for _, p, _ in pledge) == need - have + keep  # 次の返済の分を手元に残す
    EntryExit._borrow_on_shares(island, ctx, household(OWNERS))
    assert _pledged(island) - before == sum(p for _, p, _ in pledge) > 0
    assert ledger.money(island, household(OWNERS)) == need + keep + living
    assert ENTRY_PLEDGE not in island.scratch
    books_ok(game, island)


def test_no_pledge_when_the_owners_have_enough(game, base):
    """預金で出せるなら株を担保に借りない。誰も出せなければ、借りられる額をわけに書く。"""
    world, island, ctx, f, number, other = _setup(game, base)
    amount = 2_000_000
    _set_owners(island, amount)
    plan = EntryExit()._finance(island, ctx, amount, 10**12, other)
    assert isinstance(plan, list) and island.scratch[ENTRY_PLEDGE] == []
    reason, args = EntryExit()._finance(island, ctx, 10**15, 10**15, other)
    assert reason == "owners_short" and "pledge" in args


def test_margin_call_and_write_off(game, base):
    """担保の値打ち × 掛け目を残高が超えたら超えた分をすぐ返す（追い証）。返せないまま
    loans.arrears_limit 回続いたら、残りを銀行の損として消す。"""
    world, island, ctx, f, number, other = _setup(game, base)
    config = game.config
    owners = household(OWNERS)
    bank = banks.active_banks(island)[0]
    cover = int(co.owners_collateral(island) * config.get("firms.pledge_ratio"))
    open_books(island)
    amount = cover - _pledged(island) + 1000
    loan = banks.lend(island, bank, owners, amount, banks.PLEDGE, 60, ctx.turn, 300)
    assert Banking._margin_call(island, ctx) == 1000
    loan.arrears = config.get("loans.arrears_limit")
    Banking._write_off_mortgage(island, ctx, loan)
    assert banks.outstanding(island, loan) == 0
    assert island.stats["loans.pledge_write_off"] == amount
    assert Banking._margin_call(island, ctx) == 0
    books_ok(game, island)


def test_pledges_are_settled_when_the_owners_layer_goes(game, base):
    """計画経済に移って資産を持つ層をまとめる前に、株を担保にした借入を預金で返し、返しきれない
    分を銀行の損として消す（借り手のいない借入を残さない）。"""
    from hakoniwa_core.modules.politics import _settle_pledges

    world, island, ctx, f, number, other = _setup(game, base)
    owners = household(OWNERS)
    bank = banks.active_banks(island)[0]
    open_books(island)
    banks.lend(island, bank, owners, 5_000_000, banks.PLEDGE, 60, ctx.turn, 300)
    _set_owners(island, 1_000_000)
    _settle_pledges(island, ctx)
    assert _pledged(island) == 0
    assert ledger.money(island, owners) == 0
    assert island.stats["loans.pledge_write_off"] >= 4_000_000
