"""銀行の段（フェーズ2-1）: 預金の利子、準備預金の余りの利子、運転資金の出資。

フェーズ2-1 の銀行は島に1つで、融資はしません。預金金利は政策の値のまま（banking.deposit_rate）
で、銀行は預金者に「預金 × 預金金利 ÷ 12」を払います（預金が増える）。中央銀行は銀行の
準備預金のうち最低限（預金の banking.reserve_ratio）を超えた分に「政策金利 −
central_bank.reserve_spread」の利子を払います。

融資がない間は、次のターンの支払い（操業目標の分の賃金・投入財・電気代・運賃）に預金が足りない
独立採算の施設に、持ち主が足りない分を出資します（民営は資産を持つ層の預金から、国営は国庫から）。
フェーズ2-2 で、これを銀行の運転資金の融資に置き換えます。融資・銀行間の貸し借り・預金の
乗り換えもフェーズ2-2 で足します。
"""

from __future__ import annotations

from hakoniwa_core import ledger
from hakoniwa_core.economy import (
    STATE_FACILITIES,
    Payer,
    books,
    facility_entity,
    funds,
    is_bank,
    is_self_supporting,
    owner_entity,
)
from hakoniwa_core.economy.households import add_income, layer_of
from hakoniwa_core.economy.start import next_payments
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import part, to_basis


def deposits_of(island: IslandState, bank: str) -> int:
    """銀行が出している預金の合計。"""
    return -ledger.balance(island, ledger.issued(bank, ledger.DEPOSIT))


class Banking(IslandStage):
    name = "banking"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        for number in island.economy.banks:
            bank = ledger.bank_entity(number)
            self._deposit_interest(island, bank, number)
            self._reserve_interest(island, ctx, bank, number)
        for f in sorted(island.facilities, key=lambda f: f.id):
            if is_self_supporting(f) and not is_bank(ctx.config, f):
                self._working_capital(island, ctx, f)

    @staticmethod
    def _deposit_interest(island: IslandState, bank: str, number: int) -> None:
        rate = island.policy.deposit_rate
        suffix = f"/{ledger.DEPOSIT}/{bank}"
        accounts = [(n, v) for n, v in sorted(island.accounts.items()) if n.endswith(suffix)]
        paid = 0
        for name, value in accounts:
            amount = ledger.interest(value, rate)
            if amount <= 0:
                continue
            holder = ledger.parse(name)[0]
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
        required = part(
            deposits_of(island, bank), to_basis(ctx.config.get("banking.reserve_ratio"))
        )
        excess = ledger.balance(island, ledger.reserve(bank)) - required
        spread = to_basis(ctx.config.get("central_bank.reserve_spread"))
        amount = ledger.interest(excess, island.policy.policy_rate - spread)
        if amount > 0:
            ledger.pay(island, ledger.CB, bank, amount, "interest_reserve")
            books.add(island, number, books.INTEREST, amount)
            books.add_sector(island, books.CENTRAL_BANK, "interest", -amount)

    @staticmethod
    def _working_capital(island: IslandState, ctx: StageContext, f: Facility) -> None:
        """次のターンの支払いに預金が足りなければ、持ち主が足りない分を出資する。"""
        need = next_payments(island, ctx.defs, ctx.config, f)
        entity = facility_entity(f.id)
        short = need - ledger.money(island, entity)
        if short <= 0:
            return
        owner = owner_entity(f)
        payer = Payer(owner, STATE_FACILITIES if owner == ledger.GOV else None)
        amount = min(short, funds.available(island, payer))
        if amount > 0:
            funds.pay(island, payer, entity, amount, "capital_injection")
            ledger.create(island, owner, ledger.EQUITY, entity, amount, "capital_injection")
            books.add(island, f.id, books.CAPITAL, amount)


def register(registry: Registry) -> None:
    registry.add_stage(Banking())
