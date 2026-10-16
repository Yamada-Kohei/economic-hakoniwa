"""バランス調整 回4: サービスの仕事（商業・サービス業）と人口の増え方（生まれる人と亡くなる人）。"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from hakoniwa_core import autoplan, infra, measure
from hakoniwa_core.economy import GENERAL, services
from hakoniwa_core.modules.population import HUNGER, birth_rate, death_rate
from hakoniwa_core.state.generate import generate_world
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import island_of, run
from tests.core.test_finance import warmed as warmed  # noqa: F401
from tests.core.test_rules import home, make_island, with_economy


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
