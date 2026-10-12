"""記録の段: 人口・雇用・幸福度・在庫・行政力などを、このターンの統計に残す。

生産量と電力は生産の段が、食料は配分の段が書きます。統計の名前は「人口 population」
「在庫 stock.<財>」「生産 production.<財>」のように付け、サーバーが統計の表に保存します。
"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.modules.population import subsistence_persons
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import IslandState
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import from_milli


class Records(IslandStage):
    name = "records"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs, stats = ctx.defs, island.stats
        population = labor = employed = farming = 0
        mood = 0
        for tile in island.tiles:
            if tile.population <= 0:
                continue
            population += tile.population
            labor += fac.labor_force(defs, tile)
            employed += tile.ext.get("employed", 0)
            farming += min(
                fac.labor_force(defs, tile) - tile.ext.get("employed", 0),
                int(subsistence_persons(defs, tile) * fac.labor_share(defs)),
            )
            mood += tile.ext.get("happiness", 0) * tile.population
        stats["population"] = population
        stats["labor"] = labor
        stats["employed"] = employed
        stats["subsistence"] = farming
        stats["unemployment"] = (labor - employed - farming) / labor if labor else 0.0
        stats["happiness"] = mood / population if population else 0.0
        food = island.scratch.get("food")
        if food is not None:
            stats["food.ratio"] = food["ratio"] / fac.FULL
        for good, qty in stock.totals(island.warehouse).items():
            stats[f"stock.{good}"] = from_milli(qty)
        stats["facilities"] = sum(1 for f in island.facilities if f.built)
        stats["construction"] = sum(1 for f in island.facilities if not f.built)
        # 次のターンの初めに使える行政力（官庁の人手はこのターンに雇えた人数で決まる）
        stats["admin_power"] = ctx.formula("admin_power")(island, ctx)


def register(registry: Registry) -> None:
    registry.add_stage(Records())
