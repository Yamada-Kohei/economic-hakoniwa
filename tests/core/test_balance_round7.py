"""バランス調整の回7 前半: 家計の支え合い（W-17）。

ターンを回す試験は、記録の段が毎ターン帳簿を確かめ、trade_check の段が島をまたぐお金と運ぶ途中の荷を
確かめる（合わなければその場で止まる。tests/core/test_phase3_3.py の run_checked）。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import ENGINEERS, GENERAL, household
from hakoniwa_core.economy.households import SUPPORT, jobless_layer
from hakoniwa_core.modules.labor import Payroll, food_cost
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
