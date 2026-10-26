"""銀行の段（フェーズ2-2）: 銀行間と中央銀行への返済、融資の利払いと返済、預金と準備預金の利子、
運転資金の融資、預金の乗り換えと取り付け、預金金利と利ざやの見直し。

1. 前のターンに中央銀行の段で借りた銀行間の借り入れと中央銀行の貸し出しを、利子を付けて返す。
2. 融資ごとに、今の金利（式 loan_rate）で利子を払い、返す時期に入った元金を返す。預金が足りなければ
   運転資金を借りて返し（4. と同じ審査）、借りきれなければ払える分だけ払い（払えなかった利子は残高に
   足す）、遅れの回数を1つ増やす。続けて loans.arrears_limit 回で倒産（破綻の段）。計画経済の島で
   は、国営銀行が足りない分を貸して借り換えさせる。
3. 銀行は預金者に「預金 × 銀行の預金金利 ÷ 12」を払い、中央銀行は準備預金の余りに「政策金利 −
   central_bank.reserve_spread」の利子を払う。
4. 次のターンの支払い（操業目標の分の賃金・投入財・電気代・運賃）に預金が足りない独立採算の施設は、
   足りない分を運転資金として借りる（loans.working_turns ターンで返す）。金利の低い銀行から順に、そ
   の銀行が貸せる分ずつ借りる。審査は ①直近の「利払い前の利益 ＋ 減価償却 − 在庫の増加」（現金の出入
   りで見た営業の利益。売れずに積み上がった在庫は返済に使えないので引く）の平均が、借りた後の毎ター
   ンの返済の loans.coverage 倍以上（運転資金の元金は仕入れた物の売上で返すので、運転資金の元金は数
   えない）、②借入が資産の loans.max_leverage 倍以下。銀行が貸せるのは、③貸した後も銀行の自己資本比
   率が規制の値以上で、④民営施設への残高が銀行の自己資本 × banking.large_exposure 以下（大口融資の規
   制。国営施設は政府が後ろにいるので対象外。返す元金の借り換えは上限を超えても認める）の分。①は、記
   録のない施設では今の値段の変動費の利益（操業目標の分）、動き出して建設資金の据え置き
   （loans.grace_turns）の間の民営工場では全力で動いたときの変動費の利益で見る（立ち上がりの赤字は見
   ない）。①を満たさなくても、運転資金の残高が在庫の値打ちの loans.inventory_ratio 倍以内なら貸す
   （在庫の担保）。借りきれない分は、国営施設なら政府が出資する（フェーズ2-1 と同じ）。民営施設な
   ら、変動費の利益が出ている間だけ、資産を持つ層が出資する（出ていなければ縮み、続けば撤退する）。
   国債の債務不履行で損をした銀行は、banking.default_crunch_turns ターンのあいだ新しく貸さず、返す元
   金の借り換えだけを貸す（計画経済の島を除く。止める期間が終わったらログで知らせる）。
5. 家計の層は、預金の banking.switch_share までを「預金金利 − 危なさ」の高い銀行へ移す。自己資本比率
   が規制を下回った銀行と、中央銀行から借りている銀行からは banking.run_share が流れ出す（取り付
   け）。
6. 銀行ごとに預金金利と利ざやを見直す。預金金利は「政策金利 − banking.deposit_spread」へ近づけ、準備
   預金が足りない銀行と、開業して預金を集めている銀行は政策金利へ近づける。島の預金金利
   （policy.deposit_rate）は銀行の預金金利の平均。
"""

from __future__ import annotations

import math
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    OWNERS,
    STATE_FACILITIES,
    Payer,
    banks,
    books,
    companies,
    facility_entity,
    funds,
    household,
    is_bank,
    is_self_supporting,
    owner_entity,
)
from hakoniwa_core.economy import freight as freight_rates
from hakoniwa_core.economy.households import add_income, layer_of
from hakoniwa_core.economy.prices import variable_profit
from hakoniwa_core.economy.start import next_payments
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE, Facility, IslandState, Loan, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, part, to_basis


def deposits_of(island: IslandState, bank: str) -> int:
    """銀行が出している預金の合計。"""
    return banks.deposits_of(island, bank)


