"""農場の段階（W-190。FM-50・FM-51）: 手作業 → 機械化 → 近代化。"""

from __future__ import annotations

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.economy import equipment
from hakoniwa_core.game import load_game
from hakoniwa_core.state import OrderState
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    world = generate_world(3, 3, game.config, game.defs)
    game.engine.run_turn(world)
    return world


def order(game, world, island, kind, payload):
    island.orders = [OrderState(id=1, seq=1, kind=kind, payload=payload)]
    game.engine.run_turn(world)
    return island.orders[0]


def _state_farm(world):
    for island in world.islands.values():
        for f in island.facilities:
            if f.kind == "farm" and f.built and f.owner == "state" and fac.on_map(f):
                return island, f
    raise AssertionError("国営の農場がない")


def _finish(game, world, island, farm, turns=40):
    for _ in range(turns):
        if not any(fac.expansion_of(f) == farm.id for f in island.facilities):
            return
        game.engine.run_turn(world)


# --- 定義 -----------------------------------------------------------------------------------


def test_grade_recipes_follow_the_real_ratios(game):
    recipes = game.defs.facilities["farm"]["recipes"]
    manual = recipes["default"]["outputs"]["grain"]
    mech = recipes["default_mechanized"]
    modern = recipes["fertilized_modern"]
    assert mech["outputs"]["grain"] == pytest.approx(manual * 1.1, abs=1)
    assert mech["labor"] == pytest.approx(0.34)  # 約 3 分の 1
    assert mech["inputs"]["fuel"] > 0
    # 手作業・肥料なしから見て全部で約 2 倍（日本の米 1.7 倍・インドの小麦 3 倍の間）
    assert 1.9 < modern["outputs"]["grain"] / manual < 2.4
    assert modern["inputs"]["chemicals"] == 2 * recipes["fertilized"]["inputs"]["chemicals"]
    assert [r["grade"] for r in (recipes["default"], mech, modern)] == [0, 1, 2]


def test_grade_recipes_are_not_chosen_for_entry_or_prices(game):
    from hakoniwa_core.defs.kinds import is_variant

    recipes = game.defs.facilities["farm"]["recipes"]
    assert not is_variant("default", recipes["default"])
    assert is_variant("default_mechanized", recipes["default_mechanized"])


def test_regrade_keeps_the_crop(game):
    defs = game.defs
    assert fac.regrade(defs, "farm", "cotton", 1) == "cotton_mechanized"
    assert fac.regrade(defs, "farm", "cotton_fertilized", 1) == "cotton_fertilized_mechanized"
    assert fac.regrade(defs, "farm", "default", 2) == "fertilized_modern"
    assert fac.regrade(defs, "farm", "luxury_crop", 2) == "luxury_crop_fertilized_modern"


# --- 工事 -----------------------------------------------------------------------------------


def test_build_on_a_state_farm_raises_the_grade(game, world):
    island, farm = _state_farm(world)
    before = fac.workers_of(game.defs, farm)
    payload = {"facility": "farm", "x": farm.x, "y": farm.y}
    done = order(game, world, island, "build", payload)
    assert done.status == "done", done.reason
    site = next(f for f in island.facilities if fac.expansion_of(f) == farm.id)
    assert site.ext[fac.GRADE_TO] == 1
    assert fac.materials_of(game.defs, site) == dict(
        game.defs.facilities["farm"]["grade_steps"][0]["materials"]
    )
    assert farm.built and fac.grade_of(farm) == 0  # 工事の間も手作業で動く
    _finish(game, world, island, farm)
    assert fac.grade_of(farm) == 1
    assert farm.recipe.endswith("_mechanized")
    assert fac.size_of(farm) == 1
    assert fac.workers_of(game.defs, farm) < before * 0.5
    kit = equipment.kit_of(game.defs, farm)
    assert kit["machinery"] > 0 and kit["steel"] > 0
    assert any(e.kind == "grade_done" for e in island.log)


def test_second_order_while_building_and_at_the_top_fail(game, world):
    island, farm = _state_farm(world)
    payload = {"facility": "farm", "x": farm.x, "y": farm.y}
    order(game, world, island, "build", payload)
    again = order(game, world, island, "build", payload)
    assert again.status == "failed" and again.reason.startswith("reason_grade_pending")
    _finish(game, world, island, farm)
    order(game, world, island, "build", payload)
    _finish(game, world, island, farm)
    assert fac.grade_of(farm) == 2 and farm.recipe == "fertilized_modern"
    top = order(game, world, island, "build", payload)
    assert top.status == "failed" and top.reason.startswith("reason_grade_top")


