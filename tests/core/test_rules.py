"""段と命令のルールを、小さな手作りの島で確かめる。

お金の経済を使う段（雇用・生産・市場など）は、with_economy で経済を始めた島で確かめる。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.allocation import by_priority, proportional
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.economy.start import init_facility, start_economy
from hakoniwa_core.game import load_game
from hakoniwa_core.log import format_log
from hakoniwa_core.naming import facility_name
from hakoniwa_core.orders import check_order
from hakoniwa_core.state import (
    ORDER_DONE,
    ORDER_FAILED,
    ZONE_NO_DEVELOPMENT,
    Facility,
    IslandState,
    OrderState,
    Tile,
    WorldState,
)


@pytest.fixture(scope="module")
def game():
    return load_game()


def make_island(game, terrain=None, capital=(5, 5)) -> IslandState:
    """2〜13 の四角が平地、ほかは海の島。terrain で一部の地形を変えられる。"""
    size = game.config.get("island.size")
    tiles = []
    for y in range(size):
        for x in range(size):
            land = 2 <= x <= 13 and 2 <= y <= 13
            t = (terrain or {}).get((x, y), "plain" if land else "sea")
            tiles.append(Tile(x=x, y=y, terrain=t))
    island = IslandState(
        id=1,
        name="島",
        wx=0,
        wy=0,
        size=size,
        tiles=tiles,
        capital_x=capital[0],
        capital_y=capital[1],
        resources=["coal", "iron_ore"],
        regime={"politics": "democracy", "head_of_state": "republic", "economy": "planned"},
    )
    return island


def add(island, kind, x, y, built=True, recipe="default", stock=None, **kw) -> Facility:
    """施設を置く。stock は財 → 量（作られたターン 0 の組にする）。"""
    f = Facility(
        id=island.next_facility_id(), kind=kind, x=x, y=y, recipe=recipe, built=built, **kw
    )
    f.stock = {g: [[0, q]] for g, q in (stock or {}).items()}
    island.facilities.append(f)
    if island.has_economy:
        game = load_game()
        prices = initial_prices(game.defs, game.config)
        init_facility(island, game.defs, game.config, f, prices, island.economy.average_wage)
    return f


def with_economy(game, island) -> IslandState:
    """置いた施設と住民で、お金の経済を始める（首都の近くに銀行、マスを持たない運送業が増える）。"""
    start_economy(island, game.defs, game.config, 0)
    return island


def home(game, island, x, y, population, level="town") -> Facility:
    island.tile(x, y).population = population
    return fac.new_housing(island, "housing", (x, y), level)


def run(game, island, stages, turns=1, orders=()) -> WorldState:
    world = WorldState(turn=0, seed=1, islands={1: island})
    for i, (kind, payload) in enumerate(orders, start=1):
        island.orders.append(OrderState(id=i, seq=i, kind=kind, payload=payload))
    saved = game.engine.stages
    game.engine.stages = [game.registry.stages[name] for name in stages]
    try:
        for _ in range(turns):
            game.engine.run_turn(world)
    finally:
        game.engine.stages = saved
    return world


def test_allocation():
    assert proportional(10, [("a", 4), ("b", 4)]) == {"a": 4, "b": 4}
    assert proportional(7, [("a", 4), ("b", 4)]) == {"a": 4, "b": 3}
    got = by_priority(10, [("low", 8, 1), ("high", 6, 9), ("mid", 8, 5)])
    assert got == {"high": 6, "mid": 4, "low": 0}


def test_spoilage(game):
    goods = game.defs.goods
    warehouse: dict = {}
    stock.add(warehouse, "fish", 5000, 1, goods)  # 魚は2ターン持ち越せる
    stock.add(warehouse, "steel", 5000, 1, goods)  # 鋼鉄は傷まない
    assert stock.spoil(warehouse, 2, goods) == {}
    assert stock.spoil(warehouse, 3, goods) == {"fish": 5000}
    assert stock.totals(warehouse) == {"steel": 5000}
    stock.add(warehouse, "fish", 3000, 4, goods)
    stock.add(warehouse, "fish", 2000, 5, goods)
    assert stock.take(warehouse, "fish", 4000) == 4000  # 古い組から使う
    assert warehouse["fish"] == [[5, 1000]]


def test_employment_by_priority_and_commute(game):
    island = make_island(game)
    home(game, island, 5, 6, 2000)  # 労働者 1,000 人
    office = add(island, "government_office", 5, 5)  # 優先順位 9、1,000 人
    farm = add(island, "farm", 7, 7)  # 優先順位 7
    far = add(island, "farm", 12, 12)  # 通勤圏の外
    home(game, island, 2, 13, 20000, level="metropolis")  # どの施設の通勤圏の外（国庫を厚くする）
    with_economy(game, island)
    bank = next(f for f in island.facilities if f.kind == "bank")  # 優先順位 5
    run(game, island, ["employment"])
    # 賃金が同じなので、優先順位の高い施設から雇う
    assert office.workers == 1000
    assert farm.workers == 0
    assert far.workers == 0
    island.tile(5, 6).population = 3000
    run(game, island, ["employment"])
    assert (office.workers, farm.workers, bank.workers) == (1000, 500, 0)
    # 賃金の高い施設が先に雇う（同じ通勤圏の中から）
    farm.economy["wage"] = 1200
    run(game, island, ["employment"])
    assert farm.workers == 500
    assert office.economy["staff"] and farm.economy["staff"]


def test_power_market_serves_by_priority_and_needs_grid(game):
    island = make_island(game)
    home(game, island, 6, 6, 8000, level="city")  # 労働者 4,000 人
    plant = add(island, "thermal_power_plant", 5, 6, stock={"coal": 400})  # 石炭 0.4 で 3 作る
    high = add(island, "mine", 4, 6, recipe="coal", priority=9)  # 2 使う
    low = add(island, "machinery_plant", 6, 5, stock={"steel": 4000}, priority=1)  # 4 使う
    home(game, island, 11, 11, 2000, level="village")
    cut_off = add(island, "mine", 11, 10, recipe="iron_ore")  # 発電所の送電網とつながっていない
    with_economy(game, island)
    run(game, island, ["employment", "production"])
    assert plant.operating_rate == 100
    assert island.stats["power.capacity"] == 3
    # 足りない電力は優先順位の高い鉱山に先に回り、機械工場は残りの分だけ動く
    assert (high.operating_rate, high.status) == (1000, "")
    assert (low.operating_rate, low.status) == (250, "power")
    assert cut_off.workers > 0
    assert (cut_off.operating_rate, cut_off.status) == (0, "power")
    power = next(m for m in island.markets if m.good == "electricity")
    assert power.volume == 3000


def test_construction_uses_exact_materials(game):
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    site = add(island, "farm", 6, 6, built=False)  # 2 ターン、人手 200、木材 5
    with_economy(game, island)
    stock.add(island.warehouse, "wood", 3000, 0, game.defs.goods)
    stages = ["employment", "construction", "market"]
    run(game, island, stages, turns=1)
    assert site.progress == 0  # 最初のターンは国の倉庫から資材が届くだけ
    assert stock.total(site.stock, "wood") == 2500
    run(game, island, stages, turns=1)
    assert site.progress == 1000
    run(game, island, stages, turns=1)
    # 木材は残り 500 しか届いていないので、2 ターン目の工事は 1/5 だけ進む
    assert site.progress == 1200
    assert not site.built
    stock.add(island.warehouse, "wood", 10_000, 0, game.defs.goods)
    run(game, island, stages, turns=3)
    assert site.built
    assert site.status == fac.STATUS_STARTING
    assert stock.total(island.warehouse, "wood") == 13_000 - 5_000


def test_market_sells_food_to_households(game):
    island = make_island(game)
    home(game, island, 5, 6, 5000)  # 食料（栄養）10 が要る
    add(island, "government_office", 5, 5)  # 働き口（賃金が家計の使う額になる）
    farm = add(island, "farm", 6, 6, stock={"grain": 15_000})
    with_economy(game, island)
    run(game, island, ["employment", "payroll", "market", "population"])
    food = island.scratch["food"]
    assert (food["need"], food["supplied"]) == (10_000, 10_000)
    grain = next(m for m in island.markets if m.good == "grain")
    assert grain.volume > 0
    assert stock.total(farm.stock, "grain") == 15_000 - grain.volume
    assert any(t.good == "grain" and t.seller == f"fac:{farm.id}" for t in island.trades)


def test_population_starves_without_food(game):
    island = make_island(game)
    home(game, island, 5, 6, 5000)
    with_economy(game, island)
    run(game, island, ["market", "population"], turns=3)
    assert island.tile(5, 6).population < 5000


def test_housing_grows_near_vacancies_but_not_on_no_development(game):
    island = make_island(game)
    home(game, island, 3, 12, 1000, level="village")  # 官庁の通勤圏の外
    add(island, "government_office", 8, 8)  # 人手の足りない施設（公務員はいつも 1,000 人雇う）
    home(game, island, 2, 13, 20000, level="metropolis")  # 通勤圏の外（国庫を厚くする）
    with_economy(game, island)
    for tile in island.tiles:  # 官庁のそばの (9, 9) のほかは開発禁止区域（オフィス街は除く）
        if (tile.x, tile.y) != (9, 9) and tile.zone == "":
            tile.zone = ZONE_NO_DEVELOPMENT
    stages = ["execute_orders", "employment", "market", "population", "housing"]
    # 木材が市場に出ていなければ町はできない
    before = {(f.x, f.y) for f in island.facilities if f.kind == "housing"}
    run(game, island, stages, turns=2)
    assert {(f.x, f.y) for f in island.facilities if f.kind == "housing"} == before
    assert "wood" in island.ext["housing_short"]
    # 国の倉庫の木材を市場に出すと、住民が（預金の決めた割合までずつ）買って村を作る
    # （開発禁止区域には作らない）
    stock.add(island.warehouse, "wood", 20_000, 0, game.defs.goods)
    sell = {"good": "wood", "side": "sell", "quantity": 20}
    for _ in range(3):
        run(game, island, stages, orders=[("warehouse_trade", sell)])
    new = [f for f in island.facilities if f.kind == "housing" and (f.x, f.y) not in before]
    assert [(f.x, f.y) for f in new] == [(9, 9)]
    assert "wood" not in island.ext.get("housing_short", [])


def test_housing_short_is_logged_after_two_turns(game):
    island = make_island(game)
    home(game, island, 5, 5, 1000, level="village")
    add(island, "steel_mill", 8, 8)
    home(game, island, 2, 13, 20000, level="metropolis")  # 通勤圏の外（国庫を厚くする）
    with_economy(game, island)
    stages = ["employment", "population", "housing"]
    world = run(game, island, stages)
    assert "housing_short" not in [e.kind for e in island.log]
    island.log.clear()
    game.engine.stages = [game.registry.stages[n] for n in stages]
    try:
        game.engine.run_turn(world)
        assert [e.kind for e in island.log].count("housing_short") == 1
        island.log.clear()
        game.engine.run_turn(world)
    finally:
        game.engine.stages = [game.registry.stages[n] for n in game.config.stages]
    assert "housing_short" not in [e.kind for e in island.log]


def test_build_order_checks(game):
    island = make_island(game, terrain={(3, 3): "mountain", (4, 3): "forest"})
    defs, registry = game.defs, game.registry

    def reason(kind, payload):
        return check_order(defs, registry, island, kind, payload).reason

    assert reason("build", {"facility": "farm", "x": 6, "y": 6}) is None
    assert reason("build", {"facility": "farm", "x": 3, "y": 3}) == "reason_wrong_terrain"
    assert reason("build", {"facility": "fishing_port", "x": 6, "y": 6}) == "reason_not_coastal"
    assert reason("build", {"facility": "fishing_port", "x": 2, "y": 6}) is None
    assert reason("build", {"facility": "mine", "x": 3, "y": 3}) == "reason_recipe_required"
    payload = {"facility": "mine", "x": 3, "y": 3, "recipe": "coal"}
    assert reason("build", payload) is None
    island.resources = ["iron_ore"]
    assert reason("build", payload) == "reason_missing_resource"
    assert reason("build", {"facility": "housing", "x": 6, "y": 6}) == "reason_not_buildable"
    far = {"facility": "government_office", "x": 12, "y": 12}
    assert reason("build", far) == "reason_far_from_capital"
    assert reason("build", {"facility": "farm", "x": 40, "y": 6}) == "reason_out_of_bounds"
    assert reason("build", {"facility": "farm", "x": 6}) == "reason_invalid_payload"
    # 鉱山は1国2か所まで（工事中も数える）
    island.resources = ["coal", "iron_ore"]
    add(island, "mine", 3, 3, recipe="coal")
    island.tile(5, 3).terrain = island.tile(6, 3).terrain = "mountain"
    add(island, "mine", 5, 3, recipe="iron_ore", built=False)
    third = {"facility": "mine", "x": 6, "y": 3, "recipe": "coal"}
    assert reason("build", third) == "reason_too_many"


def test_orders_run_in_turn(game):
    island = make_island(game)
    add(island, "government_office", 5, 5, workers=5000)  # 行政力 5000 × 0.001 = 5
    orders = [
        ("build", {"facility": "farm", "x": 7, "y": 7, "name": "みどり農場"}),
        ("build_power_line", {"tiles": [[6, 5], [7, 5]]}),
        ("set_no_development", {"tiles": [[9, 9]], "value": True}),
        ("set_priority", {"x": 5, "y": 5, "priority": 3}),
        ("demolish", {"x": 5, "y": 5}),
        ("rename_facility", {"x": 7, "y": 7, "name": ""}),
    ]
    run(game, island, ["execute_orders"], orders=orders)
    status = [o.status for o in island.orders]
    assert status == [ORDER_DONE, ORDER_DONE, ORDER_DONE, ORDER_DONE, ORDER_FAILED, ORDER_DONE]
    assert island.orders[4].reason == "reason_capital_building"
    farm = island.facility_at(7, 7)
    assert not farm.built
    renamed = next(e for e in island.log if e.kind == "facility_renamed")
    assert renamed.args["new"] == ""  # 建てるときに付けた名前を消して、自動の名前に戻した
    assert facility_name(game.defs, game.config, island, farm) == "国営第一農場"
    assert island.tile(6, 5).power_line and island.tile(7, 5).power_line
    assert island.tile(9, 9).zone == ZONE_NO_DEVELOPMENT
    assert island.facility_at(5, 5).priority == 3
    failed = next(e for e in island.log if e.kind == "order_failed")
    assert format_log(game.defs, failed.kind, failed.args) == (
        "命令「撤去」を実行できませんでした（首都の建物は撤去できません）"
    )


def test_demolish_returns_stock_and_removes_power_line(game):
    island = make_island(game)
    add(island, "government_office", 5, 5, workers=2000)  # 行政力 2
    farm = add(island, "food_factory", 7, 7, stock={"grain": 3000})
    island.tile(8, 8).infra["power"] = 1
    orders = [("demolish", {"x": 7, "y": 7}), ("demolish", {"x": 8, "y": 8})]
    run(game, island, ["execute_orders"], orders=orders)
    assert farm not in island.facilities
    assert stock.total(island.warehouse, "grain") == 3000
    assert not island.tile(8, 8).power_line


def test_admin_power_limits_orders(game):
    island = make_island(game)
    orders = [("build", {"facility": "farm", "x": 6 + i, "y": 6}) for i in range(3)]
    run(game, island, ["execute_orders"], orders=orders)  # 中央省庁がなければ行政力 1
    assert [o.status for o in island.orders] == [ORDER_DONE, "pending", "pending"]
    assert island.log[-1].kind == "order_postponed"


@pytest.mark.parametrize(("workers", "power"), [(0, 1), (999, 1), (1000, 1), (3000, 3), (9000, 5)])
def test_admin_power_from_civil_servants(game, workers, power):
    from hakoniwa_core.modules.planning import admin_power

    island = make_island(game)
    add(island, "government_office", 5, 5, workers=workers)
    assert admin_power(island, SimpleNamespace(defs=game.defs, config=game.config)) == power


def test_rename_place(game):
    from hakoniwa_core.naming import place_of

    island = make_island(game)
    island.ext["places"] = [f"地{i}" for i in range(16)]
    island.regime["economy"] = "market"  # 計画経済の自動の名前には地名が入らない
    farm = add(island, "farm", 7, 7)
    run(
        game,
        island,
        ["execute_orders"],
        orders=[("rename_place", {"x": 6, "y": 6, "name": "緑野"})],
    )
    assert island.orders[0].status == ORDER_DONE
    assert place_of(island, 7, 7, game.config) == "緑野"  # 同じ区画（8 マス四方）
    assert place_of(island, 9, 9, game.config) != "緑野"
    assert "緑野" in facility_name(game.defs, game.config, island, farm)


def test_disaster_halts_a_facility(game, game_dir):
    from hakoniwa_core.game import load_game as load

    path = game_dir()
    text = (path / "game.yaml").read_text(encoding="utf-8")
    (path / "game.yaml").write_text(text.replace("rate: 0.02 ", "rate: 1.0 "), encoding="utf-8")
    sure = load(path / "game.yaml")
    island = make_island(sure)
    farm = add(island, "farm", 7, 7)
    run(sure, island, ["events"])
    assert farm.ext["halted_until"] >= 1
    assert island.log[0].kind.startswith("disaster_")
    assert fac.labor_demand(sure.defs, farm, 1) == 0
