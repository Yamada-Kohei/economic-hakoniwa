"""バランス調整 回4: サービスの仕事（商業・サービス業）と人口の増え方（生まれる人と亡くなる人）、
飢饉で島から人がいなくならないこと（品切れの値段・村の畑・町から村への移り住み）。"""

from __future__ import annotations

import copy
import random
from types import SimpleNamespace

import pytest

from hakoniwa_core import autoplan, infra, measure, stock
from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import GENERAL, productivity, services
from hakoniwa_core.economy.prices import variable_profit
from hakoniwa_core.market import auction
from hakoniwa_core.modules import firms
from hakoniwa_core.modules.employment import FIELDS_KEY, _take, back_to_fields
from hakoniwa_core.modules.firms import EntryExit
from hakoniwa_core.modules.market import Market
from hakoniwa_core.modules.population import FED, HUNGER, Population, birth_rate, death_rate
from hakoniwa_core.state import ACCOUNTING_SELF, OWNER_PRIVATE, WorldState
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import traders
from hakoniwa_core.units import to_basis
from tests.core.test_finance import context, island_of, run
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401
from tests.core.test_rules import add, home, make_island, with_economy


def _ctx(game):
    return SimpleNamespace(config=game.config)


# --- 買い方 -------------------------------------------------------------------------------


def test_services_share_rises_with_income(game):
    """サービスに使う割合は、所得が倍になるごとに rise ずつ増え、floor〜max に収まる。"""
    spec = game.config.get("household.services")
    assert services.budget_share(game.config, 1.0) == pytest.approx(spec["share"])
    assert services.budget_share(game.config, 2.0) == pytest.approx(spec["share"] + spec["rise"])
    assert services.budget_share(game.config, 0.5) == pytest.approx(spec["share"] - spec["rise"])
    assert services.budget_share(game.config, 1e6) == spec["max"]
    assert services.budget_share(game.config, 1e-6) == spec["floor"]
    assert services.budget_share(game.config, 0) == spec["floor"]


# --- 店と値段 -----------------------------------------------------------------------------


def test_every_money_island_has_one_services_firm_owned_by_regime(game, warmed):
    for island in warmed.islands.values():
        shops = [f for f in island.facilities if services.is_services(game.config, f)]
        assert len(shops) == 1
        f = shops[0]
        assert (f.x, f.y) == services.SERVICES_POS  # 運送業・海運業と別のマスを持たない位置
        expected = "state" if island.regime["economy"] == "planned" else "private"
        assert f.owner == expected


def test_shops_hire_and_the_price_covers_wages(game, warmed):
    """店は売上に合わせて人を雇い、値段は賃金を賄う（人件費の割合 labor_share の分だけ上乗せ
    された値段の前後）。市場経済の店は、希望価格を上げ下げして売る。"""
    world = copy.deepcopy(warmed)
    run(game, world, 12)
    island = island_of(world, "market")
    f = services.facility_of(island, game.config)
    assert f.workers > 0
    assert island.stats["services.workers"] == f.workers
    assert island.stats["services.sales"] > 0
    price = island.economy.prices["services"]
    wage = f.economy["wage"] / services.settings(game.config)["per_worker"]
    assert 0.95 * wage <= price <= 1.4 * wage
    assert "services" in f.economy.get("ask", {})
    # 店の場所ごとの人数（割り振った人数, 雇えた人数）
    quotas = f.economy[services.QUOTAS]
    assert quotas and sum(hired for _, hired in quotas.values()) <= f.workers
    assert all(hired <= planned for planned, hired in quotas.values())


def test_a_few_hungry_people_do_not_stop_a_whole_layer_buying_services(game, warmed):
    """層の中の少しの人が飢えた（飢えの蓄えをわずかに持つ）だけで、層の全員がサービスを買わなく
    なることはない（飢えた家計の買い上がる指値で取り置いた額を、ほかの買い物から引かない）。"""
    world = copy.deepcopy(warmed)
    run(game, world, 2)
    island = island_of(world, "market")
    calm = copy.deepcopy(world)
    island.economy.households[GENERAL][HUNGER] = 0.002  # 1000 人に数人が飢えた分
    run(game, world, 1)
    run(game, calm, 1)
    sold = island.stats["services.sold"]
    usual = island_of(calm, "market").stats["services.sold"]
    assert sold >= 0.8 * usual > 0


# --- 人口 -----------------------------------------------------------------------------------


