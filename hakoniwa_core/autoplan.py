"""標準の計画: 島の様子を見て、自動で命令を並べる簡単な方針。

一括シミュレーション（関門のテスト）と、開発用のサンプルの島で、プレイヤーの代わりに使います。
ゲームのルールではないので、ここの数値は game.yaml ではなくこのファイルに置いています。

方針（毎ターン、工事中の施設が少なければ、上から見て最初に足りないものを1つ建てる）:
1. 食料の生産が食べる量に届かなければ、農場か漁港
2. 発電の余裕が少なければ、発電所（石炭があれば火力、なければ水力）
3. 投入財や工事の資材の作る量が使う量に届かなければ、それを作る施設
4. 生産の連鎖（鉄鉱石 → 鋼鉄 → 機械 → 新しい工場）を太くする: 製鉄所と機械工場を増やす
建てた施設は、陸地を通る一番短い送電線で送電網につなぎます。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.geo import components, distance, shortest_path, within
from hakoniwa_core.state import ORDER_PENDING, ZONE_NONE, IslandState, OrderState
from hakoniwa_core.units import to_milli

MAX_SITES = 2  # 同時に進める工事の数
POWER_MARGIN = 1.25  # 発電の能力を、使う量のこの倍に保つ
FOOD_MARGIN = 1.1  # 食料の生産を、食べる量のこの倍に保つ
MAX_MISSING_WORKERS = 100  # 足りない働き手がこの人数より多い間は、連鎖を太くしない
# 連鎖を太くするときに、この順で数をそろえていく施設
GROWTH = ("steel_mill", "machinery_plant", "building_materials_plant", "food_factory")


def plan_turn(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> list[tuple[str, dict]]:
    """ターン turn の処理の前に足す命令（種類, 中身）の並び。"""
    if any(o.status == ORDER_PENDING for o in island.orders):
        return []
    sites = [f for f in island.facilities if not f.built]
    if len(sites) >= MAX_SITES:
        return []
    for kind, recipe in _candidates(island, defs, config, turn):
        if not _materials_ok(island, defs, kind):
            continue
        pos = _site_for(island, defs, config, kind, recipe)
        if pos is not None:
            break
    else:
        return []
    payload: dict[str, Any] = {"facility": kind, "x": pos[0], "y": pos[1]}
    if fac.default_recipe(defs, kind) is None:
        payload["recipe"] = recipe
    orders: list[tuple[str, dict]] = [("build", payload)]
    line = _power_line(island, defs, pos)
    if line:
        orders.append(("build_power_line", {"tiles": [list(p) for p in line[:64]]}))
    return orders


def queue_orders(island: IslandState, orders: list[tuple[str, dict]]) -> None:
    """命令を島の命令の列の後ろに足す。"""
    next_id = max((o.id for o in island.orders), default=0) + 1
    next_seq = max((o.seq for o in island.orders), default=0) + 1
    for i, (kind, payload) in enumerate(orders):
        order = OrderState(id=next_id + i, seq=next_seq + i, kind=kind, payload=payload)
        island.orders.append(order)


# --- 何を建てるか ---------------------------------------------------------------


def _flows(island: IslandState, defs: Defs, config: GameConfig) -> tuple[dict, dict]:
    """全力で動いたときの、財ごとの作る量と使う量（工事中の施設も含める）。"""
    supply: dict[str, int] = {}
    demand: dict[str, int] = {}
    for f in island.facilities:
        if fac.is_housing(config, f):
            continue
        recipe = fac.recipe_of(defs, f)
        for good, qty in recipe["outputs"].items():
            supply[good] = supply.get(good, 0) + qty
        for good, qty in recipe["inputs"].items():
            demand[good] = demand.get(good, 0) + qty
    population = sum(t.population for t in island.tiles)
    power = config.get("power.good")
    demand[power] = demand.get(power, 0) + population * to_milli(config.get("power.household_use"))
    return supply, demand


def _count(island: IslandState, kind: str) -> int:
    return sum(1 for f in island.facilities if f.kind == kind)


def _producers(defs: Defs, island: IslandState, good: str) -> Iterator[tuple[str, str]]:
    """good を作れる（この島で建てて動かせる）施設と作り方を、投入財の少ない順に。"""
    found = []
    for kind, fdef in defs.facilities.items():
        if fdef.get("buildable") is False:
            continue
        for rname, recipe in fdef["recipes"].items():
            if good in recipe["outputs"] and _feasible(defs, island, kind, rname):
                found.append((len(recipe["inputs"]), kind, rname))
    for _, kind, rname in sorted(found):
        yield kind, rname


def _feasible(defs: Defs, island: IslandState, kind: str, rname: str) -> bool:
    """この島の資源で、この作り方の投入財をどれも島の中で作れるか（元の原料までたどる）。"""
    return _recipe_ok(defs, island, defs.facilities[kind]["recipes"][rname], frozenset())


def _recipe_ok(defs: Defs, island: IslandState, recipe: Any, seen: frozenset[str]) -> bool:
    resource = recipe.get("requires_resource")
    if resource is not None and resource not in island.resources:
        return False
    return all(_makeable(defs, island, good, seen) for good in recipe["inputs"])


def _makeable(defs: Defs, island: IslandState, good: str, seen: frozenset[str]) -> bool:
    if good in seen:
        return False
    return any(
        _recipe_ok(defs, island, recipe, seen | {good})
        for fdef in defs.facilities.values()
        if fdef.get("buildable") is not False
        for recipe in fdef["recipes"].values()
        if good in recipe["outputs"]
    )


def _materials_ok(island: IslandState, defs: Defs, kind: str) -> bool:
    """工事の資材が、倉庫にあるか（ほかの工事の残りの分を除いて）、島の中で作れるか。"""
    reserved: dict[str, int] = {}
    for f in island.facilities:
        if not f.built:
            construction = defs.facilities[f.kind]["construction"]
            done = f.progress / (construction["turns"] * fac.FULL)
            for good, qty in construction.get("materials", {}).items():
                left = qty - int(qty * done) - f.stock.get(good, 0)
                reserved[good] = reserved.get(good, 0) + max(0, left)
    for good, qty in defs.facilities[kind]["construction"].get("materials", {}).items():
        have = stock.total(island.warehouse, good) - reserved.get(good, 0)
        if have < qty and not _makeable(defs, island, good, frozenset()):
            return False
    return True


def _candidates(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> Iterator[tuple[str, str]]:
    """建てたい施設と作り方を、建てたい順に。"""
    supply, demand = _flows(island, defs, config)
    power = config.get("power.good")
    stats = island.stats

    food_supply = sum(
        qty * defs.goods[g]["food"]["nutrition"]
        for g, qty in supply.items()
        if "food" in defs.goods[g]
    )
    food_supply -= sum(qty for g, qty in demand.items() if "food" in defs.goods[g])
    if food_supply < to_milli(stats.get("food.need", 0)) * FOOD_MARGIN:
        if _count(island, "fishing_port") * 2 < _count(island, "farm"):
            yield ("fishing_port", "default")
        yield ("farm", "default")
        return

    if supply.get(power, 0) < demand.get(power, 0) * POWER_MARGIN:
        yield from _producers(defs, island, power)
        return

    for good in sorted(demand, key=lambda g: (defs.goods[g].get("tier", 9), g)):
        if good != power and supply.get(good, 0) < demand[good]:
            yield from _producers(defs, island, good)

    # 人手の足りない施設が多いうちは増やさない（住宅が育って人が移ってくるのを待つ）
    missing = sum(max(0, fac.labor_demand(defs, f, turn) - f.workers) for f in island.facilities)
    if missing > MAX_MISSING_WORKERS:
        return
    for kind in sorted(GROWTH, key=lambda k: (_count(island, k), GROWTH.index(k))):
        for rname in defs.facilities[kind]["recipes"]:
            if _feasible(defs, island, kind, rname):
                yield (kind, rname)
                break


# --- どこに建てるか ---------------------------------------------------------------


def _site_for(
    island: IslandState, defs: Defs, config: GameConfig, kind: str, recipe: str
) -> tuple[int, int] | None:
    """住宅の通勤圏の中で首都に近いマス。開発を止めたマスと、住宅の育つ余地はなるべく避ける。"""
    capital = island.capital
    if capital is None:
        return None
    housing = fac.housing_kind(config)
    homes = [(f.x, f.y) for f in island.facilities if f.kind == housing]
    radius = fac.commute_radius(defs, config, kind)
    best, best_key = None, None
    for pos in within(island, capital, island.size):
        if island.tile(*pos).zone != ZONE_NONE:
            continue
        if fac.placement_problem(defs, island, kind, pos[0], pos[1], recipe) is not None:
            continue
        near = min((distance(pos, h) for h in homes), default=island.size)
        key = (max(0, near - radius), near <= 1, distance(pos, capital))
        if best_key is None or key < best_key:
            best, best_key = pos, key
    return best


def _power_line(island: IslandState, defs: Defs, pos: tuple[int, int]) -> list[tuple[int, int]]:
    """pos を首都の送電網につなぐ送電線のマス（施設のあるマスを除く）。"""
    capital = island.capital
    if capital is None:
        return []
    nodes = {(f.x, f.y) for f in island.facilities} | {pos}
    nodes |= {(t.x, t.y) for t in island.tiles if t.power_line}
    grid = next((set(g) for g in components(island, nodes) if capital in g), {capital})
    if pos in grid:
        return []
    occupied = island.facilities_by_tile()
    path = shortest_path(island, grid, pos, lambda p: fac.is_land(defs, island.tile(*p).terrain))
    if path is None:
        return []
    return [p for p in path if p not in occupied and p != pos]
