"""艦隊と海の動き（フェーズ4-2 の③'。設計書 15-military の MI-18・MI-22・MI-23）。

艦隊は同じ種類の艦を束ねた部隊（army.yaml の arm: sea。小さな軍艦 corvette・フリゲート frigate・
潜水艦 submarine・輸送船団 transport）で、`world.ext["military"]["units"]` に陸の部隊と
同じ形で持ちます（army.py の説明を見てください）。置き場所は世界マップの区画 (x, y) です。

- 艦隊は軍港（naval_base）のある島の区画で編成します。母港の区画にいる間は「港につないでいる」
  ことになり、燃料は航海中の 1 割だけ使います（`military.sea.port_fuel`）。
- 船の道（MI-23）は、出発の区画から行き先の区画までを直線（ブレゼンハムの線）でたどる区画の並びで、
  命令を出すときに決めて艦隊に持たせます（`order["route"]`）。1 ターンに艦の速さ（army.yaml の
  speed。区画の数）だけ区画を進むので、どのターンにどの区画にいるかが決まります。斜めの 1 歩も
  1 区画と数えます（距離の測りは貿易の船と少し違う）。
- 輸送船団（MI-22）は、陸の部隊を載せて運びます。載せる重さの上限は船の数 × 1 隻の capacity。
  載せ降ろしは母港の区画の軍港のマスでだけで、外国の島への揚陸は 4-3 です。
- 燃料は国の倉庫の燃料を、航海中の 1 隻 1 ターンあたり army.yaml の fuel だけ使います。足りないと
  艦は動けません（MI-14）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.military import army
from hakoniwa_core.state import Facility, IslandState, WorldState
from hakoniwa_core.units import QUANTITY_SCALE, to_milli

SAIL = "sail"
FUEL = "fuel"
Cell = tuple[int, int]


def grid(world: WorldState, config: GameConfig) -> int:
    """世界マップの 1 辺の区画数（島が増えたら広がる。建国の画面と同じ決まり）。"""
    from hakoniwa_core.state.generate import world_grid_size

    islands = max(config.get("world.default_islands"), len(world.islands) + 1)
    return world_grid_size(islands, config)


def home(island: IslandState) -> Cell:
    """島の区画（艦隊の母港のある区画）。"""
    return (island.wx, island.wy)


def naval_bases(island: IslandState, defs: Defs) -> list[Facility]:
    return [f for f, _ in army.bases(island, defs, "naval_base")]


def line(start: Cell, goal: Cell) -> list[Cell]:
    """start から goal までの区画の並び（start を含まず goal を含む）。ブレゼンハムの線。"""
    (x, y), (gx, gy) = start, goal
    dx, dy = abs(gx - x), abs(gy - y)
    sx, sy = (1 if gx > x else -1), (1 if gy > y else -1)
    err = dx - dy
    out: list[Cell] = []
    while (x, y) != (gx, gy):
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
        out.append((x, y))
    return out


def docked(unit: dict[str, Any], island: IslandState) -> bool:
    """母港の区画にいるか（港につないでいる）。"""
    return (unit["x"], unit["y"]) == home(island)


def capacity(spec: dict[str, Any], unit: dict[str, Any]) -> int:
    """輸送船団が運べる重さ（トン）。"""
    return int(spec.get("capacity", 0) * army.size(unit))


def cargo_weight(world: WorldState, defs: Defs, fleet: dict[str, Any]) -> float:
    """載せている部隊の重さの合計（トン）。"""
    total = 0.0
    for uid in fleet.get("cargo", []):
        unit = army.find(world, uid)
        if unit is not None:
            total += defs.army.get(unit["kind"], {}).get("weight", 0)
    return total


def speed(spec: dict[str, Any]) -> int:
    """1 ターンに進める区画の数。"""
    return max(1, int(spec["speed"]))


def fuel_need(spec: dict[str, Any], unit: dict[str, Any], config: GameConfig, port: bool) -> int:
    """艦隊が 1 ターンに使う燃料（1000 分の 1 トン）。港につないでいる間は port_fuel 倍。"""
    per_turn = to_milli(spec.get("fuel", 0)) * army.size(unit)
    if port:
        return int(per_turn * config.get("military.sea.port_fuel"))
    return per_turn


def need_soldiers(spec: dict[str, Any], unit: dict[str, Any]) -> int:
    """部隊がそろったときの兵の数（艦隊は乗員 × 艦の数）。"""
    return spec["soldiers"] * army.size(unit)


def need_equipment(spec: dict[str, Any], unit: dict[str, Any]) -> dict[str, int]:
    """部隊がそろったときの装備（財 → 数。艦隊は艦の数を掛ける）。"""
    return {
        good: qty // QUANTITY_SCALE * army.size(unit) for good, qty in spec["equipment"].items()
    }
