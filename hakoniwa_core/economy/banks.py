"""市中銀行の帳簿の見方と、融資・預金の部品（フェーズ2-2）。

銀行の貸借対照表は勘定から計算します（銀行の建物は設備の値打ちに数えない）。

| 資産 | 負債と純資産 |
|---|---|
| 準備預金 | 預金 |
| 融資 | 銀行間の借り入れ |
| 国債（時価） | 中央銀行からの借り入れ |
| 銀行間の貸し出し | 自己資本（残り） |

自己資本比率は「自己資本 ÷ 融資の残高」です（国債と準備預金は危なさを 0 として数えない）。
規制の値（game.yaml の banking.regulatory_ratio）を下回った銀行は、新しく貸しません。

融資は IslandState.loans の1件ごとに、残高を勘定 ``<銀行>/loan:<番号>/<借り手>`` に持ちます。
貸すと借り手の預金が生まれ、返すと消えます。金利は毎ターン計算し直す変動金利です（式 loan_rate）。
破綻した銀行は施設の economy["failed"] に破綻したターンを持ち、貸し出しと預金の受け入れを止めます。
国債の債務不履行で損をした銀行は、economy["crunch_until"] のターンまで新しい融資を止めます
（返す元金の借り換えだけ続ける。banking.default_crunch_turns）。
"""

from __future__ import annotations

import math
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.economy import arrears, books, debt, household
from hakoniwa_core.economy.households import layer_of
from hakoniwa_core.state import OWNER_STATE, Facility, IslandState, Loan
from hakoniwa_core.units import BASIS, money_of, part, to_basis

WORKING = "working"  # 運転資金
CONSTRUCTION = "construction"  # 建設資金
MORTGAGE = "mortgage"  # 住宅ローン（家計の層が借りる。回3）

FINANCE_VERSION = 2  # economy.finance["version"]: 2 はフェーズ2-2 の金融が始まった島


# --- 銀行 -----------------------------------------------------------------------


def bank_list(island: IslandState) -> list[str]:
    """島の銀行の主体（設立した順）。"""
    return [ledger.bank_entity(n) for n in island.economy.banks]


def bank_number(bank: str) -> int:
    return int(bank.split(":")[1])


def facility_of(island: IslandState, bank: str) -> Facility | None:
    return island.facility(bank_number(bank))


def is_failed(island: IslandState, bank: str) -> bool:
    f = facility_of(island, bank)
    return f is None or f.economy.get("failed") is not None


def active_banks(island: IslandState) -> list[str]:
    """破綻していない銀行。"""
    return [b for b in bank_list(island) if not is_failed(island, b)]


def deposits_of(island: IslandState, bank: str) -> int:
    """銀行が出している預金のうち、政府の預金を除いた合計（準備の最低限・銀行の大きさ・画面の預金に
    使う）。政府の預金（回5）は担保付きの預金として扱い、準備の最低限・利子・保護・切り捨ての外に
    置く（アメリカの国庫の税・借入勘定と同じ）。"""
    return -ledger.balance(island, ledger.issued(bank, ledger.DEPOSIT)) - government_deposit(
        island, bank
    )


def government_deposit(island: IslandState, bank: str) -> int:
    """政府が bank に預けている額（国庫の余り。回5）。"""
    return ledger.balance(island, ledger.account(ledger.GOV, ledger.DEPOSIT, bank))


def reserves_of(island: IslandState, bank: str) -> int:
    return ledger.balance(island, ledger.reserve(bank))


def required_reserves(island: IslandState, config: GameConfig, bank: str) -> int:
    """最低限の準備預金（預金 × banking.reserve_ratio）。"""
    return part(deposits_of(island, bank), to_basis(config.get("banking.reserve_ratio")))


def excess_reserves(island: IslandState, config: GameConfig, bank: str) -> int:
    return reserves_of(island, bank) - required_reserves(island, config, bank)


def loans_of(island: IslandState, bank: str) -> int:
    """銀行の融資の残高の合計。"""
    return sum(ledger.holdings(island, bank, "loan:").values())


def interbank_lent(island: IslandState, bank: str) -> int:
    return sum(ledger.holdings(island, bank, ledger.INTERBANK).values())


