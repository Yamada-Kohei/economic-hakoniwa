"""国庫の項目の予算が足りないときの分け方（LB-45）と、計画経済の人員計画（PL-59。2026-11-10）。"""

from __future__ import annotations

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, stock
from hakoniwa_core.economy import funds, payer_of
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.modules import infrastructure
from hakoniwa_core.modules import plan as plan_orders
from hakoniwa_core.modules.employment import STAFF_CAPPED, Employment
from hakoniwa_core.modules.market import FACILITY_HOLDS
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.state import OWNER_STATE
from tests.core.test_recession_round1 import context
from tests.core.test_rules import add, home, make_island, run, with_economy
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）


def _schools(game, regime="mixed", stock=4000):
    """町の横に同じ優先度の学校が2つある島。"""
    island = make_island(game)
    home(game, island, 5, 6, 30000)
    with_economy(game, island)
    island.regime["economy"] = regime
    first = add(island, "school", 6, 6, owner=OWNER_STATE, stock={"consumer_goods": stock})
    second = add(island, "school", 6, 5, owner=OWNER_STATE, stock={"consumer_goods": stock})
    return island, first, second


def _open(island, game, caps):
    order = game.config.get("fiscal.payment_order")
    funds.open_treasury(island, {}, order, caps)


def test_same_priority_schools_share_a_short_budget_in_the_same_ratio(game):
    island, first, second = _schools(game)
    ctx = context(game)
    full = fac.workers_of(game.defs, first)
    wage = island.economy.average_wage
    # 教育費の月割りは、学校1つ分の賃金にも足りない
    _open(island, game, {"education": full * wage})
    Employment().run_island(island, ctx)
    assert first.workers > 0 and second.workers > 0
    # 番号の順の満額でなく、同じ割合（端数の差だけ）
    assert abs(first.workers - second.workers) <= 1
    assert first.workers < full
    # 年の予算の月割りで減らしたので、状態に項目の名前を残す（遅配にしない）
    assert first.ext.get("short_wages") == "education"
    assert not first.economy.get("unpaid")
    assert 0 < island.stats["budget_share.education"] < 1


def test_materials_are_held_with_the_wages_and_returned_before_the_buy(game):
    island, first, second = _schools(game, stock=50)  # 次のターンの教材が足りない
    ctx = context(game)
    full = fac.workers_of(game.defs, first)
    wage = island.economy.average_wage
    _open(island, game, {"education": full * wage})
    Employment().run_island(island, ctx)
    holds = island.scratch.get(FACILITY_HOLDS, {})
    # 教材の代金も、施設ごとに取り置いた（分けたので、市場で使えるのは分けた額まで）
    assert set(holds) == {first.id, second.id}
    assert all(rationed for _, _, rationed in holds.values())


