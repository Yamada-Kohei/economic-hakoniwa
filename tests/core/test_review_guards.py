"""呼ぶ側に任せていた前提を、破られたらすぐ分かるようにした確かめ（2026-11-01 のコードの見直し）。

- 取り置き（economy/funds）は、取り置いた額より多くは戻せない・払えない
- 測り（measure.batch）は、同時に回す数が 0 以下なら断る（1本も回らず終わらなかった）
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger, measure
from hakoniwa_core.economy import Payer, funds, household
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def world():
    from hakoniwa_core.game import load_game

    game = load_game()
    return generate_world(1, 1, game.config, game.defs)


def _payer(island):
    layer = next(iter(island.economy.households))
    payer = Payer(household(layer))
    assert ledger.money(island, payer.entity) > 100
    return payer


def test_release_more_than_committed_is_an_error(world):
    w = copy.deepcopy(world)
    island = w.islands[w.island_ids()[0]]
    payer = _payer(island)
    have = funds.available(island, payer)
    funds.commit(island, payer, 100)
    assert funds.available(island, payer) == have - 100
    with pytest.raises(funds.FundsError):
        funds.release(island, payer, 101)
    funds.release(island, payer, 100)
    assert funds.available(island, payer) == have


def test_pay_more_than_committed_is_an_error(world):
    w = copy.deepcopy(world)
    island = w.islands[w.island_ids()[0]]
    payer = _payer(island)
    funds.commit(island, payer, 100)
    with pytest.raises(funds.FundsError):
        funds.pay(island, payer, ledger.GOV, 101, "test", committed=True)


def test_measure_batch_needs_at_least_one_job(tmp_path):
    with pytest.raises(ValueError):
        measure.batch([("small", 1, 1)], tmp_path, 0, False)
