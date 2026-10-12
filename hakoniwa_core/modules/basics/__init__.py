"""名前の変更（島と施設）。"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.defs import Defs
from hakoniwa_core.registry import OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext


def check_rename(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if payload["name"].strip() == island.name:
        raise OrderFailed("reason_same_name")


def rename_island(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    old, new = island.name, payload["name"].strip()
    island.name = new
    island.log.append(LogEntry("island_renamed", {"old": old, "new": new}))


def check_rename_facility(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    f = island.facility_at(payload["x"], payload["y"])
    if f is None or defs.facilities[f.kind].get("buildable") is False:
        raise OrderFailed("reason_no_facility")
    if payload["name"].strip() == f.name:
        raise OrderFailed("reason_same_name")


def rename_facility(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """施設の名前を変える。空の名前にすると、体制に合わせた自動の名前に戻る。"""
    f = island.facility_at(payload["x"], payload["y"])
    assert f is not None
    f.name = payload["name"].strip()
    island.log.append(
        LogEntry("facility_renamed", {"facility": f.kind, "x": f.x, "y": f.y, "new": f.name})
    )


def register(registry: Registry) -> None:
    registry.add_order(OrderHandler("rename_island", execute=rename_island, check=check_rename))
    registry.add_order(
        OrderHandler("rename_facility", execute=rename_facility, check=check_rename_facility)
    )
