"""国債の段（フェーズ2-1）: 利払いと償還、足りない分の自動の発行（中央銀行の直接引き受け）。

1. 今のターンの利払いと償還に国庫が足りなければ、足りない分を先に発行する（借り換え）。
2. 持ち手ごとに利子を払い、満期の銘柄は額面を返して消す。
3. 「このターンの国債以外の政府の支出（次のターンの見込み）× (1 + bonds.buffer_turns)
   ＋ 次のターンの利払いと償還 − 国庫の残高」がプラスなら、その額を自動で発行する。期間は政策の割合
   （policy.bond_terms、既定は bonds.auto_terms）で分ける。

利率は名前付きの式 bond_yield で決め、中央銀行が額面で引き受けます。入札・売れ残りの扱い・
債務不履行・売買の市場はフェーズ2-2 で足します。

決算の段より前に置くのは、中央銀行と銀行が受け取った国債の利子を、同じターンの損益に入れるためです。
"""

from __future__ import annotations

from hakoniwa_core import ledger
from hakoniwa_core.economy import BONDS, Payer, books, debt, funds
from hakoniwa_core.economy.stats import inflation
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, to_basis


def bond_yield(island: IslandState, term: str, ctx: StageContext) -> int:
    """国債の利回り（1万分の1）: r_P + λ_T + σ max(0, B/Y − 閾値) + κ max(0, π − π*)。

    λ_T は期間の上乗せ（finance.yaml の bond_term の premium）、B/Y は政府の借金の対年GDP比。
    0 から bonds.max_yield の間に収める。
    """
    settings = ctx.config.get("bonds")
    premium = ctx.defs.bond_terms()[term]["premium"]
    rate = island.policy.policy_rate / BASIS + premium
    rate += settings["sigma"] * max(0.0, debt.debt_ratio(island) - settings["debt_threshold"])
    target = ctx.config.get("central_bank.inflation_target")
    rate += settings["kappa"] * max(0.0, inflation(island.economy) - target)
    return to_basis(max(0.0, min(settings["max_yield"], rate)))


class Bonds(IslandStage):
    name = "bonds"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        payer = Payer(ledger.GOV, BONDS)
        short = debt.due(island, ctx.turn) - funds.available(island, payer)
        if short > 0:
            self._issue(island, ctx, short)
        self._pay(island, ctx, payer)
        spending = sum(v for k, v in funds.spent(island).items() if k != BONDS)
        buffer = ctx.config.get("bonds.buffer_turns")
        need = spending * (1 + buffer) + debt.due_next(island, ctx.turn)
        need -= ledger.money(island, ledger.GOV)
        if need > 0:
            self._issue(island, ctx, need)

    @staticmethod
    def _pay(island: IslandState, ctx: StageContext, payer: Payer) -> None:
        for bond in sorted(island.bonds, key=lambda b: b.id):
            for holder, amount in sorted(debt.interest_due(island, bond, ctx.turn).items()):
                funds.pay(island, payer, holder, amount, "bond_interest")
                _book_interest(island, holder, amount)
            if ctx.turn < bond.maturity:
                continue
            item = ledger.bond_item(bond.id)
            for holder, face in sorted(debt.holders(island, bond).items()):
                funds.pay(island, payer, holder, face, "bond_redemption")
                ledger.cancel(island, holder, item, ledger.GOV, face, "bond_redemption")
        island.bonds = [b for b in island.bonds if debt.outstanding(island, b) > 0]

    @staticmethod
    def _issue(island: IslandState, ctx: StageContext, amount: int) -> None:
        """amount を、自動の発行の期間の割合で分けて発行する（端数は最後の期間に足す）。"""
        terms = ctx.defs.bond_terms()
        shares = sorted(island.policy.bond_terms.items()) or [
            (name, to_basis(share)) for name, share in ctx.config.get("bonds.auto_terms").items()
        ]
        total = sum(share for _, share in shares) or 1
        left = amount
        for i, (name, share) in enumerate(shares):
            face = left if i == len(shares) - 1 else amount * share // total
            left -= face
            rate = ctx.formula("bond_yield")(island, name, ctx)
            bond = debt.underwrite(island, face, terms[name]["turns"], rate, ctx.turn)
            if bond is not None:
                island.log.append(
                    LogEntry(
                        "bonds_issued",
                        {"amount": face, "term": name, "rate": rate * 100 / BASIS},
                    )
                )


def _book_interest(island: IslandState, holder: str, amount: int) -> None:
    """受け取った国債の利子を、中央銀行か銀行の損益に入れる。"""
    if holder == ledger.CB:
        books.add_sector(island, books.CENTRAL_BANK, "interest", amount)
    elif ledger.is_bank(holder):
        books.add(island, int(holder.split(":")[1]), books.INTEREST, amount)


def register(registry: Registry) -> None:
    registry.add_stage(Bonds())
    registry.add_formula("bond_yield", bond_yield)
