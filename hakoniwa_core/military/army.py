"""部隊と基地と動き（フェーズ4-2 の③。設計書 15-military の MI-18〜21・MI-54）。

部隊は世界の状態 `world.ext["military"]["units"]` に持ちます（書くのは全島の段 `operations`
だけ。MI-54）。
部隊 1 つの形:

- id / owner（持ち主の島の番号）/ kind（army.yaml の名前）/ x, y（島のマス）
- at: 部隊のいる島の番号（持ち主の島にいる間は書かない。外国の島に揚陸した部隊だけ。`where`）
- soldiers: 部隊に入った兵の数（島の兵の合計 `island.ext["military"]` のうち、部隊に割り当てた分）
- equipment: 持っている装備（財 → 数。goods.yaml の単位。組・両）
- status: forming（編成中。兵と装備がそろうまで）/ ready（そろった）
- order: 続く命令。None / {"type": "move", "x", "y"} / {"type": "dig", "left"}

海の艦隊（4-2 の③'。同じ種類の艦を束ねた部隊。army.yaml の arm: sea）も同じ形で、次の点が違います
（詳しくは sea.py）: arm = "sea"、x, y は島のマスでなく世界マップの区画、ships = 艦の数（soldiers と
equipment は艦の数ぶんそろえる）、order は {"type": "sail", "x", "y"}、cargo = 載せた部隊の番号。
陸の部隊を艦隊に載せている間は、その部隊に aboard = 艦隊の番号を置く。

外国の島にいる部隊（4-3 の①）は、食べ物・弾薬・燃料を自分の `stores`（財 → 1000 分の 1 単位）
で持ち、
船で運んだ補給と占領したマスの取り立てで補います（MI-25・MI-64）。戦っている間は
role（attack・defend）、
entered（そのマスに入ったターン）、from（入る前のマス）、fought（続けて戦ったターン数）、engaged（戦い始めの
兵の数）を持ちます（combat.py）。

兵の賃金・食べ物は島の兵の合計に対して払うので、部隊に入っても二重には払いません。
島の陣地（塹壕）はマスの `tile.ext["trench"]`（1）に持ちます。
"""

from __future__ import annotations

import heapq
from typing import Any

from hakoniwa_core import infra
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import Facility, IslandState, Tile, WorldState
from hakoniwa_core.units import QUANTITY_SCALE, to_milli

KEY = "military"
FORMING = "forming"
READY = "ready"
TRENCH = "trench"


def world_state(world: WorldState) -> dict[str, Any]:
    """世界の軍の状態（部隊と、番号の振り出し）。"""
    state = world.ext.setdefault(KEY, {})
    state.setdefault("seq", 0)
    state.setdefault("units", [])
    return state


def units_of(world: WorldState, island_id: int) -> list[dict[str, Any]]:
    """島の持ち主の部隊と艦隊（番号の順）。"""
    return [u for u in world_state(world)["units"] if u["owner"] == island_id]


def land_units_of(world: WorldState, island_id: int) -> list[dict[str, Any]]:
    return [u for u in units_of(world, island_id) if not is_sea(u)]


def fleets_of(world: WorldState, island_id: int) -> list[dict[str, Any]]:
    return [u for u in units_of(world, island_id) if is_sea(u)]


def where(unit: dict[str, Any]) -> int:
    """部隊のいる島の番号（外国の島に揚陸していなければ持ち主の島）。"""
    return unit.get("at", unit["owner"])


def abroad(unit: dict[str, Any]) -> bool:
    """外国の島にいる部隊か（船に載っている間は、まだ持ち主の島のもの）。"""
    return where(unit) != unit["owner"] and "aboard" not in unit


def on_land(world: WorldState, island_id: int) -> list[dict[str, Any]]:
    """島の上にいる陸の部隊（誰の部隊でも。船に載っている間は含まない。番号の順）。"""
    return [
        u
        for u in world_state(world)["units"]
        if not is_sea(u) and where(u) == island_id and "aboard" not in u
    ]


def find(world: WorldState, unit_id: int) -> dict[str, Any] | None:
    return next((u for u in world_state(world)["units"] if u["id"] == unit_id), None)


def is_sea(unit: dict[str, Any]) -> bool:
    return unit.get("arm") == "sea"


def size(unit: dict[str, Any]) -> int:
    """部隊の大きさ（艦隊は艦の数、陸の部隊は 1）。兵と装備の数はこれを掛けた分がそろう数。"""
    return unit.get("ships", 1)