def interbank_borrowed(island: IslandState, bank: str) -> int:
    return -ledger.balance(island, ledger.issued(bank, ledger.INTERBANK))


# 銀行間と中央銀行から前のターンに借りて、このターンの銀行の段で返した額
# （island.scratch。銀行 → 額）
OVERNIGHT = "overnight_repaid"


def bonds_held(island: IslandState, bank: str) -> int:
    """bank が持つ自国の国債の額面の合計。"""
    return sum(ledger.holdings(island, bank, "bond:").values())


def overnight_repaid(island: IslandState, bank: str) -> int:
    """bank がこのターンの銀行の段で、銀行間と中央銀行に返した額（前のターンに借りた額）。"""
    return island.scratch.get(OVERNIGHT, {}).get(bank, 0)


def cb_borrowed(island: IslandState, bank: str) -> int:
    return -ledger.balance(island, ledger.issued(bank, ledger.CB_LOAN))


def bond_terms(config: GameConfig) -> list[int]:
    return list(config.get("bonds.bucket_terms"))


def bond_value(island: IslandState, config: GameConfig, holder: str, turn: int) -> int:
    """持ち手の国債の時価。"""
    return debt.market_value(island, holder, turn, bond_terms(config))


def net_worth(island: IslandState, config: GameConfig, bank: str, turn: int) -> int:
    """銀行の自己資本（資産 − 負債。国債は時価。外貨と外国の国債は今のレートで自国通貨に直す。
    フェーズ3-3）。"""
    from hakoniwa_core.trade import abroad

    assets = (
        reserves_of(island, bank)
        + loans_of(island, bank)
        + bond_value(island, config, bank, turn)
        + interbank_lent(island, bank)
        + abroad.foreign_assets(island, bank)
    )
    liabilities = (
        deposits_of(island, bank)
        + government_deposit(island, bank)
        + interbank_borrowed(island, bank)
        + cb_borrowed(island, bank)
    )
    return assets - liabilities


def capital_ratio(island: IslandState, config: GameConfig, bank: str, turn: int) -> float:
    """自己資本比率（融資がなければ無限大）。"""
    loans = loans_of(island, bank)
    worth = net_worth(island, config, bank, turn)
    if loans <= 0:
        return math.inf if worth >= 0 else -math.inf
    return worth / loans


def regulatory(config: GameConfig) -> float:
    return config.get("banking.regulatory_ratio")


def healthy_banks(island: IslandState, config: GameConfig, turn: int) -> list[str]:
    """破綻しておらず、自己資本比率が規制の値以上の銀行（預金の多い順）。"""
    found = [
        b
        for b in active_banks(island)
        if capital_ratio(island, config, b, turn) >= regulatory(config)
    ]
    return sorted(found, key=lambda b: (-deposits_of(island, b), b))


def lending_stopped(island: IslandState, bank: str, turn: int) -> bool:
    """国債の債務不履行で損をした銀行が、新しい融資を止めているターンか（施設の
    economy["crunch_until"] のターンの前まで。返す元金の借り換えは続ける）。"""
    f = facility_of(island, bank)
    return f is not None and turn < f.economy.get("crunch_until", turn)


# 銀行の施設の economy: 経費を切り詰めている間、直近 1 年の利益がプラスの続いたターン数
LEAN = "lean"


def watch_costs(config: GameConfig, f: Facility) -> None:
    """銀行の経費の切り詰め（回7 後半）: 直近 1 年の利益がマイナスになった銀行は、職員の下限
    （banking.staff_floor）を外して預金と融資の量に見合う人数まで減らす（支店と人を減らす。地方銀行
    の店舗の統合）。直近 1 年の利益がプラスのまま banking.lean_turns ターン続いたら元に戻す（すぐ
    戻してまた赤字にならないように）。会計の段で利益を記録した後に呼ぶ。"""
    profits = f.economy.get("profits") or []
    if len(profits) < TURNS_PER_YEAR:
        return
    if sum(profits) < 0:
        f.economy[LEAN] = 0
    elif LEAN in f.economy:
        f.economy[LEAN] += 1
        if f.economy[LEAN] >= config.get("banking.lean_turns"):
            del f.economy[LEAN]


