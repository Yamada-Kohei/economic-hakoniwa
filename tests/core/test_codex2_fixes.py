"""Codex の試し遊びの2回目（ターン50〜100）で見つかった不具合の直し。"""

from __future__ import annotations

import copy

from hakoniwa_core import ledger
from hakoniwa_core.economy import OWNERS, banks, debt, household
from hakoniwa_core.modules import failures
from hakoniwa_core.modules.bonds import Bonds
from tests.core.test_finance import books_ok, context, island_of, open_books
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401

# --- 政府が払えずに削った預金のログ（連合のターン61・63） ------------------------------


def test_transfer_logs_deposits_cut_when_the_government_cannot_pay(game, warmed, monkeypatch):
    """引き継ぎ・清算で、政府が払えずに保護される預金まで削った分も、ログの「預金の切り捨て」に
    入れる（保護されない預金を削った分だけが出ていた）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "mixed")  # 銀行が2つある島
    ctx = context(game, world)
    bank, other = banks.active_banks(island)[:2]
    worth = banks.net_worth(island, game.config, bank, world.turn)
    ledger.pay(island, bank, household(OWNERS), worth + 5_000_000, "dividend")
    monkeypatch.setattr(failures, "_government_pays", lambda *args, **kwargs: 0)  # 国庫も国債も
    monkeypatch.setattr(
        failures, "_cut_uninsured", lambda *args, **kwargs: 0
    )  # 保護されない預金がない
    island.journal.clear()
    open_books(island)
    failures.transfer(island, ctx, bank, other, liquidation=False)
    cut = sum(e.amount for e in island.journal if e.kind == "deposit_cut")
    done = next(e for e in island.log if e.kind == "bank_transferred")
    assert cut >= 5_000_000
    assert done.args["cut"] == cut
    assert done.args["insurance"] == 0
    books_ok(game, island)


# --- 救済のための国債が統計から抜ける（共和国のターン71） ------------------------------


def test_bond_stats_include_bonds_issued_to_rescue_a_bank(game, warmed):
    """破綻の段で救済のために出した国債も、そのターンの中央銀行の保有と引き受けの統計に入る（国債
    の段で統計を取った後に出すので、抜けていた）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")  # 売れ残りは中央銀行が引き受ける
    ctx = context(game, world)
    bank = banks.active_banks(island)[0]
    f = banks.facility_of(island, bank)
    f.economy["failed"] = world.turn
    island.economy.finance["resolve"] = {bank: failures.BAILOUT}
    island.economy.finance["default_turn"] = world.turn  # 銀行は買わず、中央銀行がみな引き受ける
    for loan in banks.loans_by_lender(island, bank):
        banks.write_off(island, loan, banks.outstanding(island, loan))
    banks.drop_settled(island)
    island.scratch.clear()
    open_books(island)
    money = ledger.money(island, ledger.GOV)  # 国庫を空にして、救済の額を国債で集める
    ledger.pay(island, ledger.GOV, household("general"), money, "grant")
    Bonds.record_stats(island)  # 国債の段で取った統計
    held = island.stats["bonds.central_bank"]
    failures.Failures().run_island(island, ctx)
    now = sum(debt.holdings_of(island, ledger.CB).values())
    assert now > held  # 救済のための国債を中央銀行が引き受けた
    assert island.stats["bonds.central_bank"] == now
    assert island.stats["bonds.underwritten"] == island.scratch["bond_stats"]["underwritten"] > 0
    books_ok(game, island)
