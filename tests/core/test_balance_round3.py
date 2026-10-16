"""バランス調整 回3: 食料の危機と崩れる速さ（飢えの蓄え・飢えた家計の買い上がり・賃金の遅配・倒産と
撤退の後の分け方）。"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import (
    GENERAL,
    OWNERS,
    Payer,
    arrears,
    banks,
    facility_entity,
    household,
)
from hakoniwa_core.modules import failures
from hakoniwa_core.modules.firms import remove_facility
from hakoniwa_core.modules.market import FoodMenu, gross_cost, hungry_food_orders
from hakoniwa_core.modules.population import hunger_deaths, hunger_step
from hakoniwa_core.state import OWNER_PRIVATE
from hakoniwa_core.units import QUANTITY_SCALE, part
from tests.core.test_finance import context, island_of
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401


def _private(island, workers=True):
    """動いている民営の施設（差し押さえ中・閉鎖の途中でないもの）。"""
    return next(
        f
        for f in sorted(island.facilities, key=lambda f: f.id)
        if f.owner == OWNER_PRIVATE
        and f.built
        and (f.workers > 0 or not workers)
        and "foreclosed" not in f.economy
        and "closing" not in f.economy
        and f.kind != "bank"
    )


# --- 飢えの蓄え -------------------------------------------------------------------------


def test_hunger_stock_builds_up_slowly_and_recovers(game):
    """半分しか食べられない月が続くと、飢えの蓄えは (d − tolerance) ÷ recovery に近づく。体の余力を
    超えるまでは亡くならず、超えるほど急に増える。食べられれば戻る。"""
    settings = game.config.get("population.hunger")
    ctx = SimpleNamespace(config=game.config)
    s = 0.0
    months = []
    for _ in range(36):
        s = hunger_step(s, 0.5, settings)
        months.append(s)
    limit = (0.5 - settings["tolerance"]) / settings["recovery"]
    assert months[0] < settings["reserve"]  # 1か月では亡くならない
    assert months[-1] == pytest.approx(limit, rel=0.01)
    assert hunger_deaths(settings["reserve"] - 0.01, ctx) == 0
    worst = settings["reserve"] + settings["death_excess"]
    assert hunger_deaths(worst, ctx) == pytest.approx(settings["death_rate"])
    assert hunger_deaths(worst + 0.25, ctx) > 2 * hunger_deaths(worst - 0.25, ctx)
    for _ in range(24):
        s = hunger_step(s, 0.0, settings)
    assert s == 0
    # 少しの不足（1割まで）は体が耐える
    assert hunger_step(0.0, settings["tolerance"], settings) == 0


# --- 飢えた家計の買い上がり ------------------------------------------------------------------


def _menu():
    return FoodMenu(
        values={"grain": 1.0},
        nutritions={"grain": QUANTITY_SCALE},
        qualities={"grain": 0.0},
        unit={"grain": 100},
        limits={"grain": 100},
        rate=0,
        tax=0,
        elasticity=0.5,
    )


def test_hungry_households_bid_above_the_band_by_how_much_they_lack(game):
    """飢えた家計は、要る量の段ごとに、少ししか食べられない段ほど高い指値で買う（飢える前の値段 ×
    割合 ^ (−1/弾力性)）。指値は飢える前の値段を基準にし、今の値段にはつられない。"""
    settings = game.config.get("household.hungry")
    steps, elasticity = settings["steps"], settings["elasticity"]
    menu = _menu()
    need = 100 * QUANTITY_SCALE
    shares = {"grain": need}
    offered = {"grain": need}
    orders = hungry_food_orders(
        need, shares, offered, menu, {"grain": 100}, 0, 10**12, steps, elasticity
    )
    limits = [limit for _, _, limit in orders]
    assert limits == sorted(limits, reverse=True)
    assert limits[0] == pytest.approx(100 * steps[0] ** (-1 / elasticity), rel=0.01)
    assert limits[-1] == 100
    assert sum(q for _, q, _ in orders) == pytest.approx(need, rel=0.01)
    # 値幅の上端がすでに上がっていても、基準は飢える前の値段のまま（上がり続けない）
    menu.limits["grain"] = 300
    raised = hungry_food_orders(
        need, shares, offered, menu, {"grain": 100}, 0, 10**12, steps, elasticity
    )
    assert [limit for _, _, limit in raised] == limits


def test_hungry_bids_stay_within_the_food_budget(game):
    """どの段も、食費の限り（預金を崩す分を含む）に収まる分までしか買わない。お金のない家計から
    買えなくなる。"""
    settings = game.config.get("household.hungry")
    menu = _menu()
    need = 100 * QUANTITY_SCALE
    budget = 30_000
    orders = hungry_food_orders(
        need,
        {"grain": need},
        {"grain": need},
        menu,
        {"grain": 100},
        0,
        budget,
        settings["steps"],
        settings["elasticity"],
    )
    cost = sum(gross_cost(limit, q, menu.rate, menu.tax) for _, q, limit in orders)
    assert 0 < cost <= budget
    assert sum(q for _, q, _ in orders) < need
    assert hungry_food_orders(need, {}, {}, menu, {}, 0, 0, settings["steps"], 0.5) == []


# --- 賃金の遅配 ---------------------------------------------------------------------------


def test_unpaid_wages_stay_owed_and_are_paid_first_when_money_comes(game, warmed):
    """お金のない施設の働き手はすぐには辞めず、払われなかった賃金は未払い（施設の負債）として残る。
    お金が入ると、新しく雇う前に未払いを先に払う。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f = _private(island)
    entity = facility_entity(f.id)
    staff = f.workers
    owners = household(OWNERS)
    ledger.pay(island, entity, owners, ledger.money(island, entity), "equity_return")
    game.engine.run_turn(world)
    owed = arrears.total(island, entity)
    assert owed > 0
    assert f.economy["arrears_months"] == 1
    assert island.stats["arrears.workers"] > 0
    assert f.workers >= staff // 2  # 1か月目はほとんど辞めない
    held = arrears.owed(island, entity)
    assert set(held) <= {household(GENERAL), household("engineers")}
    # お金が入れば先に払う
    ledger.pay(island, owners, entity, 10 * owed, "capital_injection")
    before = ledger.money(island, household(GENERAL))
    from hakoniwa_core.modules.employment import settle_arrears

    settle_arrears(island)
    assert arrears.total(island, entity) == 0
    assert ledger.money(island, household(GENERAL)) > before


