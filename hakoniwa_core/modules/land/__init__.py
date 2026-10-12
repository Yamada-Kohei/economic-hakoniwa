"""国土の命令: 送電線を引く、開発を止めるマスの指定と解除。"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.defs import Defs
from hakoniwa_core.facilities import is_land
from hakoniwa_core.registry import OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import ZONE_NO_DEVELOPMENT, ZONE_NONE, IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext


def _check_land_tiles(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    for x, y in payload["tiles"]:
        if not island.in_bounds(x, y):
            raise OrderFailed("reason_out_of_bounds")
        if not is_land(defs, island.tile(x, y).terrain):
            raise OrderFailed("reason_not_land", x=x, y=y)


def check_power_line(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_land_tiles(island, payload, defs)
    if all(island.tile(x, y).power_line for x, y in payload["tiles"]):
        raise OrderFailed("reason_already_done")


def build_power_line(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """送電線を引く。施設や住宅のマスは、それだけで送電網の一部になる（線は要らない）。"""
    count = 0
    for x, y in payload["tiles"]:
        tile = island.tile(x, y)
        if not tile.power_line:
            tile.infra["power"] = 1
            count += 1
    island.log.append(LogEntry("power_line_built", {"count": count}))


def set_no_development(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    zone = ZONE_NO_DEVELOPMENT if payload["value"] else ZONE_NONE
    for x, y in payload["tiles"]:
        island.tile(x, y).zone = zone
    kind = "no_development_set" if payload["value"] else "no_development_cleared"
    island.log.append(LogEntry(kind, {"count": len(payload["tiles"])}))


def register(registry: Registry) -> None:
    registry.add_order(
        OrderHandler("build_power_line", execute=build_power_line, check=check_power_line)
    )
    registry.add_order(
        OrderHandler("set_no_development", execute=set_no_development, check=_check_land_tiles)
    )
