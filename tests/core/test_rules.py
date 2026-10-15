"""段と命令のルールを、小さな手作りの島で確かめる。

お金の経済を使う段（雇用・生産・市場など）は、with_economy で経済を始めた島で確かめる。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.allocation import by_priority, proportional
from hakoniwa_core.economy import BONDS, GENERAL, Payer, debt, freight, funds, household
from hakoniwa_core.economy.households import set_workers
from hakoniwa_core.economy.prices import initial_prices, material_limit, variable_profit
from hakoniwa_core.economy.start import init_facility, start_economy
from hakoniwa_core.game import load_game
from hakoniwa_core.log import format_log
from hakoniwa_core.market import auction
from hakoniwa_core.modules.market import Market, gross_cost
from hakoniwa_core.naming import facility_name
from hakoniwa_core.orders import check_order
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
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


def with_economy(game, island, groundwork=False) -> IslandState:
    """置いた施設と住民で、お金の経済を始める（首都の近くに銀行、マスを持たない運送業が増える）。

    groundwork が False なら、フェーズ2-3 の基盤のうち学校と技術者を外す（雇用と予算の数を
    手作りの島の施設だけで確かめるため）。道路と教育水準は、生産性の係数が 1 になる初めの値のまま。
    """
    start_economy(island, game.defs, game.config, 0)
    if not groundwork:
        school = game.config.get("education.start_school")
        island.facilities = [f for f in island.facilities if f.kind != school]
        set_workers(island, game.defs, fac.ENGINEER, 0)
    return island


def without_roads(island) -> None:
    """マスの交通のインフラを外す（運賃の道のりを1マス1で確かめるため）。"""
    for tile in island.tiles:
        for kind in ("road", "rail"):
            tile.infra.pop(kind, None)
    freight.forget(island)  # 置いたときに計算したマスの重さを捨てる


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
    with_economy(game, island, groundwork=True)  # 機械工場の技術者がそろっている
    run(game, island, ["employment", "production"])
    assert plant.operating_rate == 100
    assert island.stats["power.capacity"] == 3
    # 足りない電力は優先順位の高い鉱山に先に回り、機械工場は残りの分だけ動く
    assert (high.operating_rate, high.status) == (1000, "")
    assert (low.operating_rate, low.status) == (250, "power")
    assert cut_off.workers > 0
    # 発電所のない送電網の施設は、電力不足ではなく「送電網につながっていない」
    assert (cut_off.operating_rate, cut_off.status) == (0, "no_grid")
    assert format_log(game.defs, "status_no_grid", {}) == "送電網につながっていません"
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


def test_construction_without_workers_is_logged_once(game):
    """資材があるのに通勤圏に住民がいない工事は、2ターン続いたらログで知らせる（その後は出さない）。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    site = add(island, "farm", 12, 12, built=False, stock={"wood": 5000})  # 首都の町から遠い
    with_economy(game, island)
    kinds = []
    for _ in range(3):
        run(game, island, ["employment", "construction"])
        kinds.append([e.kind for e in island.log])
    assert site.progress == 0 and site.ext["no_workers_turns"] == 3
    assert kinds == [[], ["construction_no_workers"], []]
    # 通勤圏に、建設の労働者 200 人を出せる町ができると工事が進み、数え直しになる
    labor, _ = fac.commute_labor(game.defs, game.config, island, "farm", 12, 12)
    assert labor == 0
    home(game, island, 11, 12, 2000)
    assert fac.commute_labor(game.defs, game.config, island, "farm", 12, 12)[0] > 0
    run(game, island, ["employment", "construction"])
    assert site.progress > 0 and "no_workers_turns" not in site.ext


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
    # 人手の足りない施設（原料のない間は人を雇わないので、原料を持たせておく）
    add(island, "steel_mill", 8, 8, stock={"iron_ore": 100_000, "coal": 50_000})
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


def test_producible_goods_follow_resources_and_inputs(game):
    """島で生産できる資源は、地下資源と、原料をつないだ生産方式から決まる。"""
    bare = fac.producible_goods(game.defs, [])
    assert {"grain", "wood", "electricity", "building_materials"} <= bare
    assert not {"stone", "iron_ore", "coal", "steel", "machinery"} & bare
    # 鋼材は鉄鉱石と石炭の両方が要る
    assert "steel" not in fac.producible_goods(game.defs, ["coal"])
    both = fac.producible_goods(game.defs, ["coal", "iron_ore"])
    assert {"steel", "machinery"} <= both and "stone" not in both


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


