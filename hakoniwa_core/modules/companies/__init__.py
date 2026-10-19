"""企業の資金の段（firms_funds。バランス調整の回7。設計書 5 施設と企業「企業の仕組み」）。

倒産の段の後、参入の段の前に、企業ごとに次の順で動きます（hakoniwa_core/economy/companies.py）。

1. 企業に入っていない施設を企業に入れる（民営は1つの施設の新しい企業、国営は業種の国営企業）。
   建国・移し替え・民営化・差し押さえた施設の買い取りの後に、ここでそろう。
2. 民営の企業の未払いの税を払う。
3. 施設の手元: 施設は次の支払いの firms.operating_turns ターン分と借入の返済の見込みを手元に残し、
   超えた分を企業へ上げる（firm_sweep）。足りない施設には企業の預金から回す（firm_support。工場どうし
   の内部補助）。賃金や税が未払いの施設と、閉鎖の途中の施設からは上げない。
4. 法人税: 施設の利益を足した連結の利益に、企業で繰り越した赤字を引いて掛ける（工場どうしの損益の
   通算）。
5. 配当: 連結の税引き後の利益の firms.payout。返済の遅れか未払いのある施設を持つ企業と、連結の
   現金収支がマイナスの企業は配当しない（現実の財務制限条項）。firms.idle_cash_years 年施設を
   建ても買いもせず、手元が施設の支払いの firms.firm_cash_turns ターン分を超えた企業は、超えた分も
   配当する。
   残りは内部留保で、新しい施設の出資に使う（modules/firms の参入）。
6. 立て直し: 施設が2つ以上の企業で、連結の現金収支が firms.restructure_turns ターン続けて
   マイナスで、企業の預金が施設の次の支払いに足りなければ、変動費の利益が一番低い施設を1つ閉める。島でただ1つの
   作り手の施設と、同じ企業の施設に原料を出している施設は後に回す。
7. 施設がなくなった企業は、差し押さえの残りがなければ、預金を株主に返して消す。

国営企業は名簿で、お金は施設ごと（政府が直接支える）なので、3〜7 をしない。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.economy import (
    Payer,
    arrears,
    banks,
    facility_entity,
    funds,
    is_stopped,
)
from hakoniwa_core.economy import companies as co
from hakoniwa_core.economy import freight as freight_rates
from hakoniwa_core.economy.households import add_income
from hakoniwa_core.economy.prices import variable_profit
from hakoniwa_core.economy.start import next_payments
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import (
    OWNER_PRIVATE,
    OWNER_STATE,
    Facility,
    IslandState,
    LogEntry,
)
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import part, to_basis


class FirmsFunds(IslandStage):
    name = "firms_funds"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        enroll(island, ctx)
        profits = {b.facility: b.items for b in island.books}
        for key in sorted(island.economy.firms, key=int):
            number = int(key)
            rec = island.economy.firms[key]
            if rec[co.OWNER] != OWNER_PRIVATE:
                continue
            self._run_firm(island, ctx, number, rec, profits)
        dissolve(island, ctx)
        record_stats(island, ctx)

    def _run_firm(
        self,
        island: IslandState,
        ctx: StageContext,
        number: int,
        rec: dict[str, Any],
        profits: dict[int, dict[str, int]],
    ) -> None:
        config = ctx.config
        settings = config.get("firms")
        name = co.entity(number)
        payer = Payer(name)
        rec[co.SUPPORTED] = 0
        arrears.settle(island, payer)
        members = [f for f in co.members(island, number) if "foreclosed" not in f.economy]
        built = [f for f in members if f.built]
        keep: dict[int, int] = {}
        troubled = False
        for f in built:
            entity = facility_entity(f.id)
            keep[f.id] = next_payments(island, ctx.defs, config, f) * settings[
                "operating_turns"
            ] + banks.debt_service(island, entity, ctx.turn)
            if (
                arrears.total(island, entity)
                or arrears.total(island, entity, arrears.TAX_ARREARS)
                or any(x.arrears > 0 for x in banks.loans_by_borrower(island, entity))
            ):
                troubled = True
        # 3. 余りを上げ、足りない施設に回す
        for f in built:
            entity = facility_entity(f.id)
            if "closing" in f.economy or arrears.total(island, entity):
                continue
            extra = ledger.money(island, entity) - keep[f.id]
            if extra > 0:
                ledger.pay(island, entity, name, extra, "firm_sweep")
        for f in built:
            entity = facility_entity(f.id)
            owed = arrears.total(island, entity) + arrears.total(
                island, entity, arrears.TAX_ARREARS
            )
            short = keep[f.id] + owed - ledger.money(island, entity)
            if short > 0:
                co.draw(island, f, short)
        # 4. 連結の法人税
        profit = sum(profits.get(f.id, {}).get("profit", 0) for f in built)
        depreciation = sum(profits.get(f.id, {}).get("depreciation", 0) for f in built)
        taxable = _carry_losses(rec, profit, ctx.turn, settings["loss_carry_turns"])
        owed_tax = part(taxable, island.policy.corporate_tax)
        tax = min(owed_tax, funds.available(island, payer))
        funds.pay(island, payer, ledger.GOV, tax, "tax_corporate")
        arrears.add_tax(island, name, owed_tax - tax)
        cash_flow = profit + depreciation
        rec[co.RED] = rec.get(co.RED, 0) + 1 if cash_flow < 0 else 0
        # 5. 配当
        dividend = 0
        if not troubled and rec[co.RED] == 0 and owed_tax == tax:
            dividend = part(max(0, profit - owed_tax), to_basis(settings["payout"]))
            idle = ctx.turn - rec.get(co.INVESTED, rec.get(co.FOUNDED, 0))
            if idle >= settings["idle_cash_years"] * TURNS_PER_YEAR:
                payments = sum(next_payments(island, ctx.defs, config, f) for f in built)
                dividend = max(
                    dividend, co.spare(island, number) - settings["firm_cash_turns"] * payments
                )
        dividend = max(0, min(dividend, co.spare(island, number)))
        if dividend > 0:
            holder = co.shareholder(island, number)
            funds.pay(island, payer, holder, dividend, "dividend")
            if holder.startswith("hh:"):
                add_income(island, holder[3:], dividend)
        rec["profit"] = profit
        rec["tax"] = tax
        rec["dividend"] = dividend
        # 6. 立て直し
        if len(built) >= 2 and rec[co.RED] >= settings["restructure_turns"]:
            payments = sum(next_payments(island, ctx.defs, config, f) for f in built)
            if co.spare(island, number) < payments:
                self._close_worst(island, ctx, number, built)
                rec[co.RED] = 0

    @staticmethod
    def _close_worst(
        island: IslandState, ctx: StageContext, number: int, built: list[Facility]
    ) -> None:
        defs, config = ctx.defs, ctx.config
        rates = freight_rates.facility_rates(island, defs, config)
        prices = island.economy.prices
        makers: dict[str, int] = {}
        for f in island.facilities:
            if f.built and not is_stopped(f):
                for good in fac.recipe_of(defs, f)["outputs"]:
                    makers[good] = makers.get(good, 0) + 1
        used = set()
        for f in built:
            used |= set(fac.recipe_of(defs, f)["inputs"])
        candidates = []
        for f in built:
            if "closing" in f.economy or is_stopped(f):
                continue
            outputs = set(fac.recipe_of(defs, f)["outputs"])
            sole = any(makers.get(g, 0) <= 1 for g in outputs)
            supplier = bool(outputs & used)
            profit = variable_profit(
                defs, config, f, prices, f.economy.get("wage", 0), rates.get(f.id, 0)
            )
            candidates.append(((sole or supplier), profit, f.id, f))
        if not candidates:
            return
        candidates.sort(key=lambda c: c[:3])
        f = candidates[0][3]
        f.economy["closing"] = ctx.turn
        f.economy["target"] = 0
        island.log.append(
            LogEntry(
                "firm_restructured",
                {
                    "facility": f.kind,
                    "x": f.x,
                    "y": f.y,
                    "firm": co.display_name(island, defs, number),
                },
            )
        )
        stats = island.stats
        stats["firms.restructured"] = stats.get("firms.restructured", 0) + 1


def _carry_losses(rec: dict[str, Any], profit: int, turn: int, carry: int) -> int:
    """企業の赤字を繰り越し、このターンの課税される利益を返す（古い赤字から使う）。"""
    losses = [[t, a] for t, a in rec.get(co.LOSSES, []) if turn - t < carry]
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
        rec[co.LOSSES] = losses
    else:
        rec.pop(co.LOSSES, None)
    return taxable


def enroll(island: IslandState, ctx: StageContext) -> None:
    """企業に入っていない施設を企業に入れ、持ち主の替わった施設を入れ替える。"""
    defs, config = ctx.defs, ctx.config
    for f in sorted(island.facilities, key=lambda f: f.id):
        if "foreclosed" in f.economy or not co.eligible(defs, config, f):
            if co.firm_of(f) is not None and "foreclosed" in f.economy:
                co.leave(island, f)
            continue
        number = co.firm_of(f)
        rec = co.record(island, number) if number is not None else None
        if f.owner == OWNER_STATE:
            if rec is None or rec[co.OWNER] != OWNER_STATE:
                co.leave(island, f)
                f.economy[co.FIRM] = co.state_firm(island, defs, f.kind, ctx.turn)
            continue
        if rec is None or rec[co.OWNER] != OWNER_PRIVATE:
            co.leave(island, f)
            co.join(island, f, co.new_firm(island, OWNER_PRIVATE, ctx.turn))


def dissolve(island: IslandState, ctx: StageContext) -> None:
    """施設がなく、差し押さえの残りもない企業を消す（民営は預金を株主に返し、株を消す）。"""
    held = {co.firm_of(f) for f in island.facilities}
    owners = {
        f.economy["foreclosed"].get("owner")
        for f in island.facilities
        if isinstance(f.economy.get("foreclosed"), dict)
    }
    for key in sorted(island.economy.firms, key=int):
        number = int(key)
        if number in held or co.entity(number) in owners:
            continue
        rec = island.economy.firms[key]
        if rec[co.OWNER] == OWNER_PRIVATE:
            name = co.entity(number)
            holder = co.shareholder(island, number)
            arrears.settle(island, Payer(name))
            arrears.write_off(island, name)
            left = ledger.money(island, name)
            ledger.pay(island, name, holder, left, "equity_return")
            if left > 0 and holder.startswith("hh:"):
                add_income(island, holder[3:], 0)
            value = ledger.balance(island, ledger.account(holder, ledger.EQUITY, name))
            if value > 0:
                ledger.cancel(island, holder, ledger.EQUITY, name, value, "equity_loss")
        del island.economy.firms[key]


def record_stats(island: IslandState, ctx: StageContext) -> None:
    """企業の数と、施設の一番多い民営の企業の施設の割合、企業の中で回した額の統計。"""
    counts: dict[int, int] = {}
    private = 0
    for f in island.facilities:
        number = co.private_firm_of(island, f)
        if number is not None and f.built:
            counts[number] = counts.get(number, 0) + 1
            private += 1
    stats = island.stats
    stats["firms.count"] = len(counts)
    stats["firms.groups"] = sum(1 for n in counts.values() if n >= 2)
    stats["firms.largest_share"] = max(counts.values()) / private if private else 0.0
    stats["firms.support"] = sum(rec.get(co.SUPPORTED, 0) for rec in island.economy.firms.values())
    stats["firms.cash"] = sum(
        ledger.money(island, co.entity(int(k)))
        for k, rec in island.economy.firms.items()
        if rec[co.OWNER] == OWNER_PRIVATE
    )


def register(registry: Registry) -> None:
    registry.add_stage(FirmsFunds())
