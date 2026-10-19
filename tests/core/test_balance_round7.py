"""バランス調整の回7 前半: 家計の支え合い（W-17）と企業（W-14）。

ターンを回す試験は、記録の段が毎ターン帳簿を確かめ、trade_check の段が島をまたぐお金と運ぶ途中の荷を
確かめる（合わなければその場で止まる。tests/core/test_phase3_3.py の run_checked）。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import (
    ENGINEERS,
    GENERAL,
    OWNERS,
    arrears,
    banks,
    facility_entity,
    household,
    is_stopped,
)
from hakoniwa_core.economy import companies as co
from hakoniwa_core.economy.households import SUPPORT, jobless_layer
from hakoniwa_core.economy.start import next_payments
from hakoniwa_core.modules.companies import FirmsFunds, _carry_losses
from hakoniwa_core.modules.failures import cross_default
from hakoniwa_core.modules.labor import Payroll, food_cost
from hakoniwa_core.state import OWNER_PRIVATE
from hakoniwa_core.state.generate import generate_world
from tests.core.test_finance import books_ok, context, open_books
from tests.core.test_phase3_3 import run_checked

BASE_TURNS = 24


@pytest.fixture(scope="module")
def game():
    from hakoniwa_core.game import load_game

    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """標準の計画で BASE_TURNS ターン回した6島（市場・混合・計画経済が2つずつ）。試験ごとに写す。"""
    world = generate_world(1, 6, game.config, game.defs)
    run_checked(game, world, BASE_TURNS)
    return world


def jobless_island(world):
    for i in world.island_ids():
        island = world.islands[i]
        state = island.economy.households.get(jobless_layer(island))
        if state is not None and state["persons"] > 0 and GENERAL in island.economy.households:
            return island
    raise AssertionError("失業している家計のいる島がない")


def drain(island, layer):
    """層の預金を政府に移して 0 にする（帳簿の確かめの前に呼ぶ）。"""
    entity = household(layer)
    ledger.pay(island, entity, ledger.GOV, ledger.money(island, entity), "unemployment_benefit")


# --- 家計の支え合い（W-17） ---------------------------------------------------------


def test_working_families_send_food_money_to_jobless_without_benefit(game, base):
    """手当のない国で、失業している家計が食べ物を買えないとき、働く家計が所得の上限までの範囲で
    足りない分を送り、受けた額は市場の段で食べ物に先に使う。帳簿は合う。"""
    world = copy.deepcopy(base)
    island = jobless_island(world)
    layer = jobless_layer(island)
    ctx = context(game, world)
    drain(island, layer)
    island.economy.households[layer]["income"] = [0, 0, 0]
    open_books(island)
    income = {GENERAL: 10**9, ENGINEERS: 10**9}
    island.scratch["income"] = dict(income)
    before = ledger.money(island, household(GENERAL))
    Payroll._support(island, ctx)
    need = food_cost(island, ctx, island.economy.households[layer]["persons"])
    got = ledger.money(island, household(layer))
    assert need > 0
    assert 0 < got <= need
    assert island.scratch[SUPPORT] == {layer: got}
    assert island.scratch["income"][layer] == got
    assert ledger.money(island, household(GENERAL)) < before
    sent = sum(income[d] - island.scratch["income"][d] for d in income)
    assert sent == got
    books_ok(game, island)


def test_support_is_capped_by_working_income_share(game, base):
    """送るのは、働く家計のこのターンの可処分所得の household.support.share まで。"""
    world = copy.deepcopy(base)
    island = jobless_island(world)
    layer = jobless_layer(island)
    ctx = context(game, world)
    drain(island, layer)
    island.economy.households[layer]["income"] = [0, 0, 0]
    island.scratch["income"] = {GENERAL: 1000}
    Payroll._support(island, ctx)
    share = game.config.get("household.support.share")
    assert ledger.money(island, household(layer)) <= round(1000 * share)


def test_no_support_when_jobless_can_buy_food(game, base):
    """失業している家計が自分のお金で食べ物を買えるなら、送らない。"""
    world = copy.deepcopy(base)
    island = jobless_island(world)
    layer = jobless_layer(island)
    ctx = context(game, world)
    need = food_cost(island, ctx, island.economy.households[layer]["persons"])
    island.economy.households[layer]["income"] = [need * 10] * 3
    ledger.pay(island, ledger.GOV, household(layer), need * 10, "unemployment_benefit")
    island.scratch["income"] = {GENERAL: 10**9}
    Payroll._support(island, ctx)
    assert SUPPORT not in island.scratch


# --- 企業（W-14） ---------------------------------------------------------------


def private_members(game, island):
    """民営の企業に入った、動いていて借入の遅れも未払いもない施設（番号の順）。"""
    found = []
    for f in sorted(island.facilities, key=lambda f: f.id):
        if co.private_firm_of(island, f) is None or not f.built or is_stopped(f):
            continue
        entity = facility_entity(f.id)
        if arrears.total(island, entity) or arrears.total(island, entity, arrears.TAX_ARREARS):
            continue
        found.append(f)
    return found


def market_island(world):
    for i in world.island_ids():
        island = world.islands[i]
        if island.has_economy and island.regime.get("economy") == "market":
            return island
    raise AssertionError("市場経済の島がない")


def merge(island, a, b):
    """施設 b を a の企業に入れる（試験のため。株は動かさない）。"""
    b.economy[co.FIRM] = co.firm_of(a)


def test_private_facilities_belong_to_firms_that_hold_their_shares(game, base):
    """民営の物を作る施設はみな民営の企業に入り、企業が施設の株を、資本家階級が企業の株を持つ。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    found = 0
    for f in island.facilities:
        if f.owner != OWNER_PRIVATE or not co.eligible(game.defs, game.config, f):
            continue
        if "foreclosed" in f.economy:
            continue
        number = co.private_firm_of(island, f)
        assert number is not None, f.kind
        firm = co.entity(number)
        held = ledger.balance(island, ledger.account(firm, ledger.EQUITY, facility_entity(f.id)))
        owners = ledger.balance(island, ledger.account(household(OWNERS), ledger.EQUITY, firm))
        assert held > 0 and owners > 0
        found += 1
    assert found > 0
    assert island.stats["firms.count"] > 0