def staff_wanted(island: IslandState, config: GameConfig, defs: Any, f: Facility) -> int:
    """銀行が雇いたい職員の数: 預金と融資の合計が「平均賃金 × banking.staff_volume」に届けば定義の
    人数、それより小さい銀行はその割合（下限は定義の人数の banking.staff_floor）。預金の集まらない
    銀行が、定義の人数の賃金を払い続けて赤字で破綻していた（試し遊び4回目）。経費を切り詰めている
    銀行（watch_costs）は下限なし。"""
    need = fac.workers_of(defs, f)
    if need <= 0:
        return 0
    bank = ledger.bank_entity(f.id)
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    full = config.get("banking.staff_volume") * wage
    volume = deposits_of(island, bank) + loans_of(island, bank)
    floor = 0.0 if LEAN in f.economy else config.get("banking.staff_floor")
    share = max(floor, min(1.0, volume / max(1, full)))
    return max(1, min(need, math.ceil(need * share)))


def labor_ratio(island: IslandState, config: GameConfig, defs: Any, bank: str) -> float:
    """銀行の職員が足りている割合（雇いたい人数に対して。0 の銀行は新しく貸さない）。"""
    f = facility_of(island, bank)
    if f is None:
        return 0.0
    need = staff_wanted(island, config, defs, f)
    return 1.0 if need <= 0 else min(1.0, f.workers / need)


def deposit_rate_of(island: IslandState, bank: str) -> int:
    f = facility_of(island, bank)
    if f is None:
        return 0
    return f.economy.get("deposit_rate", island.policy.deposit_rate)


def margin_of(island: IslandState, config: GameConfig, bank: str) -> int:
    f = facility_of(island, bank)
    if f is None:
        return to_basis(config.get("banking.margin"))
    return f.economy.get("margin", to_basis(config.get("banking.margin")))


def roe(island: IslandState, config: GameConfig, bank: str, turn: int) -> float:
    """自己資本利益率（直近の利益の年率 ÷ 自己資本）。記録がなければ 0。"""
    f = facility_of(island, bank)
    profits = (f.economy.get("profits") or []) if f is not None else []
    worth = net_worth(island, config, bank, turn)
    if not profits or worth <= 0:
        return 0.0
    return sum(profits) * TURNS_PER_YEAR / len(profits) / worth


# --- 預金の保護 -----------------------------------------------------------------


def insured(island: IslandState, entity: str) -> int | None:
    """主体の預金のうち、銀行ごとに守られる上限額（None は無制限）。

    家計の層は「人数 × 上限額」、施設などは1つあたりの上限額。
    """
    limit = island.policy.deposit_insurance
    if limit < 0:
        return None
    layer = layer_of(entity)
    if layer is not None:
        persons = island.economy.households.get(layer, {}).get("persons", 0)
        return persons * limit
    return limit


# --- 融資 -----------------------------------------------------------------------


def outstanding(island: IslandState, loan: Loan) -> int:
    return ledger.balance(
        island, ledger.account(loan.lender, ledger.loan_item(loan.id), loan.borrower)
    )


def loans_by_borrower(island: IslandState, borrower: str) -> list[Loan]:
    return [x for x in island.loans if x.borrower == borrower]


def loans_by_lender(island: IslandState, lender: str) -> list[Loan]:
    return [x for x in island.loans if x.lender == lender]


def borrower_facility(island: IslandState, borrower: str) -> Facility | None:
    """借り手が施設ならその施設（家計の層の住宅ローンなら None）。"""
    if not borrower.startswith("fac:"):
        return None
    return island.facility(int(borrower.split(":")[1]))


def impaired(island: IslandState, loan: Loan) -> bool:
    """返ってこないおそれの大きい融資: 返済が遅れているか、借り手が動いていない（閉鎖中・差し押さえ
    中・稼働率0、工事なら資材が買えずに止まっている）。家計の住宅ローンは遅れだけで見る。"""
    if loan.arrears > 0:
        return True
    if layer_of(loan.borrower) is not None:
        return False
    f = borrower_facility(island, loan.borrower)
    if f is None or "closing" in f.economy or "foreclosed" in f.economy:
        return True
    if not f.built:
        return f.ext.get("no_materials_turns", 0) > 0
    return f.operating_rate <= 0


