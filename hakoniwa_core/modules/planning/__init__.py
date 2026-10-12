"""計画の実行: 行政力の範囲で、並んだ命令を上から実行する段。"""

from __future__ import annotations

from hakoniwa_core.orders import check_order
from hakoniwa_core.registry import IslandStage, OrderFailed, Registry
from hakoniwa_core.state import (
    ORDER_DONE,
    ORDER_FAILED,
    ORDER_PENDING,
    IslandState,
    LogEntry,
    OrderState,
)
from hakoniwa_core.turn.context import StageContext


def admin_power(island: IslandState, ctx: StageContext) -> int:
    """そのターンに使える行政力。フェーズ1で官庁の公務員の分を足す。"""
    return ctx.config.get("orders.base_admin_power")


class ExecuteOrders(IslandStage):
    name = "execute_orders"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        registry = ctx.registry
        remaining = ctx.formula("admin_power")(island, ctx)
        pending = sorted(
            (o for o in island.orders if o.status == ORDER_PENDING), key=lambda o: (o.seq, o.id)
        )
        for order in pending:
            cost = ctx.defs.orders[order.kind]["admin_cost"] if order.kind in ctx.defs.orders else 0
            if cost > remaining:
                island.log.append(LogEntry("order_postponed", {"order": order.kind}))
                break
            remaining -= cost
            order.executed_turn = ctx.turn
            check = check_order(ctx.defs, registry, island, order.kind, order.payload)
            if not check.ok:
                self._fail(island, order, check.reason or "reason_unknown_order")
                continue
            try:
                registry.orders[order.kind].execute(island, order.payload, ctx)
            except OrderFailed as e:
                self._fail(island, order, e.reason)
                continue
            order.status = ORDER_DONE
            island.log.append(LogEntry("order_done", {"order": order.kind}))

    @staticmethod
    def _fail(island: IslandState, order: OrderState, reason: str) -> None:
        order.status = ORDER_FAILED
        order.reason = reason
        island.log.append(LogEntry("order_failed", {"order": order.kind, "reason": reason}))


def register(registry: Registry) -> None:
    registry.add_stage(ExecuteOrders())
    registry.add_formula("admin_power", admin_power)
