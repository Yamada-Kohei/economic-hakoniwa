"""決算の段: 施設と銀行の損益、減価償却、補助金、法人税、配当と国庫への納付、中央銀行の納付。

損益（税の前）= 付加価値（売上 − 仕入れ − 電気代 − 運賃 + 在庫の増減）− 賃金 + 利子（受け取った利子
               − 払った利子）+ 補助金 − 関税（商社。フェーズ3-3）− 減価償却
銀行の損益 = 利子（融資・国債・準備預金・銀行間の受け取り − 預金・銀行間・中央銀行への支払い）
             − 職員の賃金 − 貸し倒れ ＋ 国債の実現損益
商社は、外貨を売ったときの為替の実現損益（受け取った自国通貨 − 外貨の帳簿の値打ち）も損益に入れ
ます。まだ売っていない外貨の評価損益は入れません（フェーズ3-1。hakoniwa_core/trade/money.py）。
在庫の増減は、そのターンの値段で数えます（hakoniwa_core/economy/books.py）。減価償却は、工事で
実際に払った額（設備の額）を firms.depreciation_turns ターンで均等に費用にします。

国債の時価の評価: 持ち手（銀行と中央銀行）ごとに「今の時価 − 前のターンの時価 − このターンに
買った代金 ＋ 売った代金と償還の額」が時価の変化です（国債の段の bond_flows）。そのうち、売った・
償還された・債務不履行で切り下げられた分の「受け取った額 − 取得原価」（実現損益。economy/debt.py の
released）だけを損益に入れ、残り（まだ売っていない評価損益の増減）は損益に入れません。法人税・配当・
中央銀行の納付には、売るまで回りません。銀行の自己資本は国債を時価で数えるので、金利が上がると
国債を多く持つ銀行の自己資本が減り、マイナスになれば破綻します。

補助金（命令 set_subsidy、予算の項目 subsidies から払う。足りなければ比例で減らす）:
- 生産補助: 品目か工業団地のマスを指定し、独立採算の施設の売上の決めた割合を払う。
- 救済補助: 施設を指定し、そのターンの赤字を埋める。
- 設備投資補助は、工事が済んだときに払う（工事の段）。

独立採算の施設と銀行は、利益に法人税（政策の税率。赤字は firms.loss_carry_turns ターン繰り越す）を払
い、税引き後の利益の firms.payout を持ち主に払います（民営は資産を持つ層への配当、国営は国庫への納
付）。手元の資金が次のターンの支払いの見込みの firms.cash_turns ターン分を超えたら、超えた分も払いま
す。払えるのは、手元の資金のうち支払いの見込みの firms.operating_turns ターン分を残した額までです。
税引き後の利益が出なかったターンは払いません。銀行は、払った後も自己資本が目安（「融資 ×（規制の値 +
banking.risk_buffer）」と「預金 × banking.capital_floor」の大きい方）以上に残る分までしか払いませ
ん。国庫直轄の施設はお金がすべて国庫のものなので、損益を帳簿に残すだけです。国営の独立採算の施設が
firms.loss_notice_turns ターン続けて赤字なら、ログで知らせます（補助金・縮小・閉鎖はプレイヤーが選
ぶ）。

中央銀行の利益（国債と貸し出しの利子 − 準備預金の利子 ＋ 国債の実現損益）は全額を国庫に納めます。
損失は繰り越して、後の利益で埋めてから納めます。

ここで施設ごとの帳簿（FacilityBook）を作り、融資の審査に使う「利払い前の利益 ＋ 減価償却」の記録
（economy["ebitda"]）を残します。会計の形を変える命令 set_accounting もここです。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    OWNERS,
    STATE_FACILITIES,
    SUBSIDIES,
    Payer,
    banks,
    books,
    debt,
    entity_of,
    facility_entity,
    funds,
    is_bank,
    is_public_service,
    is_self_supporting,
    owner_entity,
)
from hakoniwa_core.economy.households import add_income
from hakoniwa_core.economy.start import fund_facility, next_payments
from hakoniwa_core.modules.construction import close_accounts
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
    ACCOUNTING_TREASURY,
    OWNER_STATE,
    Facility,
    FacilityBook,
    IslandState,
    LogEntry,
)
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import money_of, part, to_basis


class Accounts(IslandStage):
    name = "accounts"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        config = ctx.config
        prices = island.economy.prices
        inventory = books.inventory_changes(island, prices)
        island.scratch["inventory_changes"] = inventory
        flows = books.facility_items(island)
        valuation, realized = self._valuation(island, ctx)
        rows: list[tuple[Facility, dict[str, int]]] = []
        for f in sorted(island.facilities, key=lambda f: f.id):
            if not f.built or fac.is_housing(config, f):
                continue
            items = dict(flows.get(f.id, {}))
            items[books.INVENTORY] = inventory.get(f.id, 0)
            items[books.DEPRECIATION] = self._depreciate(f, config)
            if is_bank(config, f):
                bank = ledger.bank_entity(f.id)
                items[books.REALIZED] = realized.get(bank, 0)
                if bank in valuation:
                    items[books.VALUATION] = valuation[bank] - items[books.REALIZED]
            rows.append((f, items))
        self._subsidies(island, ctx, rows)
        settings = config.get("firms")
        for f, items in rows:
            ebitda = (
                books.value_added(items)
                - items.get(books.WAGES, 0)
                + items.get(books.SUBSIDY, 0)
                - items.get(books.TARIFF, 0)
            )
            profit = (
                ebitda
                + items.get(books.INTEREST, 0)
                + items.get(books.REALIZED, 0)
                + items.get(books.FX_REALIZED, 0)
                - items.get(books.WRITE_OFF, 0)
                - items[books.DEPRECIATION]
            )
            items[books.PROFIT] = profit
            bank = is_bank(config, f)
            if is_self_supporting(f) or bank:
                items[books.TAX], items[books.DIVIDEND] = self._settle(island, ctx, f, items)
                # 融資の審査で見る営業の現金収支（売れずに積み上がった在庫は返済に使えない）
                cash = ebitda - items.get(books.INVENTORY, 0)
                keep = settings["ebitda_turns"]
                f.economy["ebitda"] = [*f.economy.get("ebitda", []), cash][-keep:]
            if bank:
                f.economy["profits"] = [*f.economy.get("profits", []), profit][-12:]
            elif is_self_supporting(f):
                self._watch_losses(island, ctx, f, profit)
            entity = entity_of(config, f)
            island.books.append(
                FacilityBook(
                    facility=f.id,
                    items={k: v for k, v in sorted(items.items()) if v},
                    deposit=ledger.money(island, entity) if entity != ledger.GOV else 0,
                    debt=banks.debt_of(island, facility_entity(f.id)),
                    inventory=sum(
                        money_of(prices.get(g, 0), q) for g, q in stock.totals(f.stock).items()
                    ),
                )
            )
        cb_realized = realized.get(ledger.CB, 0)
        cb_valuation = valuation[ledger.CB] - cb_realized if ledger.CB in valuation else 0
        self._central_bank(island, cb_realized, cb_valuation)

    @staticmethod
    def _valuation(island: IslandState, ctx: StageContext) -> tuple[dict[str, int], dict[str, int]]:
        """国債の持ち手ごとの (時価の変化（前のターンの時価からの変化 − 売買と償還の出入り）,
        実現損益（このターンに売った・償還された・切り下げられた分）)。"""
        finance = island.economy.finance
        first = "bond_book" not in finance  # 初めのターンは今の時価を帳簿の値にする（評価損益なし）
        book = finance.setdefault("bond_book", {})
        moved = island.scratch.get("bond_flows", {})
        holders = sorted(set(banks.bank_list(island)) | {ledger.CB} | set(book) | set(moved))
        out = {}
        for holder in holders:
            value = banks.bond_value(island, ctx.config, holder, ctx.turn)
            if not first:
                out[holder] = value - book.get(holder, 0) - moved.get(holder, 0)
            if value:
                book[holder] = value
            else:
                book.pop(holder, None)
        island.scratch[debt.VALUED] = True  # この後の国債の出入りは帳簿の値に直接足す
        return out, finance.pop(debt.REALIZED, {})

    @staticmethod
    def _subsidies(
        island: IslandState, ctx: StageContext, rows: list[tuple[Facility, dict[str, int]]]
    ) -> None:
        """生産補助と救済補助を、補助金の予算から払う（足りなければ比例で減らす）。"""
        live = [s for s in island.policy.subsidies if s.get("until", 0) >= ctx.turn]
        island.policy.subsidies = live
        if not live:
            return
        wanted: dict[int, int] = {}
        for f, items in rows:
            if not is_self_supporting(f) or is_bank(ctx.config, f):
                continue
            outputs = fac.recipe_of(ctx.defs, f)["outputs"]
            for s in live:
                if s["kind"] == "production" and (
                    s.get("good") in outputs or (s.get("x"), s.get("y")) == (f.x, f.y)
                ):
                    amount = part(items.get(books.SALES, 0), s["rate"])
                elif s["kind"] == "bailout" and (s.get("x"), s.get("y")) == (f.x, f.y):
                    before = (
                        books.value_added(items)
                        - items.get(books.WAGES, 0)
                        + items.get(books.INTEREST, 0)
                        - items[books.DEPRECIATION]
                    )
                    amount = max(0, -before)
                else:
                    continue
                if amount > 0:
                    wanted[f.id] = wanted.get(f.id, 0) + amount
        total = sum(wanted.values())
        payer = Payer(ledger.GOV, SUBSIDIES)
        money = funds.available(island, payer)
        for f, items in rows:
            amount = wanted.get(f.id, 0)
            if amount <= 0:
                continue
            if total > money:
                amount = amount * money // total
            funds.pay(island, payer, facility_entity(f.id), amount, "subsidy")
            items[books.SUBSIDY] = items.get(books.SUBSIDY, 0) + amount

    @staticmethod
    def _watch_losses(island: IslandState, ctx: StageContext, f: Facility, profit: int) -> None:
        """国営の独立採算の施設が赤字続きなら、プレイヤーに知らせる（続いている間は1度だけ）。"""
        if f.owner != OWNER_STATE or profit >= 0:
            f.economy.pop("loss_turns", None)
            return
        turns = f.economy.get("loss_turns", 0) + 1
        f.economy["loss_turns"] = turns
        if turns == ctx.config.get("firms.loss_notice_turns"):
            island.log.append(
                LogEntry("state_facility_losses", {"facility": f.kind, "x": f.x, "y": f.y})
            )

    @staticmethod
    def _depreciate(f: Facility, config: Any) -> int:
        capital = f.economy.get("capital", 0)
        done = f.economy.get("depreciated", 0)
        amount = min(max(0, capital - done), capital // config.get("firms.depreciation_turns"))
        if amount > 0:
            f.economy["depreciated"] = done + amount
        return amount

    def _settle(
        self, island: IslandState, ctx: StageContext, f: Facility, items: dict[str, int]
    ) -> tuple[int, int]:
        """法人税と、配当（国営は国庫への納付）を払い、(税, 配当) を返す。"""
        settings = ctx.config.get("firms")
        payer = Payer(entity_of(ctx.config, f))
        profit = items[books.PROFIT]
        taxable = self._carry_losses(f, profit, ctx.turn, settings["loss_carry_turns"])
        tax = min(part(taxable, island.policy.corporate_tax), funds.available(island, payer))
        funds.pay(island, payer, ledger.GOV, tax, "tax_corporate")
        dividend = part(max(0, profit - tax), to_basis(settings["payout"]))
        if ledger.is_bank(payer.entity):
            bank = payer.entity
            # 自己資本の目安（規制の値に上乗せした分）を超える分からだけ払う
            room = banks.net_worth(island, ctx.config, bank, ctx.turn)
            room -= banks.capital_target(island, ctx.config, bank)
            if banks.is_failed(island, bank):
                room = 0
            dividend = max(0, min(dividend, room))
        else:
            cost = next_payments(island, ctx.defs, ctx.config, f)
            keep = settings["operating_turns"] * cost
            cash = funds.available(island, payer)
            if cash > settings["cash_turns"] * cost and profit - tax > 0:
                dividend = max(dividend, cash - settings["cash_turns"] * cost)
            dividend = min(dividend, max(0, cash - keep))
        owner = owner_entity(f)
        funds.pay(island, payer, owner, dividend, "dividend")
        if owner.startswith("hh:"):
            add_income(island, OWNERS, dividend)
        return tax, dividend

    @staticmethod
    def _carry_losses(f: Facility, profit: int, turn: int, carry: int) -> int:
        """赤字を繰り越し、今のターンの課税される利益を返す（繰り越した赤字は古い順に使う）。"""
        losses = [[t, a] for t, a in f.economy.get("losses", []) if turn - t < carry]
        if profit < 0:
            losses.append([turn, -profit])
            taxable = 0
        else:
            taxable = profit
            for entry in losses:
                use = min(entry[1], taxable)
                entry[1] -= use
                taxable -= use
            losses = [e for e in losses if e[1] > 0]
        if losses:
            f.economy["losses"] = losses
        else:
            f.economy.pop("losses", None)
        return taxable

    @staticmethod
    def _central_bank(island: IslandState, realized: int, valuation: int) -> None:
        """中央銀行の利益（利子 ＋ 国債の実現損益）を、繰り越した損失を埋めてから国庫に納める。
        まだ売っていない国債の評価損益（valuation）は記録だけで、納めない。"""
        books.add_sector(island, books.CENTRAL_BANK, books.REALIZED, realized)
        books.add_sector(island, books.CENTRAL_BANK, books.VALUATION, valuation)
        income = books.sector(island, books.CENTRAL_BANK).get("interest", 0) + realized
        total = income - island.economy.cb_loss
        if total > 0:
            ledger.pay(island, ledger.CB, ledger.GOV, total, "cb_payment")
            island.economy.cb_loss = 0
        else:
            island.economy.cb_loss = -total


# --- 命令 -------------------------------------------------------------------------


def check_accounting(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    f = island.facility_at(payload["x"], payload["y"])
    if f is None or defs.facilities[f.kind].get("buildable") is False:
        raise OrderFailed("reason_no_facility")
    if f.owner != OWNER_STATE:
        raise OrderFailed("reason_not_state_owned", facility=f.kind)
    if is_public_service(defs, f):
        raise OrderFailed("reason_public_service", facility=f.kind)
    if defs.facilities[f.kind].get("category") == "finance":
        raise OrderFailed("reason_bank_accounting")
    if payload["accounting"] not in (ACCOUNTING_SELF, ACCOUNTING_TREASURY):
        raise OrderFailed("reason_invalid_payload")
    if f.accounting == payload["accounting"]:
        raise OrderFailed("reason_accounting_unchanged", facility=f.kind)


def set_accounting(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """国営施設の会計の形を変える。

    独立採算にするときは政府が運転資金を出資し、国庫直轄に戻すときは預金を国庫に戻して出資を消す。
    """
    f = island.facility_at(payload["x"], payload["y"])
    assert f is not None
    target = payload["accounting"]
    if target == ACCOUNTING_TREASURY:
        close_accounts(island, f)
        f.accounting = target
    else:
        f.accounting = target
        if f.built and island.has_economy:
            fund_facility(island, ctx.defs, ctx.config, f, Payer(ledger.GOV, STATE_FACILITIES))
    island.log.append(
        LogEntry(
            "accounting_changed",
            {"facility": f.kind, "x": f.x, "y": f.y, "accounting": f"accounting_{target}"},
        )
    )


def register(registry: Registry) -> None:
    registry.add_stage(Accounts())
    registry.add_order(
        OrderHandler("set_accounting", execute=set_accounting, check=check_accounting)
    )
