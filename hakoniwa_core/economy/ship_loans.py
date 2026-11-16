"""船の借り（W-281。2026-11-15。束 C1）。

海運業が船を買う代金の loans.ship_ltv（8 割）までを、船を担保にした長い借り（用途 banks.SHIP）で
借ります。元金は loans.ship_turns ターン（20 年。寿命 25 年の 8 割）で毎ターン同じ額を返します
（現実の船舶融資は船を担保に船価の 6〜8 割。5〜7 年で残りをまとめて返す形が多く、元金の割り
振りは 15〜18 年が普通。前は 10 年で、1 隻の稼ぎより返済が重かった。掲示板の案 3）。頭金（残りの
2 割）は自分で用意します。前は船の代金を運転資金（6 ターンで返す短い借り）で借り、返せずに海運業
が倒れていた。

流れ:

1. 市場の段（ターン T）: 買いたい船（ships_wanted）と、そのうちの買い替え（ships_replace。寿命の
   来る船のうち、直近に使っていた船を保つのに要る分。trade/ships.py の replacement）を決める。
2. 銀行の段（T）: arrange が船の借りの審査をして、次のターンに買う数と貸す額（枠）を決め、頭金を
   用意する（海運業の手元 → 企業の預金 → 持ち主の出資。持ち主が出すのは買い替えの分だけ）。
   - 返す力: 「利払い前の利益 ＋ 減価償却」（運転資金の審査と同じ式。earnings）≧ 毎ターンの返済
     （今ある全部の借りの返済 ＋ 引き渡しの後に返し始める注文の借り ＋ 今回の元金と利子）×
     買い替えは loans.ship_replace_coverage、買い増しは loans.coverage。買い増しは、借入 ≦ 資産 ×
     loans.max_leverage も見る。
   - 買い替えが通らなければ、持ち主が出せる分は出資で買う（船がなければ稼げない）。買い増しが通らな
     ければ買わない。
   - 担保の上限（ltv_room）: 船の借りの合計（残高 ＋ 通した未引き渡しの注文の借り ＋ 今回）≦
     （持っている船の帳簿の値打ち ＋ 未引き渡しの注文の代金 ＋ 今回の代金）× loans.ship_ltv。超える
     分は頭金を増やし、用意できなければ数を減らす。銀行が貸せる額（自己資本比率と大口融資の規制）も
     超えない。
   - 計画経済の島は審査を省く（国が後ろにつく。運転資金と同じ）。
   - 枠は海運業の economy[CREDIT] に置くだけで、お金はまだ貸さない（枠のお金を賃金などほかの
     支払いに使えないようにするため。掲示板 #858・#871）。
3. 市場の段（T＋1）: 船の買い注文を出す直前に、枠の額を貸す（draw）。お金は船の買い注文の取り置き
   にだけ使う。約定の後（settle）、買えた船の代金の分だけ借りを残し、残りはそのターンのうちに返す。
4. 造船所への前注文（trade/orders.py）: 注文のとき（place）に同じ審査と上限を見て、注文ごとに貸す
   額（financed）を決める。手付け（trade.order_deposit）は自分で払い、建造の間と引き渡しの代金は、
   払うときに銀行が造船所へ直に貸し出す（finance_payment）。引き渡しまでは利子だけ払い、引き渡しの
   ターンから元金を返す。注文が取り消されて前払いが返ってきたら、まず船の借りを返す。
5. 船が沈んでも取り壊しても借りは残る（返済は続く）。そのために残高が担保の上限を超えた額は
   ``ship_loans.shortfall`` に数える（取り立てはしない。保険は入れない）。

島の stats（ターンごとの値。測りの調べが足して数える）: ship_loans.lent（貸した額）・outstanding
（残高）・returned（買えずにそのターンに返した額）・refused_replace・refused_expand（審査で断った
船の数）・over_limit（新しい貸しで上限を超えた件数。0 のはず）・shortfall（沈没・取り壊しで担保が
足りなくなった額）・arrears（船の借りの返済が遅れている海運業の数）・failed（船の借りの遅れで倒れた
数）・reviews ほか（審査の稼ぎ ÷ 返済の比。Review.note）・deferred（返済猶予をした件数）・
defer_refused（元金が払えず、時価か船の寿命の線で猶予しなかった件数。遅れに数える）・
arrears_after_defer・failed_after_defer（猶予をしたことのある海運業の遅れと倒産）・defer_capped・
defer_cap_left（猶予の上限に届いた借りの件数と、そのときの残り）・loss_after_defer（猶予をしたことの
ある海運業が倒れて貸し倒れにした船の借り）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.economy import (
    STATE_FACILITIES,
    Payer,
    banks,
    books,
    companies,
    facility_entity,
    funds,
    owner_entity,
)
from hakoniwa_core.economy import freight as freight_rates
from hakoniwa_core.economy.prices import variable_profit
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE, Facility, IslandState
from hakoniwa_core.trade import ships
from hakoniwa_core.units import QUANTITY_SCALE, money_of, part, to_basis

MILLI = QUANTITY_SCALE
CREDIT = "ship_credit"  # 海運業の economy: 銀行の段で通した次のターンの船の枠
DRAWN = "ship_credit_drawn"  # island.scratch: 市場の段で枠から貸した額と、買い注文の取り置き
FAR = 10**9  # 前注文の借りの返し始め（引き渡しのターンに決め直す）
ORDERS = "ship_orders"  # 造船所の economy: 受けた前注文（trade/orders.py の ORDERS）
DUE = "ship_order_due"  # 海運業の economy: 次のターンに自分で払う前注文の代金（trade/orders.py）
STAT = "ship_loans."
DEFERRED = "ship_deferred"  # 海運業の economy: 船の借りの返済猶予をした回数
# 海運業の economy: 船の借りごとの猶予の上限 {借りの番号: ターン}。借りた日に担保にした船の寿命の
# 終わりで決め、後から同じ借りに船を足しても延ばさない（掲示板 #1108・#1109）
DEFER_END = "ship_defer_end"
CAPPED = "ship_defer_capped"  # 海運業の economy: 猶予が上限に届いた借りの番号（数えるのは 1 回）


def _set_end(f: Facility, loan: Any, end: int) -> None:
    ends = f.economy.setdefault(DEFER_END, {})
    ends.setdefault(str(loan.id), end)


def _add(island: IslandState, key: str, value: int | float) -> None:
    if value:
        stats = island.stats
        stats[STAT + key] = stats.get(STAT + key, 0) + value


def ltv(config: GameConfig) -> float:
    return config.get("loans.ship_ltv")


def is_planned(defs: Any, island: IslandState) -> bool:
    """倒産を起こさない経済体制か（modules/banking の is_planned と同じ）。"""
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    return regime.get("owner") == OWNER_STATE


def ship_loans_of(island: IslandState, entity: str) -> list[Any]:
    return [x for x in banks.loans_by_borrower(island, entity) if x.purpose == banks.SHIP]


def open_orders(island: IslandState, f: Facility) -> list[dict[str, Any]]:
    """海運業 f が造船所に出して、まだ届いていない前注文。"""
    return [
        o for yard in island.facilities for o in yard.economy.get(ORDERS, []) if o["buyer"] == f.id
    ]


def _order_value(o: dict[str, Any]) -> int:
    return money_of(o["price"], o["qty"])


def ship_debt(island: IslandState, f: Facility) -> int:
    """船の借りの合計: 残高 ＋ 通した未引き渡しの注文で、まだ貸していない分。"""
    entity = facility_entity(f.id)
    held = sum(banks.outstanding(island, x) for x in ship_loans_of(island, entity))
    return held + sum(
        max(0, o.get("financed", 0) - o.get("lent", 0)) for o in open_orders(island, f)
    )


def collateral(island: IslandState, config: GameConfig, f: Facility, turn: int) -> int:
    """担保: 持っている船の時価（今の船の値段 × 寿命の残りの割合。在庫の船は今の値段。
    ships.market_value）＋ 未引き渡しの注文の代金。現実の船舶融資と同じく時価で見る（帳簿の値打ちは
    船団をまとめて減価償却し、解体で平均を除くので、古い船の解体で新しい船の値打ちまで減った。
    2026-11-16 の測り）。"""
    return ships.market_value(island, config, f, turn) + sum(
        _order_value(o) for o in open_orders(island, f)
    )


def _excess(island: IslandState, config: GameConfig, f: Facility, turn: int) -> int:
    """船の借りの合計が担保の上限（今回の代金なし）を超えている額。"""
    return max(0, ship_debt(island, f) - int(collateral(island, config, f, turn) * ltv(config)))


def ltv_room(island: IslandState, config: GameConfig, f: Facility, value: int, turn: int) -> int:
    """代金 value の船を今回買う（注文する）ときに、担保の上限まで新しく借りられる額。"""
    limit = int((collateral(island, config, f, turn) + value) * ltv(config))
    return max(0, limit - ship_debt(island, f))


def check_limit(island: IslandState, config: GameConfig, f: Facility, turn: int) -> None:
    """新しく貸した後に、担保の上限を超えていれば数える（買った船は在庫の船として担保に入っている）。"""
    limit = int(collateral(island, config, f, turn) * ltv(config))
    if ship_debt(island, f) > limit + 1:  # 1 は割り算の端数
        _add(island, "over_limit", 1)


# --- 審査 --------------------------------------------------------------------------


def earnings(island: IslandState, ctx: Any, f: Facility) -> float:
    """返す力の審査で見る稼ぎ: 直近 firms.ebitda_turns ターンの「利払い前の利益 ＋ 減価償却」の
    平均。記録がないか、民営工場が動き出して据え置きの間は、今の値段の変動費の利益（できたばかりなら
    操業目標の分、据え置きの間は全力の分）。運転資金の審査（modules/banking の _creditworthy）と
    同じ式。"""
    config = ctx.config
    settings = config.get("loans")
    history = banks.ebitda(f)[-config.get("firms.ebitda_turns") :]
    young = ctx.turn - f.economy.get("entered", -(10**9)) <= settings["grace_turns"]
    if history and not young:
        return sum(history) / len(history)
    freight = freight_rates.facility_rates(island, ctx.defs, config).get(f.id, 0)
    wage = f.economy.get("wage", 0)
    profit = variable_profit(ctx.defs, config, f, island.economy.prices, wage, freight)
    return profit if young else fac.scale(profit, f.economy.get("target", fac.FULL))


def _offers(island: IslandState, ctx: Any, entity: str, amount: int) -> list[tuple[int, str]]:
    """貸せる銀行と金利（金利の低い順。運転資金と同じく、職員のいない銀行は貸さない）。"""
    rate_of = ctx.formula("loan_rate")
    found = []
    for bank in banks.active_banks(island):
        if banks.labor_ratio(island, ctx.config, ctx.defs, bank) <= 0:
            continue
        found.append((rate_of(island, ctx, bank, entity, amount), bank))
    return sorted(found)


def _bank_room(island: IslandState, ctx: Any, f: Facility, offers: list[tuple[int, str]]) -> int:
    """銀行がいま f に新しく貸せる額の合計（自己資本比率と大口融資の規制、新しく貸さない期間）。"""
    if is_planned(ctx.defs, island):
        return 10**18
    config = ctx.config
    entity = facility_entity(f.id)
    total = 0
    for _, bank in offers:
        if banks.lending_stopped(island, bank, ctx.turn):
            continue
        room = banks.capital_room(island, config, bank, ctx.turn)
        if f.owner == OWNER_PRIVATE:
            room = min(room, banks.exposure_room(island, config, bank, entity, ctx.turn))
        total += room
    return total


def _pending_service(island: IslandState, ctx: Any, f: Facility, rate: int) -> int:
    """引き渡しの後に返し始める注文の借り（今の残高の分の元金と、まだ貸していない分の元金と利子）。"""
    entity = facility_entity(f.id)
    term = ctx.config.get("loans.ship_turns")
    total = 0
    for loan in ship_loans_of(island, entity):
        if ctx.turn + 1 < loan.repay_from:
            total += min(banks.outstanding(island, loan), loan.installment)
    for o in open_orders(island, f):
        left = max(0, o.get("financed", 0) - o.get("lent", 0))
        total += -(-left // term) + ledger.interest(left, rate)
    return total


class Review:
    """船の借りの審査（返す力と借入の割合）。数は 1000 分の 1 隻で、step ごとに切る。"""

    def __init__(self, island: IslandState, ctx: Any, f: Facility, unit_value: int) -> None:
        self.island, self.ctx, self.f = island, ctx, f
        self.config = ctx.config
        self.entity = facility_entity(f.id)
        self.unit_value = unit_value  # 1 隻の代金
        self.planned = is_planned(ctx.defs, island)
        offers = _offers(island, ctx, self.entity, unit_value)
        self.rate = offers[0][0] if offers else 0
        self.has_bank = bool(offers)
        self.room = _bank_room(island, ctx, f, offers)  # 銀行が貸せる額（借りる額の上限）
        self.earnings = 0.0 if self.planned else earnings(island, ctx, f)
        self.base = banks.debt_service(island, self.entity, ctx.turn) + _pending_service(
            island, ctx, f, self.rate
        )
        self.debt = banks.debt_of(island, self.entity)
        self.assets = banks.assets_of(island, f, self.entity)

    def loan(self, qty: int) -> int:
        """qty を買うときに借りる額（担保の上限の前）。"""
        return int(self.unit_value * qty / MILLI * ltv(self.config))

    def service(self, qty: int) -> int:
        """qty を借りて買ったときの毎ターンの返済（今ある借りの返済 ＋ 今回の元金と利子）。"""
        loan = self.loan(qty)
        term = self.config.get("loans.ship_turns")
        return self.base + -(-loan // term) + ledger.interest(loan, self.rate)

    def note(self, qty: int) -> None:
        """審査の「稼ぎ ÷ 返済」の比を数える（数えるだけで、審査には使わない。掲示板の決め 4）。
        reviews（件数）・review_earnings・review_service（足した額。比は足した額どうしの割り算）・
        review_below_1・review_below_1_2（比が 1.0・1.2 に届かなかった件数）。"""
        if self.planned or qty <= 0 or not self.has_bank:
            return
        service = self.service(qty)
        _add(self.island, "reviews", 1)
        _add(self.island, "review_earnings", round(self.earnings))
        _add(self.island, "review_service", service)
        if service > 0:
            ratio = self.earnings / service
            _add(self.island, "review_below_1", 1 if ratio < 1.0 else 0)
            _add(self.island, "review_below_1_2", 1 if ratio < 1.2 else 0)

    def passes(self, qty: int, coverage: float, leverage: bool) -> bool:
        if self.planned or qty <= 0:
            return True
        loan = self.loan(qty)
        service = self.service(qty)
        if service > 0 and self.earnings < coverage * service:
            return False
        if leverage:
            limit = self.config.get("loans.max_leverage") * (self.assets + loan)
            if self.debt + loan > limit:
                return False
        return True

    def most(self, have: int, upto: int, coverage: float, leverage: bool, step: int) -> int:
        """have に足して審査に通る数（upto まで、step ごと）。"""
        if not self.has_bank:
            return 0
        lo, hi = 0, upto // step
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.passes(have + mid * step, coverage, leverage):
                lo = mid
            else:
                hi = mid - 1
        return lo * step

    def split(self, replace: int, expand: int, step: int) -> tuple[int, int]:
        """（借りて買い替える数, 借りて買い増す数）。通らなかった買い替えは持ち主の出資で買う。"""
        settings = self.config.get("loans")
        self.note(replace + expand)
        ok_replace = self.most(0, replace, settings["ship_replace_coverage"], False, step)
        ok_expand = self.most(ok_replace, expand, settings["coverage"], True, step)
        return ok_replace, ok_expand


def ship_price(island: IslandState, config: GameConfig) -> int:
    return island.economy.prices.get(config.get("trade.ship_good"), 0)


def unit_value(island: IslandState, config: GameConfig) -> int:
    """市場で船 1 隻を買う代金の見込み（買い注文の指値 ＝ 値幅の上端と、運賃）。"""
    price = island.economy.prices.get(config.get("trade.ship_good"), 0)
    limit = price + part(price, to_basis(config.get("market.band")))
    amount = money_of(limit, MILLI)
    return amount + part(amount, island.economy.freight.get("household", 0))


# --- 銀行の段: 枠と頭金 ------------------------------------------------------------


def _inject(island: IslandState, f: Facility, amount: int) -> int:
    """持ち主（政府か資産を持つ層）が出資する（出せる分だけ）。出した額を返す。"""
    owner = owner_entity(f)
    payer = Payer(owner, STATE_FACILITIES) if owner == ledger.GOV else Payer(owner)
    entity = facility_entity(f.id)
    amount = min(amount, funds.available(island, payer))
    if amount > 0:
        funds.pay(island, payer, entity, amount, "capital_injection")
        ledger.create(island, payer.entity, ledger.EQUITY, entity, amount, "capital_injection")
        books.add(island, f.id, books.CAPITAL, amount)
    return max(0, amount)


def arrange(island: IslandState, ctx: Any, f: Facility, other_payments: int) -> None:
    """銀行の段: 次のターンに市場で買う船の審査をして枠を決め、頭金と前注文の自分で払う代金を
    用意する。other_payments は船の分を除いた次の支払いの見込み。"""
    config = ctx.config
    f.economy.pop(CREDIT, None)
    entity = facility_entity(f.id)
    want = f.economy.get("ships_wanted", 0)
    replace = min(want, f.economy.get("ships_replace", 0))
    expand = want - replace
    due = f.economy.get(DUE, 0)
    value = unit_value(island, config)
    _record(island, config, f)
    if (want <= 0 or value <= 0) and due <= 0:
        return
    review = Review(island, ctx, f, value)
    lend_r, lend_e = review.split(replace, expand, 1) if want > 0 else (0, 0)
    own_r = replace - lend_r  # 審査に通らなかった買い替え（持ち主の出資で）
    _add(island, "refused_replace", own_r / MILLI)
    _add(island, "refused_expand", (expand - lend_e) / MILLI)
    total = lend_r + own_r + lend_e
    loan = min(
        review.loan(lend_r + lend_e),
        # 時価（島の値段）の 8 割まで。指値の上端と運賃の分は頭金で払う
        ltv_room(island, config, f, money_of(ship_price(island, config), total), ctx.turn),
        review.room,
    )
    share_r = lend_r / (lend_r + lend_e) if lend_r + lend_e > 0 else 0.0
    loan_r = int(loan * share_r)
    loan_e = loan - loan_r
    down_r = money_of(value, lend_r) - loan_r + money_of(value, own_r)
    down_e = max(0, money_of(value, lend_e) - loan_e)
    # 頭金: 手元（次の支払いを除いた余り）→ 企業の預金 → 持ち主（買い替えと前注文の分だけ）
    spare = max(0, ledger.money(island, entity) - other_payments)
    need = due + down_r + down_e
    pool = spare + companies.draw(island, f, max(0, need - spare))
    got_due = min(due, pool)
    pool -= got_due
    got_r = min(down_r, pool)
    pool -= got_r
    got_e = min(down_e, pool)
    owed = (due - got_due) + (down_r - got_r)
    if owed > 0:
        injected = _inject(island, f, owed)
        extra = min(injected, due - got_due)
        got_due += extra
        got_r += injected - extra
    # 用意できなかった頭金の分は買わない
    if down_e > 0 and got_e < down_e:
        lend_e = lend_e * got_e // down_e
        loan_e = loan_e * got_e // down_e
    if down_r > 0 and got_r < down_r:
        lend_r = lend_r * got_r // down_r
        own_r = own_r * got_r // down_r
        loan_r = loan_r * got_r // down_r
    qty = lend_r + own_r + lend_e
    if qty <= 0 and got_due <= 0:
        return
    f.economy[CREDIT] = {
        "qty": qty,
        "replace": lend_r + own_r,
        "loan": loan_r + loan_e,
        "value": money_of(value, qty),
        "down": max(0, money_of(value, qty) - loan_r - loan_e) if qty > 0 else 0,
        "due": got_due,
    }


def _record(island: IslandState, config: GameConfig, f: Facility) -> None:
    """船の借りの残高と、返済が遅れている海運業を数える（ターンごとの値）。"""
    entity = facility_entity(f.id)
    loans = ship_loans_of(island, entity)
    _add(island, "outstanding", sum(banks.outstanding(island, x) for x in loans))
    ids = {str(x.id) for x in loans}
    if DEFER_END in f.economy:  # 返し終わった借りの上限は忘れる
        f.economy[DEFER_END] = {k: v for k, v in f.economy[DEFER_END].items() if k in ids}
    if CAPPED in f.economy:
        f.economy[CAPPED] = [k for k in f.economy[CAPPED] if k in ids]
    if any(x.arrears > 0 for x in loans):
        _add(island, "arrears", 1)
        if f.economy.get(DEFERRED):
            _add(island, "arrears_after_defer", 1)  # 猶予が明けた後の遅れ


def payments(f: Facility) -> int:
    """次の支払いの見込みに入れる船の代金（economy/start.py の next_payments）: 銀行の段で枠を決めた
    後は頭金と前注文の自分で払う分。決める前は、前注文の自分で払う分だけ。"""
    credit = f.economy.get(CREDIT)
    if credit is not None:
        return credit["down"] + credit["due"]
    return f.economy.get(DUE, 0)


# --- 貸し出し ----------------------------------------------------------------------


def lend_now(
    island: IslandState,
    ctx: Any,
    f: Facility,
    amount: int,
    repay_from: int,
    order: dict[str, Any] | None = None,
) -> int:
    """船の借りを今貸す（金利の低い銀行から、自己資本比率と大口融資の規制の残りまで）。貸した額を
    返す。order があれば、その注文の借りに足す（注文ごとに銀行 1 つにつき 1 件）。"""
    if amount <= 0:
        return 0
    config = ctx.config
    entity = facility_entity(f.id)
    planned = is_planned(ctx.defs, island)
    term = config.get("loans.ship_turns")
    left = amount
    for rate, bank in _offers(island, ctx, entity, amount):
        piece = left
        if not planned:
            if banks.lending_stopped(island, bank, ctx.turn):
                piece = 0
            if f.owner == OWNER_PRIVATE:
                piece = min(piece, banks.exposure_room(island, config, bank, entity, ctx.turn))
            piece = min(piece, banks.capital_room(island, config, bank, ctx.turn))
        if piece <= 0:
            continue
        if order is not None:
            _lend_order(island, order, bank, entity, piece, term, ctx.turn, rate)
        else:
            loan = banks.lend(
                island, bank, entity, piece, banks.SHIP, term, ctx.turn, rate, repay_from
            )
            if loan is not None:  # 今買う船の寿命の終わり（まとめた借りは前の上限のまま）
                _set_end(f, loan, ctx.turn + config.get("trade.ship_life"))
        left -= piece
        if left <= 0:
            break
    return amount - left


def _lend_order(
    island: IslandState,
    order: dict[str, Any],
    bank: str,
    entity: str,
    amount: int,
    term: int,
    turn: int,
    rate: int,
) -> None:
    ids = order.setdefault("loans", [])
    for loan in island.loans:
        if loan.id in ids and loan.lender == bank and loan.borrower == entity:
            ledger.create(island, bank, ledger.loan_item(loan.id), entity, amount, "loan")
            ledger.pay(island, bank, entity, amount, "loan")
            loan.principal += amount
            loan.installment = -(-loan.principal // max(1, loan.term))
            loan.rate = rate
            break
    else:
        loan = banks.lend(island, bank, entity, amount, banks.SHIP, term, turn, rate, FAR)
        assert loan is not None
        ids.append(loan.id)
    order["lent"] = order.get("lent", 0) + amount


# --- 市場の段: 枠から貸して、買えなかった分を返す ------------------------------------


def market_qty(f: Facility, want: int) -> int:
    """市場で船を買う数: 銀行の段で通した枠まで（枠がまだない海運業は前のとおり want）。"""
    credit = f.economy.get(CREDIT)
    if credit is None:
        return want
    return max(0, min(want, credit["qty"]))


def draw(island: IslandState, ctx: Any, f: Facility, qty: int) -> None:
    """船の買い注文を出す直前に、枠から買う数の分を貸す（お金は買い注文の取り置きにだけ使う）。"""
    credit = f.economy.get(CREDIT)
    if credit is None or qty <= 0 or credit["qty"] <= 0:
        return
    loan = credit["loan"] * min(qty, credit["qty"]) // credit["qty"]
    value = credit["value"] * min(qty, credit["qty"]) // credit["qty"]
    lent = lend_now(island, ctx, f, loan, ctx.turn + 1)
    island.scratch[DRAWN] = {"lent": lent, "value": value, "party": None, "reserved": 0}


def bid_placed(island: IslandState, party: Any) -> None:
    """船の買い注文の取り置きを覚える（約定の後に、買えた代金を取り置きの減りで数える）。"""
    drawn = island.scratch.get(DRAWN)
    if drawn is not None:
        drawn["party"], drawn["reserved"] = party, party.reserved


def note_spent(island: IslandState) -> None:
    """約定の後、取り置きを戻す前に、船に払った額を数える。"""
    drawn = island.scratch.get(DRAWN)
    if drawn is not None and drawn["party"] is not None:
        drawn["spent"] = drawn["reserved"] - drawn["party"].reserved
        drawn["party"] = None


def settle(island: IslandState, ctx: Any, f: Facility | None) -> None:
    """約定の後: 買えた船の代金 × 枠の借りの割合だけ借りを残し、残りをそのターンのうちに返す。枠は
    使い切り。"""
    drawn = island.scratch.pop(DRAWN, None)
    if f is None:
        return
    f.economy.pop(CREDIT, None)
    if drawn is None or drawn["lent"] <= 0:
        return
    spent = drawn.get("spent", 0)
    lent, value = drawn["lent"], max(1, drawn["value"])
    keep = min(lent, lent * min(spent, value) // value)
    # 担保の上限を超える分は残さない（買えた船は在庫の船として時価で担保に入る。前に沈んだ船の
    # 不足があれば、その分も）
    keep = min(keep, max(0, lent - _excess(island, ctx.config, f, ctx.turn)))
    back = lent - keep
    entity = facility_entity(f.id)
    loans = sorted(
        (x for x in ship_loans_of(island, entity) if x.repay_from <= ctx.turn + 1),
        key=lambda x: -x.start,
    )
    for loan in loans:
        if back <= 0:
            break
        amount = min(back, banks.outstanding(island, loan), ledger.money(island, entity))
        banks.repay(island, loan, amount)
        loan.principal -= min(loan.principal, amount)
        loan.installment = -(-loan.principal // max(1, loan.term))
        back -= amount
    returned = lent - keep - back
    kept = lent - returned
    _add(island, "lent", kept)
    _add(island, "returned", returned)
    if kept > 0:
        check_limit(island, ctx.config, f, ctx.turn)


# --- 前注文 ------------------------------------------------------------------------


def order_review(
    island: IslandState, ctx: Any, f: Facility, price: int, replace: int, expand: int
) -> tuple[int, int, int]:
    """前注文の審査（1 隻単位）: （注文する数, そのうち買い替え, 貸す額）。買い増しで通らない分は
    注文しない。通らなかった買い替えは貸さずに注文する（自分で払う）。"""
    config = ctx.config
    value = money_of(price, MILLI)
    review = Review(island, ctx, f, value)
    lend_r, lend_e = review.split(replace, expand, MILLI)
    own_r = replace - lend_r
    _add(island, "refused_replace", own_r / MILLI)
    _add(island, "refused_expand", (expand - lend_e) / MILLI)
    qty = replace + lend_e
    loan = min(
        review.loan(lend_r + lend_e),
        ltv_room(island, config, f, money_of(price, qty), ctx.turn),
        review.room,
    )
    return qty, replace, loan


def finance_payment(
    island: IslandState, ctx: Any, f: Facility, o: dict[str, Any], amount: int
) -> int:
    """注文 o の代金のうち、自分で払う頭金の後の amount を、銀行が買い手に貸す（注文のときに決めた
    financed まで。お金はすぐ造船所への支払いに使う）。貸した額を返す。"""
    # 沈没や取り壊しで担保の上限を超えていれば、超えた分を注文の借りから外す（自分で払う）
    over = _excess(island, ctx.config, f, ctx.turn)
    if over > 0:
        o["financed"] = max(o.get("lent", 0), o.get("financed", 0) - over)
    want = min(amount, o.get("financed", 0) - o.get("lent", 0))
    if want <= 0:
        return 0
    lent = lend_now(island, ctx, f, want, FAR, order=o)
    if lent > 0:
        _add(island, "lent", lent)
        check_limit(island, ctx.config, f, ctx.turn)
    return lent


def own_share(o: dict[str, Any], due: int) -> int:
    """注文 o に払う累計 due のうち、自分で払う分（頭金。銀行が貸す分を除く）。"""
    financed = o.get("financed", 0)
    if financed <= 0:
        return due
    return min(due, _order_value(o) - financed)


def delivered(island: IslandState, config: GameConfig, o: dict[str, Any], turn: int) -> None:
    """引き渡した注文の借りは、引き渡しのターンから元金を返す。猶予の上限は届いた船の寿命の終わり。"""
    ids = set(o.get("loans", []))
    for loan in island.loans:
        if loan.id in ids:
            loan.repay_from = turn
            f = banks.borrower_facility(island, loan.borrower)
            if f is not None:
                _set_end(f, loan, turn + config.get("trade.ship_life"))


def cancelled(island: IslandState, o: dict[str, Any], refunded: int) -> None:
    """取り消した注文の借り: 返ってきた前払いでまず返し、残りはすぐ返し始める。"""
    ids = set(o.get("loans", []))
    for loan in sorted(island.loans, key=lambda x: x.id):
        if loan.id not in ids:
            continue
        amount = min(refunded, banks.outstanding(island, loan), ledger.money(island, loan.borrower))
        if amount > 0:
            banks.repay(island, loan, amount)
            loan.principal -= min(loan.principal, amount)
            refunded -= amount
        loan.installment = -(-max(0, loan.principal) // max(1, loan.term))
        loan.repay_from = 0


# --- 返済猶予 ----------------------------------------------------------------------


def defer(island: IslandState, ctx: Any, loan: Any) -> bool:
    """船の借りの返済猶予（W-281。掲示板 #1094〜#1100・#1108・#1109）: 利子は払えたが元金を払えな
    かった海運業に、船の時価が船の借りの残り（合計）を上回る間は、遅れに数えず返し終わりを延ばす
    （毎ターンの元金は変えない。払えなかった元金はそのまま残り、返し終わりがその分遅れる）。延ばした
    後の返し終わりが、その借りの猶予の上限（借りた日に担保にした船の寿命の終わり。DEFER_END。後から
    船を足しても延びない）を超えるなら猶予しない（現実の海運不況で銀行がする返済猶予。担保の船が
    なくなる日より後へは延ばさない）。上限に届いた借りは、そのときの残りを defer_cap_left に数える
    （1 回だけ）。猶予したかを返す。"""
    config = ctx.config
    f = banks.borrower_facility(island, loan.borrower)
    if f is None or loan.installment <= 0:
        return False
    if not f.economy.get("fleet"):
        return False
    if ship_debt(island, f) >= collateral(island, config, f, ctx.turn):
        _add(island, "defer_refused", 1)
        return False
    left = banks.outstanding(island, loan)
    end = ctx.turn + -(-left // loan.installment)
    cap = f.economy.get(DEFER_END, {}).get(str(loan.id))
    if cap is None or end > cap:
        _add(island, "defer_refused", 1)
        capped = f.economy.setdefault(CAPPED, [])
        if cap is not None and str(loan.id) not in capped:
            capped.append(str(loan.id))
            _add(island, "defer_capped", 1)
            _add(island, "defer_cap_left", left)
        return False
    loan.arrears = 0
    loan.term += 1
    f.economy[DEFERRED] = f.economy.get(DEFERRED, 0) + 1
    _add(island, "deferred", 1)
    return True


# --- 船を失ったとき ----------------------------------------------------------------


def note_loss(island: IslandState, config: GameConfig, f: Facility, before: int, turn: int) -> None:
    """沈没・取り壊しで船を除いた後: 担保の上限を超えた借りが増えた分を数える（取り立てはしない）。
    before は除く前の担保。"""
    debt = ship_debt(island, f)
    if debt <= 0:
        return
    rate = ltv(config)
    was = max(0, debt - int(before * rate))
    now = max(0, debt - int(collateral(island, config, f, turn) * rate))
    _add(island, "shortfall", max(0, now - was))
