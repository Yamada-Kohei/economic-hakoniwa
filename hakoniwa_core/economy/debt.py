"""国債の残高・利払い・償還の見込み、中央銀行の直接引き受け、国債の時価。

国債の銘柄は IslandState.bonds（発行ターン・期間・利率・発行額）に、持ち手ごとの額面は勘定
``<持ち手>/bond:<番号>/gov`` に持ちます。利払いは発行の次のターンから毎ターン「額面 × 利率 ÷ 12」、
満期のターン（発行ターン ＋ 期間）に額面を返します。

時価（フェーズ2-2）: 銘柄の値段は、残りの利払いと償還を、残りの期間の利回りで割り引いた額です。
利回りは残りの期間で分けた4つの区分（game.yaml の bonds.buckets）ごとに economy.finance["yields"]
に持ち、区分の間は区分を代表する残りのターン数（bonds.bucket_terms）の利回りを直線でつなぎます。

取得原価と実現損益: 持ち手ごとに、銘柄の取得原価（買った代金）を economy.finance["bond_cost"] に
持ちます。売る・償還される・債務不履行で切り下げられると、手放した額面の割合の取得原価と、受け取った
額との差を実現損益として economy.finance["bond_realized"] に足します（決算の段が損益に入れる）。
まだ売っていない値上がり益と値下がり損（時価 − 取得原価）は損益に入れず、自己資本にだけ数えます。
"""

from __future__ import annotations

from collections.abc import Sequence

from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.state import Bond, EconomyState, IslandState
from hakoniwa_core.units import BASIS

VALUED = "bonds_valued"  # island.scratch の印: 決算の段がこのターンの国債の時価を評価した
COST = "bond_cost"  # economy.finance: 持ち手 → {銘柄の番号（文字列）→ 取得原価}
REALIZED = "bond_realized"  # economy.finance: 持ち手 → 決算の段にまだ入れていない実現損益


def outstanding(island: IslandState, bond: Bond) -> int:
    """銘柄の残っている額面（持ち手の額面の合計）。"""
    return -ledger.balance(island, ledger.issued(ledger.GOV, ledger.bond_item(bond.id)))


def total_debt(island: IslandState) -> int:
    """政府の借金（国債の額面の合計。中央銀行が持つ分も含む）。"""
    # 銘柄ごとに outstanding を呼ぶのと同じ値。国債の利回りの式から1ターンに何十回も呼ばれ、銘柄も
    # 多いので、出し手の勘定の名前（ledger.issued(GOV, bond_item(番号))）をここで作って引く
    accounts, prefix = island.accounts, f"{ledger.GOV}/{ledger.ISSUED}/bond:"
    return -sum(accounts.get(f"{prefix}{b.id}", 0) for b in island.bonds)


def holders(island: IslandState, bond: Bond) -> dict[str, int]:
    """銘柄の持ち手と額面（持ち手の名前の順）。"""
    item = ledger.bond_item(bond.id)
    suffix = f"/{item}/{ledger.GOV}"
    found = {}
    for name in sorted(ledger.names_with(island, item)):
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
    bought(island, ledger.CB, bond.id, face)
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


REVENUE = "revenue_history"  # economy.finance: 国債と中央銀行からのお金を除く国の収入（ターンごと）


def revenue_kind(kind: str) -> bool:
    """国庫に入るお金のうち、返す力の分母に数える仕訳の種類か（国債の発行と中央銀行からの納付・
    貸し借りは借りたお金か、国が中央銀行に払った利子が戻ってきたものなので数えない）。"""
    return not (kind.startswith("bond") or kind.startswith("cb_"))


def market_interest(island: IslandState) -> int:
    """中央銀行以外の持ち手（市中銀行・外国の持ち手）に1ターンに払う利子（今の持ち手のまま）。

    中央銀行に払う利子は納付で国庫に戻るので数えない（回5、市場の分だけ）。"""
    accounts = island.accounts
    issued = f"{ledger.GOV}/{ledger.ISSUED}/bond:"
    total = 0
    for b in island.bonds:
        face = -accounts.get(f"{issued}{b.id}", 0)
        face -= accounts.get(f"{ledger.CB}/{ledger.bond_item(b.id)}/{ledger.GOV}", 0)
        total += ledger.interest(face, b.rate)
    return total


