"""フェーズ3-4「政治と体制」: 政情とその段階、議会の採決、報道と集会、憲法改正と経済体制の変更、
暴動と警察、労働の動員、計画経済の公定価格・割当・配給・生産目標、値段の下限。

ターンを回す試験では、記録の段が毎ターン帳簿を確かめる（合わなければその場で止まる）。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, politics
from hakoniwa_core.economy import OWNERS
from hakoniwa_core.economy import books as econ_books
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.log import format_log
from hakoniwa_core.market import auction
from hakoniwa_core.modules import accounts as accounts_stage
from hakoniwa_core.modules import plan as plan_orders
from hakoniwa_core.modules import politics as politics_stage
from hakoniwa_core.modules.politics import Politics, parliament_gate
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
    OWNER_PRIVATE,
    OWNER_STATE,
    MarketRecord,
    OrderState,
)
from hakoniwa_core.state.generate import generate_world
from tests.core.test_finance import books_ok, context, open_books
from tests.core.test_trade import run

BASE_TURNS = 30


@pytest.fixture(scope="module")
def game():
    from hakoniwa_core.game import load_game

    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """標準の計画で BASE_TURNS ターン回した6島（市場・混合・計画経済が2つずつ）。試験ごとに写す。"""
    world = generate_world(1, 6, game.config, game.defs)
    run(game, world, BASE_TURNS)
    return world


def island_of(world, economy):
    return next(i for i in world.islands.values() if i.regime["economy"] == economy)


def order(kind, payload):
    return OrderState(id=0, seq=0, kind=kind, payload=payload)


def logs_ok(game, island):
    for e in island.log:
        text = format_log(game.defs, e.kind, e.args)
        assert "{" not in text, (e.kind, text)


# --- 政情 -----------------------------------------------------------------------------


def test_levels_follow_the_bounds(game):
    bounds = game.config.get("politics.levels")
    assert politics.level_of(bounds["demo"], game.config) == politics.CALM
    assert politics.level_of(bounds["demo"] - 0.01, game.config) == politics.DEMO
    assert politics.level_of(bounds["riot"], game.config) == politics.DEMO
    assert politics.level_of(bounds["riot"] - 0.01, game.config) == politics.RIOT
    assert politics.level_of(bounds["rebellion"], game.config) == politics.RIOT
    assert politics.level_of(bounds["rebellion"] - 0.01, game.config) == politics.REBELLION
    assert politics.level_of(0, game.config) == politics.REBELLION


def test_every_island_has_politics_and_it_started_at_the_target(game):
    world = generate_world(3, 3, game.config, game.defs)
    run(game, world, 1)
    for island in world.islands.values():
        state = politics.state_of(island)
        assert abs(state["stability"] - state["target"]) < 0.01  # 初めのターンは目標から
        assert state["level"] == politics.level_of(state["stability"], game.config)
        total = min(100.0, max(0.0, sum(state["parts"].values())))
        assert state["target"] == pytest.approx(total, abs=0.05)
        if politics.has_parliament(island, game.defs):
            assert state["election"] == world.turn + game.config.get("politics.election_turns")
            assert state["seats"] == pytest.approx(state["support"] / 100, abs=1e-3)
        assert "politics.stability" in island.stats


def test_islands_without_a_political_regime_get_the_first_choices(game, base):
    """フェーズ3-4 より前の見本の島のように政治の体制が空の島は、移し替えで初めの選択（民主主義・
    君主制）を置き、政情を目標から始め直す。経済体制の軸はそのまま。"""
    world = copy.deepcopy(base)
    island = world.islands[1]
    economy = island.regime.get("economy")
    island.regime = {"economy": economy} if economy else {}
    politics.state_of(island)["stability"] = 20.0
    run(game, world, 1)
    axes = game.defs.regime_axes()
    assert island.regime["politics"] == axes["politics"][0]
    assert island.regime["head_of_state"] == axes["head_of_state"][0]
    assert island.regime.get("economy") == economy
    state = politics.state_of(island)
    assert state["stability"] == pytest.approx(state["target"], abs=0.5)  # 初めのターンは目標から
    assert "regime_filled" in {e.kind for e in island.log}
    logs_ok(game, island)
    run(game, world, 1)
    assert "regime_filled" not in {e.kind for e in island.log}  # 1度だけ


def test_stability_moves_toward_the_target_by_the_regime_speed(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    state = politics.state_of(island)
    state["stability"] = 90.0
    ctx = context(game, world, "politics")
    Politics().run_island(island, ctx)
    found = politics.traits(island, game.defs)
    turns = found.get("stability_turns_down") or game.config.get("politics.stability_turns")
    target = state["target"]
    if target < 90:
        assert state["stability"] == pytest.approx(90 + (target - 90) / turns, abs=0.02)


def test_inflation_shock_brings_demonstrations_and_riots_and_they_pass(game, base, monkeypatch):
    """揺さぶり: 物価上昇率が年 60% の間、政情はデモから暴動まで下がり、暴動では施設が止まり、
    行政力が減る。物価が落ち着けば戻る（元の値段の動きに戻す）。

    中央銀行が国債を大量に引き受けても、今の経済では物価が年 1 割を超えて上がり続けない（報告書
    phase3/3-4-report.md）ので、ここでは物価上昇率を外から決めて政情の段の動きを確かめる。"""
    world = copy.deepcopy(base)  # 物価上昇率はどの島でも同じに変える。見るのは島 1
    island = world.islands[1]
    real = politics_stage.inflation
    shock = {"on": True}
    monkeypatch.setattr(politics_stage, "inflation", lambda e: 0.6 if shock["on"] else real(e))
    levels, halted, kinds = [], 0, set()

    def each(w):
        nonlocal halted
        levels.append(politics.level(island))
        halted += island.stats.get("politics.halted", 0)
        kinds.update(e.kind for e in island.log)
        logs_ok(game, island)

    run(game, world, 36, each)
    assert politics.DEMO in levels
    assert politics.RIOT in levels or politics.REBELLION in levels
    assert halted > 0 and "riot_halted" in kinds | {k.removesuffix("_police") for k in kinds}
    low = politics.state_of(island)["stability"]
    shock["on"] = False
    after = []
    run(game, world, 60, lambda w: after.append(politics.state_of(island)["stability"]))
    assert max(after) > max(low, game.config.get("politics.levels")["riot"]), (
        "物価が落ち着いても政情が戻らない"
    )


def test_riot_halts_urban_facilities_and_police_hold_it_down(game, base):
    world = copy.deepcopy(base)
    island = max(world.islands.values(), key=lambda i: len(i.facilities))
    ctx = context(game, world, "politics")
    candidates = politics_stage.urban_facilities(island, ctx)
    assert candidates
    plain = copy.deepcopy(island)
    Politics()._riot(plain, ctx, politics.RIOT)
    stopped = [f for f in plain.facilities if f.ext.get("halt_reason") == "riot"]
    assert all(f.ext["halted_until"] > world.turn for f in stopped)
    assert plain.log[-1].kind == "riot_halted"
    # 警察の人手が足りていれば、止まる施設は (1 − police_effect) に減る
    guarded = copy.deepcopy(island)
    police = next(k for k, d in game.defs.facilities.items() if d.get("police"))
    office = next(f for f in guarded.facilities if f.built and f.x >= 0)
    office.kind = police
    office.workers = 10**9
    assert politics_stage.police_coverage(guarded, ctx) == 1.0
    Politics()._riot(guarded, ctx, politics.RIOT)
    held = [f for f in guarded.facilities if f.ext.get("halt_reason") == "riot"]
    assert len(held) <= max(1, len(stopped))
    assert guarded.log[-1].kind == "riot_halted_police"
    logs_ok(game, guarded)


def test_destroyed_facility_is_repaired_as_state_works(game, base):
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    ctx = context(game, world, "politics")
    f = next(
        f
        for f in island.facilities
        if f.built and f.owner == OWNER_STATE and f.x >= 0 and f.kind == "farm"
    )
    open_books(island)
    politics_stage.damage(island, ctx, f)
    assert not f.built and f.ext.get("repair")
    books_ok(game, island)
    kinds = set()
    run(game, world, 12, lambda w: kinds.update(e.kind for e in island.log))
    assert f.built and "repair" not in f.ext
    assert "facility_repaired" in kinds


# --- 議会 -----------------------------------------------------------------------------


def _democracy(game, world, island):
    island.regime["politics"] = "democracy"
    state = politics.state_of(island)
    state.update({"seats": 0.6, "elected": 50.0, "support": 50.0, "election": world.turn + 48})
    return state


def test_parliament_votes_by_seats_and_support(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    state = _democracy(game, world, island)
    ctx = context(game, world)
    parliament_gate(island, order("set_central_bank_independence", {"independent": True}), ctx)
    assert state["votes"][0]["passed"] and state["votes"][0]["rule"] == "majority"
    assert island.log[-1].kind == "parliament_passed"
    with pytest.raises(OrderFailed) as failed:
        parliament_gate(island, order("change_economy", {"economy": "planned"}), ctx)
    assert failed.value.reason == "reason_parliament_rejected"
    assert not state["votes"][0]["passed"] and state["votes"][0]["yes"] == pytest.approx(0.6)
    # 支持率が選挙のときより 20 上がると、賛成は 20 × swing ポイント増える
    state["support"] = 70.0
    parliament_gate(island, order("change_economy", {"economy": "planned"}), ctx)
    swing = game.config.get("politics.parliament.swing")
    assert state["votes"][0]["yes"] == pytest.approx(0.6 + 0.2 * swing)
    assert state["votes"][0]["passed"] == (0.6 + 0.2 * swing >= 2 / 3 - 1e-4)
    logs_ok(game, island)


def test_majority_needs_more_than_half_and_two_thirds_is_inclusive(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    state = _democracy(game, world, island)
    ctx = context(game, world)
    state["seats"] = 0.5
    with pytest.raises(OrderFailed):
        parliament_gate(island, order("nationalize", {"x": 0, "y": 0}), ctx)
    state["seats"] = game.config.get("politics.parliament.two_thirds")
    parliament_gate(island, order("amend_constitution", {"politics": "authoritarian"}), ctx)
    assert state["votes"][0]["passed"]


def test_only_big_tax_changes_need_a_vote(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    state = _democracy(game, world, island)
    state["seats"] = 0.1
    ctx = context(game, world)
    now = island.policy.income_tax * 100 / 10000
    points = game.config.get("politics.parliament.tax_points")
    parliament_gate(island, order("set_taxes", {"income_tax": now + points}), ctx)
    assert "votes" not in state
    with pytest.raises(OrderFailed):
        parliament_gate(island, order("set_taxes", {"income_tax": now + points + 1}), ctx)


def test_no_vote_without_parliament(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    island.regime["politics"] = "authoritarian"
    state = politics.state_of(island)
    state["seats"] = 0.0
    parliament_gate(island, order("change_economy", {"economy": "planned"}), context(game, world))
    assert "votes" not in state


def test_rejected_order_is_logged_and_changes_nothing(game, base):
    world = copy.deepcopy(base)
    island = island_of(world, "market")
    state = _democracy(game, world, island)
    state["seats"] = 0.2
    before = dict(island.regime)
    first = min((o.seq for o in island.orders), default=0) - 1
    new_id = max((o.id for o in island.orders), default=0) + 1
    island.orders.append(
        OrderState(id=new_id, seq=first, kind="change_economy", payload={"economy": "mixed"})
    )
    game.engine.run_turn(world)
    failed = [e for e in island.log if e.kind == "order_failed"]
    assert any(e.args.get("reason") == "reason_parliament_rejected" for e in failed)
    assert island.regime["economy"] == before["economy"]
    logs_ok(game, island)


# --- 報道と集会・動員・元首 ------------------------------------------------------------


def test_press_control_raises_stability_and_stores_grievance(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    island.regime["politics"] = "democracy"
    with pytest.raises(OrderFailed):
        politics_stage.check_press(island, {"mode": "controlled"}, game.defs)
    island.regime["politics"] = "authoritarian"
    politics_stage.check_press(island, {"mode": "controlled"}, game.defs)
    state = politics.state_of(island)
    before = state["stability"]
    ctx = context(game, world)
    politics_stage.set_press(island, {"mode": "controlled"}, ctx)
    bonus = game.config.get("politics.press.bonus")["controlled"]
    assert state["stability"] == pytest.approx(min(100.0, before + bonus))
    run(game, world, 6)
    assert state.get("grievance", 0) > 0
    held = state["grievance"]
    value = state["stability"]
    politics_stage.set_press(island, {"mode": "free"}, context(game, world))
    assert state["stability"] == pytest.approx(max(0.0, value - held))
    assert "grievance" not in state
    logs_ok(game, island)


def test_mobilization_puts_the_unemployed_to_work_in_state_factories(game, base):
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    island.regime["politics"] = "democracy"
    with pytest.raises(OrderFailed):
        politics_stage.check_mobilize(island, {"on": True}, game.defs)
    island.regime["politics"] = "totalitarian"
    politics_stage.check_mobilize(island, {"on": True}, game.defs)
    politics_stage.mobilize(island, {"on": True}, context(game, world))
    assert politics.mobilized_share(island) == game.config.get("politics.mobilization.share")
    run(game, world, 3)
    assert island.stats.get("labor.mobilized", 0) >= 0
    drafted = [f for f in island.facilities if f.economy.get("mobilized")]
    if island.stats.get("unemployment", 0) > 0.02:
        assert drafted
    for f in drafted:
        assert f.owner == OWNER_STATE
    assert island.stats["happiness"] <= politics.happiness_cap(island, game.defs)


def test_leader_can_be_renamed_only_in_a_republic(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    island.regime["head_of_state"] = "monarchy"
    with pytest.raises(OrderFailed):
        politics_stage.check_rename_leader(island, {"name": "新しい元首"}, game.defs)
    island.regime["head_of_state"] = "republic"
    politics_stage.check_rename_leader(island, {"name": "新しい元首"}, game.defs)
    politics_stage.rename_leader(island, {"name": " 新しい元首 "}, context(game, world))
    assert island.names["leader"] == "新しい元首"


# --- 憲法改正と経済体制の変更 ------------------------------------------------------------


def test_amendment_starts_a_transition_and_rebuilds_the_capital(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    old = island.regime["politics"]
    new = next(r for r in ("democracy", "authoritarian", "totalitarian") if r != old)
    politics_stage.check_amend(island, {"politics": new}, game.defs)
    ctx = context(game, world)
    politics_stage.amend(island, {"politics": new}, ctx)
    state = politics.state_of(island)
    turns = game.config.get("politics.transition.turns")
    assert island.regime["politics"] == new
    assert state["transition"]["until"] == ctx.turn + turns
    assert state["productivity"] < 1
    assert state["rebuild"] and politics.capital_building(island, game.defs) == state["building"]
    with pytest.raises(OrderFailed) as failed:
        politics_stage.check_amend(island, {"politics": old}, game.defs)
    assert failed.value.reason == "reason_in_transition"
    logs_ok(game, island)
    kinds = set()
    run(game, world, turns + 2, lambda w: kinds.update(e.kind for e in island.log))
    assert "transition" not in state and "productivity" not in state
    assert {"transition_ended", "capital_rebuilt"} <= kinds
    assert not state.get("rebuild"), "首都の建物の建て替えが終わらない"
    assert "building" not in state
    assert (
        politics.capital_building(island, game.defs) == game.defs.regimes[new]["capital_building"]
    )


def test_same_or_unknown_regime_is_refused(game, base):
    island = copy.deepcopy(base).islands[1]
    with pytest.raises(OrderFailed) as same:
        politics_stage.check_change_economy(
            island, {"economy": island.regime["economy"]}, game.defs
        )
    assert same.value.reason == "reason_same_regime"
    with pytest.raises(OrderFailed) as unknown:
        politics_stage.check_amend(island, {"politics": "anarchy"}, game.defs)
    assert unknown.value.reason == "reason_unknown_regime"


def test_change_to_planned_nationalizes_everything(game, base):
    world = copy.deepcopy(base)
    island = island_of(world, "market")
    assert any(f.owner == OWNER_PRIVATE for f in island.facilities)
    open_books(island)
    politics_stage.change_economy(island, {"economy": "planned"}, context(game, world))
    books_ok(game, island)
    assert not any(f.owner == OWNER_PRIVATE for f in island.facilities)  # 民間銀行も
    assert OWNERS not in island.economy.households
    assert planning.official(island)
    assert "nationalized_all" in {e.kind for e in island.log}
    logs_ok(game, island)
    run(game, world, 6)  # 国有化の後も帳簿が崩れずに回る


def test_change_from_planned_keeps_state_facilities_and_lets_firms_in(game, base):
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    state_owned = {f.id for f in island.facilities if f.owner == OWNER_STATE}
    open_books(island)
    politics_stage.change_economy(island, {"economy": "market"}, context(game, world))
    books_ok(game, island)
    assert {f.id for f in island.facilities if f.owner == OWNER_STATE} == state_owned
    assert planning.KEY not in island.ext
    assert OWNERS in island.economy.households
    kinds = set()
    run(game, world, 24, lambda w: kinds.update(e.kind for e in island.log))
    assert island.economy.households[OWNERS]["persons"] > 0


# --- 計画経済 ---------------------------------------------------------------------------


def test_ration_gives_each_person_the_same_amount():
    persons = {"general": 100, "owners": 10}
    got = planning.ration(550, [(0, 1000, "general"), (1, 1000, "owners")], persons)
    assert got == {0: 500, 1: 50}  # 1人あたり 5 ずつ
    # 欲しい量が少ない層は、その分だけで足りる。残りは他の層に回る
    got = planning.ration(550, [(0, 1000, "general"), (1, 20, "owners")], persons)
    assert got[1] == 20 and got[0] == 530
    # 足りていれば全部
    assert planning.ration(5000, [(0, 1000, "general"), (1, 20, "owners")], persons) == {
        0: 1000,
        1: 20,
    }


def test_allocator_serves_priority_then_state_then_households_then_exports():
    tiers = [planning.EXPORTS, planning.HOUSEHOLDS, planning.STATE, planning.PRIORITY]
    layers = [None, "general", None, None]
    allocate = planning.allocator(tiers, layers, {"general": 10})
    side = [(0, 100, 0), (1, 100, 0), (2, 100, 0), (3, 100, 0)]
    assert {i: q for i, q in allocate(250, side).items() if q} == {3: 100, 2: 100, 1: 50}
    # 板寄せに入れても同じ（公定価格で値幅 0）
    buys = [auction.Order(100, 1000) for _ in range(4)]
    result = auction.clear(
        buys, [auction.Order(250, 0)], 1000, bounds=(1000, 1000), allocate_buys=allocate
    )
    assert result.price == 1000 and result.bought == [0, 50, 100, 100]


def test_planned_market_clears_at_the_official_prices(game, base):
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    prices = planning.official(island)
    assert prices
    for record in island.markets:
        if record.good in prices and record.volume > 0:
            assert record.price == prices[record.good], record.good
    state = planning.state_of(island)
    assert state["goals"], "生産目標がない"
    assert "plan.queue" in island.stats and "plan.forced_saving" in island.stats


def test_targets_follow_demand_and_capacity(game, base):
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    config = game.config
    state = planning.state_of(island)
    good = next(g for g in state["goals"] if planning.capacities(island, game.defs, config).get(g))
    history = state["demand"][good]
    markup = config.get("planning.target_markup")
    assert state["goals"][good] == int(sum(history) / len(history) * (1 + markup))
    f = next(
        f
        for f in island.facilities
        if f.built and f.owner == OWNER_STATE and good in fac.outputs_of(game.defs, f)
    )
    island.scratch.pop("plan_capacity", None)
    goal = planning.target(island, f, game.defs, config)
    assert goal is not None and goal >= 0


def test_plan_orders_check_and_log(game, base):
    world = copy.deepcopy(base)
    market = island_of(world, "market")
    planned = island_of(world, "planned")
    mixed = island_of(world, "mixed")
    defs = game.defs
    ctx = context(game, world)
    with pytest.raises(OrderFailed) as failed:
        plan_orders.check_official_price(market, {"good": "food", "price": 10}, defs)
    assert failed.value.reason == "reason_not_planned"
    plan_orders.check_official_price(planned, {"good": "food", "price": 10}, defs)
    cost = planning.solve_costs(planned, defs, game.config)["food"]
    plan_orders.set_official_price(planned, {"good": "food", "price": cost * 3}, ctx)
    assert planning.official(planned)["food"] == cost * 3
    planning.review(planned, defs, game.config, ctx.turn)
    assert planning.official(planned)["food"] == cost * 3  # 命令で決めた値段は見直しで変えない
    plan_orders.set_official_price(planned, {"good": "food", "price": 0}, ctx)
    assert planning.official(planned)["food"] == cost * 3  # 上書きをやめても次の確かめまで据え置き
    planning.review(planned, defs, game.config, ctx.turn)
    assert planning.official(planned)["food"] == cost  # 幅を超えて原価より高いので改める
    with pytest.raises(OrderFailed) as unknown:
        plan_orders.check_priority(planned, {"industries": ["magic"]}, defs)
    assert unknown.value.reason == "reason_unknown_industry"
    chosen = defs.industries()[: game.config.get("planning.max_priority") + 1]
    with pytest.raises(OrderFailed):
        plan_orders.set_priority(planned, {"industries": chosen}, ctx)
    plan_orders.set_priority(planned, {"industries": chosen[:2]}, ctx)
    assert planning.priority_industries(planned) == chosen[:2]
    with pytest.raises(OrderFailed) as floor:
        plan_orders.check_price_floor(market, {"good": "grain", "price": 100}, defs)
    assert floor.value.reason == "reason_price_controls_forbidden"
    plan_orders.check_price_floor(mixed, {"good": "grain", "price": 100}, defs)
    plan_orders.set_price_floor(mixed, {"good": "grain", "price": 100}, ctx)
    assert mixed.policy.price_floor == {"grain": 100}
    plan_orders.set_price_floor(mixed, {"good": "grain", "price": 0}, ctx)
    assert mixed.policy.price_floor == {}
    for island in (planned, mixed):
        logs_ok(game, island)


def test_official_prices_stay_unless_the_gap_passes_the_band(game, base):
    """公定価格は原則据え置き: 原価との差が幅の中なら確かめで変えず、幅を超えた財だけ原価に改める。
    据え置く財（既定は食料）は原価を下回っていても据え置く。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    defs, config = game.defs, game.config
    band, held = planning.rules(island, config)
    assert band == config.get("planning.review_band") and "grain" in held and "coal" not in held
    costs = planning.solve_costs(island, defs, config)
    state = planning.state_of(island)
    inside = round(costs["coal"] * (1 + band / 2))  # 幅の中
    state["prices"].update(
        coal=inside,
        steel=round(costs["steel"] * (1 + band * 2)),  # 幅を超えて高い
        wood=round(costs["wood"] * (1 - band * 2)),  # 幅を超えて安い
        grain=round(costs["grain"] * (1 - band * 2)),  # 据え置く財は安くても据え置く
        fish=round(costs["fish"] * (1 + band * 2)),  # 据え置く財でも高すぎれば改める
    )
    grain = state["prices"]["grain"]
    revised = planning.review(island, defs, config, world.turn)
    costs = state["costs"]
    prices = planning.official(island)
    assert prices["coal"] == inside
    assert prices["steel"] == costs["steel"] and prices["wood"] == costs["wood"]
    assert prices["grain"] == grain and prices["fish"] == costs["fish"]
    assert {"steel", "wood", "fish"} <= set(revised) and not {"coal", "grain"} & set(revised)
    assert state["review"] == world.turn + config.get("planning.review_turns")
    assert island.log[-1].kind == "official_prices_revised"
    logs_ok(game, island)


