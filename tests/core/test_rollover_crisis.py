"""借り換えの危機（bonds.rollover_crisis。国債の会議 #3981 の案 A）: 中央銀行が買い支えない国は、
借り換えの売れ残りを次のターンの削れる支出でしのぎ、それでも払えない分だけ不履行にする。"""

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import BONDS, Payer, banks, debt, funds
from hakoniwa_core.modules.bonds import (
    Bonds,
    _cb_interest,
    bond_yield,
    holding_cap,
    rollover_crisis,
    uncovered,
)
from hakoniwa_core.state import WorldState
from hakoniwa_core.units import to_basis
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
    # 中央銀行への利子（建国のときの国債）は納付で戻るので、借り換えと緊縮の額に入れない
    interest = debt.due(island, ctx.turn + 1, redemptions=False) - _cb_interest(
        island, ctx.turn + 1
    )
    planned = WAGES + WORKS + interest
    keep = planned + debt.due(island, ctx.turn) - _cb_interest(island, ctx.turn) - short
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
    # 公務員の賃金の分は国庫に残る（中央銀行への利子は払えれば払い、決算の段の納付で戻る）
    assert funds.available(island, payer) + _cb_interest(island, ctx.turn) >= WAGES


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


def test_unpaid_interest_defaults_only_the_shortfall_and_nets_the_central_bank(game, monkeypatch):
    """利払いに国庫が足りなければ、中央銀行への利子は納付と差し引き、市中の持ち手には足りない分だけ
    払わない（銘柄の額面を半分にする不履行にしない）。"""
    island, ctx, _, _, payer = setup(game, monkeypatch, 0)
    bank = ledger.bank_entity(island.economy.banks[0])
    bond = debt.underwrite(island, FACE, 24, 1200, ctx.turn - 2)  # 満期はまだ先
    ledger.transfer(island, ledger.bond_item(bond.id), ledger.GOV, ledger.CB, bank, FACE, "test")
    debt.bought(island, bank, bond.id, FACE)
    owed = debt.interest_due(island, bond, ctx.turn)[bank]
    assert owed > 0
    island.bonds = [bond]  # 満期の銘柄と建国のときの国債は外す
    cash = funds.available(island, payer)
    ledger.pay(island, ledger.GOV, household(GENERAL), cash - owed // 3, "test")
    Bonds()._pay(island, ctx, payer)
    assert debt.holders(island, bond) == {bank: FACE}
    [lost] = [e.args["amount"] for e in island.log if e.kind == "bond_coupon_default"]
    assert lost == owed - owed // 3
    assert "bond_default" not in [e.kind for e in island.log]


def need(island, ctx, gfn, capacity):
    island.scratch["bond_need"] = (ctx.turn, {"gfn": gfn, "capacity": capacity})


def test_uncovered_rollover_prices_the_expected_haircut(game, monkeypatch):
    """C（総資金需要で測る重さ）: 埋まらない割合 u の分を、切られる見込みの損 h = haircut × u の
    埋め合わせ h ÷ (1 − h) として利回りに上乗せする。切なら今と同じ。"""
    island, ctx, _, _, _ = setup(game, monkeypatch, 0)
    island.economy.finance.pop("default_turn")
    need(island, ctx, 1000, 800)  # u = 0.2
    base = bond_yield(island, 12, ctx)
    monkeypatch.setitem(game.config.raw["bonds"], "rollover_pricing", True)
    assert uncovered(island, ctx) == pytest.approx(0.2)
    loss = game.config.get("bonds.haircut") * 0.2
    assert bond_yield(island, 12, ctx) - base == pytest.approx(to_basis(loss / (1 - loss)), abs=2)
    need(island, ctx, 1000, 1200)
    assert bond_yield(island, 12, ctx) == base


def test_banks_stop_buying_once_the_expected_loss_eats_their_spare_capital(game, monkeypatch):
    """B: 銀行は、持ち高 × haircut × u が自己資本の余り（自己資本 − 規制の比率 × 融資）を超える分を
    入札で買わない。u が 0 なら上限はない。"""
    island, ctx, _, bank, _ = setup(game, monkeypatch, 0)
    monkeypatch.setitem(game.config.raw["bonds"], "rollover_pricing", True)
    need(island, ctx, 1000, 1000)
    assert holding_cap(island, ctx, bank) is None
    need(island, ctx, 1000, 500)  # u = 0.5
    worth = banks.net_worth(island, game.config, bank, ctx.turn)
    spare = worth - int(banks.regulatory(game.config) * banks.loans_of(island, bank))
    cap = holding_cap(island, ctx, bank)
    assert cap == int(max(0, spare) / (game.config.get("bonds.haircut") * 0.5))
    held = sum(debt.holdings_of(island, bank).values())
    filled, _ = Bonds._auction(island, ctx, 10**12, bond_yield(island, 12, ctx))
    assert filled.get(bank, 0) <= max(0, cap - held)


def test_rising_uncovered_share_raises_the_premium_and_shrinks_the_bank_limit(game, monkeypatch):
    """合成の 4 島（会議 #4025）: u = 0・0.5・0.9・1.0 で、上乗せは u とともに増え、銀行の上限は
    下がり、u = 1 では入札に札が入らず全額が売れ残る。"""
    island, ctx, _, bank, _ = setup(game, monkeypatch, 0)
    island.economy.finance.pop("default_turn")
    monkeypatch.setitem(game.config.raw["bonds"], "rollover_pricing", True)
    rows = []
    for gap in (0, 500, 900, 1000):
        need(island, ctx, 1000, 1000 - gap)
        rows.append((bond_yield(island, 12, ctx), holding_cap(island, ctx, bank)))
    yields = [y for y, _ in rows]
    assert yields == sorted(yields) and len(set(yields)) == 4
    assert rows[0][1] is None and rows[1][1] > rows[2][1] > rows[3][1] == 0
    filled, _ = Bonds._auction(island, ctx, 1_000_000, yields[-1])
    assert not any(ledger.is_bank(b) for b in filled)