def test_private_farm_cannot_be_raised_by_order(game, world):
    island, farm = _state_farm(world)
    farm.owner = "private"
    problem = fac.expansion_problem(game.defs, island, farm)
    assert problem is not None and problem[0] == "reason_private_expand"


def test_only_recipes_of_the_current_grade_can_be_chosen(game, world):
    island, farm = _state_farm(world)
    assert fac.switch_problem(game.defs, island, farm, "default_mechanized")[0] == (
        "reason_recipe_grade"
    )
    assert fac.switch_problem(game.defs, island, farm, "cotton") is None


# --- 機械の傷み（FM-51）---------------------------------------------------------------------


def _mechanized(game, world):
    island, farm = _state_farm(world)
    order(game, world, island, "build", {"facility": "farm", "x": farm.x, "y": farm.y})
    _finish(game, world, island, farm)
    assert fac.grade_of(farm) == 1
    return island, farm


def test_worn_machines_bring_the_fields_back_to_hand_work(game, world):
    island, farm = _mechanized(game, world)
    defs = game.defs
    full_workers = fac.workers_of(defs, farm)
    full = fac.recipe_of(defs, farm)
    farm.economy[fac.MACHINES] = 500  # 機械の半分が傷んだ
    half = fac.recipe_of(defs, farm)
    assert half["labor"] > full["labor"]  # 人手が戻る
    assert fac.workers_of(defs, farm) > full_workers
    assert half["inputs"]["fuel"] < full["inputs"]["fuel"]  # 燃料も機械の分だけ
    farm.economy[fac.MACHINES] = 0
    manual = fac.recipe_of(defs, farm)
    assert manual.get("labor", 1.0) == pytest.approx(1.0)
    assert "fuel" not in manual["inputs"]
    assert fac.workers_of(defs, farm) == defs.facilities["farm"]["workers"]


def test_worn_machines_do_not_stall_the_farm(game, world):
    """機械の傷みは稼働率の上限（設備の割合）に入れない。傷んだ分だけ手作業に戻る（FM-51）。"""
    island, farm = _mechanized(game, world)
    defs = game.defs
    have = equipment.holdings(defs, farm)
    equipment._store(defs, farm, have)
    condition = equipment.condition(farm)
    have["machinery"] = 0
    equipment._store(defs, farm, have)
    assert fac.machine_ratio(farm) == 0
    assert equipment.condition(farm) == condition  # 農場そのものは止まらない
    assert equipment.needs_repair(defs, game.config, farm)


def test_machines_are_repaired_to_new_not_to_the_operating_target(game, world):
    island, farm = _mechanized(game, world)
    defs = game.defs
    have = equipment.holdings(defs, farm)
    have["machinery"] = have["machinery"] // 2
    equipment._store(defs, farm, have)
    farm.economy["target"] = 500
    wants = equipment.wants(defs, game.config, farm)
    assert wants["machinery"] == equipment.kit_of(defs, farm)["machinery"] - have["machinery"]


# --- 通しの動き -------------------------------------------------------------------------------


def test_mechanized_farm_runs_and_frees_workers(game, world):
    """段階を上げた農場は、少ない人手で動き続け、帳簿が合う。燃料と機械の傷みの買い物が市場に出る。"""
    island, farm = _state_farm(world)
    defs = game.defs
    for _ in range(10):  # 経済を温める
        game.engine.run_turn(world)
    hands = farm.workers
    assert hands > 0
    order(game, world, island, "build", {"facility": "farm", "x": farm.x, "y": farm.y})
    _finish(game, world, island, farm)
    assert fac.grade_of(farm) == 1
    for _ in range(40):
        game.engine.run_turn(world)
    assert farm.built and "closing" not in farm.economy
    assert 0 < farm.workers < hands * 0.6  # 要らなくなった働き手は失業の層へ移った
    assert fac.workers_of(defs, farm) < defs.facilities["farm"]["workers"] * 0.5
    assert farm.operating_rate > 0  # 燃料と機械が手に入り、動いている
    from tests.core.test_finance import books_ok

    books_ok(game, island)