def defaulting(island: IslandState, loan: Loan) -> bool:
    """返済が遅れているか、借り手が倒産した（いない・差し押さえ中の）融資。"""
    if loan.arrears > 0:
        return True
    if layer_of(loan.borrower) is not None:
        return False
    f = borrower_facility(island, loan.borrower)
    return f is None or "foreclosed" in f.economy


def impaired_discount(island: IslandState, config: GameConfig, bank: str) -> int:
    """銀行の、返ってこないおそれの大きい融資を安く見たときに減る額。返済が遅れている融資は
    0 と見て（倒産すると貸し倒れになり、差し押さえた施設を売れるのは後なので）、借り手が動いて
    いない融資は額面の banking.liquidation_price で見る。"""
    keep = config.get("banking.liquidation_price")
    total = 0
    for x in loans_by_lender(island, bank):
        if defaulting(island, x):
            total += outstanding(island, x)
        elif impaired(island, x):
            total += int(outstanding(island, x) * (1 - keep))
    return total


def capital_target(island: IslandState, config: GameConfig, bank: str) -> int:
    """銀行が持っておく自己資本の目安: 「融資 ×（規制の値 + banking.risk_buffer）」と「預金 ×
    banking.capital_floor」の大きい方。配当はこれを超える分から払い、救済はここまで注入する。"""
    ratio = regulatory(config) + config.get("banking.risk_buffer")
    floor = config.get("banking.capital_floor") * deposits_of(island, bank)
    return int(max(ratio * loans_of(island, bank), floor))


def exposure(island: IslandState, bank: str, borrower: str) -> int:
    """銀行 bank から borrower への融資の残高の合計。民営の企業の施設は、同じ企業の施設への残高も
    足す（大口融資の規制は企業ごとに見る。回7）。"""
    from hakoniwa_core.economy.companies import group_borrowers

    group = group_borrowers(island, borrower)
    return sum(
        outstanding(island, x) for x in island.loans if x.lender == bank and x.borrower in group
    )


def exposure_limit(island: IslandState, config: GameConfig, bank: str, turn: int) -> int:
    """大口融資の規制: 1つの借り手への融資の残高の上限（自己資本 × banking.large_exposure）。"""
    worth = max(0, net_worth(island, config, bank, turn))
    return int(worth * config.get("banking.large_exposure"))


def exposure_room(
    island: IslandState, config: GameConfig, bank: str, borrower: str, turn: int, due: int = 0
) -> int:
    """大口融資の規制で、bank が borrower に新しく貸せる額。このターンに返す元金 due の分は借り換え
    られる。自己資本が減って上限を超えた残高も、借り換えて保つことはできる（増やせない）。"""
    held = exposure(island, bank, borrower)
    limit = max(exposure_limit(island, config, bank, turn), held)
    return max(0, limit - held + due)


def capital_room(island: IslandState, config: GameConfig, bank: str, turn: int) -> int:
    """自己資本比率の規制で、bank が新しく貸せる額（貸した後も規制の値を下回らない分）。"""
    worth = net_worth(island, config, bank, turn)
    return max(0, int(worth / regulatory(config)) - loans_of(island, bank))


def debt_of(island: IslandState, borrower: str) -> int:
    """借り手の借入の合計。"""
    return sum(outstanding(island, x) for x in loans_by_borrower(island, borrower))


def book_capital(f: Facility) -> int:
    """設備の帳簿の値打ち（設備の額 − 減価償却した額）。"""
    return max(0, f.economy.get("capital", 0) - f.economy.get("depreciated", 0))


def inventory_value(island: IslandState, f: Facility) -> int:
    prices = island.economy.prices
    return sum(money_of(prices.get(g, 0), q) for g, q in stock.totals(f.stock).items())


def assets_of(island: IslandState, f: Facility, entity: str) -> int:
    """施設の資産（設備の値打ち + 預金 + 在庫）。"""
    return book_capital(f) + ledger.money(island, entity) + inventory_value(island, f)


