"""W-190 の高層の2段（設計書 PO-16・PO-54）: 段階の大きさと費用、土地が足りないときだけ育つ。"""

from __future__ import annotations

import copy

from hakoniwa_core import facilities as fac
from hakoniwa_core import monuments, stock
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.modules.population import Housing, homes_of
from hakoniwa_core.state import ZONE_NO_DEVELOPMENT
from tests.core.test_finance import context
from tests.core.test_housing_floor import _market_island, _village
from tests.core.test_phase3_3 import base as base  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_phase3_3 import game as game  # noqa: F401


def _per_floor(game, level):
    """段階に育つ資材の、床 1 つあたりの費用（初めの値段で）。"""
    prices = initial_prices(game.defs, game.config)
    defs = game.defs
    bounds = {name: (low, high) for name, low, high in fac.level_bounds(defs)}
    low, high = bounds[level]
    cost = sum(
        prices.get(good, 0) * qty / 1000
        for good, qty in defs.settlements[level]["materials"].items()
    )
    return cost / (high - low)


def test_two_high_rise_levels_sit_above_the_metropolis_and_cost_more_per_floor(game):
    defs = game.defs
    levels = defs.settlement_levels()
    assert levels[-3:] == ["metropolis", "high_rise", "skyscraper"]
    caps = [defs.settlements[n]["capacity"] for n in levels]
    assert caps == sorted(caps) and len(set(caps)) == len(caps)
    assert (
        defs.settlements["high_rise"]["land_short"] and defs.settlements["skyscraper"]["land_short"]
    )
    assert not defs.settlements["metropolis"].get("land_short")
    # 床 1 つあたりの建設費は大都市の 1.5 倍・2 倍（高いほど鉄骨とエレベーターが要る）
    base_cost = _per_floor(game, "metropolis")
    assert abs(_per_floor(game, "high_rise") / base_cost - 1.5) < 0.1
    assert abs(_per_floor(game, "skyscraper") / base_cost - 2.0) < 0.1
    # 上下水道は大都市より厳しい
    water = [defs.settlements[n]["water"] for n in levels[-3:]]
    assert water == sorted(water) and water[-1] == 1.0


def test_the_tower_monument_counts_the_high_rise_levels_as_a_metropolis(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    _, home = _village(game, island)
    for level in ("city", "metropolis", "high_rise", "skyscraper"):
        fac.set_floor(game.defs, home, game.defs.settlements[level]["capacity"])
        home.ext["level"] = level
        assert monuments.has_metropolis(game.defs, island) == (level != "city")


def _set_water(game, tile, value):
    for kind in game.defs.infra_kinds("water"):
        tile.infra[kind] = [value, value]


def _crowd_metropolis(game, island):
    """大都市の上限まで育って人が詰まり、床が望みに足りない町と、床を足せる資材と上下水道。"""
    pos, home = _village(game, island)
    defs = game.defs
    top = defs.settlements["metropolis"]["capacity"]
    fac.set_floor(defs, home, top)
    tile = island.tile(*pos)
    tile.population = int(top * 1.6)
    _set_water(game, tile, 1000)
    held = island.economy.housing["stock"] = {}
    for good, qty in fac.floor_materials(defs, top, top + 20000).items():
        stock.add(held, good, qty * 4, 0, defs.goods)
    return pos, home, top


def _close_the_land(game, island):
    """通勤圏に町を置ける空いた平地をなくす（開発しないと決めて）。"""
    kind = fac.housing_kind(game.config)
    terrains = game.defs.facilities[kind]["terrain"]
    occupied = island.facilities_by_tile()
    for tile in island.tiles:
        if tile.terrain in terrains and (tile.x, tile.y) not in occupied:
            tile.zone = ZONE_NO_DEVELOPMENT


def _run(game, world, island, times=24):
    ctx = context(game, world)
    for _ in range(times):
        Housing().run_island(island, ctx)


def test_a_metropolis_does_not_grow_high_while_there_is_empty_land_to_spread_to(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    pos, home, top = _crowd_metropolis(game, island)
    assert Housing._empty_site(island, game.defs, fac.housing_kind(game.config), _all(island))
    _run(game, world, island)
    assert fac.floor_of(game.defs, home) <= top
    assert home.ext["level"] == "metropolis"


def _all(island):
    return [(t.x, t.y) for t in island.tiles]


def test_a_metropolis_grows_high_when_no_empty_land_is_left_and_water_allows(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    pos, home, top = _crowd_metropolis(game, island)
    _close_the_land(game, island)
    kind = fac.housing_kind(game.config)
    assert Housing._empty_site(island, game.defs, kind, _all(island)) is None
    _run(game, world, island)
    assert fac.floor_of(game.defs, home) > top
    assert home.ext["level"] == "high_rise"


def test_a_dry_metropolis_waits_for_water_before_growing_high(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    pos, home, top = _crowd_metropolis(game, island)
    _close_the_land(game, island)
    _set_water(game, island.tile(*pos), 850)  # 高層に要る 0.9 に届かない
    _run(game, world, island)
    assert fac.floor_of(game.defs, home) == top
    assert homes_of(island, game.config)[pos].ext.get("water_short")
