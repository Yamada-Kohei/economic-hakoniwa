"""マスのインフラ（道路・鉄道・上下水道・送電網）の整備度の読み書き（フェーズ2-3）。

マスの infra に、種類ごとの [整備度, 維持で戻す目標] を 1000 分の 1 で持ちます（例: 道路 60% は
{"road": [600, 600]}）。整備度は毎ターン傷みで下がり、公共事業の維持の工事で目標まで戻ります。
工事で整備度を上げると、目標も上がります。種類の定義は infrastructure.yaml です。

フェーズ1・2 の送電線 {"power": 1} は、送電網 100% と読みます。

工事の列（並んだ順に工事する）は、島の economy.infra["queue"] に [種類, x, y] で持ちます。
工事と維持の進め方は hakoniwa_core/modules/infrastructure です。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from hakoniwa_core.defs import Defs
from hakoniwa_core.state import IslandState, Tile

FULL = 1000  # 整備度 100%
POWER = "power"  # フェーズ1・2 の送電線の名前（送電網）


def _entry(tile: Tile, kind: str) -> list[int] | None:
    value = tile.infra.get(kind)
    if value is None:
        return None
    if isinstance(value, int):  # フェーズ1・2 の送電線
        return [FULL, FULL] if value > 0 else None
    return value


def level(tile: Tile, kind: str) -> int:
    """マスの kind の整備度（1000 分の 1）。"""
    entry = _entry(tile, kind)
    return entry[0] if entry else 0


def keep(tile: Tile, kind: str) -> int:
    """維持の工事で戻す目標の整備度（1000 分の 1）。"""
    entry = _entry(tile, kind)
    return entry[1] if entry else 0


def set_level(tile: Tile, kind: str, value: int, target: int | None = None) -> None:
    """整備度を value に、目標を target（書かなければ今の目標と value の高い方）にする。"""
    value = max(0, min(FULL, value))
    target = max(value, keep(tile, kind)) if target is None else max(0, min(FULL, target))
    if value <= 0 and target <= 0:
        tile.infra.pop(kind, None)
    else:
        tile.infra[kind] = [value, target]


def clear(tile: Tile) -> None:
    """マスのインフラをすべて外す（施設のないマスの取り壊し）。"""
    tile.infra.clear()


def has_any(tile: Tile) -> bool:
    return any(level(tile, kind) > 0 or keep(tile, kind) > 0 for kind in tile.infra)


def transport(defs: Defs, tile: Tile) -> int:
    """マスの交通の整備度: 道路と鉄道（role: transport）の高い方。"""
    return max((level(tile, k) for k in defs.infra_kinds("transport")), default=0)


def water(defs: Defs, tile: Tile) -> int:
    """マスの上下水道の整備度（role: water の高い方）。"""
    return max((level(tile, k) for k in defs.infra_kinds("water")), default=0)


def carries_power(defs: Defs, tile: Tile) -> bool:
    """送電網の整備度が定義の grid 以上で、電力を通すマスか。

    施設と町のマスは整備度によらず通します（生産の段の _grids）。
    """
    for kind in defs.infra_kinds("power"):
        threshold = round(defs.infrastructure[kind].get("grid", 0) * FULL)
        if level(tile, kind) > 0 and level(tile, kind) >= threshold:
            return True
    return False


def power_kind(defs: Defs) -> str:
    """送電網の種類の名前（命令 build_power_line で引く種類）。"""
    kinds = defs.infra_kinds("power")
    return kinds[0] if kinds else POWER


def path_weight(defs: Defs, tile: Tile) -> int:
    """マスを越える道のりの重さ（10 で 1 マス分）: 1 − 整備度 ×（1 − 定義の path）を、交通の
    種類ごとに出して一番軽いもの。"""
    best = 10
    for kind in defs.infra_kinds("transport"):
        value = level(tile, kind)
        if value <= 0:
            continue
        cut = value * (10 - round(defs.infrastructure[kind].get("path", 1) * 10))
        best = min(best, 10 - (cut + FULL // 2) // FULL)
    return best


def queue(island: IslandState) -> list[list[Any]]:
    """インフラの工事の列（[種類, x, y] を並んだ順に）。"""
    return island.economy.infra.setdefault("queue", [])


def queued(island: IslandState, kind: str, x: int, y: int) -> bool:
    return [kind, x, y] in island.economy.infra.get("queue", [])


def queued_at(island: IslandState, x: int, y: int) -> list[str]:
    """マスに並んでいる工事の種類。"""
    return [k for k, qx, qy in island.economy.infra.get("queue", []) if (qx, qy) == (x, y)]


def enqueue(island: IslandState, kind: str, tiles: Iterable[tuple[int, int]]) -> int:
    """工事の列に並べる（もう 100% のマスと並んでいるマスは並べない）。並べたマスの数を返す。"""
    items = queue(island)
    count = 0
    for x, y in tiles:
        if level(island.tile(x, y), kind) >= FULL or [kind, x, y] in items:
            continue
        items.append([kind, x, y])
        count += 1
    return count


def unqueue(island: IslandState, x: int, y: int) -> None:
    """マスの工事を列から外す。"""
    items = queue(island)
    items[:] = [item for item in items if (item[1], item[2]) != (x, y)]


def normalize(tile: Tile) -> None:
    """フェーズ1・2 の送電線 {"power": 1} を [整備度, 目標] の形に直す（移し替え）。"""
    for kind, value in list(tile.infra.items()):
        if isinstance(value, int):
            if value > 0:
                tile.infra[kind] = [FULL, FULL]
            else:
                tile.infra.pop(kind)