def is_planned(defs: Defs, island: IslandState) -> bool:
    """倒産を起こさない経済体制か（regimes.yaml の owner が state。計画経済）。"""
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    return regime.get("owner") == OWNER_STATE


def in_business(f: Facility) -> bool:
    """倒産・差し押さえ・閉鎖の途中でない施設か。"""
    return not any(k in f.economy for k in ("foreclosed", "closing"))


class Banking(IslandStage):
    name = "banking"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        upgrade(island, ctx)
        self._crunch_over(island, ctx)
        self._repay_overnight(island, ctx)
        lent: dict[str, list[int]] = {b: [0, 0] for b in banks.bank_list(island)}
        self._service_loans(island, ctx, lent)
        for bank in banks.bank_list(island):
            number = banks.bank_number(bank)
            self._deposit_interest(island, bank, number)
            self._reserve_interest(island, ctx, bank, number)
        self._working_capital(island, ctx, lent)
        self._switch_deposits(island, ctx)
        self._update_rates(island, ctx, lent)
        banks.drop_settled(island)

    @staticmethod
    def _crunch_over(island: IslandState, ctx: StageContext) -> None:
        """国債の債務不履行の後に新しい融資を止めていた銀行が、止める期間を終えたら知らせる。"""
        for bank in banks.bank_list(island):
            f = banks.facility_of(island, bank)
            until = f.economy.get("crunch_until") if f is not None else None
            if f is None or until is None or ctx.turn < until:
                continue
            del f.economy["crunch_until"]
            if f.economy.get("failed") is None:
                island.log.append(LogEntry("bank_lending_resumed", {"x": f.x, "y": f.y}))

    # --- 1. 銀行間と中央銀行への返済 -------------------------------------------------

    @staticmethod
    def _repay_overnight(island: IslandState, ctx: StageContext) -> None:
        """銀行間と中央銀行から前のターンに借りた分を返す。返した額は銀行ごとに island.scratch の
        banks.OVERNIGHT に残す（国債の段の入札で、返したばかりの分を手元に残すため。回5）。"""
        finance = island.economy.finance
        repaid = island.scratch.setdefault(banks.OVERNIGHT, {})
        rate = finance.get("interbank_rate", island.policy.policy_rate)
        for name, amount in sorted(island.accounts.items()):
            lender, item, borrower = ledger.parse(name)
            if item != ledger.INTERBANK or amount <= 0:
                continue
            interest = ledger.interest(amount, rate)
            ledger.pay(island, borrower, lender, amount, "interbank")
            ledger.cancel(island, lender, ledger.INTERBANK, borrower, amount, "interbank")
            repaid[borrower] = repaid.get(borrower, 0) + amount
            ledger.pay(island, borrower, lender, interest, "interest_interbank")
            books.add(island, banks.bank_number(borrower), books.INTEREST, -interest)
            books.add(island, banks.bank_number(lender), books.INTEREST, interest)
        cb_rate = finance.get("cb_loan_rate", island.policy.policy_rate)
        for name, amount in sorted(island.accounts.items()):
            holder, item, borrower = ledger.parse(name)
            if holder != ledger.CB or item != ledger.CB_LOAN or amount <= 0:
                continue
            interest = ledger.interest(amount, cb_rate)
            ledger.pay(island, borrower, ledger.CB, amount, "cb_loan")
            ledger.cancel(island, ledger.CB, ledger.CB_LOAN, borrower, amount, "cb_loan")
            repaid[borrower] = repaid.get(borrower, 0) + amount
            ledger.pay(island, borrower, ledger.CB, interest, "interest_cb_loan")
            books.add(island, banks.bank_number(borrower), books.INTEREST, -interest)
            books.add_sector(island, books.CENTRAL_BANK, "interest", interest)

    # --- 2. 融資の利払いと返済 ---------------------------------------------------------

    def _service_loans(
        self, island: IslandState, ctx: StageContext, lent: dict[str, list[int]]
    ) -> None:
        planned = is_planned(ctx.defs, island)
        rate_of = ctx.formula("loan_rate")
        due: dict[str, list[tuple[Loan, int, int]]] = {}
        for loan in sorted(island.loans, key=lambda x: x.id):
            balance = banks.outstanding(island, loan)
            if balance <= 0:
                continue
            loan.rate = rate_of(island, ctx, loan.lender, loan.borrower)
            interest = ledger.interest(balance, loan.rate)
            installment = loan.installment
            if loan.purpose == banks.MORTGAGE:  # 残り ÷ 残りの期間で出し直す
                installment = -(-balance // banks.remaining_turns(loan, ctx.turn))
            principal = min(balance, installment) if ctx.turn >= loan.repay_from else 0
            due.setdefault(loan.borrower, []).append((loan, interest, principal))
        for borrower, rows in sorted(due.items()):
            payer = Payer(borrower)
            short = sum(i + p for _, i, p in rows) - funds.available(island, payer)
            f = banks.borrower_facility(island, borrower)
            if short > 0 and f is not None:
                short -= companies.draw(island, f, short, ctx)  # 企業の中のお金から先に回す（回7）
            if short > 0 and f is not None and in_business(f):
                if planned:
                    self._refinance(island, ctx, rows[0][0], short)
                    short = 0
                else:
                    # 一時的な資金不足は、審査に通れば運転資金を借りて返す（返す元金の分は大口融資
                    # の規制を超えても借り換えられる）。借りきれなければ遅れになる
                    repaying: dict[str, int] = {}
                    for loan, _, principal in rows:
                        repaying[loan.lender] = repaying.get(loan.lender, 0) + principal
                    short = self._borrow(island, ctx, f, short, lent, planned, repaying)
                if short > 0 and owner_entity(f) == ledger.GOV:
                    # 国営施設は、借りきれない分を政府が出資して埋める（国庫が足りなければ遅れる）
                    self._inject(island, f, Payer(ledger.GOV, STATE_FACILITIES), short)
            for loan, interest, principal in rows:
                self._pay_loan(island, loan, interest, principal)
                if loan.purpose == banks.MORTGAGE:
                    self._write_off_mortgage(island, ctx, loan)

    @staticmethod
    def _write_off_mortgage(island: IslandState, ctx: StageContext, loan: Loan) -> None:
        """返済が loans.arrears_limit 回続けて遅れた層の住宅ローンは、残りを銀行の損として消す
        （仕事を失った人の焦げ付き。家と人はそのまま。回3）。"""
        if loan.arrears < ctx.config.get("loans.arrears_limit"):
            return
        left = banks.outstanding(island, loan)
        banks.write_off(island, loan, left)
        loan.arrears = 0
        stats = island.stats
        stats["loans.mortgage_write_off"] = stats.get("loans.mortgage_write_off", 0) + left

    @staticmethod
    def _pay_loan(island: IslandState, loan: Loan, interest: int, principal: int) -> None:
        borrower = loan.borrower
        money = funds.available(island, Payer(borrower))
        paid_interest = min(interest, money)
        paid_principal = min(principal, money - paid_interest)
        if paid_interest:
            ledger.pay(island, borrower, loan.lender, paid_interest, "interest_loan")
        unpaid = interest - paid_interest
        if unpaid > 0:  # 払えなかった利子は残高に足す（銀行の収入には数える）
            ledger.create(
                island, loan.lender, ledger.loan_item(loan.id), borrower, unpaid, "interest_loan"
            )
        layer = layer_of(borrower)
        if layer is not None:  # 家計の払った利子は層の所得から引く
            add_income(island, layer, -paid_interest)
        else:
            books.add(island, int(borrower.split(":")[1]), books.INTEREST, -interest)
        books.add(island, banks.bank_number(loan.lender), books.INTEREST, interest)
        banks.repay(island, loan, paid_principal)
        if paid_interest + paid_principal < interest + principal:
            loan.arrears += 1
        else:
            loan.arrears = 0

    @staticmethod
    def _refinance(island: IslandState, ctx: StageContext, loan: Loan, short: int) -> None:
        """計画経済の島で、返済に足りない分を国営銀行が運転資金として貸す（審査なし）。"""
        rate = ctx.formula("loan_rate")(island, ctx, loan.lender, loan.borrower, short)
        turns = ctx.config.get("loans.working_turns")
        banks.lend(island, loan.lender, loan.borrower, short, banks.WORKING, turns, ctx.turn, rate)

    # --- 3. 預金と準備預金の利子 -------------------------------------------------------

    @staticmethod
    def _deposit_interest(island: IslandState, bank: str, number: int) -> None:
        rate = banks.deposit_rate_of(island, bank)
        paid = 0
        for holder, value in banks.depositors_of(island, bank).items():
            amount = ledger.interest(value, rate)
            if amount <= 0:
                continue
            ledger.pay(island, bank, holder, amount, "interest_deposit")
            paid += amount
            layer = layer_of(holder)
            if layer is not None:
                add_income(island, layer, amount)
            elif holder.startswith("fac:"):
                books.add(island, int(holder[4:]), books.INTEREST, amount)
        books.add(island, number, books.INTEREST, -paid)

    @staticmethod
    def _reserve_interest(island: IslandState, ctx: StageContext, bank: str, number: int) -> None:
        excess = banks.excess_reserves(island, ctx.config, bank)
        spread = to_basis(ctx.config.get("central_bank.reserve_spread"))
        amount = ledger.interest(excess, island.policy.policy_rate - spread)
        if amount > 0:
            ledger.pay(island, ledger.CB, bank, amount, "interest_reserve")
            books.add(island, number, books.INTEREST, amount)
            books.add_sector(island, books.CENTRAL_BANK, "interest", -amount)

    # --- 4. 運転資金 -------------------------------------------------------------------

    def _working_capital(
        self, island: IslandState, ctx: StageContext, lent: dict[str, list[int]]
    ) -> None:
        """足りない運転資金を貸す。lent に銀行ごとの [貸した額, 断った額（銀行の体力が理由）] を
        足す。"""
        planned = is_planned(ctx.defs, island)
        for f in sorted(island.facilities, key=lambda f: f.id):
            if not is_self_supporting(f) or is_bank(ctx.config, f) or not in_business(f):
                continue
            entity = facility_entity(f.id)
            short = next_payments(island, ctx.defs, ctx.config, f) - ledger.money(island, entity)
            if short <= 0:
                continue
            # 民営の企業の施設は、借りる前に企業の預金から回す（回7）
            short -= companies.draw(island, f, short)
            if short <= 0:
                continue
            short = self._borrow(island, ctx, f, short, lent, planned)
            if short <= 0:
                continue
            owner = owner_entity(f)
            if owner == ledger.GOV:
                payer = Payer(owner, STATE_FACILITIES)
            elif self._viable(island, ctx, f) or f.economy.get("ships_wanted", 0) > 0:
                # 船を買いたい海運業は、船がなければ稼げないので、変動費の利益を見ずに出資する
                # （回6）
                payer = Payer(owner)
            else:
                continue
            self._inject(island, f, payer, short)

    @staticmethod
    def _inject(island: IslandState, f: Facility, payer: Payer, amount: int) -> None:
        """持ち主（政府か資産を持つ層）が施設に出資する（出せる分だけ）。"""
        entity = facility_entity(f.id)
        amount = min(amount, funds.available(island, payer))
        if amount > 0:
            funds.pay(island, payer, entity, amount, "capital_injection")
            ledger.create(island, payer.entity, ledger.EQUITY, entity, amount, "capital_injection")
            books.add(island, f.id, books.CAPITAL, amount)

    def _borrow(
        self,
        island: IslandState,
        ctx: StageContext,
        f: Facility,
        amount: int,
        lent: dict[str, list[int]],
        planned: bool,
        repaying: dict[str, int] | None = None,
    ) -> int:
        """金利の低い銀行から順に借りる。1つの銀行から借りられるのは、自己資本比率の規制と、民営施設
        なら大口融資の規制の残りまで（repaying はこのターンに銀行ごとに返す元金）。借りきれなかった
        額を返す（審査に通らなければ amount）。"""
        entity = facility_entity(f.id)
        rate_of = ctx.formula("loan_rate")
        offers = []
        for bank in banks.active_banks(island):
            share = banks.labor_ratio(island, ctx.config, ctx.defs, bank)
            if share <= 0:
                continue
            rate = rate_of(island, ctx, bank, entity, amount)
            offers.append((rate, bank))
        offers.sort()
        if not offers or (not planned and not self._creditworthy(island, ctx, f, amount, offers)):
            return amount
        turns = ctx.config.get("loans.working_turns")
        left = amount
        for rate, bank in offers:
            piece = left
            if not planned:
                due = (repaying or {}).get(bank, 0)
                piece = self._room(island, ctx, f, bank, left, lent, due)
            if piece <= 0:
                continue
            banks.lend(island, bank, entity, piece, banks.WORKING, turns, ctx.turn, rate)
            lent[bank][0] += piece
            left -= piece
            if left <= 0:
                break
        return left

    @staticmethod
    def _creditworthy(
        island: IslandState,
        ctx: StageContext,
        f: Facility,
        amount: int,
        offers: list[tuple[int, str]],
    ) -> bool:
        """運転資金の借り手の審査（返済の力と借入の割合。金利はいちばん低い銀行の金利で見る）。"""
        config = ctx.config
        settings = config.get("loans")
        entity = facility_entity(f.id)
        rate = offers[0][0]
        history = banks.ebitda(f)[-config.get("firms.ebitda_turns") :]
        young = ctx.turn - f.economy.get("entered", -(10**9)) <= settings["grace_turns"]
        if history and not young:
            earnings = sum(history) / len(history)
        else:
            # 記録がないか、民営工場が動き出して据え置きの間（立ち上がりの赤字は見ない）:
            # 今の値段の変動費の利益を、できたばかりなら操業目標の分、据え置きの間は全力の分だけ見る
            freight = freight_rates.facility_rates(island, ctx.defs, config).get(f.id, 0)
            wage = f.economy.get("wage", 0)
            profit = variable_profit(ctx.defs, config, f, island.economy.prices, wage, freight)
            earnings = profit if young else fac.scale(profit, f.economy.get("target", fac.FULL))
        service = banks.debt_service(island, entity, ctx.turn) + ledger.interest(amount, rate)
        if service > 0 and earnings < settings["coverage"] * service:
            # 利益で返せなくても、売れ残りの在庫を担保に運転資金だけは貸す（在庫を売って返す）
            working = sum(
                banks.outstanding(island, x)
                for x in banks.loans_by_borrower(island, entity)
                if x.purpose == banks.WORKING
            )
            secured = part(banks.inventory_value(island, f), to_basis(settings["inventory_ratio"]))
            if working + amount > secured:
                return False
        debt_after = banks.debt_of(island, entity) + amount
        assets = banks.assets_of(island, f, entity) + amount
        return debt_after <= settings["max_leverage"] * assets

    @staticmethod
    def _room(
        island: IslandState,
        ctx: StageContext,
        f: Facility,
        bank: str,
        amount: int,
        lent: dict[str, list[int]],
        due: int,
    ) -> int:
        """bank が f に貸せる額（amount まで）。大口融資の規制は民営施設だけにかける（国営施設は政府
        が後ろにいるので対象外）。自己資本比率の規制で断った分は lent に足す（利ざやを上げる）。

        国債の債務不履行で損をした銀行は、新しい融資を止めて、このターンに返す元金 due の借り換え
        だけを貸す（止めて断った分も lent に足す）。"""
        config = ctx.config
        want = amount
        if banks.lending_stopped(island, bank, ctx.turn):
            want = min(want, due)
            lent[bank][1] += amount - want
        if f.owner == OWNER_PRIVATE:
            entity = facility_entity(f.id)
            room = banks.exposure_room(island, config, bank, entity, ctx.turn, due)
            want = min(want, room)
        piece = min(want, banks.capital_room(island, config, bank, ctx.turn))
        lent[bank][1] += want - piece
        return piece

    @staticmethod
    def _viable(island: IslandState, ctx: StageContext, f: Facility) -> bool:
        """今の値段で変動費の利益が出ているか（借りられない民営施設に持ち主が出資するか）。"""
        freight = freight_rates.facility_rates(island, ctx.defs, ctx.config).get(f.id, 0)
        wage = f.economy.get("wage", 0)
        return variable_profit(ctx.defs, ctx.config, f, island.economy.prices, wage, freight) > 0

    # --- 5. 預金の乗り換えと取り付け ----------------------------------------------------

    def _switch_deposits(self, island: IslandState, ctx: StageContext) -> None:
        active = banks.active_banks(island)
        if len(active) < 2:
            return
        config = ctx.config
        scores = {b: self._score(island, ctx, b) for b in active}
        best = max(active, key=lambda b: (scores[b], banks.deposits_of(island, b), b))
        runs = [
            b
            for b in active
            if b != best
            and (
                banks.capital_ratio(island, config, b, ctx.turn) < banks.regulatory(config)
                or banks.cb_borrowed(island, b) > 0
            )
        ]
        switch = to_basis(config.get("banking.switch_share"))
        run = to_basis(config.get("banking.run_share"))
        for entity in banks.household_entities(island):
            held = ledger.deposits_by_bank(island, entity)
            moved_runs = 0
            for bank in runs:
                amount = part(held.get(bank, 0), run)
                ledger.move_deposit(island, entity, bank, best, amount, "deposit_switch")
                moved_runs += amount
            held = ledger.deposits_by_bank(island, entity)
            room = part(sum(held.values()), switch)
            for bank in sorted(held, key=lambda b: (scores.get(b, -math.inf), b)):
                if room <= 0 or bank == best or scores.get(bank, -math.inf) >= scores[best]:
                    continue
                amount = min(held[bank], room)
                ledger.move_deposit(island, entity, bank, best, amount, "deposit_switch")
                room -= amount
            held = ledger.deposits_by_bank(island, entity)
            if held:  # 取引銀行は預金のいちばん多い銀行
                main = max(held, key=lambda b: (held[b], b))
                island.economy.deposit_bank[entity] = banks.bank_number(main)

    @staticmethod
    def _score(island: IslandState, ctx: StageContext, bank: str) -> float:
        """預金者から見た銀行の値打ち: 預金金利 − 危なさ（年率）。"""
        config = ctx.config
        ratio = banks.capital_ratio(island, config, bank, ctx.turn)
        line = banks.regulatory(config) + config.get("banking.risk_buffer")
        risk = max(0.0, line - ratio) * config.get("banking.risk_weight") if ratio < line else 0.0
        return banks.deposit_rate_of(island, bank) / BASIS - risk

    # --- 6. 預金金利と利ざや -------------------------------------------------------------

    @staticmethod
    def _update_rates(island: IslandState, ctx: StageContext, lent: dict[str, list[int]]) -> None:
        config = ctx.config
        settings = config.get("banking")
        step = to_basis(settings["rate_step"])
        policy = island.policy.policy_rate
        low, high = (to_basis(v) for v in settings["margin_range"])
        weighted = total = 0
        for bank in banks.bank_list(island):
            f = banks.facility_of(island, bank)
            if f is None:
                continue
            deposits = banks.deposits_of(island, bank)
            # 預金金利: 目安（政策金利 − deposit_spread）へ近づけ、手元の流動性が足りなければ
            # 上げる。流動性は準備預金の余りと国債の額面（流通市場ですぐ売れる。回5 から）
            target = max(0, policy - to_basis(settings["deposit_spread"]))
            liquid = banks.excess_reserves(island, config, bank) + banks.bonds_held(island, bank)
            liquidity = liquid / deposits if deposits else 1.0
            if liquidity < settings["liquidity_target"]:
                target = policy
            if deposits < f.economy.get("deposit_goal", 0):
                target = policy  # 開業したばかりの銀行は、目当ての預金が集まるまで高い金利で集める
            else:
                f.economy.pop("deposit_goal", None)
            rate = f.economy.get("deposit_rate", island.policy.deposit_rate)
            rate = min(rate + step, target) if rate < target else max(rate - step, target)
            f.economy["deposit_rate"] = max(0, min(policy, rate))
            # 利ざや: 体力が理由で断った銀行は上げ、貸せる余裕があるのに貸さなかった銀行は下げる
            margin = f.economy.get("margin", to_basis(settings["margin"]))
            given, refused = lent.get(bank, [0, 0])
            if refused > 0:
                margin += step
            elif given == 0:
                margin -= step
            f.economy["margin"] = max(low, min(high, margin))
            weighted += f.economy["deposit_rate"] * deposits
            total += deposits
        if total > 0:
            island.policy.deposit_rate = weighted // total


# --- フェーズ2-1 の島の立て直し ----------------------------------------------------


def upgrade(island: IslandState, ctx: StageContext) -> None:
    """フェーズ2-1 から続く島の金融を、フェーズ2-2 の形にする（1度だけ）。

    すでに債務超過の銀行は、自己資本が預金の banking.capital_ratio になるまで出資して立て直す。
    民間銀行は資産を持つ層が出せるだけ出し、残りと国営銀行の分は政府が出す（足りなければ国債を出す）。
    """
    from hakoniwa_core.modules.failures import _government_pays

    finance = island.economy.finance
    if finance.get("version", 0) >= banks.FINANCE_VERSION:
        return
    finance["version"] = banks.FINANCE_VERSION
    config = ctx.config
    ratio = to_basis(config.get("banking.capital_ratio"))
    for bank in banks.bank_list(island):
        f = banks.facility_of(island, bank)
        worth = banks.net_worth(island, config, bank, ctx.turn)
        if f is None or worth >= 0:
            continue
        need = part(banks.deposits_of(island, bank), ratio) - worth
        paid = 0
        if f.owner != OWNER_STATE and OWNERS in island.economy.households:
            owners = Payer(household(OWNERS))
            paid = min(need, funds.available(island, owners))
            if paid > 0:
                funds.pay(island, owners, bank, paid, "capital_injection")
                ledger.create(
                    island, household(OWNERS), ledger.EQUITY, bank, paid, "capital_injection"
                )
        public = _government_pays(island, ctx, bank, need - paid, "capital_injection")
        if public > 0:
            ledger.create(island, ledger.GOV, ledger.EQUITY, bank, public, "capital_injection")
        island.log.append(
            LogEntry("bank_recapitalized", {"x": f.x, "y": f.y, "amount": paid + public})
        )


# --- 命令 -------------------------------------------------------------------------

ENTRY = ("free", "license", "closed")


def check_bank_policy(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if "entry" not in payload and "deposit_insurance" not in payload:
        raise OrderFailed("reason_invalid_payload")
    if payload.get("entry") is not None and payload["entry"] not in ENTRY:
        raise OrderFailed("reason_invalid_payload")
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    if payload.get("entry") in ("free", "license") and regime.get("owner") == OWNER_STATE:
        raise OrderFailed("reason_planned_economy")


def set_bank_policy(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """民間銀行の参入（自由・許可制・禁止）と、預金保護の上限額（-1 で無制限）を決める。"""
    policy = island.policy
    if "entry" in payload:
        policy.bank_entry = payload["entry"]
    if "deposit_insurance" in payload:
        policy.deposit_insurance = int(payload["deposit_insurance"])
    island.log.append(
        LogEntry(
            "bank_policy_set",
            {
                "entry": f"bank_entry_{entry_policy(island, ctx)}",
                "insurance": policy.deposit_insurance,
            },
        )
    )


def entry_policy(island: IslandState, ctx: StageContext) -> str:
    """民間銀行の参入の政策（決めていなければ経済体制の既定）。"""
    if island.policy.bank_entry:
        return island.policy.bank_entry
    regime = ctx.defs.regimes.get(island.regime.get("economy", ""), {})
    return str(regime.get("bank_entry", "closed"))


def check_bank_license(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    if regime.get("owner") == OWNER_STATE:
        raise OrderFailed("reason_planned_economy")
    if (island.policy.bank_entry or regime.get("bank_entry", "closed")) == "closed":
        raise OrderFailed("reason_bank_entry_closed")


def grant_bank_license(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """民間銀行の免許を出す（参入と撤退の段で、空いたオフィス街があれば民間銀行が開業する）。"""
    island.economy.finance["bank_license"] = True
    island.log.append(LogEntry("bank_license_granted", {}))


def register(registry: Registry) -> None:
    registry.add_stage(Banking())
    registry.add_formula("loan_rate", banks.loan_rate)
    registry.add_order(
        OrderHandler("set_bank_policy", execute=set_bank_policy, check=check_bank_policy)
    )
    registry.add_order(
        OrderHandler("grant_bank_license", execute=grant_bank_license, check=check_bank_license)
    )
