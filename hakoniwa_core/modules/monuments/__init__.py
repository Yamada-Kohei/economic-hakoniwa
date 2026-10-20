"""記念碑の段（フェーズ4-1。世界の段 monuments）: 国の歴史を書き足し、倒れる像を取り除く。

歴史（``island.ext["history"]``）の使い道は hakoniwa_core/monuments.py。この段は称号を決める
rankings の後に、そのターンのログから次を書き足します。

- 暴動・反乱・災害・戦争の始まりと終わりの最後のターン（unrest）と、最後に始まった戦争（war）
- 飢えで亡くなった人の数（dead.hunger。戦死者は戦闘の段が足す）
- 最初の仕訳（first_entry: 帳簿の碑に刻む。仕訳は 24 ターンで消えるので、最初に見たときに写す）
- 持っている称号（titles: 産業 → 得たターン。失えば消す。碑は建てた後で失っても残る）

民主主義の国には元首の像は建てられず、体制が民主主義に変わると建っている像（工事中も）は倒されます。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import monuments
from hakoniwa_core.economy import experience
from hakoniwa_core.registry import Registry, WorldStage
from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.turn.context import StageContext

LEADER_KIND = "monument_leader"


def _matches(kind: str, names: tuple[str, ...]) -> bool:
    return any(kind.startswith(n) if n.endswith("_") else kind == n for n in names)


class Monuments(WorldStage):
    name = "monuments"

    def collect(self, island: IslandState, ctx: StageContext) -> Any:
        return None

    def resolve(self, requests: dict[int, Any], ctx: StageContext) -> dict[int, Any]:
        assert ctx.before is not None
        update(ctx.before, ctx)
        return {}

    def apply(self, island: IslandState, result: Any, ctx: StageContext) -> None:
        return None


def update(world: WorldState, ctx: StageContext) -> None:
    held = world.ext.get(experience.RANKINGS, {}).get("titles", {})
    for island_id in world.island_ids():
        island = world.islands[island_id]
        h = monuments.history(island)
        h["turn"] = ctx.turn
        h["rule"] = dict(ctx.config.get("monuments"))
        for entry in island.log:
            if _matches(entry.kind, monuments.UNREST):
                h["unrest"] = ctx.turn
            if entry.kind in monuments.WAR_STARTED:
                h["war"] = {
                    "country": entry.args.get("country", ""),
                    "turn": entry.args.get("turn", ctx.turn),
                    "id": entry.args.get("war", 0),
                }
            if entry.kind == "population_hunger_deaths":
                monuments.add_dead(island, monuments.HUNGER_DEAD, int(entry.args.get("count", 0)))
        if "first_entry" not in h and island.journal:
            first = island.journal[0]
            h["first_entry"] = {"turn": ctx.turn, "kind": first.kind, "amount": first.amount}
        titles = {i: t for i, t in h.get("titles", {}).items() if held.get(i) == island_id}
        for industry, holder in held.items():
            if holder == island_id:
                titles.setdefault(industry, ctx.turn)
        h["titles"] = titles
        _topple_leader(island, ctx)


def _topple_leader(island: IslandState, ctx: StageContext) -> None:
    """民主主義の国の元首の像は倒される（体制が変わったとき）。"""
    if island.regime.get("politics", monuments.FREE_POLITICS) != monuments.FREE_POLITICS:
        return
    for f in [f for f in island.facilities if f.kind == LEADER_KIND]:
        island.facilities.remove(f)
        args = {"facility": f.kind, "x": f.x, "y": f.y}
        island.log.append(LogEntry("monument_toppled", args))


def register(registry: Registry) -> None:
    registry.add_stage(Monuments())
