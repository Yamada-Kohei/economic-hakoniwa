"""決算の段: 施設と銀行の損益、減価償却、法人税、配当と国庫への納付、中央銀行の納付。

損益（税の前）= 付加価値（売上 − 仕入れ − 電気代 − 運賃 + 在庫の増減）− 賃金 + 受け取った利子
               − 減価償却
在庫の増減は、そのターンの値段で数えます（hakoniwa_core/economy/books.py）。減価償却は、工事で
実際に払った額（設備の額）を firms.depreciation_turns ターンで均等に費用にします。

独立採算の施設と銀行は、利益に法人税（政策の税率。赤字は firms.loss_carry_turns ターン繰り越す）を
払い、税引き後の利益の firms.payout を持ち主に払います（民営は資産を持つ層への配当、国営は国庫への
納付）。手元の資金が次のターンの支払いの見込みの firms.cash_turns ターン分を超えたら、超えた分も
払います。払えるのは、手元の資金のうち支払いの見込みの firms.operating_turns ターン分を残した額
までです。税引き後の利益が出なかったターンは払いません（売れずに止まって支払いの見込みが小さく
なった施設が、赤字のまま手元の資金を持ち主に払ってしまわないように）。国庫直轄の施設は
お金がすべて国庫のものなので、損益を帳簿に残すだけです。

中央銀行の利益（国債の利子 − 準備預金の利子）は全額を国庫に納めます。損失は繰り越して、
後の利益で埋めてから納めます。

ここで施設ごとの帳簿（FacilityBook）を作ります。会計の形を変える命令 set_accounting もここです。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    OWNERS,
    STATE_FACILITIES,
    Payer,
    books,
    entity_of,
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
        for f in sorted(island.facilities, key=lambda f: f.id):
            if not f.built or fac.is_housing(config, f):
                continue
            items = dict(flows.get(f.id, {}))
            items[books.INVENTORY] = inventory.get(f.id, 0)
            items[books.DEPRECIATION] = self._depreciate(f, config)
            profit = (
                books.value_added(items)
                - items.get(books.WAGES, 0)
                + items.get(books.INTEREST, 0)
                - items[books.DEPRECIATION]
            )
            items[books.PROFIT] = profit
            if is_self_supporting(f) or is_bank(config, f):
                items[books.TAX], items[books.DIVIDEND] = self._settle(island, ctx, f, items)
            entity = entity_of(config, f)
            island.books.append(
                FacilityBook(
                    facility=f.id,
                    items={k: v for k, v in sorted(items.items()) if v},
                    deposit=ledger.money(island, entity) if entity != ledger.GOV else 0,
                    inventory=sum(
                        money_of(prices.get(g, 0), q) for g, q in stock.totals(f.stock).items()
                    ),
                )
            )
        self._central_bank(island)

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
        cost = next_payments(island, ctx.defs, ctx.config, f)
        if ledger.is_bank(payer.entity):
            keep, cash = 0, 0
        else:
            keep = settings["operating_turns"] * cost
            cash = funds.available(island, payer)
        dividend = part(max(0, profit - tax), to_basis(settings["payout"]))
        if cash > settings["cash_turns"] * cost and profit - tax > 0:
            dividend = max(dividend, cash - settings["cash_turns"] * cost)
        if not ledger.is_bank(payer.entity):
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
    def _central_bank(island: IslandState) -> None:
        income = books.sector(island, books.CENTRAL_BANK).get("interest", 0)
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
