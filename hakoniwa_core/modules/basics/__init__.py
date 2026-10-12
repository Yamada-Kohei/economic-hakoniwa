"""名前の変更（島・地名・施設）。"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.defs import Defs
from hakoniwa_core.naming import place_index
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


def check_rename_place(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not island.in_bounds(payload["x"], payload["y"]):
        raise OrderFailed("reason_out_of_bounds")
    if not payload["name"].strip():
        raise OrderFailed("reason_invalid_payload")
    if not island.ext.get("places"):
        raise OrderFailed("reason_no_places")


def rename_place(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """マスを含む区画の地名を変える。その区画の施設の自動の名前も変わる。"""
    places = island.ext["places"]
    i = place_index(island, payload["x"], payload["y"], ctx.config) % len(places)
    old, new = places[i], payload["name"].strip()
    if old == new:
        raise OrderFailed("reason_same_name")
    places[i] = new
    island.log.append(
        LogEntry("place_renamed", {"x": payload["x"], "y": payload["y"], "old": old, "new": new})
    )


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
    registry.add_order(OrderHandler("rename_place", execute=rename_place, check=check_rename_place))
    registry.add_order(
        OrderHandler("rename_facility", execute=rename_facility, check=check_rename_facility)
    )