# --- ターン91の試し遊びで見つかった点 -------------------------------------------------


def test_initial_prices_use_recipes_the_island_can_run(game):
    """石材のない島の建材の初めの値段は、木材から作る原価で決まる（木材の方式の工場が原料を買える）。"""
    defs, config = game.defs, game.config
    everywhere = initial_prices(defs, config)
    no_stone = initial_prices(defs, config, ["coal", "iron_ore"])
    assert no_stone["building_materials"] > everywhere["building_materials"]
    every_resource = ["bauxite", "coal", "crude_oil", "iron_ore", "stone"]
    assert initial_prices(defs, config, every_resource) == everywhere
    plant = Facility(id=1, kind="building_materials_plant", x=0, y=0, recipe="wood")
    wage = config.get("labor.initial_wage")
    assert variable_profit(defs, config, plant, everywhere, wage, 0) < 0
    assert variable_profit(defs, config, plant, no_stone, wage, 0) > 0
    # 島に石材がなくても作れない財（鉱石など）は、どの島でも同じ値段
    assert initial_prices(defs, config, [])["coal"] == everywhere["coal"]


def test_facility_hires_only_for_inputs_on_hand(game):
    """原料が届くまで人を雇わない。足りない理由（市場で買えなかったわけ）が状態に出る。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    plant = add(island, "building_materials_plant", 6, 6, recipe="wood")
    add(island, "hydro_power_plant", 7, 6)  # 送電網でつながった発電所（原料がいらない）
    with_economy(game, island)
    run(game, island, ["employment", "production"])
    assert plant.workers == 0
    assert (plant.operating_rate, plant.status) == (0, "input:wood")
    # 半分の原料があれば、半分だけ雇って動く
    stock.add(plant.stock, "wood", 6000, 0, game.defs.goods)
    plant.economy["short"] = {"wood": "price"}
    run(game, island, ["employment", "production"])
    assert plant.workers == 500
    assert (plant.operating_rate, plant.status) == (500, "input_price:wood")
    plant.economy["short"] = {"wood": "funds"}
    stock.add(plant.stock, "wood", 3000, 0, game.defs.goods)
    run(game, island, ["employment", "production"])
    assert (plant.operating_rate, plant.status) == (250, "funds:wood")
    assert format_log(game.defs, "status_input_price", {}) == "原料が割高"


def test_inputs_are_bought_ahead_of_a_rising_target(game):
    """操業目標は市場の後に上がるので、上がる分（max_target_change）まで先に仕入れる。
    雇いたいだけ雇えなかった施設は、今の人手より少し多い分まで。"""
    island = make_island(game)
    plant = add(island, "building_materials_plant", 6, 6, recipe="wood")
    with_economy(game, island)
    plant.status = "target"
    plant.economy.update(target=400, wanted=400)
    plant.workers = 400
    ctx = SimpleNamespace(defs=game.defs, config=game.config, turn=1)
    assert Market._input_needs(ctx, plant, "electricity") == [("wood", 12_000 * 600 // 1000)]
    plant.workers = 200  # 400 人雇いたかったが 200 人しか来なかった
    assert Market._input_needs(ctx, plant, "electricity") == [("wood", 12_000 * 300 // 1000)]


def test_market_records_why_inputs_were_not_bought(game):
    """原料を買えなかったわけ: 指値（採算の合う値段）より高いと price、売りがないと supply。

    計画経済の島の市場は公定価格で動き、工場は値段で買い控えないので、混合経済の島で確かめる。"""
    island = make_island(game)
    island.regime["economy"] = "mixed"
    home(game, island, 5, 6, 6000)
    plant = add(island, "building_materials_plant", 6, 6, recipe="wood")
    camp = add(island, "lumber_camp", 7, 7, stock={"wood": 20_000})
    with_economy(game, island)
    # 建材が石材から作る原価の値段のままだと、木材から作る工場は木材の値段を払えない
    island.economy.prices["building_materials"] = initial_prices(game.defs, game.config)[
        "building_materials"
    ]
    run(game, island, ["market"])
    assert plant.economy["short"] == {"wood": "price"}
    assert stock.total(plant.stock, "wood") == 0
    # 木材の方式の原価の値段なら買える
    island.economy.prices["building_materials"] = initial_prices(
        game.defs, game.config, island.resources
    )["building_materials"]
    run(game, island, ["market"])
    assert "short" not in plant.economy
    assert stock.total(plant.stock, "wood") > 0
    # 売りに出ていなければ supply
    camp.stock.clear()
    plant.stock.clear()
    run(game, island, ["market"])
    assert plant.economy["short"] == {"wood": "supply"}


def test_construction_waiting_for_materials_is_shown_and_logged_once(game):
    """資材が手に入らない工事は、足りない資材を状態に出し、2ターン続いたらログで知らせる。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    site = add(island, "farm", 6, 6, built=False)  # 木材 5 が要る
    with_economy(game, island)
    kinds = []
    for _ in range(3):
        run(game, island, ["employment", "construction", "market"])
        kinds.append([e.kind for e in island.log if e.kind.startswith("construction")])
    assert site.progress == 0 and site.ext["waiting"] == ["wood"]
    assert kinds == [[], ["construction_no_materials"], []]
    text = format_log(
        game.defs,
        "construction_no_materials",
        {"facility": "farm", "x": 6, "y": 6, "goods": ["wood"]},
    )
    assert text == "(6, 6) の農場の工事は、資材（木材）が手に入らないため進んでいません"
    assert format_log(
        game.defs, "status_construction_materials", {"goods": ["wood", "machinery"]}
    ) == ("建設中（資材の木材・機械を待っています）")
    # 資材が届けば進み、待っている資材は消える
    stock.add(island.warehouse, "wood", 5000, 0, game.defs.goods)
    run(game, island, ["employment", "construction", "market"], turns=2)
    assert site.progress > 0
    assert "waiting" not in site.ext and "no_materials_turns" not in site.ext