def annual_revenue(economy: EconomyState) -> int:
    """年の国の収入（直近12ターンの合計。12ターンに足りなければ平均の12倍。記録がなければ 0）。"""
    history = economy.finance.get(REVENUE, [])[-YEAR:]
    if not history:
        return 0
    return sum(history) * YEAR // len(history)


def burden(island: IslandState) -> float:
    """返す力の比: 中央銀行以外に払う利子（年）÷ 国の収入（年）。収入の記録がなければ 0、収入が
    0 で利子があれば無限大（利回りは上限に届く）。"""
    interest = market_interest(island) * YEAR
    if interest <= 0 or not island.economy.finance.get(REVENUE):
        return 0.0
    revenue = annual_revenue(island.economy)
    return interest / revenue if revenue > 0 else float("inf")


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


# --- 取得原価と実現損益 -------------------------------------------------------------


def start_costs(island: IslandState, turn: int, terms: Sequence[int]) -> None:
    """取得原価の記録がなければ、持っている銘柄をターン turn の時価で記録して始める（前の版の
    世界。前の版で損益に入れた評価損益を、売ったときにもう一度数えないように）。"""
    finance = island.economy.finance
    if COST in finance:
        return
    table: dict[str, dict[str, int]] = {}
    for bond in island.bonds:
        value = bond_price(island, bond, turn, terms)
        for holder, face in holders(island, bond).items():
            if face > 0:
                table.setdefault(holder, {})[str(bond.id)] = int(face * value)
    finance[COST] = table


def cost_of(island: IslandState, holder: str) -> int:
    """持ち手の国債の取得原価の合計。"""
    return sum(island.economy.finance.get(COST, {}).get(holder, {}).values())


def bought(island: IslandState, holder: str, number: int, amount: int) -> None:
    """持ち手が銘柄を代金 amount で手に入れた（取得原価に足す）。"""
    if amount <= 0:
        return
    costs = island.economy.finance.setdefault(COST, {}).setdefault(holder, {})
    costs[str(number)] = costs.get(str(number), 0) + amount


def released(
    island: IslandState, holder: str, number: int, face: int, held: int, received: int
) -> int:
    """持ち手が、持っていた額面 held のうち face を手放して received を受け取った（売った・償還
    された・切り下げられた）。手放した割合の取得原価との差を実現損益に足して返す。"""
    if face <= 0 or held <= 0:
        return 0
    table = island.economy.finance.setdefault(COST, {})
    costs = table.setdefault(holder, {})
    key = str(number)
    cost = costs.get(key, held)  # 記録がなければ額面で買ったとみなす
    gone = cost if face >= held else cost * face // held
    if face >= held:
        costs.pop(key, None)
    else:
        costs[key] = cost - gone
    if not costs:
        table.pop(holder, None)
    gain = received - gone
    if gain:
        realized = island.economy.finance.setdefault(REALIZED, {})
        realized[holder] = realized.get(holder, 0) + gain
    return gain


def traded(
    island: IslandState, number: int, seller: str, buyer: str, face: int, amount: int
) -> None:
    """売り手が銘柄の額面 face を代金 amount で買い手に渡す（勘定を動かす前に呼ぶ）。"""
    item = ledger.bond_item(number)
    held = ledger.balance(island, ledger.account(seller, item, ledger.GOV))
    released(island, seller, number, face, held, amount)
    bought(island, buyer, number, amount)


def move_costs(island: IslandState, source: str, target: str) -> None:
    """持ち手 source の取得原価と、まだ決算に入れていない実現損益を target に移す（銀行の
    破綻処理）。"""
    finance = island.economy.finance
    costs = finance.get(COST, {}).pop(source, None)
    if costs:
        mine = finance[COST].setdefault(target, {})
        for key, cost in costs.items():
            mine[key] = mine.get(key, 0) + cost
    gain = finance.get(REALIZED, {}).pop(source, 0)
    if gain:
        finance[REALIZED][target] = finance[REALIZED].get(target, 0) + gain