def test_workers_quit_little_by_little_while_wages_go_unpaid(game, warmed):
    """未払いが続く月数に応じて、少しずつ辞める（labor.arrears_quit × 月数）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f = _private(island)
    entity = facility_entity(f.id)
    staff = []
    for _ in range(4):
        ledger.pay(island, entity, household(OWNERS), ledger.money(island, entity), "equity_return")
        for loan in banks.loans_by_borrower(island, entity):  # 新しく借りられないようにする
            loan.arrears = 0
        game.engine.run_turn(world)
        if "foreclosed" in f.economy or f not in island.facilities:
            break
        staff.append(f.workers)
    assert len(staff) >= 2
    assert staff == sorted(staff, reverse=True)
    assert staff[-1] > 0


# --- 倒産と撤退の後の分け方 ---------------------------------------------------------------------


def test_foreclosure_money_goes_to_wages_then_taxes_then_lenders_by_share(game, warmed):
    """差し押さえた施設のお金は、未払い賃金 → 未払いの税 → 貸し手（貸した額の割合）の順に分け、
    余れば元の持ち主に返す。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "mixed")
    first, second = banks.active_banks(island)[:2]
    f = _private(island)
    entity = facility_entity(f.id)
    owners = household(OWNERS)
    general = household(GENERAL)
    ledger.pay(island, entity, owners, ledger.money(island, entity), "equity_return")
    seized = {
        "bank": first,
        "owner": owners,
        "claims": {
            arrears.WAGES: {general: 100_000},
            arrears.TAXES: 50_000,
            arrears.LOANS: {first: 300_000, second: 100_000},
        },
    }
    ledger.pay(island, owners, entity, 350_000, "capital_injection")
    money = {name: ledger.money(island, name) for name in (general, ledger.GOV, owners)}
    failures.distribute(island, entity, 350_000, seized, "foreclosure_sale")
    got = {name: ledger.money(island, name) - v for name, v in money.items()}
    tax = part(100_000, island.policy.income_tax)
    assert got[general] == 100_000 - tax
    assert got[ledger.GOV] == 50_000 + tax
    assert got[owners] == 0
    assert ledger.money(island, entity) == 0  # 残りの 200,000 は貸し手へ（3 対 1）
    claims = seized["claims"]
    assert claims[arrears.WAGES] == {} and claims[arrears.TAXES] == 0
    assert claims[arrears.LOANS] == {first: 150_000, second: 50_000}
    # 売れた代金の残りで貸し手に返しきり、余りは元の持ち主へ
    ledger.pay(island, owners, entity, 300_000, "capital_injection")
    before = ledger.money(island, owners)
    failures.distribute(island, entity, 300_000, seized, "foreclosure_sale")
    assert claims[arrears.LOANS] == {}
    assert ledger.money(island, owners) - before == 100_000