def test_sites_are_funded_by_priority_and_show_why_they_wait(game):
    """公共事業費が足りないときは、優先度の高い工事から資材を注文する（番号の順ではない）。
    注文しきれなかった工事は、お金が足りないことを状態とログに出す。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    seller = add(island, "building_materials_plant", 8, 8, recipe="wood")
    seller.accounting = ACCOUNTING_SELF  # 原料の代金を国庫から取り置かない
    stock.add(seller.stock, "building_materials", 20_000, 0, game.defs.goods)
    first = add(island, "food_factory", 6, 6, built=False)  # 建材 10 を 3 ターンで
    urgent = add(island, "food_factory", 7, 6, built=False, priority=9)
    with_economy(game, island)
    without_roads(island)  # 道路の維持の資材が、先に公共事業費を使わないように
    # 国庫には、優先度の高い工事の1ターン分の建材を、指値（値幅の上端といつもの値段の段の安い方）と
    # 運賃で買えるだけ残す
    step = fac.step_materials(game.defs, urgent)["building_materials"]
    limit = material_limit(island, game.config, "building_materials", 1000)
    rate = freight.compute(island, game.defs, game.config)[0][urgent.id]
    gov = ledger.money(island, ledger.GOV)
    ledger.pay(
        island, ledger.GOV, household(GENERAL), gov - gross_cost(limit, step, rate, 0), "test"
    )
    run(game, island, ["market"])
    assert stock.total(urgent.stock, "building_materials") == step
    assert stock.total(first.stock, "building_materials") == 0
    assert first.ext["short_funds"] and "short_funds" not in urgent.ext
    run(game, island, ["construction"], turns=2)
    assert first.ext["waiting"] == ["building_materials"]
    funds = [e.args for e in island.log if e.kind == "construction_no_funds"]
    assert [(a["x"], a["y"], a["goods"]) for a in funds] == [(6, 6, ["building_materials"])]
    assert format_log(
        game.defs, "status_construction_funds", {"goods": ["building_materials"]}
    ) == ("建設中（公共事業費が足りず、資材の建材を買えません）")


def test_urgent_site_materials_come_before_other_sites_wages(game):
    """公共事業費が足りないときは、優先度の高い工事が市場で買う資材の代金を、優先度の低い工事の
    作業員の賃金より先に取り置く。賃金が足りずに雇いきれなかった工事は、そのわけを状態に出す。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    seller = add(island, "building_materials_plant", 8, 8, recipe="wood")
    seller.accounting = ACCOUNTING_SELF  # 原料の代金を国庫から取り置かない
    stock.add(seller.stock, "building_materials", 20_000, 0, game.defs.goods)
    other = add(island, "food_factory", 6, 6, built=False)  # 資材がそろっていて、全員を雇いたい
    urgent = add(island, "food_factory", 7, 6, built=False, priority=9)  # 資材がない
    with_economy(game, island)
    without_roads(island)  # 道路の維持の作業員と資材が、先に公共事業費を使わないように
    for good, qty in fac.step_materials(game.defs, other).items():
        stock.add(other.stock, good, qty, 0, game.defs.goods)
    seller.economy["sales"] = {"building_materials": [20_000, 0, 0]}  # 前のターンに売りに出た量
    # 国庫には、急ぎの工事の1ターン分の建材の代金と、もう一方の工事の作業員の半分の賃金だけ残す
    step = fac.step_materials(game.defs, urgent)["building_materials"]
    limit = material_limit(island, game.config, "building_materials", 1000)
    rate = freight.compute(island, game.defs, game.config)[0][urgent.id]
    labor = game.defs.facilities["food_factory"]["construction"]["labor"]
    wage = island.economy.average_wage or game.config.get("labor.initial_wage")
    keep = gross_cost(limit, step, rate, 0) + wage * (labor // 2)
    gov = ledger.money(island, ledger.GOV)
    ledger.pay(island, ledger.GOV, household(GENERAL), gov - keep, "test")
    run(game, island, ["employment", "construction", "market"])
    assert other.workers == labor // 2
    assert other.ext["short_wages"] and "short_wages" not in urgent.ext
    assert stock.total(urgent.stock, "building_materials") == step
    assert "short_funds" not in urgent.ext
    assert format_log(game.defs, "status_construction_wages", {}) == (
        "建設中（公共事業費が足りず、作業員を雇いきれません）"
    )


def test_maturing_bonds_are_refinanced_in_their_turn(game):
    """満期の国債の額面は、前のターンに発行して国庫に置いておかず、償還のターンに借り換える
    （国債の残高と国庫が、償還の前のターンだけ膨らんで見えないように）。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    base = debt.total_debt(island)  # 建国のときの国債
    face = 50_000_000
    bond = debt.underwrite(island, face, 2, 0, 0)  # ターン 2 に満期（利子なし）
    ledger.pay(island, ledger.GOV, household(GENERAL), face, "test")  # 借りた分は使った
    run(game, island, ["budget", "bonds"])  # ターン 1
    assert debt.total_debt(island) == base + face
    assert not [e for e in island.log if e.kind.startswith("bonds_")]
    # 満期のターン: 予算の段は利払いの分だけ取り置き、国債の段で足りない分を借り換えてから返す
    bond.term = 1
    run(game, island, ["budget"])
    interest = debt.due(island, 1, redemptions=False)
    assert funds.fiscal_state(island)["reserved"][BONDS] == interest
    usable = funds.available(island, Payer(ledger.GOV, BONDS))
    run(game, island, ["budget", "bonds"])
    refinanced = [e.args["amount"] for e in island.log if e.kind == "bonds_refinanced"]
    assert refinanced == [face + interest - usable]
    assert bond not in island.bonds


def test_seller_raises_only_up_to_the_best_unfilled_bid(game):
    """品不足でも、売り手の希望価格は買えなかった買い手の一番高い指値まで上げる（家計は消費財を
    いつもの値段の 1.05 倍までしか買わないので、それを超えて1つも売れないターンを作らない）。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    factory = add(island, "consumer_goods_factory", 6, 7)
    with_economy(game, island)
    island.economy.households[GENERAL]["income"] = [10_000_000] * 3
    usual = island.economy.prices["consumer_goods"]
    island.economy.price_history["consumer_goods"] = [usual] * 12
    volumes = []
    for _ in range(6):
        stock.add(factory.stock, "consumer_goods", 2000, 0, game.defs.goods)
        run(game, island, ["market"])
        record = next(m for m in island.markets if m.good == "consumer_goods")
        assert record.demand > record.supply  # 品不足
        volumes.append(record.volume)
    assert all(v > 0 for v in volumes)


def test_no_dividend_in_a_loss_turn(game):
    """売れずに止まって支払いの見込みが小さい施設でも、赤字のターンは手元の資金を持ち主に払わない。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    mill = add(island, "steel_mill", 6, 6, accounting=ACCOUNTING_SELF)
    with_economy(game, island)
    entity = f"fac:{mill.id}"
    cash = ledger.money(island, entity)
    assert cash > 0
    mill.economy["target"] = 0
    run(game, island, ["accounts"])
    book = next(b for b in island.books if b.facility == mill.id)
    assert book.items["profit"] < 0
    assert book.items.get("dividend", 0) == 0
    assert ledger.money(island, entity) == cash


# --- 運賃 -------------------------------------------------------------------------


def freight_island(game):
    """首都 (5, 5) から遠い (12, 12) の林業場の近くに建材工場、首都のそばに林業場と消費財工場。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    far = add(island, "lumber_camp", 12, 12)
    plant = add(island, "building_materials_plant", 12, 10, recipe="wood")
    near = add(island, "lumber_camp", 5, 7)
    goods = add(island, "consumer_goods_factory", 6, 7, recipe="wood")
    with_economy(game, island)
    without_roads(island)
    return island, far, plant, near, goods


def test_freight_depends_on_distance_to_trading_partners(game):
    """運賃の率は取引の相手までの道のり（1 あたり1%）の半分。拠点から遠くても、近くに相手が
    いれば安い。量の足りない分は、次に近い相手まで運ぶとして数える。"""
    island, far, plant, near, goods = freight_island(game)
    rates, _ = freight.compute(island, game.defs, game.config)
    # 遠くの林業場の木材 10 は、2 マス先の建材工場（12 使う）がみな買える
    assert rates[far.id] == 2 * 100 // 2
    # 首都のそばの林業場は、1 マス先の消費財工場（8 使う）と、10 マス先の建材工場（残りの 2）
    assert rates[near.id] == (8 * 50 + 2 * 500) // 10
    # 陸でつながらない所の施設は、相手にも拠点にも行けないので上限の率
    island.tiles[15 * island.size + 15].terrain = "plain"
    alone = add(island, "lumber_camp", 15, 15)
    rates, _ = freight.compute(island, game.defs, game.config)
    assert rates[alone.id] == 2000
    # 家計の率: 住んでいるマスから、食料と消費財の一番近い売り手まで（売り手のない財は数えない）。
    # (5, 6) から (6, 7) の消費財工場まで 2
    _, household = freight.compute(island, game.defs, game.config)
    assert household == 2 * 100 // 2


def test_market_pairs_nearest_buyers_and_sellers(game):
    """約定は道のりの近い組から割り当てる（遠くの林業場の木材は、近くの建材工場へ）。"""
    island, far, plant, near, goods = freight_island(game)
    stock.add(far.stock, "wood", 10_000, 0, game.defs.goods)
    stock.add(near.stock, "wood", 10_000, 0, game.defs.goods)
    run(game, island, ["market"])
    sellers = {t.seller for t in island.trades if t.good == "wood" and t.buyer == f"fac:{plant.id}"}
    assert f"fac:{far.id}" in sellers
    assert auction.match_nearest([5, 3], [4, 4], lambda b, s: abs(b - s)) == [
        (0, 0, 4),
        (1, 1, 3),
        (0, 1, 1),
    ]


def test_transport_keeps_freight_income_to_hire_drivers(game):
    """独立採算の運送業は、運賃の収入から賃金に充てる分を手元に残し、次のターンに運転手を雇う
    （手元の資金 0 で始まっても、収入をみな配当に回してずっと雇えないままにならない）。"""
    island, *_ = freight_island(game)
    carrier = next(f for f in island.facilities if f.kind == "transport")
    carrier.accounting = ACCOUNTING_SELF
    entity = f"fac:{carrier.id}"
    assert ledger.money(island, entity) == 0
    stages = [s.name for s in game.engine.stages]
    run(game, island, stages)
    payroll = freight.payroll(island, game.config)  # 次のターンの雇用は、このターンの収入で見る
    assert payroll > 0
    assert ledger.money(island, entity) >= payroll
    run(game, island, stages)
    assert carrier.workers == payroll // carrier.economy["wage"] > 0
