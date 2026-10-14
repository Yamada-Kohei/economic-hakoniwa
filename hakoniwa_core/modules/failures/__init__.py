"""破綻の段（フェーズ2-2）: 返済の遅れが続いた施設の倒産と、銀行の破綻・破綻処理。

施設の倒産（計画経済の島では起きない）: 融資の返済が loans.arrears_limit ターン続けて遅れた施設は倒
産し、止まって労働者は失業する。預金は貸し手に借入の割合で返し、返しきれない分は貸し手の損失（貸し倒
れ）、出資は 0 になる。施設はいちばん多く貸していた銀行が差し押さえ（economy["foreclosed"]）、
firms.foreclosure_turns ターンの間、帳簿の設備の値打ちの firms.foreclosure_price で売りに出す（買う
のは参入する民間か、国有化する政府。参入と撤退の段）。

銀行の破綻: 自己資本がマイナスになった銀行と、資金繰りが破綻した銀行（中央銀行の段）は破綻し、新しい
融資と預金の受け入れを止める。プレイヤーは命令 resolve_bank で処理を選ぶ（行政力を使わない）。次の
ターンまでに選ばれなければ「引き継ぎ」、引き継いでも自己資本比率が規制の値を下回らない銀行がなければ
「救済」にする。

返ってこないおそれの大きい融資は、破綻処理では安く見る（返済が遅れているか借り手が倒産した融資は 0、
借り手が動いていない融資は額面の banking.liquidation_price）。

- 救済: 政府が自己資本を「融資 ×（規制の値 + banking.risk_buffer）」と「預金 ×
  banking.capital_floor」の大きい方まで（資金繰りの破綻なら足りない準備預金も）注入し、銀行は国営銀
  行として残る（融資の少ない銀行が、次の貸し倒れですぐまた破綻しないように）。
- 引き継ぎ: 預金・融資・国債・銀行間の貸し借りを、健全な銀行のうち預金の最も多い銀行（引き継いでも
  自己資本比率が規制の値を下回らない銀行があれば、そのうち預金の最も多い銀行）に移す。危ない融資を
  安く見た分は、預金保険から引き継ぐ銀行に渡る（後で貸し倒れても引き継いだ銀行が倒れないように）。
- 清算: 融資をすべて額面の banking.liquidation_price で健全な銀行に売ったとして損失を数え、引き継ぎ
  と同じく移す（安く買えた分は買った銀行の得になる）。

引き継ぎと清算の損失（資産が負債に足りない分）は、保護されない預金（銀行ごとに上限額を超えた分）と、
破綻した銀行に貸していた銀行の貸し出しが、同じ割合で負う（破綻が連鎖することがある）。それでも足りない
分は政府が払う（預金保険）。政府のお金が足りなければ国債を出し、それでも足りなければ保護される預金も削る。

引き継ぐ健全な銀行がない島（銀行が1つなど）で引き継ぎか清算を選ぶと、政府が銀行を引き取り、出資を
消して、損失を同じ順番（保護されない預金と銀行間の貸し手、預金保護、保護される預金）で負わせて立て直す
（resolve_in_place）。

救済と預金保護に国庫から出すのは、次のターンの優先度の高い支払いを残した額まで（rescue_room）。
救済で債務超過を埋めきれなければ、残りは預金の切り捨てで埋める。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import Payer, banks, books, facility_entity, funds, owner_entity
from hakoniwa_core.modules.banking import is_planned
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import OWNER_STATE, Facility, IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import part, to_basis

BAILOUT = "bailout"
TRANSFER = "transfer"
LIQUIDATION = "liquidation"
METHODS = (BAILOUT, TRANSFER, LIQUIDATION)


class Failures(IslandStage):
    name = "failures"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        if not is_planned(ctx.defs, island):
            for f in sorted(island.facilities, key=lambda f: f.id):
                if "foreclosed" in f.economy:
                    continue
                entity = facility_entity(f.id)
                limit = ctx.config.get("loans.arrears_limit")
                if any(x.arrears >= limit for x in banks.loans_by_borrower(island, entity)):
                    bankrupt(island, ctx, f)
        self._resolve(island, ctx)
        for bank in banks.active_banks(island):
            f = banks.facility_of(island, bank)
            assert f is not None
            worth = banks.net_worth(island, ctx.config, bank, ctx.turn)
            if worth < 0 or f.economy.get("liquidity_failure") == ctx.turn:
                reason = "capital" if worth < 0 else "liquidity"
                f.economy["failed"] = ctx.turn
                island.log.append(
                    LogEntry(f"bank_failed_{reason}", {"x": f.x, "y": f.y, "facility": f.kind})
                )
                stats = island.stats
                stats["banks.failed"] = stats.get("banks.failed", 0) + 1
        island.stats["banks"] = len(banks.bank_list(island))

    @staticmethod
    def _resolve(island: IslandState, ctx: StageContext) -> None:
        orders: dict[str, str] = island.economy.finance.pop("resolve", {})
        for bank in banks.bank_list(island):
            f = banks.facility_of(island, bank)
            if f is None or f.economy.get("failed") is None:
                continue
            method = orders.get(bank)
            if method is None and f.economy["failed"] >= ctx.turn:
                continue  # このターンに破綻した: 次のターンまでプレイヤーの命令を待つ
            others = [b for b in banks.healthy_banks(island, ctx.config, ctx.turn) if b != bank]
            able = [b for b in others if _can_absorb(island, ctx, b, bank)]
            if method is None:
                method = TRANSFER if able else BAILOUT
            if method == BAILOUT:
                bailout(island, ctx, bank)
            elif not others:  # 引き継ぐ銀行がない: 政府が引き取り、預金の切り捨てで立て直す
                resolve_in_place(island, ctx, bank)
            else:
                target = (able or others)[0]
                transfer(island, ctx, bank, target, liquidation=method == LIQUIDATION)


def _can_absorb(island: IslandState, ctx: StageContext, target: str, bank: str) -> bool:
    """target が bank の融資を引き継いでも、自己資本比率が規制の値を下回らないか。"""
    worth = banks.net_worth(island, ctx.config, target, ctx.turn)
    loans = banks.loans_of(island, target) + banks.loans_of(island, bank)
    return loans <= 0 or worth >= banks.regulatory(ctx.config) * loans


# --- 施設の倒産 -------------------------------------------------------------------


def bankrupt(island: IslandState, ctx: StageContext, f: Facility) -> None:
    """施設を倒産させ、いちばん多く貸していた銀行が差し押さえる。"""
    entity = facility_entity(f.id)
    loans = sorted(banks.loans_by_borrower(island, entity), key=lambda x: x.id)
    owed = {x.id: banks.outstanding(island, x) for x in loans}
    total = sum(owed.values())
    money = ledger.money(island, entity)
    paid = 0
    for i, loan in enumerate(loans):
        share = money - paid if i == len(loans) - 1 else money * owed[loan.id] // max(1, total)
        share = min(share, owed[loan.id])
        banks.repay(island, loan, share)
        paid += share
    lender_totals: dict[str, int] = {}
    for loan in loans:
        lender_totals[loan.lender] = lender_totals.get(loan.lender, 0) + owed[loan.id]
        banks.write_off(island, loan, banks.outstanding(island, loan))
    banks.drop_settled(island)
    left = ledger.money(island, entity)
    owner = owner_entity(f)
    ledger.pay(island, entity, owner, left, "equity_return")
    _cancel_equity(island, entity)
    lender = max(lender_totals, key=lambda b: (lender_totals[b], b)) if lender_totals else None
    price = part(banks.book_capital(f), to_basis(ctx.config.get("firms.foreclosure_price")))
    f.economy["foreclosed"] = {"bank": lender, "since": ctx.turn, "price": price}
    f.economy["target"] = 0
    f.workers = 0
    island.log.append(LogEntry("facility_bankrupt", {"facility": f.kind, "x": f.x, "y": f.y}))
    stats = island.stats
    stats["firms.bankrupt"] = stats.get("firms.bankrupt", 0) + 1


def _cancel_equity(island: IslandState, entity: str) -> None:
    """entity への出資をすべて消す（持ち主の損）。"""
    suffix = f"/{ledger.EQUITY}/{entity}"
    for name, value in sorted(island.accounts.items()):
        if name.endswith(suffix) and not ledger.is_issued(name) and value > 0:
            holder = ledger.parse(name)[0]
            ledger.cancel(island, holder, ledger.EQUITY, entity, value, "equity_loss")


def close_foreclosure(island: IslandState, f: Facility) -> None:
    """差し押さえた施設のお金を、差し押さえた銀行に回収として渡す。"""
    seized = f.economy.pop("foreclosed", None) or {}
    bank = seized.get("bank")
    entity = facility_entity(f.id)
    money = ledger.money(island, entity)
    if bank and money > 0 and not banks.is_failed(island, bank):
        ledger.pay(island, entity, bank, money, "foreclosure_recovery")
        books.add(island, banks.bank_number(bank), books.WRITE_OFF, -money)
    elif money > 0:
        ledger.pay(island, entity, ledger.GOV, money, "foreclosure_recovery")


# --- 銀行の破綻処理 ---------------------------------------------------------------


def rescue_room(island: IslandState, ctx: StageContext) -> int:
    """国庫から銀行の救済と預金保護に出せる額。

    救済は支出の項目の外なので、次のターンの優先度の高い支払い（fiscal.payment_order で
    fiscal.rescue_after までの項目の、このターンに使った額と、次のターンの国債の利払い）を国庫に
    残す。残さないと、国債の不履行の後のように、救済が毎ターン国庫を空にして公務員を雇えず、
    利払いもできずに不履行が続く。
    """
    from hakoniwa_core.economy import BONDS
    from hakoniwa_core.economy.debt import due

    fiscal = ctx.config.get("fiscal")
    order = list(fiscal["payment_order"])
    last = fiscal.get("rescue_after")
    items = order[: order.index(last) + 1] if last in order else []
    spent = funds.spent(island)
    keep = sum(spent.get(item, 0) for item in items if item != BONDS)
    if BONDS in items:
        keep += due(island, ctx.turn + 1, redemptions=False)
    margin = to_basis(fiscal["reserve_margin"])
    keep += part(keep, margin)
    return max(0, funds.available(island, Payer(ledger.GOV)) - keep)


def _government_pays(
    island: IslandState, ctx: StageContext, target: str, amount: int, kind: str
) -> int:
    """政府が target に amount を払う（国庫に出せる額が足りなければ国債を出す）。払えた額を返す。"""
    from hakoniwa_core.modules.bonds import Bonds

    if amount <= 0:
        return 0
    short = amount - rescue_room(island, ctx)
    if short > 0:
        Bonds().raise_funds(island, ctx, short, "bonds_issued")
    paid = min(amount, rescue_room(island, ctx))
    funds.pay(island, Payer(ledger.GOV), target, paid, kind)
    return paid


def _cut_deposit(island: IslandState, bank: str, entity: str, amount: int) -> None:
    ledger.post(
        island,
        ledger.issued(bank, ledger.DEPOSIT),
        ledger.account(entity, ledger.DEPOSIT, bank),
        amount,
        "deposit_cut",
    )


def _cut_uninsured(island: IslandState, bank: str, loss: int) -> int:
    """損失 loss を、保護されない預金（預金者ごとに上限額を超えた分）と、bank に貸していた銀行の
    貸し出しが同じ割合で負う（破綻が連鎖することがある）。削った額を返す。"""
    if loss <= 0:
        return 0
    depositors = banks.depositors_of(island, bank)
    uninsured = {e: v - banks.layer_deposit_insured(island, e, v) for e, v in depositors.items()}
    lenders = {
        ledger.parse(name)[0]: value
        for name, value in sorted(island.accounts.items())
        if ledger.parse(name)[1:] == (ledger.INTERBANK, bank) and value > 0
    }
    exposed = sum(uninsured.values()) + sum(lenders.values())
    if exposed <= 0:
        return 0
    ratio = min(1.0, loss / exposed)
    total = 0
    for entity, amount in sorted(uninsured.items()):
        cut = int(amount * ratio)
        if cut > 0:
            _cut_deposit(island, bank, entity, cut)
            total += cut
    for lender, amount in sorted(lenders.items()):
        cut = int(amount * ratio)
        if cut > 0:
            ledger.cancel(island, lender, ledger.INTERBANK, bank, cut, "interbank_loss")
            books.add(island, banks.bank_number(lender), books.WRITE_OFF, cut)
            total += cut
    return total


def _cut_all(island: IslandState, bank: str, amount: int) -> int:
    """政府も払えない損失 amount を、残りの預金（保護される分も）から額の割合で削る。"""
    if amount <= 0:
        return 0
    remaining = banks.depositors_of(island, bank)
    total = sum(remaining.values())
    done = 0
    for entity, value in sorted(remaining.items()):
        cut = min(value, -(-amount * value // max(1, total)))
        cut = min(cut, amount - done)
        if cut > 0:
            _cut_deposit(island, bank, entity, cut)
            done += cut
    return done


def _bail_in(island: IslandState, bank: str, hole: int) -> int:
    """国庫で埋めきれない債務超過 hole を、預金の切り捨てで埋める（保護されない預金と銀行間の
    貸し手から、足りなければ保護される預金も）。削った額を返す。"""
    cut = _cut_uninsured(island, bank, hole)
    return cut + _cut_all(island, bank, hole - cut)


def bailout(island: IslandState, ctx: StageContext, bank: str) -> None:
    """救済: 政府が自己資本を目安（banks.capital_target）まで注入し、国営銀行として残す
    （返ってこないおそれの大きい融資は安く見る。banks.impaired_discount）。

    国庫に出せる額（rescue_room）と国債で集めた額で債務超過を埋めきれなければ、残りは預金の
    切り捨てで埋める（債務超過のまま残して、次のターンにまた破綻しないように）。
    """
    f = banks.facility_of(island, bank)
    assert f is not None
    config = ctx.config
    worth = banks.net_worth(island, config, bank, ctx.turn)
    worth -= banks.impaired_discount(island, config, bank)
    want = banks.capital_target(island, config, bank) - worth
    want = max(0, want) + max(0, -banks.excess_reserves(island, config, bank))
    _cancel_equity(island, bank)
    paid = _government_pays(island, ctx, bank, want, "bank_bailout")
    if paid > 0:
        ledger.create(island, ledger.GOV, ledger.EQUITY, bank, paid, "bank_bailout")
    cut = _bail_in(island, bank, -(worth + paid))
    f.owner = OWNER_STATE
    f.economy.pop("failed", None)
    f.economy.pop("liquidity_failure", None)
    island.log.append(LogEntry("bank_bailed_out", {"x": f.x, "y": f.y, "amount": paid}))
    if cut > 0:
        island.log.append(LogEntry("bank_bailed_in", {"x": f.x, "y": f.y, "cut": cut}))


def resolve_in_place(island: IslandState, ctx: StageContext, bank: str) -> None:
    """引き継ぐ銀行のない島の引き継ぎ・清算: 政府が銀行を引き取り、出資を消して、損失を保護され
    ない預金と銀行間の貸し手、預金保護（国庫）、保護される預金の順に負わせて立て直す。資金繰りの
    破綻なら、足りない準備預金も政府が出資する。救済と違い、自己資本の目安までは入れない。"""
    f = banks.facility_of(island, bank)
    assert f is not None
    config = ctx.config
    worth = banks.net_worth(island, config, bank, ctx.turn)
    worth -= banks.impaired_discount(island, config, bank)
    loss = max(0, -worth)
    _cancel_equity(island, bank)
    cut = _cut_uninsured(island, bank, loss)
    cover = loss - cut
    paid = _government_pays(island, ctx, bank, cover, "deposit_insurance")
    cut += _cut_all(island, bank, cover - paid)
    liquidity = max(0, -banks.excess_reserves(island, config, bank))
    injected = _government_pays(island, ctx, bank, liquidity, "bank_bailout")
    if injected > 0:
        ledger.create(island, ledger.GOV, ledger.EQUITY, bank, injected, "bank_bailout")
    f.owner = OWNER_STATE
    f.economy.pop("failed", None)
    f.economy.pop("liquidity_failure", None)
    island.log.append(
        LogEntry(
            "bank_resolved_in_place",
            {"x": f.x, "y": f.y, "cut": cut, "insurance": paid + injected},
        )
    )


def transfer(
    island: IslandState, ctx: StageContext, bank: str, target: str, liquidation: bool
) -> None:
    """引き継ぎ・清算: 損失を保護されない預金と銀行間の貸し手が負い、残りを政府が払って、
    すべてを target に移す。"""
    config = ctx.config
    f = banks.facility_of(island, bank)
    assert f is not None
    worth = banks.net_worth(island, config, bank, ctx.turn)
    if liquidation:
        discount = 1 - config.get("banking.liquidation_price")
        worth -= int(banks.loans_of(island, bank) * discount)
    else:
        worth -= banks.impaired_discount(island, config, bank)
    loss = max(0, -worth)
    cut_total = _cut_uninsured(island, bank, loss)
    cover = max(0, loss - cut_total)
    paid = _government_pays(island, ctx, bank, cover, "deposit_insurance")
    _cut_all(island, bank, cover - paid)  # 政府も払えない分は、残りの預金から比例で削る
    _cancel_equity(island, bank)
    _move_everything(island, bank, target)
    fac.vacate(island, f)
    island.economy.banks.remove(f.id)
    for entity, number in list(island.economy.deposit_bank.items()):
        if number == f.id:
            island.economy.deposit_bank[entity] = banks.bank_number(target)
    stock.move_to_warehouse(f.stock, island.warehouse, ctx.turn, ctx.defs.goods)
    book = island.economy.finance.get("bond_book", {})
    if bank in book:
        book[target] = book.get(target, 0) + book.pop(bank)
    kind = "bank_liquidated" if liquidation else "bank_transferred"
    t = banks.facility_of(island, target)
    island.log.append(
        LogEntry(
            kind,
            {
                "x": f.x,
                "y": f.y,
                "to_x": t.x if t else -1,
                "to_y": t.y if t else -1,
                "cut": cut_total,
                "insurance": paid,
            },
        )
    )


def _move_everything(island: IslandState, bank: str, target: str) -> None:
    """bank の持ち物と、bank が出している持ち物（出資を除く）を target に移す。"""
    for entity, amount in banks.depositors_of(island, bank).items():
        ledger.move_deposit(island, entity, bank, target, amount, "bank_transfer")
    for name in sorted(island.accounts):
        value = island.accounts.get(name, 0)
        if value == 0:
            continue
        holder, item, issuer = ledger.parse(name)
        if item == ledger.ISSUED or item == ledger.RESERVE:
            continue
        if holder == bank:  # 銀行が持つ物（融資・国債・銀行間の貸し出し）
            if value > 0:
                ledger.post(
                    island, ledger.account(target, item, issuer), name, value, "bank_transfer"
                )
        elif (
            issuer == bank and item != ledger.EQUITY
        ):  # 銀行が出している物（銀行間・中央銀行の借り入れ）
            ledger.cancel(island, holder, item, bank, value, "bank_transfer")
            if holder != target:
                ledger.create(island, holder, item, target, value, "bank_transfer")
    for loan in island.loans:
        if loan.lender == bank:
            loan.lender = target
    reserves = banks.reserves_of(island, bank)
    if reserves > 0:
        ledger.post(island, ledger.reserve(target), ledger.reserve(bank), reserves, "bank_transfer")
    elif reserves < 0:
        ledger.post(
            island, ledger.reserve(bank), ledger.reserve(target), -reserves, "bank_transfer"
        )
    # 銀行どうしの貸し借りが同じ銀行の中に入ったら消す
    own = ledger.balance(island, ledger.account(target, ledger.INTERBANK, target))
    if own > 0:
        ledger.cancel(island, target, ledger.INTERBANK, target, own, "bank_transfer")


# --- 命令 -------------------------------------------------------------------------


def check_resolve_bank(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    f = island.facility_at(payload["x"], payload["y"])
    if f is None or defs.facilities[f.kind].get("category") != "finance":
        raise OrderFailed("reason_not_bank")
    if f.economy.get("failed") is None:
        raise OrderFailed("reason_bank_not_failed")
    if payload["method"] not in METHODS:
        raise OrderFailed("reason_invalid_payload")


def resolve_bank(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """破綻した銀行の処理（救済・引き継ぎ・清算）を選ぶ（このターンの破綻の段で行う）。"""
    f = island.facility_at(payload["x"], payload["y"])
    assert f is not None
    table = island.economy.finance.setdefault("resolve", {})
    table[ledger.bank_entity(f.id)] = payload["method"]
    island.log.append(
        LogEntry(
            "bank_resolution_chosen",
            {"x": f.x, "y": f.y, "method": f"resolution_{payload['method']}"},
        )
    )


def register(registry: Registry) -> None:
    registry.add_stage(Failures())
    registry.add_order(OrderHandler("resolve_bank", execute=resolve_bank, check=check_resolve_bank))