def net_assets(island: IslandState, f: Facility, entity: str, extra: int = 0) -> int:
    """施設の純資産（資産 − 借入 − 未払いの賃金と税。0 未満なら 0）。国有化・民営化の値段に使う。

    extra は、これから施設に入れるお金（民営化の前に出資する運転資金の見積もり）。0 で止める前に
    足す。
    """
    unpaid = arrears.total(island, entity) + arrears.total(island, entity, arrears.TAX_ARREARS)
    return max(0, assets_of(island, f, entity) + extra - debt_of(island, entity) - unpaid)


def lend(
    island: IslandState,
    lender: str,
    borrower: str,
    amount: int,
    purpose: str,
    term: int,
    turn: int,
    rate: int,
    repay_from: int | None = None,
) -> Loan | None:
    """銀行 lender が borrower に amount を貸す（借り手の預金が生まれる）。

    運転資金は、同じ銀行から借りている運転資金に足して1件にまとめる（返す期間は今から数え直す）。
    住宅ローンも同じ銀行と層ごとに1件にまとめ、残りの期間を残高の重みで混ぜ直す（毎月借りても
    件数が積もらない。回3）。
    """
    if amount <= 0:
        return None
    if purpose == MORTGAGE:
        for loan in island.loans:
            if loan.lender == lender and loan.borrower == borrower and loan.purpose == MORTGAGE:
                held = outstanding(island, loan)
                left = remaining_turns(loan, turn)
                ledger.create(island, lender, ledger.loan_item(loan.id), borrower, amount, "loan")
                ledger.pay(island, lender, borrower, amount, "loan")
                loan.principal = held + amount
                loan.term = max(1, (held * left + amount * term) // loan.principal)
                loan.start, loan.rate = turn, rate
                loan.installment = -(-loan.principal // loan.term)
                return loan
    if purpose == WORKING:
        for loan in island.loans:
            if loan.lender == lender and loan.borrower == borrower and loan.purpose == WORKING:
                ledger.create(island, lender, ledger.loan_item(loan.id), borrower, amount, "loan")
                ledger.pay(island, lender, borrower, amount, "loan")
                loan.principal = outstanding(island, loan)
                loan.start, loan.term, loan.rate = turn, term, rate
                loan.installment = -(-loan.principal // max(1, term))
                loan.repay_from = turn + 1
                return loan
    economy = island.economy
    economy.loan_seq += 1
    loan = Loan(
        id=economy.loan_seq,
        lender=lender,
        borrower=borrower,
        purpose=purpose,
        principal=amount,
        term=term,
        start=turn,
        rate=rate,
        installment=-(-amount // max(1, term)),
        repay_from=turn + 1 if repay_from is None else repay_from,
    )
    island.loans.append(loan)
    ledger.create(island, lender, ledger.loan_item(loan.id), borrower, amount, "loan")
    economy.deposit_bank.setdefault(borrower, bank_number(lender))
    ledger.pay(island, lender, borrower, amount, "loan")
    return loan


def move_mortgages(island: IslandState, source: str, target: str, moved: int, persons: int) -> None:
    """層を移った moved 人（元の層は移る前 persons 人）の住宅ローンを、預金と同じく1人あたりの額で
    移った先の層に付け替える（失業した人は借金を持って失業者の層へ移り、職に就けば戻る。回3）。"""
    for loan in sorted(loans_by_borrower(island, source), key=lambda x: x.id):
        if loan.purpose != MORTGAGE:
            continue
        amount = outstanding(island, loan) * moved // persons
        if amount <= 0:
            continue
        item = ledger.loan_item(loan.id)
        ledger.cancel(island, loan.lender, item, source, amount, "layer_transfer")
        loan.principal -= min(loan.principal, amount)
        dest = next(
            (
                x
                for x in island.loans
                if x.lender == loan.lender and x.borrower == target and x.purpose == MORTGAGE
            ),
            None,
        )
        if dest is None:
            island.economy.loan_seq += 1
            dest = Loan(
                id=island.economy.loan_seq,
                lender=loan.lender,
                borrower=target,
                purpose=MORTGAGE,
                principal=0,
                term=loan.term,
                start=loan.start,
                rate=loan.rate,
                installment=0,
                repay_from=loan.repay_from,
            )
            island.loans.append(dest)
        item = ledger.loan_item(dest.id)
        ledger.create(island, dest.lender, item, target, amount, "layer_transfer")
        dest.principal += amount
        dest.installment = -(-dest.principal // remaining_turns(dest, dest.start))


def remaining_turns(loan: Loan, turn: int) -> int:
    """融資を返し終えるまでの残りのターン数（1 以上）。"""
    return max(1, loan.start + loan.term - turn)


def repay(island: IslandState, loan: Loan, amount: int, kind: str = "loan_repayment") -> None:
    """元金を返す（借り手の預金が消える）。"""
    if amount <= 0:
        return
    ledger.pay(island, loan.borrower, loan.lender, amount, kind)
    ledger.cancel(island, loan.lender, ledger.loan_item(loan.id), loan.borrower, amount, kind)


def write_off(island: IslandState, loan: Loan, amount: int) -> None:
    """返ってこない元金を、貸し手の損失として消す。"""
    if amount <= 0:
        return
    ledger.cancel(
        island, loan.lender, ledger.loan_item(loan.id), loan.borrower, amount, "loan_write_off"
    )
    books.add(island, bank_number(loan.lender), books.WRITE_OFF, amount)


def drop_settled(island: IslandState) -> None:
    """返し終わった融資を消す。"""
    island.loans = [x for x in island.loans if outstanding(island, x) > 0]


def debt_service(island: IslandState, borrower: str, turn: int) -> int:
    """借り手が毎ターン返す額の見込み（今の金利の利子 ＋ 返す時期に入った元金）。"""
    total = 0
    for loan in loans_by_borrower(island, borrower):
        balance = outstanding(island, loan)
        total += ledger.interest(balance, loan.rate)
        if turn + 1 >= loan.repay_from and loan.purpose != WORKING:
            total += min(balance, loan.installment)
    return total


def loan_rate(island: IslandState, ctx: Any, bank: str, borrower: str, extra: int = 0) -> int:
    """融資の金利: r_L = r_P + m + ρ × D ÷ A（名前付きの式 loan_rate の既定）。

    m は銀行ごとの利ざや、D は借り手の借入（extra は新しく借りる額）、A は資産。経済体制が政策融資を
    する体制（regimes.yaml の policy_loans。混合経済）では、国営銀行が基幹産業（経済体制で国営と
    決まっている施設）に貸すときの利ざやを 0 にする。
    """
    config = ctx.config
    f = island.facility(int(borrower.split(":")[1])) if borrower.startswith("fac:") else None
    margin = margin_of(island, config, bank)
    lender = facility_of(island, bank)
    regime = ctx.defs.regimes.get(island.regime.get("economy", ""), {})
    if f is not None and lender is not None and regime.get("policy_loans"):
        if lender.owner == OWNER_STATE and f.kind in regime.get("state_owned", ()):
            margin = 0
    rate = island.policy.policy_rate + margin
    if f is not None:
        assets = assets_of(island, f, borrower) + extra
        debt_now = debt_of(island, borrower) + extra
        if assets > 0:
            rate += to_basis(config.get("banking.rho")) * min(debt_now, assets) // assets
    return max(0, rate)


def ebitda(f: Facility) -> list[int]:
    return list(f.economy.get("ebitda") or [])


def layer_deposit_insured(island: IslandState, entity: str, amount: int) -> int:
    """銀行1つの預金 amount のうち守られる額。"""
    limit = insured(island, entity)
    return amount if limit is None else min(amount, limit)


def depositors_of(island: IslandState, bank: str) -> dict[str, int]:
    """銀行の預金者と額（預金者の名前の順。政府の預金は除く）。"""
    suffix = f"/{ledger.DEPOSIT}/{bank}"
    found = {}
    for name in sorted(island.accounts):
        if (
            name.endswith(suffix)
            and not ledger.is_issued(name)
            and not name.startswith(f"{ledger.GOV}/")
        ):
            value = island.accounts[name]
            if value:
                found[ledger.parse(name)[0]] = value
    return found


def household_entities(island: IslandState) -> list[str]:
    return [household(layer) for layer in sorted(island.economy.households)]


def annual(rate: int) -> float:
    return rate / BASIS
