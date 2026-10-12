"""施設についての共通の計算（定義の引き方・置ける場所・人手や資材の割合）。

段や命令はここを通して施設の定義を読みます。割合はすべて 1000 分の 1 の整数（1000 が 100%）です。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.geo import distance, is_coastal
from hakoniwa_core.state import OWNER_STATE, ZONE_OFFICE, Facility, IslandState, Tile

FULL = 1000  # 割合の 100%
STATUS_STARTING = "starting"  # 工事が済んで、まだ動いたことがない（施設の status）


def ratio(have: int, need: int) -> int:
    """have / need を 1000 分の 1 で（上限 1000）。need が 0 なら 1000。"""
    if need <= 0:
        return FULL
    return min(FULL, have * FULL // need)


def scale(amount: int, rate: int) -> int:
    """amount の rate / 1000（切り捨て）。"""
    return amount * rate // FULL


def facility_def(defs: Defs, f: Facility) -> Mapping[str, Any]:
    return defs.facilities[f.kind]


def recipe_of(defs: Defs, f: Facility) -> Mapping[str, Any]:
    return defs.facilities[f.kind]["recipes"][f.recipe]


def priority_of(defs: Defs, config: GameConfig, f: Facility) -> int:
    if f.priority is not None:
        return f.priority
    return defs.facilities[f.kind].get("priority", config.get("labor.default_priority"))


def commute_radius(defs: Defs, config: GameConfig, kind: str) -> int:
    return defs.facilities[kind].get("commute_radius", config.get("labor.commute_radius"))


def housing_kind(config: GameConfig) -> str:
    return config.get("housing.facility")


def is_housing(config: GameConfig, f: Facility) -> bool:
    return f.kind == housing_kind(config)


def on_map(f: Facility) -> bool:
    """マスを持つ施設か（運送業のようなマスを持たない施設は x, y が -1）。"""
    return f.x >= 0 and f.y >= 0


def is_tileless(defs: Defs, kind: str) -> bool:
    return bool(defs.facilities[kind].get("tileless"))


def workers_needed(defs: Defs, kind: str) -> int:
    """全力で動くのに要る労働者の数。"""
    return defs.facilities[kind]["workers"]


def is_halted(f: Facility, turn: int) -> bool:
    """災害などで止まっているか。"""
    return f.ext.get("halted_until", 0) >= turn


def labor_share(defs: Defs, island: IslandState) -> float:
    """住民のうち一般の労働者になる割合。

    働かない家計の層（資産を持つ層。島全体の人数として持つ）の分だけ減る。
    """
    share = defs.workers["general"].get("share", 1.0)
    idle = sum(h.get("share", 0.0) for h in island.economy.households.values())
    return share * (1 - idle)


def labor_force(defs: Defs, island: IslandState, tile: Tile) -> int:
    return int(tile.population * labor_share(defs, island))


def labor_ratio(defs: Defs, f: Facility) -> int:
    return ratio(f.workers, workers_needed(defs, f.kind))


def step_materials(defs: Defs, f: Facility) -> dict[str, int]:
    """工事を今から1ターン分（1000）進めるのに要る資材。全部の工事で合計がちょうど定義の量になる。"""
    construction = defs.facilities[f.kind]["construction"]
    total_progress = construction["turns"] * FULL
    start = min(f.progress, total_progress)
    end = min(f.progress + FULL, total_progress)
    return {
        good: qty * end // total_progress - qty * start // total_progress
        for good, qty in sorted(construction.get("materials", {}).items())
    }


def material_ratio(defs: Defs, f: Facility) -> int:
    """工事中の施設が、手元の資材で今のターンにどれだけ進めるか（1000 分の 1）。"""
    need = step_materials(defs, f)
    return min((ratio(stock.total(f.stock, g), q) for g, q in need.items() if q > 0), default=FULL)


def labor_demand(defs: Defs, f: Facility, turn: int) -> int:
    """このターンに雇いたい人数。工事中は手元の資材で進められる分の人手、動いている施設は
    操業目標（economy["target"]、1000 分の 1）の分だけ。"""
    fdef = defs.facilities[f.kind]
    if not f.built:
        labor = fdef["construction"].get("labor", 0)
        return -(-labor * material_ratio(defs, f) // FULL)  # 切り上げ
    if is_halted(f, turn):
        return 0
    return scale(workers_needed(defs, f.kind), f.economy.get("target", FULL))


def placement_problem(
    defs: Defs, island: IslandState, kind: str, x: int, y: int, recipe: str | None = None
) -> tuple[str, dict[str, Any]] | None:
    """施設 kind を (x, y) に建てられないときは (理由, 引数) を返す。建てられれば None。"""
    if kind not in defs.facilities:
        return ("reason_unknown_facility", {})
    fdef = defs.facilities[kind]
    if not island.in_bounds(x, y):
        return ("reason_out_of_bounds", {})
    tile = island.tile(x, y)
    if tile.terrain not in fdef["terrain"]:
        return ("reason_wrong_terrain", {"facility": kind, "terrain": tile.terrain})
    if island.facility_at(x, y) is not None:
        return ("reason_tile_occupied", {})
    zone = fdef.get("zone")
    if zone is not None and tile.zone != zone:
        return ("reason_zone_required", {"facility": kind, "zone": f"zone_{zone}"})
    if zone is None and tile.zone == ZONE_OFFICE:
        return ("reason_office_zone", {})
    if fdef.get("coastal") and not is_coastal(island, (x, y), lambda t: is_land(defs, t)):
        return ("reason_not_coastal", {"facility": kind})
    limit = fdef.get("max_per_island")
    if limit is not None and sum(1 for f in island.facilities if f.kind == kind) >= limit:
        return ("reason_too_many", {"facility": kind, "max": limit})
    near = fdef.get("near_capital")
    if near is not None:
        capital = island.capital
        if capital is None or distance(capital, (x, y)) > near:
            return ("reason_far_from_capital", {"facility": kind, "distance": near})
    recipes = fdef["recipes"]
    if recipe is None:
        if len(recipes) > 1:
            return ("reason_recipe_required", {"facility": kind})
        recipe = next(iter(recipes))
    if recipe not in recipes:
        return ("reason_unknown_recipe", {"facility": kind})
    resource = recipes[recipe].get("requires_resource")
    if resource is not None and resource not in island.resources:
        return ("reason_missing_resource", {"good": resource})
    return None


def is_land(defs: Defs, terrain: str) -> bool:
    return bool(defs.terrain.get(terrain, {}).get("land"))


def default_recipe(defs: Defs, kind: str) -> str | None:
    """作り方が1つだけならその名前、複数なら None。"""
    recipes = defs.facilities[kind]["recipes"]
    return next(iter(recipes)) if len(recipes) == 1 else None


def settlement_of(defs: Defs, population: int) -> str:
    """住民の数から決まる、町（住宅地）の見た目の段階（村・町など）。"""
    levels = defs._settlement_levels
    found = levels[0]
    for level in levels:
        if population >= defs.settlements[level]["min_population"]:
            found = level
    return found


def housing_capacity(defs: Defs, f: Facility) -> int:
    level = f.ext.get("level") or defs.settlement_levels()[0]
    return defs.settlements[level]["capacity"]


def new_housing(island: IslandState, kind: str, pos: tuple[int, int], level: str) -> Facility:
    """町（住宅地）を建てる（自動で建つので工事はない。資源は呼ぶ側が倉庫から使う）。"""
    home = Facility(
        id=island.next_facility_id(),
        kind=kind,
        x=pos[0],
        y=pos[1],
        owner=OWNER_STATE,
        built=True,
        ext={"level": level},
    )
    island.facilities.append(home)
    return home