def test_births_fall_with_education_and_income(game):
    spec = game.config.get("population.births")
    poor = SimpleNamespace(economy=SimpleNamespace(education=200))
    taught = SimpleNamespace(economy=SimpleNamespace(education=900))
    ctx = _ctx(game)
    assert birth_rate(taught, 1.0, ctx) < birth_rate(poor, 1.0, ctx)
    assert birth_rate(poor, 4.0, ctx) < birth_rate(poor, 1.0, ctx)
    # 学んだ人ばかりで豊かになるほど low に近づき、high を超えない
    assert birth_rate(SimpleNamespace(economy=SimpleNamespace(education=1000)), 9, ctx) == (
        pytest.approx(spec["low"])
    )
    assert birth_rate(SimpleNamespace(economy=SimpleNamespace(education=0)), 0, ctx) <= spec["high"]
    # 目安: 学校に通えた人が6割で建国のころの所得なら、年 2.5〜3.5%
    start = SimpleNamespace(economy=SimpleNamespace(education=600))
    assert 0.025 <= birth_rate(start, 1.0, ctx) <= 0.035


def test_deaths_fall_with_food_and_water_and_crowding_raises_them(game):
    spec = game.config.get("population.deaths")
    ctx = _ctx(game)
    crowd = spec["crowd"]
    town = death_rate(0, 0.5, crowd, ctx)
    assert death_rate(1000, 0.5, crowd, ctx) < town  # 上下水道
    assert death_rate(0, 1.0, crowd, ctx) < town  # 食事の質
    assert death_rate(0, 0.5, crowd // 8, ctx) < town  # 村は町ほど汚れた水で死なない
    assert death_rate(1000, 1.0, crowd, ctx) == pytest.approx(spec["base"])
    # 目安: 上下水道がなく穀物ばかりの農村で年 1.5〜2.2%、同じ暮らしの大きな町で 2.5〜3.5%
    assert 0.015 <= death_rate(0, 0.2, crowd // 8, ctx) <= 0.022
    assert 0.025 <= death_rate(0, 0.2, crowd, ctx) <= 0.035


def test_population_grows_by_births_without_jobs_and_hunger_holds_births(game, warmed):
    """仕事がなくても、食べられていれば人が生まれる。飢えの蓄えが、いちばん弱い人が亡くなり
    始める量に届いた層からは、市場で食料を買う人は生まれない。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    hungry = copy.deepcopy(world)
    reserve = game.config.get("population.hunger")["reserve_min"]
    for h in island_of(hungry, "market").economy.households.values():
        h[HUNGER] = reserve
    run(game, world, 1)
    run(game, hungry, 1)
    born = island.stats["population.births"]
    assert born > 0 and island.stats["population.deaths"] > 0
    assert island_of(hungry, "market").stats["population.births"] < born * 0.6


def test_autoplan_brings_water_to_crowded_towns(game):
    island = make_island(game)
    crowded = game.config.get("population.deaths")["crowd"] // 2
    home(game, island, 5, 6, crowded)
    home(game, island, 9, 9, crowded // 4)
    with_economy(game, island)
    order = autoplan._water_order(island, game.defs, game.config)
    assert order is not None
    kind, payload = order
    assert kind == "build_infrastructure" and payload["tiles"] == [[5, 6]]
    island.tile(5, 6).infra[payload["kind"]] = infra.FULL // 2
    assert autoplan._water_order(island, game.defs, game.config) is None


# --- 測り -----------------------------------------------------------------------------------


def test_measure_counts_services_workers_and_births(game):
    world = generate_world(1, 2, game.config, game.defs)
    meter = measure.Measure(world, game.defs, game.config)
    scenario = measure.SCENARIOS["small"]
    for _ in range(12):
        measure.queue_scenario_orders(world, scenario, game.defs, game.config)
        game.engine.run_turn(world)
        meter.record(world)
    window = meter.window(1, 12)
    assert 0 < window["labor.services"] < 1
    assert window["births.per_1000_year"] > 0 and window["deaths.per_1000_year"] > 0
    assert "population.growth" in window and "services.per_person_year" in window


# --- 飢饉 -----------------------------------------------------------------------------------


def _farm_island(game, recipe="cotton"):
    """村が1つ、町が1つ、民営の農場が1つの島（農場は作り方 recipe で動いている）。"""
    island = make_island(game)
    home(game, island, 4, 4, 1000, "village")
    home(game, island, 9, 9, 4000)
    with_economy(game, island)
    farm = add(island, "farm", 6, 6, recipe=recipe, owner=OWNER_PRIVATE)
    farm.accounting = ACCOUNTING_SELF  # 民営の施設は自分の売上で払う
    return island, farm


def _ctx_of(game, island, turn=0):
    return context(game, WorldState(turn=turn, seed=1, islands={island.id: island}), "test")


def test_missing_grain_price_rises_while_a_farm_could_switch_back(game):
    """島で最後の穀物の農場が綿に替えても、買いたい人がいるのに売りに出ない穀物の値段は、
    作り方を替えれば穀物を作れる農場の変動費（民営なら、綿より利益が大きくなる値段）に届くまで
    上がる（回4。前は今の作り方で作る施設しか見ず、値段が上がらないまま島が飢えた）。"""
    island, farm = _farm_island(game)
    ctx = _ctx_of(game, island)
    prices = island.economy.prices
    prices["grain"] = low = 1000
    floors = Market._supply_floors(island, ctx, "grain", prices, {})
    assert floors and min(floors) > low
    island.scratch["wanted"] = {"grain": 5000}
    Market._record(island, ctx, {}, {}, {})
    assert prices["grain"] == auction.band(low, to_basis(game.config.get("market.band")))[1] > low
    # 閉じようとしている農場は作り方を替えないので、値段の当てにしない
    farm.economy["closing"] = 1
    assert Market._supply_floors(island, ctx, "grain", prices, {}) == []


def test_private_farm_switch_price_beats_its_current_crop(game):
    """民営の農場が穀物に戻る値段は、穀物の変動費の利益が今の作物（綿）より firms.recipe_margin
    の割合だけ大きくなる値段（作り方の見直しと同じ物差し）。"""
    island, farm = _farm_island(game)
    ctx = _ctx_of(game, island)
    prices = dict(island.economy.prices)
    prices["cotton"] *= 4  # 綿がよく売れている
    floor = min(Market._supply_floors(island, ctx, "grain", prices, {}))
    defs, config = game.defs, game.config
    wage = farm.economy.get("wage", 0)

    def grain_profit(price):  # 穀物の作り方（肥料あり・なし）の、変動費の利益の大きいほう
        return max(
            variable_profit(
                defs,
                config,
                productivity.probe(island, defs, config, "farm", name, (farm.x, farm.y)),
                {**prices, "grain": price},
                wage,
                0,
            )
            for name in ("default", "fertilized")
        )

    now = variable_profit(defs, config, farm, prices, wage, 0)
    goal = now * (1 + config.get("firms.recipe_margin"))
    assert grain_profit(floor) >= goal - 1 and now > 0
    assert grain_profit(floor - 2) < goal


def test_villagers_go_back_to_their_fields_when_food_cannot_be_bought(game):
    """前のターンに一般の家計が食べられなかった割合の、村の働き手が畑に戻る。食べ物が買えるように
    なっても、市場に食べ物が出るまでは戻らず、出たら1ターンに labor.back_to_work ずつ戻る。"""
    island, _ = _farm_island(game)
    ctx = SimpleNamespace(defs=game.defs, config=game.config)
    labor = {(4, 4): 500, (9, 9): 2000}
    general = island.economy.households[GENERAL]
    finance = island.economy.finance
    tolerance = game.config.get("population.hunger.tolerance")
    general[FED] = round(fac.FULL * (1 - tolerance))  # 体が耐えられる少しの不足
    assert back_to_fields(island, ctx, labor) == {}
    general[FED] = 600  # 4割が食べられなかった
    finance["supply"] = {"grain": [0] * 6, "food": [0] * 6}
    assert back_to_fields(island, ctx, labor) == {(4, 4): 200}  # 町の働き手は畑を持たない
    assert island.stats["labor.back_to_fields"] == 200
    general[FED] = fac.FULL
    assert back_to_fields(island, ctx, labor) == {(4, 4): 200}  # 食べ物が売りに出ていない
    finance["supply"]["grain"][-1] = 5000
    back = game.config.get("labor.back_to_work")
    assert back_to_fields(island, ctx, labor) == {(4, 4): int(500 * 0.4 * (1 - back))}
    for _ in range(40):
        back_to_fields(island, ctx, labor)
    assert back_to_fields(island, ctx, labor) == {} and FIELDS_KEY not in island.ext


def test_food_makers_hire_people_back_from_their_fields():
    free, fields = {(1, 1): 3}, {(1, 1): 5}
    assert _take(free, fields, (1, 1), 6) == 6
    assert free == {(1, 1): 0} and fields == {(1, 1): 2}
    assert _take(free, {}, (1, 1), 6) == 0  # 食べ物を作らない施設は、畑に戻った人を雇わない


def test_hungry_villagers_feed_themselves_instead_of_working(game, warmed):
    """食べられなかった次のターンには、村の働き手が畑に戻り、自給の農業の人が増える（農場などの
    食べ物を作る施設は、畑に戻った人も雇える）。"""
    world = copy.deepcopy(warmed)
    calm = copy.deepcopy(world)
    island = island_of(world, "market")
    island.economy.households[GENERAL][FED] = 500
    run(game, world, 1)
    run(game, calm, 1)
    usual = island_of(calm, "market").stats
    assert island.stats["labor.back_to_fields"] > 0
    assert island.stats["subsistence"] > usual["subsistence"]
    assert island.stats["employed"] < usual["employed"]
    farms = sum(f.workers for f in island.facilities if f.kind == "farm")
    assert farms >= 0.9 * sum(
        f.workers for f in island_of(calm, "market").facilities if f.kind == "farm"
    )


def test_town_people_flee_to_villages_after_months_of_hunger(game):
    """飢えの蓄えが population.hunger.flee を超えると、食べられなかった割合の町の人が、空きの
    ある近い村へ移る（家の蓄えがあるうちは移らない）。"""
    island = make_island(game)
    home(game, island, 9, 9, 4000)
    home(game, island, 4, 4, 1000, "village")
    home(game, island, 12, 12, 1000, "village")
    with_economy(game, island)
    ctx = SimpleNamespace(defs=game.defs, config=game.config)
    general = island.economy.households[GENERAL]
    general[FED] = 500
    general[HUNGER] = 0.3
    free = {(4, 4): 1400, (12, 12): 1400}
    assert Population._flee_famine(island, ctx, random.Random(1), dict(free)) == 0
    general[HUNGER] = game.config.get("population.hunger")["flee"]
    assert Population._flee_famine(island, ctx, random.Random(1), free) == 2000
    assert island.tile(9, 9).population == 2000
    assert island.tile(4, 4).population + island.tile(12, 12).population == 4000
    assert free[(4, 4)] + free[(12, 12)] == 800
    assert island.stats["population.fled_to_villages"] == 2000
    assert island.log[-1].kind == "famine_flight" and island.log[-1].args == {"count": 2000}


def test_farms_review_their_crop_at_once_when_grain_is_missing(game):
    """島で品切れの財（直近に売りに出ず、買いたい人のいた財）を作れる民営の施設は、見直しの順番を
    待たずに作り方を見直す（作り始めの買いたい量が少なくても、余る心配で避けない）。"""
    island, farm = _farm_island(game)
    finance = island.economy.finance
    finance["supply"] = {"grain": [0] * 6, "cotton": [9000] * 6}
    finance["demand"] = {"grain": [0, 0, 0, 0, 0, 300], "cotton": [9000] * 6}
    assert firms._missing(finance) == {"grain"}
    island.economy.price_history["grain"] = [island.economy.prices["grain"] * 3]
    turn = next(t for t in range(1, 13) if (t + farm.id) % game.config.get("firms.recipe_turns"))
    EntryExit()._review_recipes(island, _ctx_of(game, island, turn - 1))
    assert farm.ext.get(fac.RECIPE_NEXT) in ("default", "fertilized")
    # 穀物が売りに出ていれば、順番まで待つ
    del farm.ext[fac.RECIPE_NEXT]
    finance["supply"]["grain"][-1] = 300
    assert firms._missing(finance) == set()
    EntryExit()._review_recipes(island, _ctx_of(game, island, turn - 1))
    assert fac.RECIPE_NEXT not in farm.ext


def test_spoiled_cargo_leaves_the_trading_company_books(game):
    """商社の荷が傷んで捨てられたら、持っているつもりの量（held）と仕入れ値からも外す。前の版で
    外し忘れた分は、貿易の段の終わりに手元と運ぶ途中の荷に合わせる（持っているつもりの荷が残って
    いて、食べ物の輸入の注文が出ず、向きも替えられなかった）。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    trading = add(island, game.config.get("trade.trader_facility"), 8, 8)
    life = game.defs.goods["grain"]["shelf_life"]
    trading.stock = {"grain": [[0, 4000], [life, 1000]]}  # 古い 4000 が傷む
    item = traders.entry(trading, "grain")
    item["dir"], item["held"] = traders.IMPORT, [5000, 50000]
    Market._spoil(island, SimpleNamespace(turn=life, defs=game.defs, config=game.config))
    assert stock.total(trading.stock, "grain") == 1000
    assert item["held"] == [1000, 10000]
    # 前の版の世界: 傷んだ荷が held に残っている（運ぶ途中の 500 は残す）
    item["held"], item["transit"] = [9000, 90000], 500
    traders.update_targets(island, game.defs, game.config, {}, {})
    assert traders.goods_of(trading)["grain"]["held"] == [1500, 15000]
