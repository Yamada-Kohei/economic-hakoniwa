"""施設の建設と取り壊し（命令）と、工事の進み（段）。

建設の命令は工事中の施設を置くだけです。資材は配分の段で倉庫から、人手は雇用の段で通勤圏から
毎ターン割り当てられ、そろった分だけこの段で工事が進みます。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.naming import assign_serial
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import OWNER_STATE, Facility, IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext


def check_build(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    kind = payload["facility"]
    if kind in defs.facilities and defs.facilities[kind].get("buildable") is False:
        raise OrderFailed("reason_not_buildable", facility=kind)
    problem = fac.placement_problem(
        defs, island, kind, payload["x"], payload["y"], payload.get("recipe")
    )
    if problem is not None:
        raise OrderFailed(problem[0], **problem[1])


def build(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    kind, x, y = payload["facility"], payload["x"], payload["y"]
    economy = ctx.defs.regimes.get(island.regime.get("economy", ""), {})
    f = Facility(
        id=island.next_facility_id(),
        kind=kind,
        x=x,
        y=y,
        recipe=payload.get("recipe") or fac.default_recipe(ctx.defs, kind) or "default",
        owner=OWNER_STATE,  # 命令で建てるのは国営（民営の工場が自分で建つのはフェーズ2から）
        accounting=economy.get("state_accounting", "treasury"),
        name=payload.get("name", "").strip(),
    )
    assign_serial(island, f)
    island.facilities.append(f)
    island.log.append(LogEntry("construction_started", {"facility": kind, "x": x, "y": y}))


def check_demolish(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    x, y = payload["x"], payload["y"]
    if not island.in_bounds(x, y):
        raise OrderFailed("reason_out_of_bounds")
    if island.capital == (x, y):
        raise OrderFailed("reason_capital_building")
    if island.facility_at(x, y) is None and not island.tile(x, y).power_line:
        raise OrderFailed("reason_nothing_to_demolish")


def demolish(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """施設を取り壊す。施設がなければ送電線を外す。施設の在庫は倉庫に戻す。"""
    x, y = payload["x"], payload["y"]
    f = island.facility_at(x, y)
    if f is None:
        island.tile(x, y).infra.pop("power", None)
        island.log.append(LogEntry("power_line_removed", {"x": x, "y": y}))
        return
    stock.move_to_warehouse(f.stock, island.warehouse, ctx.turn, ctx.defs.goods)
    island.facilities.remove(f)
    island.log.append(LogEntry("facility_demolished", {"facility": f.kind, "x": x, "y": y}))


class Construction(IslandStage):
    """工事中の施設を、手元の資材と雇えた人手の分だけ進める。"""

    name = "construction"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs = ctx.defs
        for f in island.facilities:
            if f.built:
                continue
            cdef = defs.facilities[f.kind]["construction"]
            rate = min(fac.ratio(f.workers, cdef.get("labor", 0)), fac.material_ratio(defs, f))
            if rate <= 0:
                continue
            total = cdef["turns"] * fac.FULL
            before = f.progress
            f.progress = min(before + rate, total)
            # 進んだ分だけ資材を使う。引き算で決めるので、工事全体でちょうど定義の量になる
            for good, qty in sorted(cdef.get("materials", {}).items()):
                stock.stock_take(f.stock, good, qty * f.progress // total - qty * before // total)
            if f.progress >= total:
                self._complete(island, f, ctx)

    @staticmethod
    def _complete(island: IslandState, f: Facility, ctx: StageContext) -> None:
        f.built = True
        f.workers = 0
        f.status = fac.STATUS_STARTING
        stock.move_to_warehouse(f.stock, island.warehouse, ctx.turn, ctx.defs.goods)
        island.log.append(LogEntry("construction_done", {"facility": f.kind, "x": f.x, "y": f.y}))


def register(registry: Registry) -> None:
    registry.add_order(OrderHandler("build", execute=build, check=check_build))
    registry.add_order(OrderHandler("demolish", execute=demolish, check=check_demolish))
    registry.add_stage(Construction())
