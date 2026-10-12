"""状態と JSON（dict）の変換。バックアップや一括シミュレーションの比較に使う。"""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from hakoniwa_core.state.model import (
    Bond,
    EconomyState,
    Facility,
    IslandState,
    LogEntry,
    OrderState,
    Policy,
    Tile,
    WorldState,
)

# このターンだけの値なので、写しに入れない
_TRANSIENT = ("stats", "scratch", "journal", "trades", "markets", "books")
_NESTED = ("tiles", "facilities", "orders", "log", "ext", "bonds", "economy", "policy")


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
    rest = {k: v for k, v in d.items() if k in known and k not in _NESTED}
    return IslandState(
        **rest,
        tiles=[build(Tile, t) for t in d["tiles"]],
        facilities=[facility_from_dict(f) for f in d.get("facilities", [])],
        orders=[build(OrderState, o) for o in d.get("orders", [])],
        log=[LogEntry(**e) for e in d.get("log", [])],
        bonds=[build(Bond, b) for b in d.get("bonds", [])],
        economy=build(EconomyState, d.get("economy") or {}),
        policy=build(Policy, d.get("policy") or {}),
        ext=dict(d.get("ext") or {}),
    )


def facility_from_dict(d: dict[str, Any]) -> Facility:
    f = build(Facility, d)
    f.stock = stock_from_json(f.stock)
    return f


def stock_from_json(value: Any) -> dict[str, list[list[int]]]:
    """在庫を組の形に。フェーズ1の写しの施設の在庫（財 → 量）は、作られたターン 0 の組にする。"""
    out: dict[str, list[list[int]]] = {}
    for good, v in (value or {}).items():
        if isinstance(v, int):
            if v > 0:
                out[good] = [[0, v]]
        else:
            batches = [[int(made), int(qty)] for made, qty in v if qty > 0]
            if batches:
                out[good] = batches
    return out


def build(cls: type, data: dict[str, Any]) -> Any:
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})
