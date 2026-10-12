"""施設の建設と取り壊し（命令）と、工事の進み（段）。

建設の命令は工事中の施設を置くだけです。資材は市場の段で国の倉庫から先に使い、足りない分を
市場で買います。人手は雇用の段で通勤圏から島の平均賃金で雇います。資材と賃金は国庫が払い
（国の工事）、そろった分だけこの段で工事が進みます。払った額が施設の設備の額になり、
完成すると減価償却が始まります。独立採算の施設には、完成のときに政府が運転資金を出資します。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    CONSTRUCTION,
    Payer,
    default_accounting,
    facility_entity,
    is_self_supporting,
    owner_entity,
)
from hakoniwa_core.economy.start import fund_facility, init_facility
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
    f = Facility(
        id=island.next_facility_id(),
        kind=kind,
        x=x,
        y=y,
        recipe=payload.get("recipe") or fac.default_recipe(ctx.defs, kind) or "default",
        owner=OWNER_STATE,  # 命令で建てるのは国営（民営の工場が自分で建つのはフェーズ2-2から）
        accounting=default_accounting(ctx.defs, island, kind, OWNER_STATE),
        name=payload.get("name", "").strip(),
        economy={"capital": 0},
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
    f = island.facility_at(x, y)
    if f is None and not island.tile(x, y).power_line:
        raise OrderFailed("reason_nothing_to_demolish")
    if f is not None and defs.facilities[f.kind].get("category") == "finance":
        raise OrderFailed("reason_cannot_demolish", facility=f.kind)


def demolish(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """施設を取り壊す。施設がなければ送電線を外す。

    施設の在庫は国の倉庫に戻し、独立採算の施設の預金は出資の払い戻しとして持ち主に返す。
    """
    x, y = payload["x"], payload["y"]
    f = island.facility_at(x, y)
    if f is None:
        island.tile(x, y).infra.pop("power", None)
        island.log.append(LogEntry("power_line_removed", {"x": x, "y": y}))
        return
    close_accounts(island, f)
    stock.move_to_warehouse(f.stock, island.warehouse, ctx.turn, ctx.defs.goods)
    island.facilities.remove(f)
    island.log.append(LogEntry("facility_demolished", {"facility": f.kind, "x": x, "y": y}))


def close_accounts(island: IslandState, f: Facility) -> None:
    """独立採算の施設の預金を持ち主に返し、出資を消す。"""
    if not island.has_economy or not is_self_supporting(f):
        return
    entity = facility_entity(f.id)
    owner = owner_entity(f)
    ledger.pay(island, entity, owner, ledger.money(island, entity), "equity_return")
    for name, value in sorted(ledger.holdings(island, owner, ledger.EQUITY).items()):
        if name.endswith(f"/{entity}"):
            ledger.cancel(island, owner, ledger.EQUITY, entity, value, "equity_return")


class Construction(IslandStage):
    """工事中の施設を、手元の資材と雇えた人手の分だけ進める。"""

    name = "construction"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs = ctx.defs
        for f in sorted(island.facilities, key=lambda f: f.id):
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
                stock.take(f.stock, good, qty * f.progress // total - qty * before // total)
            if f.progress >= total:
                self._complete(island, f, ctx)

    @staticmethod
    def _complete(island: IslandState, f: Facility, ctx: StageContext) -> None:
        f.built = True
        f.workers = 0
        f.status = fac.STATUS_STARTING
        stock.move_to_warehouse(f.stock, island.warehouse, ctx.turn, ctx.defs.goods)
        island.log.append(LogEntry("construction_done", {"facility": f.kind, "x": f.x, "y": f.y}))
        if not island.has_economy:
            return
        capital = f.economy.get("capital", 0)
        init_facility(
            island,
            ctx.defs,
            ctx.config,
            f,
            dict(island.economy.prices),
            island.economy.average_wage,
        )
        f.economy["capital"] = capital
        if is_self_supporting(f):
            fund_facility(island, ctx.defs, ctx.config, f, Payer(ledger.GOV, CONSTRUCTION))


def register(registry: Registry) -> None:
    registry.add_order(OrderHandler("build", execute=build, check=check_build))
    registry.add_order(OrderHandler("demolish", execute=demolish, check=check_demolish))
    registry.add_stage(Construction())