def test_bankruptcy_pays_unpaid_wages_before_the_lenders(game, warmed):
    """倒産した施設の預金は、先に未払い賃金に払う。払いきれなかった請求は差し押さえの記録に残る。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f = next(
        f
        for f in sorted(island.facilities, key=lambda f: f.id)
        if f.owner == OWNER_PRIVATE
        and f.built
        and banks.loans_by_borrower(island, facility_entity(f.id))
        and "foreclosed" not in f.economy
    )
    entity = facility_entity(f.id)
    general = household(GENERAL)
    ledger.pay(island, household(OWNERS), entity, 100_000, "capital_injection")
    money = ledger.money(island, entity)
    arrears.add_wages(island, entity, general, money + 5_000)
    before = ledger.money(island, general)
    debt = banks.debt_of(island, entity)
    failures.bankrupt(island, context(game, world, "failures"), f)
    assert ledger.money(island, entity) == 0
    assert ledger.money(island, general) > before  # 預金はまず働き手へ
    seized = f.economy["foreclosed"]
    claims = seized["claims"]
    assert claims[arrears.WAGES][general] == 5_000
    assert sum(claims[arrears.LOANS].values()) == debt  # 貸し手には何も残らなかった
    assert arrears.total(island, entity) == 0  # 帳簿の未払いは記録に移した


def test_leftover_stock_is_bought_by_the_state_at_market_price(game, warmed):
    """撤退した施設の残りの在庫は、国が市場の値段で引き取る（ただで国の倉庫に移さない）。代金は
    未払い賃金から順に分け、余りは持ち主に返る。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f = _private(island)
    good = "grain"
    f.stock = {good: [[world.turn, 50 * QUANTITY_SCALE]]}
    value = arrears.stock_value(f.stock, island.economy.prices)
    assert value > 0
    gov = ledger.money(island, ledger.GOV)
    held = stock.totals(island.warehouse).get(good, 0)
    ctx = context(game, world, "entry_exit")
    remove_facility(island, ctx, f)
    assert ledger.money(island, ledger.GOV) <= gov - value + 1  # 国が代金を払った
    assert stock.totals(island.warehouse).get(good, 0) >= held + 50 * QUANTITY_SCALE
    assert f not in island.facilities


def test_unpaid_wages_lower_the_value_of_a_facility(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f = _private(island)
    entity = facility_entity(f.id)
    value = banks.net_assets(island, f, entity)
    arrears.add_wages(island, entity, household(GENERAL), 1_000)
    arrears.add_tax(island, entity, 500)
    assert banks.net_assets(island, f, entity) == max(0, value - 1_500)
    assert arrears.total(island, entity, arrears.TAX_ARREARS) == 500
    payer = Payer(entity)
    ledger.pay(island, household(OWNERS), entity, 10_000, "capital_injection")
    assert arrears.settle(island, payer) == 1_500
    assert arrears.total(island, entity) == 0


# --- 畑の作付け ---------------------------------------------------------------------------


def test_farm_moves_its_fields_to_a_new_crop_little_by_little(game):
    """作物を替えた農場の穀物は一度に消えず、1ターンに畑の 1/育つ期間 ずつ減る。蒔いた綿は育つ
    期間のあいだ何も出さず、みな綿になったら元の1つの作り方に戻る（Codex E17）。"""
    from hakoniwa_core import facilities as fac
    from tests.core.test_rules import add, make_island

    defs = game.defs
    island = make_island(game)
    farm = add(island, "farm", 8, 8)
    fac.switch_recipe(defs, farm, "cotton", 1)
    grain_full = defs.facilities["farm"]["recipes"]["default"]["outputs"]["grain"]
    cotton_full = defs.facilities["farm"]["recipes"]["cotton"]["outputs"]["cotton"]
    grow = fac.grow_turns(defs, "farm", "cotton")
    grain, cotton = [], []
    for turn in range(1, 20):
        fac.step_fields(defs, farm, turn)
        outputs = fac.recipe_of(defs, farm)["outputs"]
        grain.append(outputs.get("grain", 0))
        cotton.append(outputs.get("cotton", 0))
    assert grain[0] == grain_full * 4 // 5  # 穀物は5か月で育つので、1か月に 1/5 ずつ蒔き直す
    assert grain == sorted(grain, reverse=True) and grain[4] == 0
    assert cotton[:grow] == [0] * grow  # 綿は6か月たつまで何も出さない
    assert cotton[grow] > 0 and cotton[-1] == cotton_full
    assert fac.FIELDS not in farm.economy and farm.ext[fac.CROP_READY] <= 19
    assert fac.recipe_of(defs, farm) is defs.facilities["farm"]["recipes"]["cotton"]
    # 肥料の有無だけの替え方はすぐ
    fac.switch_recipe(defs, farm, "cotton_fertilized", 20)
    assert fac.FIELDS not in farm.economy and farm.recipe == "cotton_fertilized"