def test_official_price_reform_and_rules_orders(game, base):
    world = copy.deepcopy(base)
    planned = island_of(world, "planned")
    market = island_of(world, "market")
    defs, config = game.defs, game.config
    ctx = context(game, world)
    with pytest.raises(OrderFailed) as failed:
        plan_orders.check_goods(market, {"goods": []}, defs)
    assert failed.value.reason == "reason_not_planned"
    with pytest.raises(OrderFailed) as unknown:
        plan_orders.check_goods(planned, {"held": ["magic"]}, defs)
    assert unknown.value.reason == "reason_unknown_good"
    # 決まり: 幅と据え置く財
    plan_orders.set_rules(planned, {"band": 5, "held": ["electricity", "grain"]}, ctx)
    assert planning.rules(planned, config) == (0.05, ["electricity", "grain"])
    plan_orders.set_rules(planned, {"band": 30}, ctx)
    assert planning.rules(planned, config) == (0.3, ["electricity", "grain"])
    # 改定: 名指しした財を今の原価に改め、上書きもやめる。空なら上書きしていない財すべて
    costs = planning.solve_costs(planned, defs, config)
    state = planning.state_of(planned)
    state["prices"]["coal"] = costs["coal"] * 2
    plan_orders.set_official_price(planned, {"good": "steel", "price": costs["steel"] * 2}, ctx)
    plan_orders.review_official_prices(planned, {"goods": ["steel"]}, ctx)
    assert planning.official(planned)["steel"] == state["costs"]["steel"]
    assert (
        "steel" not in state["manual"]
        and planned.economy.prices["steel"] == state["costs"]["steel"]
    )
    assert planning.official(planned)["coal"] == costs["coal"] * 2  # 名指ししていない
    plan_orders.set_official_price(planned, {"good": "food", "price": 7}, ctx)
    plan_orders.review_official_prices(planned, {}, ctx)
    assert planning.official(planned)["coal"] == state["costs"]["coal"]
    assert planning.official(planned)["food"] == 7  # 上書きした財は、名指ししなければそのまま
    assert planned.log[-1].kind == "official_prices_reformed"
    assert defs.orders["review_official_prices"].get("parliament") == "majority"
    logs_ok(game, planned)


