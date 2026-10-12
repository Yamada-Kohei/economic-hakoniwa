"""状態と JSON（dict）の変換。バックアップや一括シミュレーションの比較に使う。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from hakoniwa_core.state.model import IslandState, LogEntry, OrderState, Tile, WorldState


def world_to_dict(world: WorldState) -> dict[str, Any]:
    return {
        "turn": world.turn,
        "seed": world.seed,
        "ext": world.ext,
        "islands": [island_to_dict(world.islands[i]) for i in world.island_ids()],
    }


def island_to_dict(island: IslandState) -> dict[str, Any]:
    return asdict(island)


def world_from_dict(data: dict[str, Any]) -> WorldState:
    islands = [island_from_dict(d) for d in data["islands"]]
    return WorldState(
        turn=data["turn"],
        seed=data["seed"],
        ext=dict(data.get("ext") or {}),
        islands={i.id: i for i in islands},
    )


def island_from_dict(d: dict[str, Any]) -> IslandState:
    return IslandState(
        id=d["id"],
        name=d["name"],
        wx=d["wx"],
        wy=d["wy"],
        size=d["size"],
        tiles=[Tile(**t) for t in d["tiles"]],
        orders=[OrderState(**o) for o in d.get("orders", [])],
        log=[LogEntry(**e) for e in d.get("log", [])],
        ext=dict(d.get("ext") or {}),
    )