def test_firm_sweeps_extra_cash_and_supports_short_plant(game, base):
    """企業の資金の段: 手元の多すぎる工場から企業へ上げ、足りない工場へ企業から回す。帳簿は合う。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    a, b = private_members(game, island)[:2]
    merge(island, a, b)
    ctx = context(game, world)
    open_books(island)
    ea, eb = facility_entity(a.id), facility_entity(b.id)
    ledger.pay(island, eb, ea, ledger.money(island, eb), "subsidy")
    ledger.pay(island, ledger.GOV, ea, ledger.money(island, ledger.GOV) // 2, "subsidy")
    FirmsFunds().run_island(island, ctx)
    settings = game.config.get("firms")

    def keep(f):
        entity = facility_entity(f.id)
        return next_payments(island, game.defs, game.config, f) * settings[
            "operating_turns"
        ] + banks.debt_service(island, entity, ctx.turn)

    assert ledger.money(island, ea) == keep(a)
    assert ledger.money(island, eb) >= keep(b) > 0
    rec = co.record(island, co.firm_of(a))
    assert rec[co.SUPPORTED] > 0
    assert island.stats["firms.support"] > 0
    books_ok(game, island)


def test_losses_carry_within_the_firm():
    """連結の赤字は企業で繰り越し、後の利益から古い順に引く。期限を過ぎた赤字は消える。"""
    rec = {}
    assert _carry_losses(rec, -100, 1, 60) == 0
    assert _carry_losses(rec, -50, 2, 60) == 0
    assert _carry_losses(rec, 120, 3, 60) == 0
    assert rec[co.LOSSES] == [[2, 30]]
    assert _carry_losses(rec, 50, 4, 60) == 20
    assert co.LOSSES not in rec
    rec = {co.LOSSES: [[1, 100]]}
    assert _carry_losses(rec, 40, 70, 60) == 40


def test_investor_prefers_firm_with_same_kind(game, base):
    """新しい施設の出資は、預金で出せる企業のうち同じ種類の施設を持つ企業が先に出す。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    f = private_members(game, island)[0]
    number = co.firm_of(f)
    cash = ledger.money(island, ledger.GOV) // 2
    ledger.pay(island, ledger.GOV, co.entity(number), cash, "subsidy")
    chosen = co.choose_investor(island, game.defs, f.kind, cash)
    assert chosen == (co.entity(number), number)
    assert co.choose_investor(island, game.defs, f.kind, cash * 10**6) is None


def test_default_of_one_plant_bankrupts_the_firms_borrowers(game, base):
    """企業の1つの工場の返済の遅れが限度に届くと、同じ企業の借入のある工場もみな倒産する
    （クロスデフォルト）。帳簿は合う。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    borrowers = [
        f
        for f in private_members(game, island)
        if banks.loans_by_borrower(island, facility_entity(f.id))
    ]
    a, b = borrowers[:2]
    merge(island, a, b)
    ctx = context(game, world)
    open_books(island)
    loan = banks.loans_by_borrower(island, facility_entity(a.id))[0]
    loan.arrears = game.config.get("loans.arrears_limit")
    cross_default(island, ctx, a)
    assert "foreclosed" in a.economy and "foreclosed" in b.economy
    assert co.firm_of(a) is None and co.firm_of(b) is None
    assert any(e.kind == "firm_bankrupt" for e in island.log)
    assert island.stats["firms.cascade"] == 2
    books_ok(game, island)
