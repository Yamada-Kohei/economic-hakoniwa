"""フェーズ3-2「品目と自然条件」: 品目と作り方、気候帯と肥沃度、必需品とぜいたく品、自動車と
トラック、作り方の変更、移し替え、品不足の知らせ、関門の品目の確かめ。

ターンを回す試験では、記録の段が毎ターン帳簿を確かめる（合わなければその場で止まる）。
"""

from __future__ import annotations

import copy
import random

import pytest

from hakoniwa_core import autoplan, nature, stock
from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import needs, services, trucks
from hakoniwa_core.economy.prices import initial_prices, material_limit, unit_floor
from hakoniwa_core.gate import Recorder
from hakoniwa_core.market import auction
from hakoniwa_core.modules import records
from hakoniwa_core.modules.firms import EntryExit
from hakoniwa_core.modules.production import check_change_recipe
from hakoniwa_core.modules.upgrade import Upgrade
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE, Facility, MarketRecord
from hakoniwa_core.state.generate import _weighted_sample, generate_island
from hakoniwa_core.trade import blocs, island_state
from hakoniwa_core.trade import traders as trade_traders
from hakoniwa_core.units import QUANTITY_SCALE, round_money, scale_price
from tests.core.test_finance import context, island_of, order
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401

NEW_GOODS = [
    "crude_oil",
    "bauxite",
    "cotton",
    "luxury_crop",
    "fuel",
    "chemicals",
    "aluminium",
    "meat",
    "clothing",
    "automobile",
]

# --- 品目と作り方 -------------------------------------------------------------------


def test_there_are_24_goods_and_every_good_has_a_maker(game):
    defs = game.defs
    # 回4 でサービス、回7 で小売り、回7 後半で自動車部品、フェーズ4-2 で銅・電子機器・兵器の 9 品目
    assert len(defs.goods) == 35
    assert set(NEW_GOODS) <= set(defs.goods)
    made = {
        g
        for fdef in defs.facilities.values()
        for recipe in fdef["recipes"].values()
        for g in recipe["outputs"]
    }
    made.add(services.good_of(game.config))  # 商業・サービス業が働く人の数だけ出す（作り方はない）
    assert set(defs.goods) <= made  # どの財も、どこかの施設の作り方で作れる


def test_every_household_good_has_a_need(game):
    """家計が買う財（consumer）はどれも、必需品・ぜいたく品・耐久消費財・サービス・小売りのどれかに
    分けてある（燃料は車を動かすのに買うので、種類を持たない）。"""
    defs = game.defs
    kinds = {needs.NECESSITY, needs.LUXURY, needs.DURABLE, needs.SERVICE, needs.RETAIL}
    for good, gdef in defs.goods.items():
        if gdef.get("consumer") and good != "fuel":
            assert gdef.get("need") in kinds, good
    assert needs.goods_of(defs, needs.DURABLE) == ["automobile"]
    assert set(needs.goods_of(defs, needs.LUXURY)) == {"consumer_goods", "luxury_crop"}
    assert needs.goods_of(defs, needs.SERVICE) == ["services"]
    assert needs.goods_of(defs, needs.RETAIL) == ["retail"]


def test_joint_products_split_their_cost_by_the_world_prices(game):
    """製油所のように作る物が2つあるときは、変動費を初めの値段で割り振る（今の値段で割り振ると、
    余った物の値段が下がるほどその物の取り分が減り、値段が 0 に向かった）。"""
    defs, config = game.defs, game.config
    f = Facility(id=1, kind="refinery", x=0, y=0)
    prices = dict(initial_prices(defs, config))
    before = unit_floor(defs, config, f, "fuel", prices, 1000, 0)
    prices["chemicals"] *= 10  # 化学品の今の値段が上がっても
    prices["fuel"] //= 10  # 燃料の今の値段が下がっても
    assert unit_floor(defs, config, f, "fuel", prices, 1000, 0) == before
    assert before > 0
    assert unit_floor(defs, config, f, "chemicals", prices, 1000, 0) > 0