def new_unit(
    world: WorldState, island_id: int, kind: str, x: int, y: int, ships: int = 0
) -> dict[str, Any]:
    """部隊を作る。ships を決めると艦隊（x, y は世界マップの区画）。"""
    state = world_state(world)
    state["seq"] += 1
    unit = {
        "id": state["seq"],
        "owner": island_id,
        "kind": kind,
        "x": x,
        "y": y,
        "soldiers": 0,
        "equipment": {},
        "status": FORMING,
        "order": None,
    }
    if ships:
        unit.update(arm="sea", ships=ships, cargo=[])
    state["units"].append(unit)
    return unit


def remove(world: WorldState, unit: dict[str, Any]) -> None:
    world_state(world)["units"].remove(unit)


def assigned(world: WorldState, island_id: int) -> int:
    """部隊に入っている兵の数。"""
    return sum(u["soldiers"] for u in units_of(world, island_id))


# --- 基地 -------------------------------------------------------------------------------


def bases(island: IslandState, defs: Defs, key: str) -> list[tuple[Facility, Any]]:
    """建ち終わった基地の施設（facilities.yaml の military: <key> を持つもの）と、その値。"""
    out = []
    for f in island.facilities:
        value = defs.facilities[f.kind].get("military", {}).get(key)
        if f.built and value:
            out.append((f, value))
    return out


def barracks_at(island: IslandState, defs: Defs, x: int, y: int) -> int:
    """マス (x, y) の兵舎の兵の定員（なければ 0）。"""
    return sum(cap for f, cap in bases(island, defs, "barracks") if (f.x, f.y) == (x, y))


def housed(world: WorldState, defs: Defs, island_id: int, x: int, y: int) -> int:
    """マス (x, y) の兵舎に置く部隊の兵の定員の合計（編成中の部隊も、そろったときの数で数える。
    船に載せた部隊は兵舎にいない）。"""
    return sum(
        defs.army.get(u["kind"], {}).get("soldiers", u["soldiers"])
        for u in land_units_of(world, island_id)
        if (u["x"], u["y"]) == (x, y) and "aboard" not in u and where(u) == island_id
    )


def has_trench(tile: Tile) -> bool:
    return bool(tile.ext.get(TRENCH))


# --- 動き (MI-21) -----------------------------------------------------------------------


def tile_at(island: IslandState, x: int, y: int) -> Tile | None:
    return island.tile(x, y) if island.in_bounds(x, y) else None


def enter_cost(tile: Tile, defs: Defs, config: GameConfig) -> int | None:
    """マスに入るのに使う動きの量（1000 分の 1 マス）。海のマスは入れない（None）。
    山と森は 2 マス分、道路の整備度が満ちたマスは 1.5 倍進める（コストが 3 分の 2）。"""
    if not defs.terrain[tile.terrain]["land"]:
        return None
    base = to_milli(config.get("military.move.terrain_cost").get(tile.terrain, 1))
    bonus = to_milli(config.get("military.move.road_bonus"))
    road = max((infra.level(tile, kind) for kind in defs.infra_kinds("transport")), default=0)
    return max(1, base * QUANTITY_SCALE // (QUANTITY_SCALE + bonus * road // infra.FULL))


def path(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    start: tuple[int, int],
    goal: tuple[int, int],
) -> list[tuple[int, int]] | None:
    """start から goal までの一番安い道（start を含まず goal を含む）。たどり着けなければ None。
    同じ費用なら、座標の小さいマスを先にたどる（結果が島の順番に左右されない）。"""
    if start == goal:
        return []
    best = {start: 0}
    came: dict[tuple[int, int], tuple[int, int]] = {}
    queue = [(0, start)]
    while queue:
        cost, here = heapq.heappop(queue)
        if here == goal:
            break
        if cost > best.get(here, cost):
            continue
        x, y = here
        for nxt in ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)):
            tile = tile_at(island, *nxt)
            step = enter_cost(tile, defs, config) if tile is not None else None
            if step is None:
                continue
            total = cost + step
            if total < best.get(nxt, total + 1):
                best[nxt] = total
                came[nxt] = here
                heapq.heappush(queue, (total, nxt))
    if goal not in came:
        return None
    out = [goal]
    while came[out[-1]] != start:
        out.append(came[out[-1]])
    return out[::-1]
