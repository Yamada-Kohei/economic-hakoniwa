"""島そのものに対する基本の命令（名前の変更など）。"""

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


def register(registry: Registry) -> None:
    registry.add_order(OrderHandler("rename_island", execute=rename_island, check=check_rename))
