"""工業団地の外の工場（FM-61。W-256。2026-11-10）。

民営の工場は、空いた工業団地のマスにも建て増しの場所にも建てられないとき、指定のない人の住んで
いないマスに建つ。そのときは造成（firms.site_work）の分だけ工期・人手・資材・建設費が増える。
"""

from __future__ import annotations

import copy

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.economy import GENERAL, OWNERS, household
from hakoniwa_core.economy.prices import construction_cost, initial_prices, site_cost
from hakoniwa_core.modules.construction import Construction
from hakoniwa_core.modules.firms import EntryExit, needs_site_work
from hakoniwa_core.state import ZONE_INDUSTRIAL, ZONE_NO_DEVELOPMENT, ZONE_NONE, Facility
from hakoniwa_core.units import QUANTITY_SCALE
from tests.core.test_finance import context, island_of
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401

FACTORIES = ("steel_mill", "consumer_goods_factory", "machinery_plant", "textile_mill")


def _no_zones(warmed):  # noqa: F811
    """市場経済の島から工業団地の指定をみな外した世界。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    for tile in island.tiles:
        if tile.zone == ZONE_INDUSTRIAL:
            tile.zone = ZONE_NONE
    return world, island


def _open_site(game, world, island):  # noqa: F811
    """空き地に建つ工場と、その場所（建て増しでないもの）。"""
    ctx = context(game, world)
    for kind in FACTORIES:
        site = EntryExit()._site(island, ctx, kind, "default")
        if isinstance(site, str):
            continue
        if needs_site_work(island, game.defs, game.config, kind, site[0]):
            return kind, site[0]
    raise AssertionError("空き地に建つ工場がない")


def test_factory_opens_on_open_land_when_no_zone_is_free(game, warmed):  # noqa: F811
    """工業団地がなければ、工場も指定のない人の住んでいないマスに建つ。"""
    world, island = _no_zones(warmed)
    kind, pos = _open_site(game, world, island)
    tile = island.tile(*pos)
    assert tile.zone == ZONE_NONE and tile.population == 0
    assert island.facility_at(*pos) is None


def test_industrial_zone_comes_before_open_land(game, warmed):  # noqa: F811
    """同じマスが工業団地なら、そこに造成なしで建つ（工業団地 → 建て増し → 空き地の順）。"""
    world, island = _no_zones(warmed)
    kind, pos = _open_site(game, world, island)
    island.tile(*pos).zone = ZONE_INDUSTRIAL
    site = EntryExit()._site(island, context(game, world), kind, "default")
    assert not isinstance(site, str)
    assert island.tile(*site[0]).zone == ZONE_INDUSTRIAL
    assert not needs_site_work(island, game.defs, game.config, kind, site[0])


def test_no_open_land_means_no_zone(game, warmed):  # noqa: F811
    """開発を止めるマスと人の住むマスには建たない。空き地もなければ建たずにわけを残す。"""
    world, island = _no_zones(warmed)
    kind, _ = _open_site(game, world, island)
    for tile in island.tiles:
        if tile.zone == ZONE_NONE and tile.population == 0:
            tile.zone = ZONE_NO_DEVELOPMENT
    site = EntryExit()._site(island, context(game, world), kind, "default")
    # 建て増しの場所があればそこ、なければ建たない
    assert isinstance(site, str) or island.facility_at(*site[0]) is not None


def test_land_based_facilities_and_shops_need_no_site_work(game, warmed):  # noqa: F811
    """土地で営む施設（firms.open_land）は前から空き地に建ち、造成は足さない。"""
    world, island = _no_zones(warmed)
    _, pos = _open_site(game, world, island)
    for kind in game.config.get("firms.open_land"):
        assert not needs_site_work(island, game.defs, game.config, kind, pos), kind
    assert not needs_site_work(island, game.defs, game.config, "bank", pos)  # オフィス街の施設


def test_site_cost_adds_materials_and_labor(game):  # noqa: F811
    """造成の費用 = 造成の資材の代金 + 延びた工期の施設の人手と、工期の全部の造成の作業員の賃金。"""
    defs, config = game.defs, game.config
    prices = initial_prices(defs, config)
    wage = 100
    kind = "steel_mill"
    site = config.get("firms.site_work")
    cdef = defs.facilities[kind]["construction"]
    materials = sum(prices[g] * q / QUANTITY_SCALE for g, q in site["materials"].items())
    labor = cdef["labor"] * site["turns"] + site["labor"] * (cdef["turns"] + site["turns"])
    assert site_cost(defs, config, kind, prices, wage) == materials + labor * wage
    assert site_cost(defs, config, kind, prices, wage) > 0
    assert construction_cost(defs, kind, prices, wage) > 0


def test_open_land_works_lengthen_the_build(game, warmed):  # noqa: F811
    """空き地の工事は、工期が firms.site_work.turns 延び、人手と資材が増える。終われば造成の記録は
    消え、マスは工業団地にならない。"""
    world, island = _no_zones(warmed)
    kind, pos = _open_site(game, world, island)
    defs, config = game.defs, game.config
    works = config.get("firms.site_work")
    cdef = defs.facilities[kind]["construction"]
    f = Facility(id=island.next_facility_id(), kind=kind, x=pos[0], y=pos[1], recipe="default")
    base_labor = fac.construction_labor(defs, f)
    base_materials = fac.materials_of(defs, f)
    f.ext[fac.SITE_WORK] = {"labor": works["labor"], "materials": dict(works["materials"])}
    f.ext["build_total"] = (cdef["turns"] + works["turns"]) * fac.FULL
    assert fac.total_progress(defs, f) == (cdef["turns"] + works["turns"]) * fac.FULL
    assert fac.construction_labor(defs, f) == base_labor + works["labor"]
    for good, qty in works["materials"].items():
        assert fac.materials_of(defs, f)[good] == base_materials.get(good, 0) + qty
    island.facilities.append(f)
    Construction._complete(island, f, context(game, world, "construction"))
    assert fac.SITE_WORK not in f.ext and "build_total" not in f.ext
    assert island.tile(*pos).zone == ZONE_NONE


def test_private_build_on_open_land_records_site_work(game, warmed):  # noqa: F811
    """参入の工事を空き地で始めると、造成の記録と延びた工期を持つ。"""
    world, island = _no_zones(warmed)
    kind, pos = _open_site(game, world, island)
    defs, config = game.defs, game.config
    ctx = context(game, world, "entry_exit")
    prices = initial_prices(defs, config)
    wage = island.economy.average_wage
    capital = round(
        construction_cost(defs, kind, prices, wage) + site_cost(defs, config, kind, prices, wage)
    )
    # 出資の元手を資本家階級に持たせる（この試験が見るのは造成の記録）
    owners, general = household(OWNERS), household(GENERAL)
    ledger.pay(island, general, owners, ledger.money(island, general) // 2, "layer_transfer")
    before = {f.id for f in island.facilities}
    failure = EntryExit()._build(island, ctx, kind, "default", pos, capital, prices, 0)
    assert failure is None, failure
    f = next(f for f in island.facilities if f.id not in before)
    works = config.get("firms.site_work")
    assert f.ext[fac.SITE_WORK]["labor"] == works["labor"]
    turns = defs.facilities[kind]["construction"]["turns"] + works["turns"]
    assert fac.total_progress(defs, f) == turns * fac.FULL
