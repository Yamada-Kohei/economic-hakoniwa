"""組み替えの切り下げの上限（bonds.restructure_cap。G3a）と、利払いの不足でも先に削る
（bonds.coupon_austerity。G3c）。国債の会議 #5039・設計 BK-55。

G3a: 組み替えの切り下げを銀行ごとに、切り下げ後の自己資本が最低線（規制の比率 × 融資）を
下回らない分までに抑える。止めた分は、線より上に資本のある銀行は期間の延長、線以下だが
自己資本の残っている銀行は利率の引き下げ、切る前から自己資本が 0 以下の銀行は今の扱いで
受け、組み替えごとに銀行ごとの記録を残す。
G3c: 借り換えの危機で国債の支払いに国庫が足りないときも、削れる支出の取り置きを先に使い、
残りの不足だけを払わない。"""

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import banks, debt, funds
from hakoniwa_core.modules.bonds import Bonds, bond_yield, capped_cut
from hakoniwa_core.units import part, to_basis
from tests.core.test_rollover_crisis import FACE, LADDER, WORKS, _restructure_setup
from tests.core.test_rollover_crisis import setup as crisis_setup
from tests.core.test_rules import GENERAL, household
from tests.core.test_rules import game as game  # noqa: F401


def test_capped_cut_solves_the_general_form():
    """上限の解き方: 線が動かなければ「自己資本 − 線」が上限。本来の切り下げ額と持ち高も
    上限になる。線が切り下げで動く一般形（線が切った額の半分だけ上がる）でも式で解く。"""
    assert capped_cut(1000, lambda x: 300, 10**9, 10**9) == 700
    assert capped_cut(1000, lambda x: 300, 10**9, 500) == 500
    assert capped_cut(1000, lambda x: 300, 400, 10**9) == 400
    line_at = lambda x: 300 + x // 2  # noqa: E731
    want = max(x for x in range(2000) if 1000 - x >= line_at(x))
    assert capped_cut(1000, line_at, 10**9, 10**9) == want
    assert capped_cut(300, lambda x: 300, 10**9, 10**9) == 0
    assert capped_cut(-5, lambda x: 300, 10**9, 10**9) == 0