def test_private_farm_raises_its_own_grade_when_it_pays(game):
    """民営の農場は、浮く賃金と増える収穫が建設費に見合い、銀行の審査が通れば自分で上げる（FM-52）。"""
    from dataclasses import replace

    from hakoniwa_core import ledger
    from hakoniwa_core.economy import OWNERS, household
    from hakoniwa_core.modules.firms import EntryExit
    from tests.core.test_finance import books_ok, context

    world = generate_world(3, 3, game.config, game.defs)
    for _ in range(40):
        game.engine.run_turn(world)
    island = world.islands[1]
    assert island.regime["economy"] == "market"
    farm = next(f for f in island.facilities if f.kind == "farm" and f.owner == "private")
    ctx = context(game, world)
    # 島の資本家階級の預金は小さいので、機械の代金を出せるようにしておく（増資の経路を通る）
    ledger.pay(island, ledger.GOV, household(OWNERS), 20_000_000, "subsidy")
    every = game.config.get("firms.recipe_turns")
    ctx = replace(ctx, turn=ctx.turn + (-(ctx.turn + farm.id)) % every)
    EntryExit()._raise_grades(island, ctx)
    site = next((f for f in island.facilities if fac.expansion_of(f) == farm.id), None)
    assert site is not None and site.ext[fac.GRADE_TO] == 1 and site.ext.get("private_entry")
    assert any(e.kind == "grade_started" for e in island.log)
    # 資材は市場で買う。この島の鋼鉄は海外の商人だけが売る高値なので、手元に置いて工事を進める
    for good, qty in fac.materials_of(game.defs, site).items():
        stock.add(site.stock, good, qty, 0, game.defs.goods)
    _finish(game, world, island, farm, turns=60)
    assert fac.grade_of(farm) == 1 and farm.owner == "private"
    for _ in range(20):
        game.engine.run_turn(world)
    assert farm.built and "closing" not in farm.economy
    books_ok(game, island)


def test_private_farm_stays_manual_when_wages_are_low(game):
    """賃金が安く人が余る島では、機械化のもうけ率が参入に要る利益率に届かず、上げない（FM-52）。"""
    from hakoniwa_core.modules.firms import EntryExit, average_prices
    from tests.core.test_finance import context

    world = generate_world(3, 3, game.config, game.defs)
    for _ in range(40):
        game.engine.run_turn(world)
    island = world.islands[1]
    farm = next(f for f in island.facilities if f.kind == "farm" and f.owner == "private")
    ctx = context(game, world)
    prices = average_prices(island, game.config.get("firms.price_turns"))
    step = fac.grade_steps(game.defs, "farm")[0]
    upper = fac.regrade(game.defs, "farm", farm.recipe, 1)
    farm.economy["wage"] = 1006
    dear = EntryExit()._grade_return(island, ctx, farm, upper, step, prices, 0)[0]
    farm.economy["wage"] = 100
    cheap = EntryExit()._grade_return(island, ctx, farm, upper, step, prices, 0)[0]
    threshold = EntryExit._threshold(island, ctx)
    assert dear > threshold > cheap


# --- 標準の計画（FM-52）---------------------------------------------------------------------


def _warehouse_for(game, island, grade):
    step = fac.grade_steps(game.defs, "farm")[grade]
    for good, qty in step["materials"].items():
        stock.add(island.warehouse, good, qty, 0, game.defs.goods)


def test_autoplan_mechanizes_state_farms_only_when_hands_are_short(game, world):
    from hakoniwa_core import autoplan

    island, farm = _state_farm(world)
    _warehouse_for(game, island, 0)
    island.stats["labor"] = 1000
    island.stats["unemployment"] = 0.2  # 人が余っている
    assert autoplan._grade_order(island, game.defs, game.config) is None
    natural = game.config.get("labor.natural_unemployment")
    island.stats["unemployment"] = natural * 0.2  # 人手が足りない
    for f in island.facilities:  # 国の施設が雇いきれない人数
        if f.built:
            f.workers = 0
    order = autoplan._grade_order(island, game.defs, game.config)
    assert order == ("build", {"facility": "farm", "x": farm.x, "y": farm.y})


def test_autoplan_needs_the_materials_and_one_site_at_a_time(game, world):
    from hakoniwa_core import autoplan

    island, farm = _state_farm(world)
    island.stats.update(labor=1000, unemployment=0.0)
    for f in island.facilities:
        if f.built:
            f.workers = 0
    island.warehouse.clear()
    made = autoplan._made(island, game.defs)
    step = fac.grade_steps(game.defs, "farm")[0]
    if any(g not in made for g in step["materials"]):
        assert autoplan._grade_order(island, game.defs, game.config) is None  # 資材がない
    _warehouse_for(game, island, 0)
    assert autoplan._grade_order(island, game.defs, game.config) is not None
    order(game, world, island, "build", {"facility": "farm", "x": farm.x, "y": farm.y})
    assert autoplan._grade_order(island, game.defs, game.config) is None  # 工事の間は次を出さない
