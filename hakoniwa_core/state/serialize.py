"""状態と JSON（dict）の変換。バックアップや一括シミュレーションの比較に使う。"""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from hakoniwa_core.state.model import (
    Facility,
    IslandState,
    LogEntry,
    OrderState,
    Tile,
    WorldState,
)

# このターンだけの値なので、写しに入れない
_TRANSIENT = ("stats", "scratch")


def world_to_dict(world: WorldState) -> dict[str, Any]:
    return {
        "turn": world.turn,
        "seed": world.seed,
        "ext": world.ext,
        "islands": [island_to_dict(world.islands[i]) for i in world.island_ids()],
    }


def island_to_dict(island: IslandState) -> dict[str, Any]:
    data = asdict(island)
    for key in _TRANSIENT:
        data.pop(key, None)
    return data


def world_from_dict(data: dict[str, Any]) -> WorldState:
    islands = [island_from_dict(d) for d in data["islands"]]
    return WorldState(
        turn=data["turn"],
        seed=data["seed"],
        ext=dict(data.get("ext") or {}),
        islands={i.id: i for i in islands},
    )


def island_from_dict(d: dict[str, Any]) -> IslandState:
    """前のフェーズの写し（新しい項目がないもの）も読めるようにする。"""
    known = {f.name for f in fields(IslandState)} - set(_TRANSIENT)
    rest = {
        k: v
        for k, v in d.items()
        if k in known and k not in ("tiles", "facilities", "orders", "log", "ext")
    }
    return IslandState(
        **rest,
        tiles=[_build(Tile, t) for t in d["tiles"]],
        facilities=[_build(Facility, f) for f in d.get("facilities", [])],
        orders=[_build(OrderState, o) for o in d.get("orders", [])],
        log=[LogEntry(**e) for e in d.get("log", [])],
        ext=dict(d.get("ext") or {}),
    )


def _build(cls: type, data: dict[str, Any]) -> Any:
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})
