"""国債の残高・利払い・償還の見込みと、中央銀行の直接引き受け。

国債の銘柄は IslandState.bonds（発行ターン・期間・利率・発行額）に、持ち手ごとの額面は勘定
``<持ち手>/bond:<番号>/gov`` に持ちます。利払いは発行の次のターンから毎ターン「額面 × 利率 ÷ 12」、
満期のターン（発行ターン ＋ 期間）に額面を返します。フェーズ2-1 では国債は中央銀行が直接
引き受けます（入札と売買の市場はフェーズ2-2）。
"""

from __future__ import annotations

from hakoniwa_core import ledger
from hakoniwa_core.state import Bond, EconomyState, IslandState

YEAR = 12


def outstanding(island: IslandState, bond: Bond) -> int:
    """銘柄の残っている額面（持ち手の額面の合計）。"""
    return -ledger.balance(island, ledger.issued(ledger.GOV, ledger.bond_item(bond.id)))


def total_debt(island: IslandState) -> int:
    """政府の借金（国債の額面の合計。中央銀行が持つ分も含む）。"""
    return sum(outstanding(island, b) for b in island.bonds)


def holders(island: IslandState, bond: Bond) -> dict[str, int]:
    """銘柄の持ち手と額面（持ち手の名前の順）。"""
    suffix = f"/{ledger.bond_item(bond.id)}/{ledger.GOV}"
    found = {}
    for name in sorted(island.accounts):
        if name.endswith(suffix) and not ledger.is_issued(name):
            found[ledger.parse(name)[0]] = island.accounts[name]
    return found


def interest_due(island: IslandState, bond: Bond, turn: int) -> dict[str, int]:
    """ターン turn に払う利子（持ち手ごと）。発行したターンには払わない。"""
    if turn <= bond.issued or turn > bond.maturity:
        return {}
    out = {}
    for holder, face in holders(island, bond).items():
        amount = ledger.interest(face, bond.rate)
        if amount > 0:
            out[holder] = amount
    return out


def due(island: IslandState, turn: int, redemptions: bool = True) -> int:
    """ターン turn の利払いと償還の合計（今の持ち手のまま）。redemptions が偽なら利払いだけ。"""
    total = 0
    for bond in island.bonds:
        total += sum(interest_due(island, bond, turn).values())
        if redemptions and turn >= bond.maturity:
            total += outstanding(island, bond)
    return total


def issue_bond(island: IslandState, face: int, term: int, rate: int, turn: int) -> Bond:
    """国債の銘柄を作る（持ち物の勘定は呼ぶ側が作る）。"""
    island.economy.bond_seq += 1
    bond = Bond(id=island.economy.bond_seq, issued=turn, term=term, rate=rate, face=face)
    island.bonds.append(bond)
    return bond


def underwrite(island: IslandState, face: int, term: int, rate: int, turn: int) -> Bond | None:
    """政府が国債を出し、中央銀行が額面で引き受ける（国庫が face だけ増える）。"""
    if face <= 0:
        return None
    bond = issue_bond(island, face, term, rate, turn)
    item = ledger.bond_item(bond.id)
    ledger.create(island, ledger.CB, item, ledger.GOV, face, "bond_issue")
    ledger.pay(island, ledger.CB, ledger.GOV, face, "bond_issue")
    return bond


def annual_gdp(economy: EconomyState) -> int:
    """年の GDP（直近12ターンの合計。12ターンに足りなければ平均の12倍）。"""
    history = economy.gdp_history[-YEAR:]
    if not history:
        return 0
    return sum(history) * YEAR // len(history)


def debt_ratio(island: IslandState) -> float:
    """政府の借金の対年GDP比（GDP の記録がなければ 0）。"""
    gdp = annual_gdp(island.economy)
    return total_debt(island) / gdp if gdp > 0 else 0.0