def test_cap_off_keeps_the_g2_restructure(game, monkeypatch):
    """restructure_cap が切なら、組み替えは G2 のまま（上限の記録は残らない）。"""
    island, ctx, bond, later, bank, payer = _restructure_setup(game, monkeypatch)
    market = [
        (h, f) for b in island.bonds for h, f in debt.holders(island, b).items() if h != ledger.CB
    ]
    cut = sum(f // 2 for _, f in market)  # haircut 0.5
    Bonds()._roll_over(island, ctx, payer)
    stats = island.scratch["bond_stats"]
    assert stats["restructure_cut"] == cut
    assert "restructure_stopped" not in stats
    assert "restructure_banks" not in stats


def capped_setup(game, monkeypatch, capital_of, loans=1_000_000):
    """G2 の比べの場面に restructure_cap を入れ、銀行の自己資本と融資を固定する。
    capital_of は（最低線, 銀行）→ 自己資本。最低線は規制の比率 × 融資で数える。"""
    monkeypatch.setitem(game.config.raw["bonds"], "restructure_cap", True)
    island, ctx, bond, later, bank, payer = _restructure_setup(game, monkeypatch)
    line = int(banks.regulatory(game.config) * loans)
    monkeypatch.setattr(banks, "loans_of", lambda i, b: loans)
    monkeypatch.setattr(banks, "net_worth", lambda i, c, b, t: capital_of(line, b))
    return island, ctx, bond, later, bank, payer, line


def records_of(island):
    return {r["bank"]: r for r in island.scratch["bond_stats"]["restructure_banks"]}


def test_the_cap_stops_the_cut_at_the_capital_line(game, monkeypatch):
    """線より上に資本のある銀行: 切り下げは線ちょうどで止まり、止めた分は期間の延長で受ける。
    銀行ごとの記録（capital_before・min_line・差・持ち高・h_max・預金・融資停止）も残る。"""
    spare = 10_000
    island, ctx, bond, later, bank, payer, line = capped_setup(
        game, monkeypatch, lambda line, b: line + spare
    )
    total_before = debt.total_debt(island)
    Bonds()._roll_over(island, ctx, payer)
    stats = island.scratch["bond_stats"]
    records = records_of(island)
    rec = records[bank]
    assert rec["min_line"] == line and rec["gap"] == spare
    assert rec["actual"] == spare  # 本来の切り下げ（額面の半分）よりずっと小さい
    assert rec["capital_before"] - rec["actual"] == rec["min_line"]  # 線ちょうどで止まる
    assert rec["stopped"] == rec["intended"] - spare and rec["group"] == "line"
    assert rec["holdings"] > 0 and rec["h_max"] == pytest.approx(spare / rec["holdings"])
    assert "deposits" in rec and "lending_stopped" in rec
    for r in records.values():  # どの銀行も線を割らない（線までか、本来の切り下げまで）
        assert r["capital_before"] - r["actual"] >= r["min_line"] or r["actual"] == r["intended"]
    assert stats["restructure_extended"] == sum(
        r["stopped"] for r in records.values() if r["group"] == "line"
    )
    assert stats["restructure_stopped"] == (
        stats["restructure_extended"] + stats["restructure_rate_cut"] + stats["restructure_left"]
    )
    assert stats["restructure_hmax"] == pytest.approx(min(r["h_max"] for r in records.values()))
    assert debt.total_debt(island) == total_before - stats["restructure_cut"]
    Bonds.record_stats(island)
    assert island.stats["bonds.restructure_extended"] == stats["restructure_extended"]
    assert island.stats["bonds.restructure_hmax"] == pytest.approx(stats["restructure_hmax"])


def test_a_bank_without_capital_is_cut_nothing_and_recorded_apart(game, monkeypatch):
    """切る前から自己資本が 0 以下の銀行（h_max = 0 の組）: 1 円も切らず、止めた分は
    「線で止めた分」と分けて、今の扱いに残した額として記録する（#5042 の 2）。"""
    island, ctx, bond, later, bank, payer, line = capped_setup(
        game, monkeypatch, lambda line, b: -1
    )
    Bonds()._roll_over(island, ctx, payer)
    stats = island.scratch["bond_stats"]
    records = records_of(island)
    rec = records[bank]
    assert rec["group"] == "nocap" and rec["h_max"] == 0.0
    assert rec["actual"] == 0 and rec["stopped"] == rec["intended"] > 0
    assert stats["restructure_stopped_nocap"] == sum(
        r["stopped"] for r in records.values() if r["group"] == "nocap"
    )
    assert stats["restructure_left"] == stats["restructure_stopped_nocap"]
    assert stats["restructure_extended"] == 0 and stats["restructure_rate_cut"] == 0


def test_a_bank_below_the_line_swaps_the_stopped_part_at_a_reduced_rate(game, monkeypatch):
    """線以下だが自己資本の残っている銀行: 切らず、止めた分は利率を下げたラダーに替える
    （利率の引き下げで受ける）。残りはふつうのラダーに替える。"""
    island, ctx, bond, later, bank, payer, line = capped_setup(
        game, monkeypatch, lambda line, b: line // 2
    )
    assert line > 0
    yields = {t: bond_yield(island, t, ctx) for t in LADDER}
    Bonds()._roll_over(island, ctx, payer)
    stats = island.scratch["bond_stats"]
    records = records_of(island)
    rec = records[bank]
    assert rec["group"] == "rate" and rec["actual"] == 0
    assert rec["stopped"] == rec["intended"] > 0
    assert stats["restructure_rate_cut"] == sum(
        r["stopped"] for r in records.values() if r["group"] == "rate"
    )
    haircut = to_basis(game.config.get("bonds.haircut"))
    new = [b for b in island.bonds if b.issued == ctx.turn]
    low_face = 0
    std_face = 0
    for b in new:
        low_rate = yields[b.term] - part(yields[b.term], haircut)
        face = debt.holders(island, b).get(bank, 0)
        if b.rate == low_rate and low_rate < yields[b.term]:
            low_face += face
        elif b.rate == yields[b.term]:
            std_face += face
    assert low_face == rec["stopped"]  # 止めた分は利率を下げた銘柄に替わる
    assert std_face == rec["holdings"] - rec["stopped"]  # 残りはふつうのラダー
    assert low_face + std_face == rec["holdings"] - rec["actual"]


def coupon_setup(game, monkeypatch, held_of, on=True):
    """利払いの不足の場面（借り換えの危機）: 満期の先の銘柄の利子が、国庫の自由なお金では
    半分しか払えない。削れる支出（公共事業費）の取り置きの残りが held_of(利子) ある。"""
    island, ctx, _bond, _bank, payer = crisis_setup(game, monkeypatch, 0)
    if on:
        monkeypatch.setitem(game.config.raw["bonds"], "coupon_austerity", True)
    bank = ledger.bank_entity(island.economy.banks[0])
    bond = debt.underwrite(island, FACE, 24, 1200, ctx.turn - 2)  # 満期はまだ先
    ledger.transfer(island, ledger.bond_item(bond.id), ledger.GOV, ledger.CB, bank, FACE, "test")
    debt.bought(island, bank, bond.id, FACE)
    owed = debt.interest_due(island, bond, ctx.turn)[bank]
    assert owed > 0
    island.bonds = [bond]
    state = funds.fiscal_state(island)
    state["reserved"] = {"construction": WORKS + held_of(owed)}
    cash = funds.available(island, payer)
    ledger.pay(island, ledger.GOV, household(GENERAL), cash - owed // 2, "test")
    assert funds.available(island, payer) == owed // 2
    return island, ctx, bond, bank, payer, owed


def test_coupon_austerity_pays_the_interest_from_cuttable_reservations(game, monkeypatch):
    """G3c: 利払いが足りないときも、先に削れる支出の取り置きを使って全額払う（利払いの
    不足の不履行にならない）。崩した分は緊縮として数える。"""
    island, ctx, bond, bank, payer, owed = coupon_setup(
        game, monkeypatch, held_of=lambda owed: owed
    )
    gap = owed - owed // 2
    Bonds()._pay(island, ctx, payer)
    assert "bond_coupon_default" not in [e.kind for e in island.log]
    assert debt.holders(island, bond) == {bank: FACE}
    stats = island.scratch["bond_stats"]
    assert stats["coupon_austerity"] == gap and stats["austerity"] == gap
    state = funds.fiscal_state(island)
    assert state["reserved"]["construction"] == WORKS + owed - gap  # 取り置きが崩れた分だけ減る
    Bonds.record_stats(island)
    assert island.stats["bonds.coupon_austerity"] == gap


def test_coupon_austerity_leaves_only_the_remainder_unpaid(game, monkeypatch):
    """G3c: 削れる取り置きで届かない分だけが、利払いの不足の不履行になる。"""
    island, ctx, bond, bank, payer, owed = coupon_setup(game, monkeypatch, held_of=lambda owed: 100)
    gap = owed - owed // 2
    assert gap > 100
    Bonds()._pay(island, ctx, payer)
    [lost] = [e.args["amount"] for e in island.log if e.kind == "bond_coupon_default"]
    assert lost == gap - 100
    assert island.scratch["bond_stats"]["coupon_austerity"] == 100


def test_without_coupon_austerity_the_shortfall_defaults_as_before(game, monkeypatch):
    """coupon_austerity が切なら、取り置きがあっても今までどおり不足分を払わない。"""
    island, ctx, bond, bank, payer, owed = coupon_setup(
        game, monkeypatch, held_of=lambda owed: owed, on=False
    )
    Bonds()._pay(island, ctx, payer)
    [lost] = [e.args["amount"] for e in island.log if e.kind == "bond_coupon_default"]
    assert lost == owed - owed // 2
    assert island.scratch.get("bond_stats", {}).get("coupon_austerity", 0) == 0
    assert funds.fiscal_state(island)["reserved"]["construction"] == WORKS + owed