def test_a_higher_priority_school_is_filled_first(game):
    island, first, second = _schools(game)
    second.priority = 9
    ctx = context(game)
    full = fac.workers_of(game.defs, first)
    wage = island.economy.average_wage
    _open(island, game, {"education": full * wage * 3 // 2})
    Employment().run_island(island, ctx)
    assert second.workers == full
    assert first.workers < full


def _planned(game):
    """町の横に原料のそろった国営の製鉄所がある計画経済の島。"""
    island = make_island(game)
    home(game, island, 5, 6, 30000)
    with_economy(game, island)
    island.regime["economy"] = "planned"
    stock = {"iron_ore": 400000, "coal": 200000}
    mill = add(island, "steel_mill", 6, 6, owner=OWNER_STATE, stock=stock)
    return island, mill


def test_staff_limit_releases_idle_people_at_once_and_workers_monthly(game):
    island, mill = _planned(game)
    defs, config = game.defs, game.config
    full = fac.workers_of(defs, mill)
    mill.workers = full
    mill.operating_rate = fac.FULL // 2  # 半分は働いていない（抱えた余りの人）
    mill.ext[planning.STAFF_CAP] = full // 4
    worked = -(-full * mill.operating_rate // fac.FULL)
    release = config.get("planning.staff_release")
    # 働いていない人はその月に、働いていた人は前の人数の staff_release ずつ
    limit = planning.staff_limit(island, defs, config, mill)
    assert limit == max(full // 4, worked - -(-int(full * release * 1000) // 1000))
    # 枠より少なければ枠まで
    mill.workers = full // 8
    assert planning.staff_limit(island, defs, config, mill) == full // 4
    # 計画経済でない島と民営の施設には枠がない
    island.regime["economy"] = "mixed"
    assert planning.staff_limit(island, defs, config, mill) is None


def test_capped_facility_hires_up_to_the_cap_and_shows_the_cause(game):
    island, mill = _planned(game)
    ctx = context(game)
    mill.economy["target"] = fac.FULL
    mill.ext[planning.STAFF_CAP] = 10
    Employment().run_island(island, ctx)
    assert mill.workers <= 10
    assert mill.ext.get(STAFF_CAPPED)


def test_set_staff_plan_checks_and_sets_caps(game):
    island, mill = _planned(game)
    defs, config = game.defs, game.config
    ctx = context(game)
    plan_orders.check_staff_plan(island, {"plans": [{"x": 6, "y": 6, "cap": 100}]}, defs, config)
    plan_orders.set_staff_plan(island, {"plans": [{"x": 6, "y": 6, "cap": 100}]}, ctx)
    assert mill.ext[planning.STAFF_CAP] == 100
    plan_orders.set_staff_plan(island, {"plans": [{"x": 6, "y": 6, "cap": None}]}, ctx)
    assert planning.STAFF_CAP not in mill.ext
    # 同じ施設を2回は出せない
    with pytest.raises(OrderFailed) as failed:
        twice = {"plans": [{"x": 6, "y": 6, "cap": 1}, {"x": 6, "y": 6, "cap": 2}]}
        plan_orders.check_staff_plan(island, twice, defs, config)
    assert failed.value.reason == "reason_duplicate_staff_plan"
    # 運送業（人数を荷で決める）は外す
    transport = config.get("freight.facility")
    with pytest.raises(OrderFailed) as failed:
        plan = {"plans": [{"kind": transport, "cap": 1}]}
        plan_orders.check_staff_plan(island, plan, defs, config)
    assert failed.value.reason == "reason_not_staff_plannable"
    # 計画経済でない島は出せない
    island.regime["economy"] = "mixed"
    with pytest.raises(OrderFailed) as failed:
        plan = {"plans": [{"x": 6, "y": 6, "cap": 1}]}
        plan_orders.check_staff_plan(island, plan, defs, config)
    assert failed.value.reason == "reason_not_planned"


def test_planned_islands_hire_by_priority_not_wage(game):
    island, mill = _planned(game)
    ctx = context(game)
    other = add(island, "steel_mill", 7, 6, owner=OWNER_STATE)
    mill.priority, other.priority = 9, 5
    key = Employment._hire_key
    # 計画経済: 賃金が安くても優先度の高い施設が先（PL-59）。ほかの体制は賃金の高い順
    assert key(ctx, True, (mill, 900)) < key(ctx, True, (other, 2000))
    assert key(ctx, False, (mill, 900)) > key(ctx, False, (other, 2000))


def test_public_works_priority_can_be_set_by_order(game):
    island, _ = _planned(game)
    ctx = context(game)
    infrastructure.set_public_works(island, {"upkeep_priority": 9, "build_priority": 6}, ctx)
    assert infrastructure.priority(island, game.config, True) == 9
    assert infrastructure.priority(island, game.config, False) == 6
    assert island.log[-1].kind == "public_works_priority"


def test_materials_leave_the_power_bill_and_come_after_the_arrears(game):
    island, first, second = _schools(game)
    ctx = context(game)
    full = fac.workers_of(game.defs, first)
    wage = island.economy.average_wage
    _open(island, game, {})  # 月割りはない: 国庫の残りで止まる
    payer = payer_of(game.defs, game.config, first)
    # 国庫の残りを学校1つ半の賃金だけにする
    funds.commit(island, payer, funds.available(island, payer) - full * wage * 3 // 2)
    Employment().run_island(island, ctx)
    power = Employment._power_per_worker(island, ctx, first)
    hired = first.workers + second.workers
    # 材料の取り置きは、遅配を埋めて、雇った人の電気代（生産の段で買う）を残した後のお金から
    assert not first.economy.get("unpaid") and not second.economy.get("unpaid")
    assert funds.available(island, payer) >= hired * power


def test_build_priority_above_upkeep_keeps_the_upkeep_count_apart(game):
    island, _ = _planned(game)
    ctx = context(game)
    infrastructure.set_public_works(island, {"upkeep_priority": 3, "build_priority": 8}, ctx)
    run(game, island, ["infrastructure"])  # 町の道路が傷む（維持の仕事）
    infra.enqueue(island, "road", [(3, 3)])  # 新しい工事
    materials = infrastructure.works_stock(island)
    stock.add(materials, "building_materials", 10_000, 0, game.defs.goods)
    stock.add(materials, "stone", 10_000, 0, game.defs.goods)
    wage = island.economy.average_wage
    infrastructure.plan(island, ctx, upkeep=False, wage=wage)
    built = island.scratch[infrastructure.WORKS]["labor"]
    infrastructure.plan(island, ctx, upkeep=True, wage=wage)
    state = island.scratch[infrastructure.WORKS]
    # 維持の人数は維持で足した分だけ（先に見た新しい工事の分を含めない）
    assert built > 0 and state["labor"] > built  # 両方に仕事がある
    assert state.get("upkeep_labor", 0) == state["labor"] - built


def test_a_damaged_facility_is_not_held_to_its_staff_cap(game):
    island, mill = _planned(game)
    mill.ext[planning.STAFF_CAP] = 0
    assert planning.staff_cap(island, game.defs, mill) == 0
    mill.built = False  # 暴動で壊れて直している
    assert planning.staff_cap(island, game.defs, mill) is None
    # 計画経済をやめたら、雇わない施設の枠も消える
    island.regime["economy"] = "mixed"
    mill.built = True
    Employment().run_island(island, context(game))
    assert planning.STAFF_CAP not in mill.ext


def test_budget_squeezed_materials_show_the_budget_as_the_cause(game):
    from hakoniwa_core.modules.production import Production

    island, first, _ = _schools(game)
    good = next(iter(fac.recipe_of(game.defs, first)["inputs"]))
    first.ext[funds.SHORT_MATERIALS] = "education"
    assert Production._short(first, good, game.defs) == "budget:education"
    first.economy["short"] = {good: "price"}  # 市場で高くて買えなかったなら、そのわけ
    assert Production._short(first, good, game.defs) != "budget:education"
