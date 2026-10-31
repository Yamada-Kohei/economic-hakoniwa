"""電力の代金の取り置き: 家計のお金が電力の要る量に足りないとき、消費税込みの取り置きが使えるお金を
超えない（測りの open-s2 で t=361 の次に FundsError で止まった）。

1単位の値段に1単位の税（切り捨て）を足した値段で量を決めていたので、代金にまとめて掛けた税の
ほうが大きくなり、取り置く額が使えるお金を超えていた。"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import Payer, funds, household
from hakoniwa_core.modules.production import Production
from hakoniwa_core.state.generate import generate_world
from tests.core.test_finance import context


@pytest.fixture(scope="module")
def game():
    from hakoniwa_core.game import load_game

    return load_game()


@pytest.fixture(scope="module")
def world(game):
    return generate_world(1, 1, game.config, game.defs)


@pytest.mark.parametrize("keep", [89_012, 345_678, 1_234_567])
@pytest.mark.parametrize("tax", [500, 800, 1000])
def test_households_short_of_money_reserve_no_more_than_they_have(game, world, keep, tax):
    w = copy.deepcopy(world)
    island = w.islands[w.island_ids()[0]]
    island.policy.consumption_tax = tax
    left = {}
    for layer in island.economy.households:  # どの層も keep だけを残して取り置き済みにする
        payer = Payer(household(layer))
        funds.commit(island, payer, max(0, ledger.money(island, payer.entity) - keep))
        left[layer] = funds.available(island, payer)
    assert keep in left.values()  # 電力の代金に足りない層がある
    Production().run_island(island, context(game, w, "production"))  # FundsError で止まらない