def test_held_goods_below_cost_are_subsidized(game, base):
    """据え置く財を原価より安く売ると、差を補助金で埋める: 独立採算の施設には国庫から払い、国庫
    直轄の施設は帳簿にだけ残す。帳簿は崩れない。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    defs, config = game.defs, game.config
    costs = planning.solve_costs(island, defs, config)
    state = planning.state_of(island)
    state["prices"]["grain"] = max(1, costs["grain"] // 2)
    farms = [
        f
        for f in island.facilities
        if f.built and "grain" in fac.outputs_of(defs, f) and f.owner == OWNER_STATE
    ]
    assert farms
    farm = farms[0]  # 1つは独立採算にして、国庫から払うのを確かめる
    open_books(island)
    where = {"x": farm.x, "y": farm.y, "accounting": ACCOUNTING_SELF}
    accounts_stage.set_accounting(island, where, context(game, world))
    books_ok(game, island)
    seen, paid = [], []

    def each(w):
        seen.append(dict(island.stats))
        paid.extend(
            b.items.get(econ_books.SUBSIDY, 0) for b in island.books if b.facility == farm.id
        )

    run(game, world, 3, each)
    assert planning.official(island)["grain"] == max(1, costs["grain"] // 2)
    assert any(r.get("plan.subsidy", 0) > 0 for r in seen)
    assert all(r.get("plan.subsidy_unpaid", 0) == 0 for r in seen)
    assert any(p > 0 for p in paid)
    logs_ok(game, island)


def test_price_floor_keeps_the_market_price_up(game, base):
    world = copy.deepcopy(base)
    island = island_of(world, "mixed")
    good = "grain"
    price = next(r.price for r in island.markets if r.good == good)
    island.policy.price_floor[good] = price * 3
    run(game, world, 2)
    now = next(r.price for r in island.markets if r.good == good)
    assert now >= price * 3


def test_half_price_plan_queues_and_saves_but_keeps_producing(game, base):
    """揺さぶり: 計画経済の島で消費財の公定価格を原価の半分にすると、行列と強制貯蓄は増えるが、
    生産は止まらない。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    control = copy.deepcopy(world)
    goods = [g for g in ("food", "clothing", "consumer_goods") if g in planning.official(island)]
    half = {g: max(1, planning.official(island)[g] // 2) for g in goods}

    def extra(i):
        if i.id == island.id and world.turn == BASE_TURNS:
            return [("set_official_price", {"good": g, "price": p}) for g, p in half.items()]
        return []

    shocked, normal = [], []
    run(game, world, 36, lambda w: shocked.append(dict(island.stats)), extra)
    run(game, control, 36, lambda w: normal.append(dict(control.islands[island.id].stats)))
    assert all(planning.official(island)[g] == p for g, p in half.items())

    def mean(rows, key):
        return sum(r.get(key, 0) for r in rows[-24:]) / 24

    assert mean(shocked, "plan.queue") > mean(normal, "plan.queue")
    assert mean(shocked, "plan.forced_saving") > mean(normal, "plan.forced_saving")
    for g in goods:
        assert mean(shocked, f"production.{g}") > 0
    assert all(r.get("gdp", 0) > 0 for r in shocked)
    assert not {e.kind for e in island.log} & {"facility_bankrupt", "facility_closing"}


def test_planned_trade_is_a_state_monopoly(game, base):
    world = copy.deepcopy(base)
    run(game, world, 12)
    island = island_of(world, "planned")
    assert ledger.money(island, ledger.GOV) is not None
    # 計画経済の島の商社は国営で、島の市場には公定価格でしか売らない
    from hakoniwa_core.trade import is_trader

    traders = [f for f in island.facilities if is_trader(game.config, f)]
    assert traders and all(f.owner == OWNER_STATE for f in traders)


def test_planned_traders_export_unsold_goods_whatever_the_price(game, base):
    from hakoniwa_core.trade import traders as trade_traders
    from hakoniwa_core.trade import traders_of

    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    found = trade_traders.prospects(island, game.defs, game.config)
    prices = island.economy.prices
    # 公定価格が圏で売れる値段より高く、値段で見ると輸出しない物
    good = next(
        g
        for g, p in sorted(found.items())
        if p.export_bloc is not None and not trade_traders.exportable(p, prices.get(g, 0))
    )
    sellers = traders_of(island, game.config)
    assert sellers
    for f in sellers:
        trade_traders.goods_of(f).pop(good, None)
    island.markets = [m for m in island.markets if m.good != good]
    island.markets.append(
        MarketRecord(good=good, price=prices[good], volume=0, demand=0, supply=5000)
    )
    trade_traders.update_targets(island, game.defs, game.config, {}, {})
    # 国内で売れ残った物は、値段によらず輸出の向きにする（ボーキサイトの島の製錬所が動くように）
    items = [trade_traders.goods_of(f).get(good) for f in sellers]
    assert any(item and item.get("dir") == trade_traders.EXPORT for item in items), good
