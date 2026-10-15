"""称号の段（フェーズ3-3。世界の段 rankings）: 各産業の経験の累積が世界で1位の国に称号を付ける。

経験の累積は生産の段が足します（hakoniwa_core/economy/experience.py）。この段は記録の段の後に、
産業ごとの1位の島を ``world.ext["rankings"]["titles"]``（産業 → 島の番号）に書き、称号を得た島と
失った島のログに知らせます。島ごとの統計には、持っている称号の数（titles）と、産業ごとの世界の順位
（experience_rank.<産業>。1 が1位）を残します。統計の画面と、ほかの国から見た島の画面に出します。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.economy import experience
from hakoniwa_core.registry import Registry, WorldStage
from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.turn.context import StageContext


class Rankings(WorldStage):
    name = "rankings"

    def collect(self, island: IslandState, ctx: StageContext) -> Any:
        return None

    def resolve(self, requests: dict[int, Any], ctx: StageContext) -> dict[int, Any]:
        assert ctx.before is not None
        update(ctx.before, ctx)
        return {}

    def apply(self, island: IslandState, result: Any, ctx: StageContext) -> None:
        return None


def update(world: WorldState, ctx: StageContext) -> None:
    state = world.ext.setdefault(experience.RANKINGS, {})
    before: dict[str, int] = state.get("titles", {})
    titles = experience.leaders(world, ctx.defs)
    for industry in sorted(set(before) | set(titles)):
        old, new = before.get(industry), titles.get(industry)
        if old == new:
            continue
        if new is not None and new in world.islands:
            world.islands[new].log.append(LogEntry("title_gained", {"industry": industry}))
        if old is not None and old in world.islands:
            if new is not None and new in world.islands:
                args = {"industry": industry, "country": world.islands[new].name}
                world.islands[old].log.append(LogEntry("title_lost", args))
            else:
                world.islands[old].log.append(LogEntry("title_lost_any", {"industry": industry}))
    state["titles"] = titles
    state["turn"] = ctx.turn
    ranks = _ranks(world)
    for island_id in world.island_ids():
        island = world.islands[island_id]
        if not island.has_economy:
            continue
        island.stats["titles"] = float(sum(1 for holder in titles.values() if holder == island_id))
        for industry, rank in sorted(ranks.get(island_id, {}).items()):
            island.stats[f"experience_rank.{industry}"] = float(rank)


def _ranks(world: WorldState) -> dict[int, dict[str, int]]:
    """島ごと・産業ごとの、経験の累積の世界の順位（累積のある島だけ。同じなら番号の小さい島が上）。"""
    by_industry: dict[str, list[tuple[int, int]]] = {}
    for island_id in world.island_ids():
        rows = world.islands[island_id].ext.get(experience.KEY, {})
        for industry, (amount, _) in rows.items():
            if amount > 0:
                by_industry.setdefault(industry, []).append((-amount, island_id))
    out: dict[int, dict[str, int]] = {}
    for industry, rows in by_industry.items():
        for rank, (_, island_id) in enumerate(sorted(rows), start=1):
            out.setdefault(island_id, {})[industry] = rank
    return out


def register(registry: Registry) -> None:
    registry.add_stage(Rankings())
