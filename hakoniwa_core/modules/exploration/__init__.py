"""探鉱の命令と段（設計書 15-military MI-68。W-234）。中身の決まりは hakoniwa_core/exploration.py。

- 命令 explore: 島がまだ持っていない鉱業資源を 1 つ選び、それを掘る施設（鉱山・採石場・油井）の建つ
  地形のマスに探鉱隊を置く（工事は建設の命令と同じに進む。国営だけ・島に 1 つ）。探鉱隊は公務の施設
  で、賃金と仕入れ（機械と燃料）は予算の公共事業費から払う（学校と同じ形。施設の定義の budget）。
- 段 exploration（市場の段の後。その月の賃金と仕入れを払ってから取り除くため）: 建った探鉱隊の
  進みに、このターンの稼働率を足す。全力で explore.turns ターン分たまると、島の地質に鉱床があれば
  鉱業資源に足し、なければ「見つからなかった」と覚える。どちらでも探鉱隊は取り壊し、マスは空く
  （送電網は残す）。探鉱中にその資源の鉱脈が出たり、鉱脈で島の鉱業資源が上限に届いたりしたら、
  見つけずに終わる。
- やめるときは取り壊しの命令（払ったお金は戻らず、進みも消える）。災害や攻撃で探鉱隊がなくなっても、
  壊れて直す工事になっても、進みは消える（進みは施設が持つ）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import exploration, infra
from hakoniwa_core import facilities as fac
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.modules import construction
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import Facility, IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext


def check_explore(
    island: IslandState, payload: dict[str, Any], defs: Defs, config: GameConfig
) -> None:
    good, x, y = payload["resource"], payload["x"], payload["y"]
    problem = exploration.problem(island, defs, config, good)
    if problem is None:
        problem = fac.placement_problem(defs, island, exploration.facility_kind(config), x, y)
    if problem is None and island.tile(x, y).terrain not in exploration.miner_terrains(defs, good):
        problem = ("reason_explore_terrain", {"good": good, "terrain": island.tile(x, y).terrain})
    if problem is not None:
        raise OrderFailed(problem[0], **problem[1])


def explore(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    check_explore(island, payload, ctx.defs, ctx.config)
    good, x, y = payload["resource"], payload["x"], payload["y"]
    kind = exploration.facility_kind(ctx.config)
    construction.build(island, {"facility": kind, "x": x, "y": y}, ctx)
    f = island.facility_at(x, y)
    assert f is not None and f.kind == kind
    f.ext[exploration.EXPLORE] = {"good": good, "progress": 0}
    island.log.append(LogEntry("explore_started", {"good": good, "x": x, "y": y}))


class Exploration(IslandStage):
    """探鉱隊の進みと、探鉱の終わり。"""

    name = "exploration"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        kind = exploration.facility_kind(ctx.config)
        for f in [f for f in island.facilities if f.kind == kind]:
            spec = f.ext.get(exploration.EXPLORE)
            if spec is None:  # 命令によらず置かれた探鉱隊（ないはず）は、探す物がないので除く
                construction.remove(island, f, ctx)
                continue
            good = spec["good"]
            if good in island.resources or exploration.is_full(island, ctx.config):
                # 探している資源の鉱脈が出たか、鉱脈で島の鉱業資源が上限に届いた
                self._finish(island, f, ctx, "explore_stopped", {"good": good})
                continue
            if f.ext.get("repair"):  # 攻撃や暴動で壊れた: 調べた記録も失い、直してから始め直す
                spec["progress"] = 0
            if not f.built or f.ext.get("occupied"):
                continue
            spec["progress"] += f.operating_rate
            if spec["progress"] < ctx.config.get("explore.turns") * exploration.FULL:
                continue
            found = exploration.has_deposit(island, ctx.config, ctx.seed, good)
            island.ext.setdefault(exploration.EXPLORED, {})[good] = (
                exploration.FOUND if found else exploration.NONE
            )
            if found:
                island.resources = sorted([*island.resources, good])
                self._finish(island, f, ctx, "explore_found", {"good": good})
            else:
                self._finish(island, f, ctx, "explore_none", {"good": good})

    @staticmethod
    def _finish(
        island: IslandState, f: Facility, ctx: StageContext, kind: str, args: dict[str, Any]
    ) -> None:
        construction.remove(island, f, ctx)
        # 施設のマスは送電網の一部なので、送電網を残して、まわりの施設の送電網が切れないようにする
        # （閉鎖や倒産で施設を除くときと同じ。facilities.vacate）
        infra.set_level(island.tile(f.x, f.y), infra.POWER, infra.FULL)
        island.log.append(LogEntry(kind, args))


def register(registry: Registry) -> None:
    registry.add_order(OrderHandler("explore", execute=explore, check_settings=check_explore))
    registry.add_stage(Exploration())
