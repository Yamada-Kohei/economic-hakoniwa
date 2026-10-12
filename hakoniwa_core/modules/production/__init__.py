"""生産と電力の段。

稼働率 u = min(人手の充足率, 投入財の充足率, 電力の充足率)。産出量 = 基本の産出量 × u です
（インフラと教育の係数はフェーズ2から。式は output_rate で差し替えられます）。

電力はためておけないので、送電網ごとにその場で発電と使用を突き合わせます。施設や住宅のある
マスと送電線のマスが送電網で、上下左右につながったまとまりが1つの送電網です。発電が足りなければ
優先順位の低い所から停電させます。燃料の要らない発電所から先に動かします。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.allocation import by_priority
from hakoniwa_core.geo import components, distance, within
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import Facility, IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import from_milli, to_milli

HOUSEHOLDS = "households"  # 送電網の中の住民をまとめた使い手


@dataclass
class _Run:
    """このターンの1つの施設の動き。"""

    f: Facility
    recipe: Any
    rate: int  # 電力を除いた稼働率
    cause: str  # rate を下げている理由


def output_rate(island: IslandState, f: Facility, rate: int, ctx: StageContext) -> int:
    """産出の倍率（1000 分の 1）。フェーズ1は稼働率そのまま。

    フェーズ2でインフラと教育の係数を掛ける。
    """
    return rate


class Production(IslandStage):
    name = "production"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs, config = ctx.defs, ctx.config
        power = config.get("power.good")
        runs = [self._prepare(f, ctx, power) for f in island.facilities if self._operates(f, ctx)]
        served = self._solve_power(island, runs, ctx, power)
        produced: dict[str, int] = {}
        for run in runs:
            f, rate, cause = run.f, run.rate, run.cause
            if f.id in served and served[f.id] < rate:
                rate = served[f.id]
                if run.recipe["inputs"].get(power):
                    cause = "power"
                else:  # 発電所は使われる分だけ動くのがふつうなので、止まったときだけ理由にする
                    cause = "no_demand" if rate == 0 else ""

            for good, need in sorted(run.recipe["inputs"].items()):
                if good != power:
                    stock.stock_take(f.stock, good, fac.scale(need, rate))
            out_rate = ctx.formula("output_rate")(island, f, rate, ctx)
            for good, qty in sorted(run.recipe["outputs"].items()):
                amount = fac.scale(qty, out_rate)
                produced[good] = produced.get(good, 0) + amount
                if good != power:
                    stock.add(island.warehouse, good, amount, ctx.turn, defs.goods)
            self._update_status(island, f, rate, cause)
        for good, amount in sorted(produced.items()):
            island.stats[f"production.{good}"] = from_milli(amount)
        self._spread_pollution(island, ctx)

    @staticmethod
    def _operates(f: Facility, ctx: StageContext) -> bool:
        return f.built and not fac.is_housing(ctx.config, f)

    @staticmethod
    def _prepare(f: Facility, ctx: StageContext, power: str) -> _Run:
        recipe = fac.recipe_of(ctx.defs, f)
        if fac.is_halted(f, ctx.turn):
            return _Run(f, recipe, 0, "halted")
        rate, cause = fac.labor_ratio(ctx.defs, f), "workers"
        for good, need in sorted(recipe["inputs"].items()):
            if good == power:
                continue
            r = fac.ratio(f.stock.get(good, 0), need)
            if r < rate:
                rate, cause = r, f"input:{good}"
        return _Run(f, recipe, rate, cause if rate < fac.FULL else "")

    # --- 電力 ----------------------------------------------------------------

    def _solve_power(
        self, island: IslandState, runs: list[_Run], ctx: StageContext, power: str
    ) -> dict[int, int]:
        """送電網ごとに発電と使用を突き合わせ、電力にかかわる施設の稼働率（施設の番号ごと）を返す。

        住宅のマスには、住民が受け取れた電力の割合を ext["power"] に書く。
        """
        config = ctx.config
        grid_of = self._grids(island)
        household_use = config.get("power.household_use")
        household_priority = config.get("power.household_priority")
        housing = fac.housing_kind(config)

        plants: dict[int, list[tuple[_Run, int]]] = {}
        users: dict[int, list[tuple[Any, int, int]]] = {}
        for run in runs:
            grid = grid_of[(run.f.x, run.f.y)]
            out = run.recipe["outputs"].get(power, 0)
            use = run.recipe["inputs"].get(power, 0)
            if out:
                plants.setdefault(grid, []).append((run, out))
            elif use:
                priority = fac.priority_of(ctx.defs, config, run.f)
                users.setdefault(grid, []).append((run.f.id, fac.scale(use, run.rate), priority))
        homes: dict[int, list[tuple[int, int]]] = {}
        for f in island.facilities:
            if f.kind == housing:
                homes.setdefault(grid_of[(f.x, f.y)], []).append((f.x, f.y))
        run_rate = {run.f.id: run.rate for run in runs}
        rates: dict[int, int] = {}
        totals = {"capacity": 0, "demand": 0, "used": 0}
        for grid in sorted(set(plants) | set(users) | set(homes)):
            demand = list(users.get(grid, []))
            home_need = sum(island.tile(x, y).population for x, y in homes.get(grid, ()))
            home_need = to_milli(home_need * household_use)
            if home_need:
                demand.append((HOUSEHOLDS, home_need, household_priority))
            # 燃料の要らない発電所から先に、同じなら優先順位の高い順・番号の順に動かす
            supply = sorted(
                plants.get(grid, []),
                key=lambda p: (
                    bool(p[0].recipe["inputs"]),
                    -fac.priority_of(ctx.defs, config, p[0].f),
                    p[0].f.id,
                ),
            )
            capacity = sum(fac.scale(out, run.rate) for run, out in supply)
            served = by_priority(capacity, demand)
            used = sum(served.values())
            left = used
            for run, out in supply:
                give = min(fac.scale(out, run.rate), left)
                left -= give
                rates[run.f.id] = fac.ratio(give, out)
            for key, need, _ in demand:
                if key != HOUSEHOLDS:
                    rates[key] = fac.scale(run_rate[key], fac.ratio(served[key], need))
            home_rate = fac.ratio(served.get(HOUSEHOLDS, 0), home_need) if home_need else 0
            for x, y in homes.get(grid, ()):
                tile = island.tile(x, y)
                if tile.population and home_rate:
                    tile.ext["power"] = home_rate
                else:
                    tile.ext.pop("power", None)
            totals["capacity"] += capacity
            totals["demand"] += sum(need for _, need, _ in demand)
            totals["used"] += used
        for key, value in totals.items():
            island.stats[f"power.{key}"] = from_milli(value)
        return rates

    @staticmethod
    def _grids(island: IslandState) -> dict[tuple[int, int], int]:
        """送電網: 施設や住宅のあるマスと送電線のマスの、上下左右につながったまとまり。"""
        nodes = {(f.x, f.y) for f in island.facilities}
        nodes |= {(t.x, t.y) for t in island.tiles if t.power_line}
        grid_of = {}
        for i, group in enumerate(components(island, nodes)):
            for pos in group:
                grid_of[pos] = i
        return grid_of

    # --- 状態とログ ------------------------------------------------------------

    @staticmethod
    def _update_status(island: IslandState, f: Facility, rate: int, cause: str) -> None:
        before_rate, before_status = f.operating_rate, f.status
        f.operating_rate = rate
        f.status = cause if rate < fac.FULL else ""
        where = {"facility": f.kind, "x": f.x, "y": f.y}
        if rate == 0 and (before_rate > 0 or before_status == fac.STATUS_STARTING):
            kind, _, good = cause.partition(":")
            args = {**where, "good": good} if good else where
            island.log.append(LogEntry(f"facility_stopped_{kind}", args))
        elif rate > 0 and before_rate == 0:
            started = before_status == fac.STATUS_STARTING
            kind = "facility_started" if started else "facility_restarted"
            island.log.append(LogEntry(kind, where))

    @staticmethod
    def _spread_pollution(island: IslandState, ctx: StageContext) -> None:
        """汚染: 施設のマスで一番濃く、radius マス先に向かって薄くなる。"""
        totals: dict[tuple[int, int], int] = {}
        for f in island.facilities:
            pollution = ctx.defs.facilities[f.kind].get("pollution")
            if not pollution or not f.built or f.operating_rate <= 0:
                continue
            amount = fac.scale(to_milli(pollution["amount"]), f.operating_rate)
            radius = pollution["radius"]
            for pos in within(island, (f.x, f.y), radius):
                share = amount * (radius + 1 - distance(pos, (f.x, f.y))) // (radius + 1)
                totals[pos] = totals.get(pos, 0) + share
        for tile in island.tiles:
            tile.pollution = round(from_milli(totals.get((tile.x, tile.y), 0)))


def register(registry: Registry) -> None:
    registry.add_stage(Production())
    registry.add_formula("output_rate", output_rate)
