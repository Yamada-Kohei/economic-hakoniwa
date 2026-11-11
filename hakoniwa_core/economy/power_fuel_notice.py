"""火力発電所が燃料の備えで動いている間に出す知らせ（UI-87）。"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import freight, is_seized, is_stopped, power_fuel
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.economy.prices import subsidized_floor
from hakoniwa_core.state import Facility, IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import round_money

NOTICES = "fuel_notices"  # f.economy: 再通知を待つ印と、いま続いている知らせ
LOW = "power_fuel_low"
PRICE = "power_fuel_unprofitable"
REASONS = {"supply": "fuel_short_supply", "price": "fuel_short_price", "funds": "fuel_short_funds"}


def stock_status(defs: Defs, config: GameConfig, f: Facility) -> list[dict[str, Any]]:
    """今の作り方の燃料の量・直近の使用量での月数・次の操業見込みでの目標。"""
    if not f.built or not power_fuel.is_thermal(defs, f):
        return []
    plan = f.economy.get("input_plan", f.economy.get("target", fac.FULL))
    inputs = fac.recipe_of(defs, f)["inputs"]
    flow = f.economy.get(power_fuel.FLOW, {})
    out = []
    for good in sorted(power_fuel.fuel_goods(defs, f)):
        expected = fac.scale(inputs[good], plan)
        have = stock.total(f.stock, good)
        used = flow.get("used", {}).get(good, 0)
        basis = used or expected
        out.append(
            {
                "good": good,
                "quantity": have,
                "expected": expected,
                "months": have / basis if basis > 0 else None,
                "target": round_money(expected * config.get("power.fuel_stock_turns")),
                "target_months": config.get("power.fuel_stock_turns"),
            }
        )
    return out


def _record(
    island: IslandState,
    f: Facility,
    state: dict[str, Any],
    kind: str,
    flag: str,
    **args: Any,
) -> None:
    """注意は続いている間に表示し、ターンログは回復するまで1度だけ残す。"""
    entry = {"kind": kind, "args": {"x": f.x, "y": f.y, "facility": f.kind, **args}}
    state["active"].append(entry)
    if not state.get(flag):
        island.log.append(LogEntry(kind, entry["args"]))
        state[flag] = True


def after_market(island: IslandState, ctx: StageContext) -> None:
    """生産と市場・国の倉庫からの受け取りが済んだ後、当ターンの減り方を確かめる。"""
    if not island.has_economy:
        return
    defs, config = ctx.defs, ctx.config
    power = config.get("power.good")
    rates = freight.facility_rates(island, defs, config)
    for f in island.facilities:
        if not power_fuel.is_thermal(defs, f):
            continue
        state = f.economy.setdefault(NOTICES, {})
        state["active"] = []
        if (
            not f.built
            or is_stopped(f)
            or is_seized(f)
            or fac.is_halted(f, ctx.turn)
            or fac.is_halted(f, ctx.turn + 1)
            or f.economy.get("input_plan", 0) <= 0
        ):
            continue
        rows = stock_status(defs, config, f)
        if not rows:
            continue
        flow = f.economy.get(power_fuel.FLOW, {})
        used = flow.get("used", {}) if flow.get("turn") == ctx.turn else {}
        acquired = flow.get("acquired", {}) if flow.get("turn") == ctx.turn else {}
        notice = config.get("power.fuel_notice_turns")
        if all(r["quantity"] >= r["expected"] * notice * 2 for r in rows):
            state["low_notified"] = False
        low = [
            r
            for r in rows
            if r["quantity"] < r["expected"] * notice
            and used.get(r["good"], 0) > acquired.get(r["good"], 0)
        ]
        if low:
            row = min(low, key=lambda r: r["quantity"] / used[r["good"]])
            good = row["good"]
            # 使う分が在庫で足りる月には short が空になる。買い足しの失敗は需要に混ぜず、
            # 市場が別に残した同じ分類の理由を文面にだけ使う。
            reason = f.economy.get("short", {}).get(
                good, f.economy.get("fuel_restock_short", {}).get(good, "supply")
            )
            _record(
                island,
                f,
                state,
                LOW,
                "low_notified",
                good=good,
                months=row["quantity"] / used[good],
                reason=REASONS.get(reason, REASONS["supply"]),
            )
        if planning.is_planned(island, defs):
            continue
        reference = f.economy.get("power_previous_price", island.economy.prices.get(power, 0))
        cap = island.policy.price_cap.get(power)
        if cap is not None:
            reference = min(reference, cap)
        wage = f.economy.get("wage", island.economy.average_wage)
        rate = rates.get(f.id, 0)
        current_floor = subsidized_floor(
            island,
            defs,
            config,
            f,
            power,
            island.economy.prices,
            wage,
            rate,
            ctx.turn,
            fuel_prices={},
        )
        if current_floor < reference * config.get("power.notice_reset"):
            state["price_notified"] = False
        burned = [r for r in rows if used.get(r["good"], 0) > 0]
        if f.status == "power_price" or not burned or current_floor <= reference:
            continue
        stored_floor = subsidized_floor(
            island, defs, config, f, power, island.economy.prices, wage, rate, ctx.turn
        )
        if stored_floor < current_floor and stored_floor <= reference:
            months = min(r["quantity"] / used[r["good"]] for r in burned)
            _record(island, f, state, PRICE, "price_notified", months=months)
