"""W-190・W-202 の国の住宅（設計書 PO-55・PO-56・FS-26・FS-27）: 命令 build_housing、住宅費、
国の床の家賃、計画経済の国が建てる町。

仕組みの試験は、作った場面（決めた在庫、お金、床）で確かめる。世界を何ターンも回す試験では、
止まらない・お金が合う・崩れない、だけを見る。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import GENERAL, JOBLESS, OWNERS, household
from hakoniwa_core.economy import public_housing as pub
from hakoniwa_core.modules.population import (
    Housing,
    build_housing,
    check_build_housing,
    dwelling_status,
    homes_of,
)
from hakoniwa_core.modules.trade import check
from hakoniwa_core.registry import OrderFailed
from tests.core.test_finance import books_ok, context, open_books
from tests.core.test_housing_floor import _market_island, _village
from tests.core.test_phase3_3 import base as base  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_phase3_3 import game as game  # noqa: F401
from tests.core.test_phase3_3 import run_checked


def _village_cap_home(game, world):
    """基準の世界の、同じ村の施設（床を足す前の値を見るため）。"""
    island = _market_island(world)
    return _village(game, island)[1]


def _planned_island(world):
    return next(
        i for i in world.islands.values() if i.has_economy and i.regime.get("economy") == "planned"
    )


def _public_stock(game, island, low, high, times=1):
    held = pub.state(island)["stock"]
    for good, qty in fac.floor_materials(game.defs, low, high).items():
        stock.add(held, good, qty * times, 0, game.defs.goods)


# --- 家賃 ---------------------------------------------------------------------------


def test_public_floor_goes_to_the_lowest_income_layers_first_and_rent_is_per_floor(game):
    min_space = game.config.get("housing.space")["min"]
    # 層の望む広さ（㎡）: 失業 600、一般 2400、資産を持つ層 1200。国の床は 150 人分（= 1800㎡）
    layers = {JOBLESS: 600.0, GENERAL: 2400.0, OWNERS: 1200.0}
    taken = pub.tenants(layers, 150, min_space)
    assert taken == {JOBLESS: 50.0, GENERAL: 100.0}
    rent = pub.rent(game.config, taken)
    unit = game.config.get("household.base_income") * game.config.get("housing.public_rent")
    assert rent[JOBLESS] == round(50 * unit) and rent[GENERAL] == round(100 * unit)
    assert OWNERS not in rent
    # 国の床が余れば家賃は住む分だけ
    assert sum(pub.tenants(layers, 10_000, min_space).values()) == pytest.approx(
        sum(layers.values()) / min_space
    )


def test_rent_is_paid_to_the_treasury_and_waived_when_a_layer_cannot_pay(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    pos, home = _village(game, island)
    open_books(island)
    pub.set_floor(home, fac.floor_of(game.defs, home))  # 村の床をぜんぶ国の床にする
    status = dwelling_status(game.defs, game.config, island, homes_of(island, game.config))
    before = ledger.money(island, ledger.GOV)
    deposits = ledger.money(island, household(GENERAL))
    Housing._collect_rent(island, game.config, homes_of(island, game.config), status)
    paid = island.stats["housing.rent"]
    assert paid > 0
    assert ledger.money(island, ledger.GOV) == before + paid
    assert ledger.money(island, household(GENERAL)) <= deposits
    books_ok(game, island)
    # 払えない層は払えるだけ（国の未収にしない）
    for layer in island.economy.households:
        ledger.pay(
            island,
            household(layer),
            household(GENERAL) if layer != GENERAL else household(OWNERS),
            ledger.money(island, household(layer)),
            "layer_transfer",
        )
    Housing._collect_rent(island, game.config, homes_of(island, game.config), status)
    assert island.stats["housing.rent"] <= paid
    books_ok(game, island)


# --- 命令 build_housing -------------------------------------------------------------


def test_build_housing_checks_the_site_and_the_target(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    pos, home = _village(game, island)
    x, y = pos
    both = {"x": x, "y": y, "floors": 10, "level": "town"}
    with pytest.raises(OrderFailed):
        check_build_housing(island, both, game.defs)
    with pytest.raises(OrderFailed):
        check_build_housing(island, {"x": x, "y": y}, game.defs)
    with pytest.raises(OrderFailed):  # 範囲の外
        check_build_housing(island, {"x": 10**6, "y": 0, "floors": 1}, game.defs)
    with pytest.raises(OrderFailed):  # もう届いている段階
        check_build_housing(island, {"x": x, "y": y, "level": "village"}, game.defs)
    check_build_housing(island, {"x": x, "y": y, "floors": 100}, game.defs)
    check_build_housing(island, {"x": x, "y": y, "level": "city"}, game.defs)
    # 施設のあるマスと、町を置けない地形
    occupied = next(f for f in island.facilities if f.kind != home.kind and fac.on_map(f))
    with pytest.raises(OrderFailed):
        check_build_housing(island, {"x": occupied.x, "y": occupied.y, "floors": 1}, game.defs)
    sea = next(
        t for t in island.tiles if t.terrain not in game.defs.facilities[home.kind]["terrain"]
    )
    with pytest.raises(OrderFailed):
        check_build_housing(island, {"x": sea.x, "y": sea.y, "floors": 1}, game.defs)


def test_build_housing_queues_the_work_and_the_state_adds_floor_a_step_at_a_time(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    pos, home = _village(game, island)
    ctx = context(game, world)
    cap = fac.floor_of(game.defs, home)
    build_housing(island, {"x": pos[0], "y": pos[1], "level": "city"}, ctx)
    works = pub.works_pending(island)
    assert works == [{"x": pos[0], "y": pos[1], "floor": game.defs.settlements["city"]["capacity"]}]
    # 同じマスの命令は置き換える
    build_housing(island, {"x": pos[0], "y": pos[1], "floors": 50}, ctx)
    assert pub.works_pending(island) == [{"x": pos[0], "y": pos[1], "floor": cap + 50}]
    # 資材がなければ床は増えず、足りない資材を次のターンに買う分に書く
    Housing().run_island(island, ctx)
    assert fac.floor_of(game.defs, home) == cap
    assert pub.state(island)["want"]
    assert pub.floor_of(home) == 0
    # 資材があれば 1 ターンに step だけ足し、足した床は国の床
    _public_stock(game, island, cap, cap + 50, times=2)
    Housing().run_island(island, ctx)
    step = fac.floor_of(game.defs, home) - cap
    assert 0 < step <= 50
    assert pub.floor_of(home) == step
    for _ in range(10):
        Housing().run_island(island, ctx)
    assert fac.floor_of(game.defs, home) == cap + 50
    assert pub.floor_of(home) == 50
    assert pub.works_pending(island) == []  # 済んだ工事は列から外れる


def test_build_housing_on_empty_land_founds_a_town_of_public_floor(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    kind = fac.housing_kind(game.config)
    site = Housing._empty_site(island, game.defs, kind, [(t.x, t.y) for t in island.tiles])
    assert site is not None
    ctx = context(game, world)
    build_housing(island, {"x": site[0], "y": site[1], "floors": 300}, ctx)
    held = pub.state(island)["stock"]
    for good, qty in fac.floor_materials(game.defs, 0, 600).items():
        stock.add(held, good, qty, 0, game.defs.goods)
    for _ in range(8):
        Housing().run_island(island, ctx)
    home = homes_of(island, game.config)[site]
    assert fac.floor_of(game.defs, home) == 300
    assert pub.floor_of(home) == 300


def test_a_high_rise_order_ignores_the_empty_land_rule_but_still_needs_water(game, base):
    from tests.core.test_high_rise import _crowd_metropolis, _set_water

    world = copy.deepcopy(base)
    island = _market_island(world)
    pos, home, top = _crowd_metropolis(game, island)
    held = pub.state(island)["stock"]
    for good, qty in fac.floor_materials(game.defs, top, top + 20000).items():
        stock.add(held, good, qty * 4, 0, game.defs.goods)
    island.economy.housing["stock"] = {}  # 家計は資材を持たない
    ctx = context(game, world)
    _set_water(game, island.tile(*pos), 850)  # 高層に要る 0.9 に届かない
    build_housing(island, {"x": pos[0], "y": pos[1], "level": "high_rise"}, ctx)
    for _ in range(6):
        Housing().run_island(island, ctx)
    assert fac.floor_of(game.defs, home) == top
    assert home.ext.get("water_short")
    _set_water(game, island.tile(*pos), 1000)
    for _ in range(12):
        Housing().run_island(island, ctx)
    assert fac.floor_of(game.defs, home) > top
    assert home.ext["level"] == "high_rise"  # 空いた平地があっても国の命令なら育つ
    assert pub.floor_of(home) == fac.floor_of(game.defs, home) - top


# --- 計画経済 -----------------------------------------------------------------------


def test_planned_islands_start_with_public_floor_and_a_housing_budget(game, base):
    planned = _planned_island(base)
    market = _market_island(base)
    homes = homes_of(planned, game.config)
    assert homes and all(pub.floor_of(h) == fac.floor_of(game.defs, h) for h in homes.values())
    assert planned.policy.budget.get("housing", 0) > 0
    assert market.policy.budget.get("housing", 0) == 0
    assert pub.tile_total(market) == 0


def test_public_builds_use_the_state_stock_and_a_new_village_is_public(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    need = fac.floor_materials(game.defs, 0, 600)
    island.economy.housing["want"] = {}  # 回してきた分の家計の注文は消す
    # 国の資材がなければ何も使わず、国の次のターンの注文（want）に書く。家計の注文には書かない
    assert Housing._pay_goods(island, need, True)
    assert pub.state(island)["want"] and not island.economy.housing["want"]
    held = pub.state(island)["stock"]
    for good, qty in need.items():
        stock.add(held, good, qty, 0, game.defs.goods)
    assert Housing._pay_goods(island, need, True) == []
    assert all(stock.total(held, g) == 0 for g in need)
    kind = fac.housing_kind(game.config)
    site = Housing._empty_site(island, game.defs, kind, [(t.x, t.y) for t in island.tiles])
    village = game.defs.settlement_levels()[0]
    public = Housing._new_village(island, game.defs, kind, site, village, True)
    private = Housing._new_village(island, game.defs, kind, (site[0] + 99, 0), village, False)
    assert pub.floor_of(public) == fac.floor_of(game.defs, public)
    assert pub.floor_of(private) == 0


def test_in_a_planned_country_households_buy_no_housing_but_in_a_market_one_they_do(game, base):
    from tests.core.test_housing_floor import _lacking_village

    market = copy.deepcopy(base)
    island, pos, home = _lacking_village(game, market)
    Housing().run_island(island, context(game, market))
    assert island.economy.housing["move"]  # 市場の国: 足りない層が住み替えの資材を買う
    planned = copy.deepcopy(base)
    island = _planned_island(planned)
    pos, home = _village(game, island)
    spec = game.config.get("housing.space")
    status = dwelling_status(game.defs, game.config, island, homes_of(island, game.config))
    home.ext[fac.FLOOR] = int(status[pos].want / spec["min"] * 0.7)
    island.economy.housing["stock"] = {}
    for _ in range(3):
        Housing().run_island(island, context(game, planned))
    assert island.economy.housing["move"] == {}  # 計画経済の国: 家計は買わない
    assert island.economy.housing["want"] == {}


def test_the_world_runs_with_public_housing_and_the_books_balance(game, base):
    world = copy.deepcopy(base)
    market = _market_island(world)
    pos, home = _village(game, market)
    market.policy.budget["housing"] = 10**9
    ctx = context(game, world)
    build_housing(market, {"x": pos[0], "y": pos[1], "level": "city"}, ctx)
    run_checked(game, world, 24)
    assert check(world) == []
    # 国が住宅費で資材を市場で買い、床を足して、国の床の家賃が国庫に入る
    assert pub.floor_of(home) > 0
    assert fac.floor_of(game.defs, home) > fac.floor_of(game.defs, _village_cap_home(game, base))
    assert market.stats.get("housing.rent", 0) > 0
