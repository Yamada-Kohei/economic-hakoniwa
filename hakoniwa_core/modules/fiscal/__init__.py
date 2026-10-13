"""財政: ターンの初めの国庫の取り置き（予算の段 budget）と、税率・予算の命令。

予算の段は、支出の項目ごとに「前のターンの支出 × (1 + fiscal.reserve_margin)」を見積もり
（国債は今のターンの利払いと償還）、fiscal.payment_order の順に国庫を取り置きます
（hakoniwa_core/economy/funds.py）。年の予算を決める項目（fiscal.budget_items）は、予算の
12分の1までしか使えません。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import BONDS, funds
from hakoniwa_core.economy.debt import due
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, part, to_basis

YEAR = 12
TAXES = ("income_tax", "corporate_tax", "consumption_tax")


class Budget(IslandStage):
    name = "budget"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        fiscal = ctx.config.get("fiscal")
        margin = to_basis(fiscal["reserve_margin"])
        estimates = {
            item: amount + part(amount, margin) for item, amount in island.economy.spending.items()
        }
        # 国債は利払いの分だけ取り置く。満期の償還は国債の段で、足りない分を借り換えて払う
        # （償還の額面を先に取り置くと、ほかの支出の取り置きが国庫に残らない）
        estimates[BONDS] = due(island, ctx.turn, redemptions=False)
        caps = {item: island.policy.budget.get(item, 0) // YEAR for item in fiscal["budget_items"]}
        funds.open_treasury(island, estimates, list(fiscal["payment_order"]), caps)


# --- 命令 -------------------------------------------------------------------------


def check_taxes(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not any(key in payload for key in TAXES):
        raise OrderFailed("reason_invalid_payload")


def set_taxes(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """税率（%）を決める。書いた税だけ変わり、次の段から効く。"""
    policy = island.policy
    for key in TAXES:
        if key in payload:
            setattr(policy, key, to_basis(payload[key] / 100))
    island.log.append(
        LogEntry("taxes_set", {key: getattr(policy, key) * 100 / BASIS for key in TAXES})
    )


def check_budget(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not payload:
        raise OrderFailed("reason_invalid_payload")


def set_budget(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """年の予算を決める（毎ターンその12分の1まで使う）。

    決められるのは fiscal.budget_items の項目。
    """
    items = ctx.config.get("fiscal.budget_items")
    for item in sorted(payload):
        if item in items:
            island.policy.budget[item] = int(payload[item])
            island.log.append(
                LogEntry("budget_set", {"item": f"budget_{item}", "amount": int(payload[item])})
            )


def register(registry: Registry) -> None:
    registry.add_stage(Budget())
    registry.add_order(OrderHandler("set_taxes", execute=set_taxes, check=check_taxes))
    registry.add_order(OrderHandler("set_budget", execute=set_budget, check=check_budget))