# --- 気候帯と肥沃度 -----------------------------------------------------------------


def test_world_rows_split_into_cold_temperate_and_tropical(game):
    config = game.config
    zones = [nature.zone_of(wy, 10, config) for wy in range(10)]
    assert zones[0] == "cold" and zones[-1] == "tropical"
    assert zones == sorted(zones, key=["cold", "temperate", "tropical"].index)
    assert {z: zones.count(z) for z in set(zones)} == {"cold": 3, "temperate": 4, "tropical": 3}


def test_climate_decides_which_recipes_an_island_can_use(game, warmed):
    defs = game.defs
    cold = next(i for i in warmed.islands.values() if nature.climate_of(i) == "cold")
    hot = next(i for i in warmed.islands.values() if nature.climate_of(i) == "tropical")
    problem = fac.recipe_problem(defs, cold, "farm", "cotton")
    assert problem is not None and problem[0] == "reason_climate"
    assert fac.recipe_problem(defs, cold, "farm", "luxury_crop")[0] == "reason_climate"
    assert fac.recipe_problem(defs, hot, "farm", "cotton") is None
    assert fac.recipe_problem(defs, cold, "farm", "default") is None
    farm = defs.facilities["farm"]["recipes"]["default"]
    assert nature.recipe_multiplier(defs, game.config, cold, farm) == pytest.approx(0.7)
    assert nature.household_power(defs, cold) == pytest.approx(1.3)
    assert nature.household_power(defs, hot) == pytest.approx(1.0)


def test_fertility_is_spread_on_plains_in_patches(game):
    config = game.config
    low, high = (round(v * 1000) for v in config.get("climate.fertility"))
    island = generate_island(random.Random(5), 1, "島", 0, 0, config)
    nature.spread_fertility(island, config, seed=5)
    plains = [t for t in island.tiles if t.terrain == "plain"]
    values = [nature.fertility(t) for t in plains]
    assert plains and all(low <= v <= high for v in values)
    assert max(values) - min(values) > (high - low) // 3  # 島の中で差がある
    assert all(nature.FERTILITY not in t.ext for t in island.tiles if t.terrain != "plain")
    again = generate_island(random.Random(5), 1, "島", 0, 0, config)
    nature.spread_fertility(again, config, seed=5)
    assert [nature.fertility(t) for t in again.tiles] == [nature.fertility(t) for t in island.tiles]
    # 隣り合うマスは似た値（まとまりになる）
    by_pos = {(t.x, t.y): nature.fertility(t) for t in plains}
    steps = [abs(v - by_pos[(x + 1, y)]) for (x, y), v in by_pos.items() if (x + 1, y) in by_pos]
    assert sum(steps) / len(steps) < (high - low) / 5


def test_pollution_wears_fertility_down_to_the_floor(game):
    config = game.config
    island = generate_island(random.Random(5), 1, "島", 0, 0, config)
    tile = next(t for t in island.tiles if t.terrain == "plain")
    tile.ext[nature.FERTILITY] = 520
    tile.pollution = 3
    for _ in range(11):
        nature.wear_fertility(island, config)
    assert tile.ext[nature.FERTILITY] == 520  # 1年たまるまでは下がらない
    nature.wear_fertility(island, config)
    assert tile.ext[nature.FERTILITY] == 500  # 0.02 下がり、下限 0.5 で止まる
    for _ in range(12):
        nature.wear_fertility(island, config)
    assert tile.ext[nature.FERTILITY] == 500


# --- 移し替え -----------------------------------------------------------------------


