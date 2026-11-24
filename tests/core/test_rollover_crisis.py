"""借り換えの危機（bonds.rollover_crisis。国債の会議 #3981 の案 A）: 中央銀行が買い支えない国は、
借り換えの売れ残りを次のターンの削れる支出でしのぎ、それでも払えない分だけ不履行にする。"""

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import BONDS, Payer, debt, funds
from hakoniwa_core.modules.bonds import Bonds, rollover_crisis
from hakoniwa_core.state import WorldState
from tests.core.test_finance import context
from tests.core.test_rules import GENERAL, home, household, make_island, with_economy
from tests.core.test_rules import game as game  # noqa: F401

FACE = 2_000_000
WAGES = 1_000_000  # 公務員の賃金（先に払う項目。削れない）
WORKS = 1_600_000  # 公共事業費（削れる項目）


def setup(game, monkeypatch, short):
    """独立した中央銀行の島に、銀行が持つ満期の国債 FACE を置き、銀行が国債を買わない（不履行の
    後で市場が閉じた）ターンにする。国庫は、次のターンの支出を残すと元本と利払いに short
    足りない額。"""
    monkeypatch.setitem(game.config.raw["bonds"], "rollover_crisis", True)
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    island.policy.cb_independent = True
    world = WorldState(seed=1, turn=4, islands={island.id: island})
    ctx = context(game, world, "bonds")
    bond = debt.underwrite(island, FACE, 1, 0, ctx.turn - 1)  # このターンに満期（利子なし）
    bank = ledger.bank_entity(island.economy.banks[0])
    ledger.transfer(island, ledger.bond_item(bond.id), ledger.GOV, ledger.CB, bank, FACE, "test")
    debt.bought(island, bank, bond.id, FACE)
    island.economy.finance["default_turn"] = ctx.turn - 1  # 銀行は入札に来ない
    state = funds.fiscal_state(island)
    state["reserved"], state["caps"] = {}, {}
    state["used"] = {"civil_servants": WAGES, "construction": WORKS}
    interest = debt.due(island, ctx.turn + 1, redemptions=False)
    planned = WAGES + WORKS + interest
    keep = planned + debt.due(island, ctx.turn) - short
    payer = Payer(ledger.GOV, BONDS)
    extra = funds.available(island, payer) - keep
    ledger.pay(island, ledger.GOV, household(GENERAL), extra, "test")
    assert funds.available(island, payer) == keep
    return island, ctx, bond, bank, payer


def crisis(island):
    return [e for e in island.log if e.kind.startswith("bond_rollover_")]


def test_austerity_absorbs_the_unsold_rollover_without_default(game, monkeypatch):
    short = WORKS // 2
    island, ctx, bond, bank, payer = setup(game, monkeypatch, short)
    assert rollover_crisis(island, ctx)
    stage = Bonds()
    stage._roll_over(island, ctx, payer)
    [event] = crisis(island)
    assert event.kind == "bond_rollover_austerity"
    assert event.args == {
        "principal": FACE,
        "raised": 0,
        "room": WORKS,
        "cut": short,
        "central_bank": 0,
        "default": 0,
    }
    assert debt.holders(island, bond) == {bank: FACE}
    stage._pay(island, ctx, payer)
    assert bond not in island.bonds  # 次のターンの公共事業費を削って全額を返した
    assert not [e for e in island.log if "default" in e.kind and e.kind != event.kind]
    assert island.scratch["bond_stats"]["austerity"] == short


def test_only_the_shortfall_beyond_cuttable_spending_defaults(game, monkeypatch):
    lost = 200_000
    island, ctx, bond, bank, payer = setup(game, monkeypatch, WORKS + lost)
    stage = Bonds()
    stage._roll_over(island, ctx, payer)
    [event] = crisis(island)
    assert event.kind == "bond_rollover_default"
    assert (event.args["cut"], event.args["default"]) == (WORKS, lost)
    assert debt.holders(island, bond) == {bank: FACE - lost}  # 払えない分だけ切り捨て
    assert island.economy.finance[debt.REALIZED][bank] == -lost
    assert island.economy.finance["default_turn"] == ctx.turn
    stage._pay(island, ctx, payer)
    assert bond not in island.bonds  # 残りは全額返した（銘柄ごとの不履行にならない）
    kinds = [e.kind for e in island.log]
    assert "bond_default" not in kinds  # 不履行の罰（利回りの上乗せと市場の閉鎖）だけ付く
    assert any(k.startswith("bond_default_penalty") for k in kinds)
    assert funds.available(island, payer) >= WAGES  # 公務員の賃金の分は国庫に残る


@pytest.mark.parametrize(
    ("independent", "unsold", "on"),
    [(True, "underwrite", True), (False, "cut", True), (False, "underwrite", False)],
)
def test_only_a_dependent_central_bank_that_underwrites_backs_the_rollover(
    game, monkeypatch, independent, unsold, on
):
    island, ctx, _, _, _ = setup(game, monkeypatch, 0)
    island.policy.cb_independent = independent
    island.policy.bond_unsold = unsold
    assert rollover_crisis(island, ctx) is on
    monkeypatch.setitem(game.config.raw["bonds"], "rollover_crisis", False)
    assert not rollover_crisis(island, ctx)
