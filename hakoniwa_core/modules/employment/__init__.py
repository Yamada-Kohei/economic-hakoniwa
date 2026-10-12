"""通勤と雇用: 施設に働き手を割り当てる段。

各施設は通勤圏（既定3マス）に住む人を雇います。優先順位の高い施設から、近いマスの人を先に
割り当てます。同じ優先順位なら番号の小さい施設が先です。雇われなかった人は失業し、
村の住民は自給の農業に戻ります（人口の段で扱う）。
"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core.geo import within
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import IslandState
from hakoniwa_core.turn.context import StageContext


class Employment(IslandStage):
    name = "employment"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs, config = ctx.defs, ctx.config
        free = {(t.x, t.y): fac.labor_force(defs, t) for t in island.tiles if t.population > 0}
        employed: dict[tuple[int, int], int] = {}
        wanted = []
        for f in island.facilities:
            f.workers = 0
            demand = fac.labor_demand(defs, f, ctx.turn)
            if demand > 0:
                wanted.append((-fac.priority_of(defs, config, f), f.id, f, demand))
        wanted.sort(key=lambda w: (w[0], w[1]))
        for _, _, f, demand in wanted:
            radius = fac.commute_radius(defs, config, f.kind)
            for pos in within(island, (f.x, f.y), radius):
                available = free.get(pos, 0)
                if available <= 0:
                    continue
                hired = min(available, demand - f.workers)
                free[pos] = available - hired
                employed[pos] = employed.get(pos, 0) + hired
                f.workers += hired
                if f.workers >= demand:
                    break
        for tile in island.tiles:
            count = employed.get((tile.x, tile.y), 0)
            if count:
                tile.ext["employed"] = count
            else:
                tile.ext.pop("employed", None)


def register(registry: Registry) -> None:
    registry.add_stage(Employment())
