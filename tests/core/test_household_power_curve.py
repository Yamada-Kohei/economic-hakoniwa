"""家計は電気を買える分だけ買う（LB-47、power.household_curve）と、払えない電力の統計（UI-89）。

前は家計の層ごとに1本の注文で、指値は「使う額 × power.household_share ÷ 要る量」だった。電気料金が
これを少しでも超えると層の全員が1つも買わなかった（LAN の世界 8003 の島3 で停電 98% が約 50
ターン）。
"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from hakoniwa_core.economy import Payer, funds, household
from hakoniwa_core.modules.market import elastic_orders
from hakoniwa_core.modules.production import Production
from hakoniwa_core.state.generate import generate_world
from tests.core.test_finance import context
from tests.core.test_trade import run

POWER = "electricity"


@pytest.fixture(scope="module")
def game():
    from hakoniwa_core.game import load_game

    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """標準の計画で 12 ターン回した6島（市場・混合・計画経済が2つずつ）。試験ごとに写す。"""
    world = generate_world(1, 6, game.config, game.defs)
    run(game, world, 12)
    return world


def settings(game, curve, share=None):
    raw = copy.deepcopy(game.config.raw)
    raw["power"]["household_curve"] = curve
    if share is not None:
        raw["power"]["household_share"] = share
    return dataclasses.replace(game.config, raw=raw)


def produce(game, base, config, economy="market", tax=None):
    """島を写して生産の段だけを回し、(島, 回す前の層ごとの電気の予算) を返す。"""
    world = copy.deepcopy(base)
    island = next(i for i in world.islands.values() if i.regime["economy"] == economy)
    if tax is not None:
        island.policy.consumption_tax = tax
    island.stats = {}
    island.scratch.pop("household_power", None)  # 前のターンの払った額
    ctx = dataclasses.replace(context(game, world, "production"), config=config)
    budgets = Production._household_budgets(island, ctx)
    Production().run_island(island, ctx)
    return island, budgets


def test_households_buy_what_they_can_when_the_bill_tops_their_share(game, base):
    """電気料金が使う額の割合で払える値段を超えても、層の全員が 0 にはならず、払える分だけ買う。"""
    # 割合は電気料金が払える値段を超える大きさにする（発電所の働き手を電力会社 1 社分にして電気の
    # 原価がおよそ半分になったので、0.02 では払えてしまう）
    off, _ = produce(game, base, settings(game, False, share=0.01))
    on, _ = produce(game, base, settings(game, True, share=0.01))
    assert off.stats["power.household.general"] == 0  # 前の形: 指値が料金を下回り、全員が 0
    assert 0 < on.stats["power.household.general"] < 1
    assert on.stats["power.used"] > off.stats["power.used"]
    # 発電は売れ残っているので電力不足でなく、払えない分に数える
    assert on.stats["power.short_supply"] == off.stats["power.short_supply"] == 0
    assert 0 < on.stats["power.household_unaffordable"] < off.stats["power.household_unaffordable"]


@pytest.mark.parametrize("share", [0.3, 0.05, 0.02])
@pytest.mark.parametrize("tax", [None, 2000, 5000])
@pytest.mark.parametrize("economy", ["market", "mixed"])
def test_bill_stays_within_the_share_and_leaves_food(game, base, share, tax, economy):
    """どの値段で約定しても、層が払う額（消費税込み、どの送電網の分も合わせて）は、使う額 ×
    household_share と、使う額から食べ物の見込みを除いた残りを超えない。"""
    island, budgets = produce(game, base, settings(game, True, share=share), economy, tax)
    paid = island.scratch.get("household_power", {})
    assert paid
    for layer, amount in paid.items():
        assert amount <= budgets[layer]
    for layer in island.economy.households:  # 取り置きは全部戻っている
        payer = Payer(household(layer))
        assert funds.available(island, payer) >= 0


def test_planned_islands_keep_the_ration(game, base):
    """計画経済の公定価格の島は今のまま（公定価格で要る量を注文し、配給で分ける）。"""
    off, _ = produce(game, base, settings(game, False), "planned")
    on, _ = produce(game, base, settings(game, True), "planned")
    assert on.scratch.get("household_power") == off.scratch.get("household_power")
    assert {k: v for k, v in on.stats.items() if k.startswith("power.")} == {
        k: v for k, v in off.stats.items() if k.startswith("power.")
    }


def test_curve_off_keeps_the_old_single_order(game, base):
    """切なら前の1本の注文のまま（既定は切）。"""
    assert game.config.get("power.household_curve") is False
    default, _ = produce(game, base, game.config)
    off, _ = produce(game, base, settings(game, False))
    assert default.scratch.get("household_power") == off.scratch.get("household_power")


def test_band_steps_fill_the_gap_and_the_top_step_is_always_there():
    """値幅の中の段で、倍率の段の間の値段でも払える分を取りこぼさない。上端の段はいつも出す。"""
    want = 10_000
    band = (900, 1100)
    plain = elastic_orders(want, want, 900, 0.2, [0.5, 1.0, 2.0], band, 10**12, 0, 0)
    steps = elastic_orders(
        want, want, 900, 0.2, [0.5, 1.0, 2.0], band, 10**12, 0, 0, (900, 1000, 1100)
    )
    assert [lim for _, lim in plain] == [1100, 900]  # 2 倍の段は上端にまとまる
    assert [lim for _, lim in steps] == [1100, 1000, 900]  # 1000 で約定しても曲線どおり買う
    assert sum(q for q, lim in steps if lim >= 1000) == round(want * (1000 / 900) ** -0.2)
    # 真ん中がずっと安くても、上端の段は出る（電気は持ち越せない）
    top = elastic_orders(want, want, 100, 0.2, [0.5, 1.0, 3.0], band, 10**12, 0, 0, (900, 1100))
    assert top and top[0][1] == 1100 and 0 < top[0][0] < want
    # 予算で払える量より多くは買わない（どの段でも）
    tight = elastic_orders(want, want, 1000, 0.2, [1.0], band, 5_000, 0, 0, (900, 1000, 1100))
    total = 0
    for qty, limit in tight:
        total += qty
        assert total * limit // 1000 <= 5_000
