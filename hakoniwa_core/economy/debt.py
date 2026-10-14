"""国債の残高・利払い・償還の見込み、中央銀行の直接引き受け、国債の時価。

国債の銘柄は IslandState.bonds（発行ターン・期間・利率・発行額）に、持ち手ごとの額面は勘定
``<持ち手>/bond:<番号>/gov`` に持ちます。利払いは発行の次のターンから毎ターン「額面 × 利率 ÷ 12」、
満期のターン（発行ターン ＋ 期間）に額面を返します。

時価（フェーズ2-2）: 銘柄の値段は、残りの利払いと償還を、残りの期間の利回りで割り引いた額です。
利回りは残りの期間で分けた4つの区分（game.yaml の bonds.buckets）ごとに economy.finance["yields"]
に持ち、区分の間は区分を代表する残りのターン数（bonds.bucket_terms）の利回りを直線でつなぎます。
"""

from __future__ import annotations

from collections.abc import Sequence

from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.state import Bond, EconomyState, IslandState
from hakoniwa_core.units import BASIS

YEAR = 12
VALUED = "bonds_valued"  # island.scratch の印: 決算の段がこのターンの国債の時価を評価した


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


def reopen_turn(island: IslandState, config: GameConfig) -> int | None:
    """債務不履行の後、銀行がまた国債を買うようになるターン（不履行のターン ＋
    bonds.default_exclusion_turns）。不履行の記録がなければ None。"""
    since = island.economy.finance.get("default_turn")
    if since is None:
        return None
    return since + config.get("bonds.default_exclusion_turns")


def market_closed(island: IslandState, config: GameConfig, turn: int) -> bool:
    """債務不履行の後で、銀行が国債を買わない（入札で売れず、売買の市場でも買わない）ターンか。"""
    reopen = reopen_turn(island, config)
    return reopen is not None and turn < reopen


# --- 時価（フェーズ2-2） -----------------------------------------------------------


def remaining(bond: Bond, turn: int) -> int:
    """ターン turn の後に残る利払いの回数（= 満期までのターン数）。"""
    return max(0, bond.maturity - turn)


def bucket_of(months: int, edges: Sequence[int]) -> int:
    """残りのターン数 months の区分の番号（0 から len(edges)）。"""
    for i, edge in enumerate(edges):
        if months < edge:
            return i
    return len(edges)


def curve_yield(yields: Sequence[int], terms: Sequence[int], months: int) -> int:
    """残りのターン数 months の利回り（区分の利回りを代表のターン数で直線でつなぐ）。"""
    if not yields:
        return 0
    if months <= terms[0]:
        return yields[0]
    for i in range(1, len(terms)):
        if months <= terms[i]:
            a, b = terms[i - 1], terms[i]
            return yields[i - 1] + (yields[i] - yields[i - 1]) * (months - a) // (b - a)
    return yields[-1]


def price(bond: Bond, turn: int, rate: int) -> float:
    """額面1あたりの値段: 残りの利払い（毎ターン 利率 ÷ 12）と償還を、利回り rate（年率、
    1万分の1）の1ターンあたりの複利で割り引いた額。"""
    months = remaining(bond, turn)
    if months <= 0:
        return 1.0
    coupon = bond.rate / BASIS / YEAR
    j = max(0.0, rate / BASIS / YEAR)
    if j == 0:
        return 1.0 + coupon * months
    factor = (1 + j) ** -months
    return coupon * (1 - factor) / j + factor


def bond_price(island: IslandState, bond: Bond, turn: int, terms: Sequence[int]) -> float:
    """島の今の利回りの曲線での、銘柄の額面1あたりの値段。"""
    yields = island.economy.finance.get("yields") or []
    if not yields:
        return 1.0
    return price(bond, turn, curve_yield(yields, terms, remaining(bond, turn)))


def holdings_of(island: IslandState, holder: str) -> dict[int, int]:
    """持ち手 holder が持つ銘柄（番号 → 額面）。"""
    found = {}
    for name, value in ledger.holdings(island, holder, "bond:").items():
        found[int(ledger.parse(name)[1].split(":")[1])] = value
    return found


def market_value(island: IslandState, holder: str, turn: int, terms: Sequence[int]) -> int:
    """持ち手 holder の持つ国債の時価の合計。"""
    by_id = {b.id: b for b in island.bonds}
    total = 0.0
    for number, face in sorted(holdings_of(island, holder).items()):
        bond = by_id.get(number)
        total += face * (bond_price(island, bond, turn, terms) if bond is not None else 1.0)
    return int(total)