def test_upgrade_gives_old_islands_a_climate_and_blends_it_in(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    zone = nature.climate_of(island)
    island.ext.pop(nature.CLIMATE)
    island.economy.ext.pop("nature")
    Upgrade().run_island(island, ctx)
    assert nature.climate_of(island) == zone  # 世界マップの区画から決める
    turns = game.config.get("climate.blend_turns")
    assert island.ext[nature.BLEND] == turns
    assert island.economy.ext["happiness_from"] == "happiness_phase3_1"
    assert any(e.kind == "nature_started" for e in island.log)
    farm = game.defs.facilities["farm"]["recipes"]["default"]
    target = nature.output_multiplier(game.defs, island, "grain")
    assert nature.recipe_multiplier(game.defs, game.config, island, farm) == pytest.approx(1.0)
    for _ in range(turns // 2):
        Upgrade().run_island(island, ctx)
    half = nature.recipe_multiplier(game.defs, game.config, island, farm)
    assert half == pytest.approx(1 + (target - 1) / 2)
    for _ in range(turns):
        Upgrade().run_island(island, ctx)
    assert nature.BLEND not in island.ext
    assert nature.recipe_multiplier(game.defs, game.config, island, farm) == pytest.approx(target)
    Upgrade().run_island(island, ctx)  # 2度は置かない
    assert sum(e.kind == "nature_started" for e in island.log) == 1


def test_upgrade_prices_new_goods_at_the_island_price_level(game, warmed):
    """前のフェーズから続く島には新しい品目の値段がない。値段がないと 1 と見て、値幅が 1 のまま
    動かなかったので、島の初めの値段に今の物価指数の倍率を掛けた値段を置く。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    economy = island.economy
    for good in NEW_GOODS:
        economy.prices.pop(good)
        economy.price_history.pop(good, None)
    economy.cpi_history = [*economy.cpi_history, 80.0]
    Upgrade().run_island(island, ctx)
    base = initial_prices(game.defs, game.config, island.resources)
    for good in NEW_GOODS:
        assert economy.prices[good] == round_money(base[good] * 0.8)
        assert economy.price_history[good] == [economy.prices[good]]
    grain = economy.prices["grain"]
    Upgrade().run_island(island, ctx)  # 値段のある財は変えない
    assert economy.prices["grain"] == grain


# --- 必需品とぜいたく品 --------------------------------------------------------------


def test_necessity_share_falls_as_real_income_grows(game):
    config = game.config
    assert needs.necessity_share(config, 1.0) == pytest.approx(0.4)
    assert needs.necessity_share(config, 2.0) == pytest.approx(0.36)
    assert needs.necessity_share(config, 0.5) == pytest.approx(0.4)  # 上限は share
    assert needs.necessity_share(config, 64.0) == pytest.approx(0.3)  # 下限 floor
    assert needs.necessity_share(config, 0.0) == pytest.approx(0.4)


def test_luxury_budget_moves_past_goods_that_are_not_on_sale():
    weights = {"consumer_goods": 3.0, "luxury_crop": 1.0}
    # 両方とも十分に売りに出ていれば、重みのとおり
    assert needs.split(4000, weights, {"consumer_goods": 10**9, "luxury_crop": 10**9}) == {
        "consumer_goods": 3000,
        "luxury_crop": 1000,
    }
    # 嗜好品が 200 の分しか売りに出ていなければ、残りは消費財に回る
    got = needs.split(4000, weights, {"consumer_goods": 10**9, "luxury_crop": 200})
    assert got == {"consumer_goods": 3800, "luxury_crop": 200}
    # どちらも上限に届いたら、残りは重みで足す（買えない分は買いたい量として市場に残る）
    got = needs.split(4000, weights, {"consumer_goods": 1000, "luxury_crop": 200})
    assert got["consumer_goods"] == 1000 + 2100 and got["luxury_crop"] == 200 + 700


def test_cars_are_wanted_by_income_and_bought_a_little_at_a_time(game):
    config = game.config
    assert needs.desired_cars(config, "general", 1000, 1.0) == 0  # base_income の 1.5 倍から
    assert needs.desired_cars(config, "general", 1000, 3.0) == 200 * QUANTITY_SCALE
    assert needs.desired_cars(config, "general", 1000, 2.25) == 100 * QUANTITY_SCALE
    assert needs.desired_cars(config, "owners", 10, 0.5) == 2500
    wear = needs.wear_per_turn(config, 120_000)
    assert wear == 1000  # 年 10% が傷む
    # 欲しい台数との差の 1 割と、傷む分
    assert needs.cars_wanted(config, 120_000, 220_000) == 10_000 + wear
    assert needs.cars_wanted(config, 120_000, 100_000) == wear
    assert needs.car_fuel(config, 100_000) == {"fuel": 100}


# --- トラック -----------------------------------------------------------------------


def _transport(island):
    return next(f for f in island.facilities if f.kind == "transport")


def test_transport_buys_trucks_when_they_cost_less_than_drivers(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    config = game.config
    f = _transport(island)
    f.economy["wage"] = 1000
    f.economy["wanted"] = 120
    f.economy.pop(trucks.TRUCKS, None)
    island.economy.prices["automobile"] = 30_000
    island.economy.prices["fuel"] = 200_000
    assert trucks.pays(island, config, 1000)
    wanted = trucks.orders(island, config, f)
    # 欲しいのは 120 人 × 半分 ÷ 3 人 = 20 台、その 1 割
    assert wanted == {"automobile": 2000}
    island.economy.prices["automobile"] = 3_000_000  # 高ければ運転手を雇う
    assert not trucks.pays(island, config, 1000)
    assert "automobile" not in trucks.orders(island, config, f)


def test_trucks_replace_drivers_only_while_they_have_fuel(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    config = game.config
    f = _transport(island)
    f.economy.pop(trucks.TRUCKS, None)
    stock.add(f.stock, "automobile", 10 * QUANTITY_SCALE, world.turn, game.defs.goods)
    capital = f.economy.get("capital", 0)
    assert trucks.commission(island, config, f) == 10 * QUANTITY_SCALE
    assert trucks.owned(f) == 10 * QUANTITY_SCALE
    assert stock.total(f.stock, "automobile") == 0
    assert f.economy["capital"] > capital  # 設備の額に足す
    # 燃料がなければ動かない
    for good in config.get("freight.trucks.fuel"):
        stock.take(f.stock, good, stock.total(f.stock, good))
    assert trucks.drive(island, config, f, 100) == 0
    # 4 台分の燃料: 4 台が 12 人分の運転手を替える
    stock.add(f.stock, "fuel", 4 * 5, world.turn, game.defs.goods)
    assert trucks.drive(island, config, f, 100) == 12
    assert f.economy[trucks.WORKING] == 4 * QUANTITY_SCALE
    assert stock.total(f.stock, "fuel") == 0
    # 替えられるのは雇いたい運転手の半分まで
    stock.add(f.stock, "fuel", 100, world.turn, game.defs.goods)
    assert trucks.drive(island, config, f, 12) == 6


# --- 作り方の変更 -------------------------------------------------------------------


def test_change_recipe_is_for_state_facilities_and_usable_recipes(game, warmed):
    defs = game.defs
    world = copy.deepcopy(warmed)
    hot = next(i for i in world.islands.values() if nature.climate_of(i) == "tropical")
    farm = next(f for f in hot.facilities if f.kind == "farm" and f.recipe == "default")
    farm.owner = OWNER_STATE
    check_change_recipe(hot, {"x": farm.x, "y": farm.y, "recipe": "cotton"}, defs)
    with pytest.raises(OrderFailed) as err:
        check_change_recipe(hot, {"x": farm.x, "y": farm.y, "recipe": "default"}, defs)
    assert err.value.reason == "reason_recipe_unchanged"
    with pytest.raises(OrderFailed) as err:
        check_change_recipe(hot, {"x": farm.x, "y": farm.y, "recipe": "rice"}, defs)
    assert err.value.reason == "reason_unknown_recipe"
    farm.owner = OWNER_PRIVATE  # 民営は 12 ターンごとに自分で見直す
    with pytest.raises(OrderFailed) as err:
        check_change_recipe(hot, {"x": farm.x, "y": farm.y, "recipe": "cotton"}, defs)
    assert err.value.reason == "reason_not_state_owned"
    cold = island_of(world, "market")  # 寒冷の島では綿を作れない
    farm = next(f for f in cold.facilities if f.kind == "farm")
    farm.owner = OWNER_STATE
    with pytest.raises(OrderFailed) as err:
        check_change_recipe(cold, {"x": farm.x, "y": farm.y, "recipe": "cotton"}, defs)
    assert err.value.reason == "reason_climate"


def test_a_mine_cannot_switch_to_another_deposit(game, warmed):
    """鉱山は掘る鉱床のマスに建つので、掘る物は替えられない（替えられると、値段の高い方へ掘る物を
    替え合って、石炭と鉄鉱石の品不足が行ったり来たりした）。"""
    defs = game.defs
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    island.resources = ["coal", "iron_ore"]
    mine = Facility(id=999, kind="mine", x=0, y=0, recipe="coal", owner=OWNER_PRIVATE, built=True)
    problem = fac.switch_problem(defs, island, mine, "iron_ore")
    assert problem is not None and problem[0] == "reason_recipe_other_resource"
    farm = Facility(id=998, kind="farm", x=0, y=0, owner=OWNER_PRIVATE, built=True)
    assert fac.switch_problem(defs, island, farm, "fertilized") is None


def test_change_recipe_switches_at_the_market_and_halves_the_next_turn(game, warmed):
    """畑でない施設（火力発電所）は、次の市場の段で一度に替え、次のターンは稼働率が半分。"""
    KIND = "thermal_power_plant"
    world = copy.deepcopy(warmed)
    island = next(
        i for i in world.islands.values() if any(f.kind == KIND and f.built for f in i.facilities)
    )
    plant = next(f for f in island.facilities if f.kind == KIND and f.built)
    plant.owner = OWNER_STATE
    other = next(
        name
        for name in game.defs.facilities[plant.kind]["recipes"]
        if name != plant.recipe and fac.switch_problem(game.defs, island, plant, name) is None
    )
    payload = {"x": plant.x, "y": plant.y, "recipe": other}
    state = order(game, world, island, "change_recipe", payload)
    assert state.status == "done", state.reason
    assert plant.recipe == other
    assert plant.ext[fac.RECIPE_CHANGED] == world.turn + 1
    assert any(e.kind == "recipe_changed" for e in island.log)
    island.orders = []
    game.engine.run_turn(world)
    assert plant.operating_rate <= fac.FULL // 2
    if plant.operating_rate == fac.FULL // 2:
        assert plant.status == "recipe_change"


def test_change_recipe_of_a_farm_resows_the_fields_little_by_little(game, warmed):
    """農場の作物を替えると、1ターンに畑の 1/育つ期間 ずつ蒔き直し、蒔いた畑は育つまで何も出さない
    （回3。Codex E17）。稼働率は半分にしない。"""
    world = copy.deepcopy(warmed)
    hot = next(i for i in world.islands.values() if nature.climate_of(i) == "tropical")
    farm = next(f for f in hot.facilities if f.kind == "farm" and f.recipe == "default" and f.built)
    farm.owner = OWNER_STATE
    state = order(game, world, hot, "change_recipe", {"x": farm.x, "y": farm.y, "recipe": "cotton"})
    assert state.status == "done", state.reason
    assert farm.recipe == "cotton"
    assert fac.RECIPE_CHANGED not in farm.ext
    assert farm.economy[fac.FIELDS] == {"default": fac.FULL}
    assert any(e.kind == "recipe_changed" for e in hot.log)
    hot.orders = []
    game.engine.run_turn(world)
    grain = fac.grow_turns(game.defs, "farm", "default")
    cotton = fac.grow_turns(game.defs, "farm", "cotton")
    step = -(-fac.FULL // grain)
    assert farm.economy[fac.FIELDS] == {"default": fac.FULL - step}
    assert farm.economy[fac.SOWN] == [["cotton", step, world.turn + cotton]]
    outputs = fac.recipe_of(game.defs, farm)["outputs"]
    full = game.defs.facilities["farm"]["recipes"]["default"]["outputs"]["grain"]
    assert outputs == {"grain": full * (fac.FULL - step) // fac.FULL}
    assert farm.status != "recipe_change"


def test_private_farm_turns_to_a_new_crop_only_when_it_is_short(game, warmed):
    """民営の作り方の見直しで、今作っていない物（綿）に替えるのは、綿の直近の買いたい量が売りに
    出た量を、新しい作り方の全力の産出の firms.entry_gap 倍以上上回るときだけ（綿の値段が高い
    ターンに民営の農場がそろって綿に替え、綿が余って値段が初めの 0.1 倍まで下がった）。"""
    world = copy.deepcopy(warmed)
    hot = next(i for i in world.islands.values() if nature.climate_of(i) == "tropical")
    farm = next(f for f in hot.facilities if f.kind == "farm" and f.recipe == "default" and f.built)
    ctx = context(game, world)
    prices = dict(hot.economy.prices)
    full = game.defs.facilities["farm"]["recipes"]["cotton"]["outputs"]["cotton"]
    finance = hot.economy.finance
    finance.setdefault("demand", {})["cotton"] = [3 * full] * 6
    finance.setdefault("supply", {})["cotton"] = [3 * full] * 6  # 足りている
    profits = EntryExit._recipe_profits(hot, ctx, farm, prices, set(), 0)
    assert "default" in profits and "cotton" not in profits
    finance["supply"]["cotton"] = [3 * full - full // 2] * 6  # 全力の産出の半分だけ足りない
    profits = EntryExit._recipe_profits(hot, ctx, farm, prices, set(), 0)
    assert "cotton" in profits


# --- 品不足の知らせ -----------------------------------------------------------------


def test_shortage_is_logged_once_after_three_turns(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    config = game.config
    island.economy.ext.pop(records.SHORTAGE, None)
    island.markets = [MarketRecord("clothing", 100, 50, 100, 50)]
    island.log.clear()
    for _ in range(5):
        records._shortages(island, config)
    logged = [e for e in island.log if e.kind == "shortage_continued"]
    assert len(logged) == 1
    assert logged[0].args == {"good": "clothing", "turns": 3, "share": 50}
    assert island.economy.ext[records.SHORTAGE] == {"clothing": 5}
    island.markets = [MarketRecord("clothing", 100, 95, 100, 95)]  # 9 割 5 分買えれば品不足でない
    records._shortages(island, config)
    assert records.SHORTAGE not in island.economy.ext


# --- 貿易: 輸入が引き合う値段と標準の計画 ----------------------------------------------


def test_bloc_view_lists_goods_on_sale_and_goods_short():
    world = type("W", (), {})()
    world.islands = {
        1: type("I", (), {"wx": 0, "wy": 0})(),
        2: type("I", (), {"wx": 3, "wy": 0})(),
    }
    bloc = {
        "name": "圏",
        "currency": "1",
        "members": [1, 2],
        "rep": 1,
        "prices": {"steel": 100, "automobile": 900},
        "supply": {"steel": 5000, "fuel": 0},
        "demand": {"steel": 3000, "automobile": 200},
        "volume": {"steel": 3000},
    }
    view = blocs.view_of(world, bloc, 1)
    assert view["supply"] == ["steel"]
    assert view["short"] == ["automobile"]


def test_import_floor_only_for_goods_on_sale_in_the_bloc(game, warmed):
    """圏の市場に売りがなかった財には、輸入が引き合う値段を付けない（誰も作っていない自動車で、
    島の値段と圏の買いの指値が互いを追いかけて上がり続けた）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    view = {
        "name": "圏",
        "currency": island.id,
        "members": 2,
        "rep": island.id,
        "cells": 2,
        "prices": {"automobile": 50_000, "steel": 5_000},
        "supply": ["steel"],
        "short": ["automobile"],
    }
    island_state(island)["blocs"] = {"1": view}
    got = trade_traders.prospects(island, game.defs, game.config)
    assert got["steel"].import_floor > 0
    assert got["automobile"].import_floor == 0


# --- 一括シミュレーションと関門 ------------------------------------------------------


def test_standard_plan_builds_necessities_then_luxuries_then_cars(game):
    """標準の計画は、家計の買う物の施設を必需品、ぜいたく品（重い順）、自動車、燃料の順に建てる
    （名前の順では自動車工場が先に建ち、その工事で公共事業費が尽きて、どの工事も止まった）。"""
    defs, config = game.defs, game.config
    order = autoplan._consumer_order(defs, config)
    rank = {g: i for i, g in enumerate(order)}
    necessities = [g for g in order if defs.goods[g].get("need") == needs.NECESSITY]
    assert max(rank[g] for g in necessities) < rank["consumer_goods"] < rank["luxury_crop"]
    assert rank["luxury_crop"] < rank["automobile"] < rank["fuel"]
    # 自動車工場は、投入財（電力を除く）がどれも島で作っているか貿易で手に入るときだけ
    inputs = set(defs.facilities["automobile_plant"]["recipes"]["default"]["inputs"])
    inputs.discard(config.get("power.good"))
    assert autoplan._inputs_at_hand(defs, config, "automobile_plant", "default", inputs)
    some = set(sorted(inputs)[1:])
    assert not autoplan._inputs_at_hand(defs, config, "automobile_plant", "default", some)


def test_construction_bids_stop_at_the_top_household_tier(game, warmed):
    """工事とインフラの資材の指値は、値幅の上端と、いつもの値段（12 ターンの平均）の一番上の段の
    安い方（値幅の上端で買い続けると、品薄の島で機械と鋼鉄が初めの数十〜数百倍になった）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    config = game.config
    top = max(config.get("household.consumer_goods_tiers"))
    island.economy.prices["steel"] = 2000
    island.economy.price_history["steel"] = [1000] * 12
    assert material_limit(island, config, "steel", 1000) == scale_price(1000, top)
    island.economy.price_history["steel"] = [4000] * 12  # 値段が下がってきたときは値幅の上端
    assert material_limit(island, config, "steel", 1000) == auction.band(2000, 1000)[1]


def test_sample_resources_are_weighted_and_distinct():
    rng = random.Random(3)
    picks = [tuple(_weighted_sample(rng, ["a", "b", "c"], [100, 1, 1], 2)) for _ in range(200)]
    assert all(len(set(p)) == 2 for p in picks)
    assert sum("a" in p for p in picks) > 190
    assert _weighted_sample(rng, ["a", "b"], [1, 1], 5) in (["a", "b"], ["b", "a"])


def test_gate_counts_islands_making_and_trading_each_good(game, warmed):
    world = copy.deepcopy(warmed)
    recorder = Recorder(world, game.defs, game.config)
    recorder.record(world)
    goods = recorder.goods(world.turn)
    assert set(goods) == set(game.defs.goods)
    made, traded = goods["grain"]
    assert made >= 1 and traded >= 1
    assert goods["automobile"][0] == 0  # 3 島のどこでも作っていない
    failures = recorder.failures(world.turn)
    assert "世界: automobile がどの島でも作られていない" in failures
    assert not any("grain" in line for line in failures if line.startswith("世界"))
    assert recorder.goods(world.turn + 1) == {}
