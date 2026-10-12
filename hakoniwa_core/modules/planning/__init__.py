"""計画の実行: 行政力の範囲で、並んだ命令を上から実行する段。"""

from __future__ import annotations

from typing import Any

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
    """そのターンに使える行政力（1ターンに実行できる命令の数）。

    中央省庁で働く公務員の数 × 係数（施設の定義の admin_power、働く人1人あたり）の整数部分で、
    最低 orders.base_admin_power、最大 orders.max_admin_power。人数は前のターンに雇えた人数。
    """
    power = 0.0
    for f in island.facilities:
        per_worker = ctx.defs.facilities[f.kind].get("admin_power", 0)
        if f.built and per_worker:
            power += f.workers * per_worker
    low = ctx.config.get("orders.base_admin_power")
    high = ctx.config.get("orders.max_admin_power")
    # 1e-9 は 0.001 × 1000 などの小数の誤差で 1 少なくならないように
    return max(low, min(high, int(power + 1e-9)))


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
                self._fail(island, order, check.reason or "reason_unknown_order", check.args)
                continue
            try:
                registry.orders[order.kind].execute(island, order.payload, ctx)
            except OrderFailed as e:
                self._fail(island, order, e.reason, e.args_)
                continue
            order.status = ORDER_DONE
            island.log.append(LogEntry("order_done", {"order": order.kind}))

    @staticmethod
    def _fail(island: IslandState, order: OrderState, reason: str, args: dict[str, Any]) -> None:
        order.status = ORDER_FAILED
        order.reason = reason
        order.reason_args = dict(args)
        entry = {"order": order.kind, "reason": reason}
        if args:
            entry["reason_args"] = dict(args)
        island.log.append(LogEntry("order_failed", entry))


def register(registry: Registry) -> None:
    registry.add_stage(ExecuteOrders())
    registry.add_formula("admin_power", admin_power)
