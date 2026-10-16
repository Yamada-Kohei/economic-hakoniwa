"""賃金と税の未払い、倒産と撤退の後の分け方（バランス調整の回3）。

賃金の遅配: 払い手（政府・独立採算の施設）が賃金を払えなかった分は「未払い賃金」として残します
（持ち物 wage_arrears。持ち手は家計の層、出し手は払い手）。働く人はすぐには辞めず、払われないまま
働き続け、未払いが続く月数に応じて少しずつ辞めます（雇用の段。labor.arrears_quit）。お金が入れば、
新しく雇う前に未払いを先に払います（雇用の段の初め。政府は予算の段で支出の項目 arrears に先に
取り置く）。現実の例は1990年代のロシアで、公務員や工場の賃金の遅配が数か月続き、多くの人は働き
続けました。

未払いの税: 施設が法人税を払えなかった分は「未払いの税」（tax_arrears。持ち手は政府）として残し、
未払い賃金の次に払います。

倒産と撤退の後の分け方（Codex E24・E25）: 施設のお金と、設備と在庫を売った代金を、現実の倒産の
手続きと同じ順で分けます。先に未払い賃金、次に未払いの税、残りを貸し手に貸した額の割合で分け、
余れば持ち主に返します。倒産のときに払いきれなかった分は、差し押さえの記録（economy["foreclosed"]
の claims）に残し、後で設備が売れたときや取り壊したときに同じ順で分けます。撤退や取り壊しで残った
在庫は、国が市場の値段で引き取り（国庫が足りなければ足りる分だけ）、その代金も同じ順で分けます。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import ledger
from hakoniwa_core import stock as stock_module
from hakoniwa_core.economy import Payer, funds
from hakoniwa_core.economy.households import add_income, layer_of
from hakoniwa_core.state import IslandState, Stock
from hakoniwa_core.units import money_of, part

WAGE_ARREARS = "wage_arrears"
TAX_ARREARS = "tax_arrears"
ARREARS = "arrears"  # 政府の支出の項目（未払い賃金の支払い。fiscal.payment_order の先頭）

WAGES, TAXES, LOANS = "wages", "taxes", "loans"  # 差し押さえの記録に残す請求の種類


def total(island: IslandState, issuer: str, item: str = WAGE_ARREARS) -> int:
    """issuer が item（未払い賃金か未払いの税）で負っている額の合計。"""
    return -ledger.balance(island, ledger.issued(issuer, item))


def owed(island: IslandState, issuer: str, item: str = WAGE_ARREARS) -> dict[str, int]:
    """issuer が item で負っている額（持ち手ごと）。"""
    if total(island, issuer, item) <= 0:
        return {}
    suffix = f"/{item}/{issuer}"
    return {
        ledger.parse(name)[0]: value
        for name, value in sorted(island.accounts.items())
        if value > 0 and name.endswith(suffix) and not ledger.is_issued(name)
    }


def add_wages(island: IslandState, issuer: str, holder: str, amount: int) -> None:
    """払えなかった賃金 amount を、holder（家計の層）への未払い賃金として残す。"""
    if amount > 0:
        ledger.create(island, holder, WAGE_ARREARS, issuer, amount, "wage_arrears")


def add_tax(island: IslandState, issuer: str, amount: int) -> None:
    """払えなかった法人税 amount を、政府への未払いの税として残す。"""
    if amount > 0:
        ledger.create(island, ledger.GOV, TAX_ARREARS, issuer, amount, "tax_arrears")


def _shares(amount: int, claims: dict[str, int]) -> dict[str, int]:
    """amount を請求 claims の額の割合で分ける（端数は名前の順の最後に。請求より多くは
    払わない）。"""
    whole = sum(claims.values())
    if amount <= 0 or whole <= 0:
        return {}
    amount = min(amount, whole)
    out, given = {}, 0
    names = sorted(claims)
    for i, name in enumerate(names):
        share = amount - given if i == len(names) - 1 else amount * claims[name] // whole
        share = min(share, claims[name])
        if share > 0:
            out[name] = share
            given += share
    return out


def _received(island: IslandState, holder: str, amount: int) -> None:
    """家計の層が受け取った未払い賃金を、所得税を引いて層の所得にする（賃金の段と同じ）。"""
    layer = layer_of(holder)
    if layer is None or amount <= 0:
        return
    tax = part(amount, island.policy.income_tax)
    ledger.pay(island, holder, ledger.GOV, tax, "tax_income")
    add_income(island, layer, amount - tax)


def settle(island: IslandState, payer: Payer) -> int:
    """payer の未払い賃金、次に未払いの税を、いま使えるお金の分だけ払う。払った額を返す。"""
    issuer = payer.entity
    paid = 0
    for item, kind in ((WAGE_ARREARS, "wage_arrears_paid"), (TAX_ARREARS, "tax_arrears_paid")):
        claims = owed(island, issuer, item)
        if not claims:
            continue
        for holder, share in _shares(funds.available(island, payer), claims).items():
            funds.pay(island, payer, holder, share, kind)
            ledger.cancel(island, holder, item, issuer, share, kind)
            if item == WAGE_ARREARS:
                _received(island, holder, share)
            paid += share
    return paid


def write_off(island: IslandState, issuer: str) -> dict[str, Any]:
    """倒産した issuer の払いきれなかった未払い賃金と未払いの税を帳簿から消し、後で分けるための
    請求の記録（{"wages": {持ち手: 額}, "taxes": 額}）を返す。"""
    claims: dict[str, Any] = {WAGES: {}, TAXES: 0}
    for holder, value in owed(island, issuer, WAGE_ARREARS).items():
        ledger.cancel(island, holder, WAGE_ARREARS, issuer, value, "wage_arrears_lost")
        claims[WAGES][holder] = value
    for holder, value in owed(island, issuer, TAX_ARREARS).items():
        ledger.cancel(island, holder, TAX_ARREARS, issuer, value, "tax_arrears_lost")
        claims[TAXES] += value
    return claims


def pay_claims(
    island: IslandState, payer: str, amount: int, claims: dict[str, Any], lender_paid
) -> int:
    """payer のお金 amount を、請求の記録 claims に決まった順（未払い賃金 → 未払いの税 → 貸し手の
    割合）で払い、claims を払った分だけ減らす。貸し手への支払いは lender_paid(銀行, 額) が払う
    （回収の帳簿付けは呼ぶ側）。払い残したお金を返す。"""
    left = amount
    wages: dict[str, int] = claims.get(WAGES, {})
    for holder, share in _shares(left, wages).items():
        ledger.pay(island, payer, holder, share, "wage_arrears_paid")
        _received(island, holder, share)
        wages[holder] -= share
        left -= share
    claims[WAGES] = {h: v for h, v in wages.items() if v > 0}
    tax = min(left, claims.get(TAXES, 0))
    if tax > 0:
        ledger.pay(island, payer, ledger.GOV, tax, "tax_arrears_paid")
        claims[TAXES] -= tax
        left -= tax
    loans: dict[str, int] = claims.get(LOANS, {})
    for bank, share in _shares(left, loans).items():
        lender_paid(bank, share)
        loans[bank] -= share
        left -= share
    claims[LOANS] = {b: v for b, v in loans.items() if v > 0}
    return left


def stock_value(held: Stock, prices: dict[str, int]) -> int:
    """在庫を市場の値段で数えた値打ち。"""
    return sum(money_of(prices.get(g, 0), q) for g, q in stock_module.totals(held).items())


def sell_stock_to_state(island: IslandState, entity: str, held: Stock) -> int:
    """撤退や取り壊しで残った在庫の代金を、国が市場の値段で施設に払う（国庫が足りなければ足りる分
    だけ）。在庫は呼ぶ側が国の倉庫に移す。払った額を返す。"""
    value = stock_value(held, island.economy.prices)
    price = min(value, funds.available(island, Payer(ledger.GOV)))
    if price > 0:
        funds.pay(island, Payer(ledger.GOV), entity, price, "sale")
    return price
