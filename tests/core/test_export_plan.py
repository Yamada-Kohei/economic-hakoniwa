"""計画経済の輸出の計画（タブ 10 PL-62・タブ 9 FX-77）。

国営の商社は輸出で得た外貨だけで輸入するのに、輸出は売れ残りだけだったので、石炭も機械も作れない
島は燃料と設備の資材を買えず、発電所が止まっていた（調べ: 共有フォルダの plan-power-20261112）。
国が要る外貨を見積もり、それを稼ぐ輸出を生産目標に足す。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core.economy import entity_of, export_plan
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.modules import trade as trade_stage
from hakoniwa_core.orders import check_order
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import blocs, traders_of
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.trade import traders as trade_traders
from hakoniwa_core.units import QUANTITY_SCALE, money_of
from tests.core.test_finance import context
from tests.core.test_trade import _context, run

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


def planned_islands(world):
    return [i for i in world.islands.values() if i.regime["economy"] == "planned"]


def room_of(island, game, good):
    """国内の分を除いた余り（export_plan._choose と同じ: 全力の産出、前のターンに目標に届かなかった
    財は作れた量まで）。"""
    config = game.config
    domestic = planning.domestic_goal(island, config, good) or 0
    room = planning.capacities(island, game.defs, config).get(good, 0) - domestic
    made = island.stats.get(f"production.{good}")
    if planning.state_of(island).get("achieved", {}).get(good, 1.0) < 1 and made is not None:
        room = min(room, round(made * QUANTITY_SCALE) - domestic)
    return room


def exportable(world, game, goods=("wood", "grain", "fish", "stone", "iron_ore")):
    """余りがあり、外貨の圏へ輸出でき、行列がなく輸入もしていない (計画経済の島, 財)。"""
    config = game.config
    for island in planned_islands(world):
        found = trade_traders.prospects(island, game.defs, config)
        for g in goods:
            p = found.get(g)
            if (
                p is not None
                and p.export_bloc is not None
                and p.export_currency != island.id
                and room_of(island, game, g) > 0
                and not planning.state_of(island).get("queues", {}).get(g)
                and g not in trade_traders.imported(island, config)
            ):
                return island, g
    raise AssertionError("輸出できる余りのある財がない")


def no_seen():
    return {"imported": {}, "exported": {}, "returned": {}, "proceeds": {}}


def test_goal_adds_the_export_plan_and_stands_alone_without_demand(game, base):
    """生産目標 ＝ 国内の分 ＋ 輸出の計画の量。需要の履歴のない財も輸出の計画の量だけで目標を立てる
    （国内の需要が 0 で止まっていた鉱山も、輸出の計画で動き出す）。"""
    world = copy.deepcopy(base)
    island = planned_islands(world)[0]
    config = game.config
    state = planning.state_of(island)
    good = next(iter(state["demand"]))
    domestic = planning.domestic_goal(island, config, good)
    export_plan.state_of(island)["plan"] = {good: 5000, "iron_ore": 7000}
    state["demand"].pop("iron_ore", None)
    assert planning.goal_of(island, config, good) == domestic + 5000
    assert planning.goal_of(island, config, "iron_ore") == 7000
    export_plan.state_of(island)["plan"] = {}
    assert planning.goal_of(island, config, "iron_ore") is None


def test_plan_and_unsold_exports_are_separate_orders_and_first_imports_keep_weight(game, base):
    """商社は計画の分と売れ残りの分を別の注文で出し、計画の分に印を付ける。扱える重さは 1 段の財の
    輸入の見込みを先に取り置く（FX-77）。"""
    world = copy.deepcopy(base)
    island = planned_islands(world)[0]
    found = trade_traders.prospects(island, game.defs, game.config)
    good = next(g for g, p in found.items() if p.export_bloc is not None and g == "wood")
    [f, *_] = traders_of(island, game.config)
    trade_traders.goods_of(f).clear()
    item = trade_traders.entry(f, good)
    item.update({"dir": trade_traders.EXPORT, "x": 3000, trade_traders.PLAN_X: 4000})
    orders = [
        o
        for o in trade_traders.home_orders(island, game.defs, game.config, world.turn)
        if o.facility is f and o.good == good
    ]
    assert sorted((o.qty, o.plan) for o in orders) == [(3000, False), (4000, True)]
    # 石炭を扱える重さいっぱいに輸入するつもりなら、輸出の荷を買う重さは残らない
    coal = trade_traders.entry(f, "coal")
    cap = game.config.get("trade.trader_capacity") * QUANTITY_SCALE
    coal.update({"dir": trade_traders.IMPORT, "m": cap * QUANTITY_SCALE})
    orders = [
        o
        for o in trade_traders.home_orders(island, game.defs, game.config, world.turn)
        if o.facility is f and o.good == good
    ]
    assert orders == []
    # 外貨の圏の石炭は、商社の持つ外貨で買える量の重さだけ取り置く（外貨がなければ取り置かない。
    # 買えない石炭のために、外貨を稼ぐ輸出の荷を締め出さない）
    view = next(
        (
            (int(b), v)
            for b, v in trade_traders.views(island).items()
            if v["currency"] != island.id and v["prices"].get("coal", 0) > 0
        ),
        None,
    )
    if view is not None:
        coal["bm"] = view[0]
        fxmoney.holdings(island).get(entity_of(game.config, f), {}).pop(
            str(view[1]["currency"]), None
        )
        assert trade_traders.first_import_weight(island, game.defs, f) == 0
        orders = [
            o
            for o in trade_traders.home_orders(island, game.defs, game.config, world.turn)
            if o.facility is f and o.good == good
        ]
        assert (4000, True) in [(o.qty, o.plan) for o in orders]


def test_export_plan_buys_are_not_counted_as_domestic_demand(game, base):
    """輸出の計画の分の買いは、約定しなかった分も含めて注文の量で生産目標の需要の履歴から除く（除か
    ないと輸出の買いが次のターンの需要に入り、目標が毎ターン膨らむ）。"""
    from hakoniwa_core.modules.market import UNFUNDED

    world = copy.deepcopy(base)
    island, good = exportable(world, game)
    qty = min(20 * QUANTITY_SCALE, room_of(island, game, good))
    export_plan.state_of(island)["named"] = {good: qty}
    # 計画を今決めて（作れる量はターンごとに揺れるので、余りのある今の世界で）、次のターンに買う
    export_plan.update(world, island, game.defs, game.config, world.turn, no_seen(), {}, {}, {})
    assert export_plan.plans(island).get(good) == qty
    run(game, world, 1)
    ordered = island.scratch.get(planning.EXPORT_ORDERED, {}).get(good, 0)
    assert ordered > 0, "輸出の計画の分の注文が出ていない"
    record = next(m for m in island.markets if m.good == good)
    history = planning.state_of(island)["demand"][good]
    unfunded = island.scratch.get(UNFUNDED, {}).get(good, 0)
    assert history[-1] == max(0, record.demand - ordered) + unfunded


def test_earn_is_need_plus_reserve_gap_minus_unsold(game, base):
    """要る外貨は 1 段の財の輸入できた量と行列を圏の値段で数え、稼ぐ額 ＝ 要る外貨 ＋（蓄えの目標 −
    持つ外貨）÷ draw_turns − 売れ残りの稼ぎの平均（本人の B）。"""
    world = copy.deepcopy(base)
    config = game.config
    island = next(
        i
        for i in planned_islands(world)
        if (p := trade_traders.prospects(i, game.defs, config).get("coal")) is not None
        and p.import_bloc is not None
        and p.import_currency != i.id
    )
    p = trade_traders.prospects(island, game.defs, config)["coal"]
    c = str(p.import_currency)
    state = export_plan.state_of(island)
    export_plan.update(world, island, game.defs, config, world.turn, no_seen(), {}, {}, {})
    before = state["need"].get(c, 0)
    seen = no_seen()
    seen["imported"]["coal"] = 10 * QUANTITY_SCALE
    state["unsold"] = {c: [300, 600]}
    export_plan.update(world, island, game.defs, config, world.turn, seen, {}, {}, {})
    need = state["need"][c]
    assert need - before == money_of(p.import_price, 10 * QUANTITY_SCALE)
    assert need == state["needs"][c]["fuel"] + state["needs"][c]["equipment"]
    reserve = round(need * config.get("planning.export.reserve_turns"))
    assert state["reserve"][c] == reserve
    unsold = (300 + 600 + 0) // 3  # このターンの 0 も履歴に入る
    draw = config.get("planning.export.draw_turns")
    expected = need + (reserve - state["held"][c]) // draw - unsold
    assert state["earn"][c] == max(0, expected)


def test_reserve_below_target_keeps_fx_from_consumer_goods_only(game, base):
    """蓄えが目標より少ない間は、planning.export.reserve_rank（既定 4: 消費財）から下の段の輸入に
    蓄えを使わない。原料（3 段）と 1・2 段は使える。"""
    world = copy.deepcopy(base)
    island = planned_islands(world)[0]
    [bloc] = blocs.member_of(world, island.id)
    [f, *_] = traders_of(island, game.config)
    entity = entity_of(game.config, f)
    currency = bloc["currency"]
    if currency == island.id:
        pytest.skip("自国の通貨の圏")
    holding = fxmoney.holding(island, entity, currency)
    if holding <= 0:
        fxmoney.holdings(island).setdefault(entity, {})[str(currency)] = [1_000_000, 1_000_000]
        holding = 1_000_000
    others = [e for e in export_plan._entities(island, game.config) if e != entity]
    for e in others:
        fxmoney.holdings(island).get(e, {}).pop(str(currency), None)
    export_plan.state_of(island)["reserve"] = {str(currency): holding * 2}
    r = trade_stage._Round(world, _context(game, world))
    key = (island.id, entity, currency)
    r.fx_queue = {}
    assert r._fx_ahead(key, "automobile") == holding  # 目標に届かない: 消費財には使わない
    assert r._fx_ahead(key, "iron_ore") == 0  # 原料は国営工場が使うので止めない
    assert r._fx_ahead(key, "coal") == 0
    assert r._fx_ahead(key, "machinery") == 0
    # 3 段まで守る設定では原料も止める。ただし国営施設の設備に使う財（木材など）は使える
    # （本人の表の 3-1）
    game.config.raw["planning"]["export"]["reserve_rank"] = 3
    try:
        assert r._fx_ahead(key, "iron_ore") == holding
        export_plan.state_of(island)["essential"] = ["iron_ore"]
        assert r._fx_ahead(key, "iron_ore") == 0
    finally:
        game.config.raw["planning"]["export"]["reserve_rank"] = 4
    export_plan.state_of(island)["essential"] = []
    export_plan.state_of(island)["reserve"] = {str(currency): holding // 4}
    assert r._fx_ahead(key, "automobile") == holding // 4  # 目標を超える分は使える
    # 市場経済の島は蓄えを残さない
    market = next(i for i in world.islands.values() if i.regime["economy"] == "market")
    assert export_plan.reserve_of(market, game.config, entity, currency) == 0


def test_named_goods_skip_the_sellable_cap_and_none_drops_a_good(game, base):
    """命令で名指しした量は圏で売れる見込みで縮めず（余りまで）、輸出しないと名指しした財は候補から
    外す（本人の表の 2-2）。"""
    world = copy.deepcopy(base)
    config = game.config
    island, good = exportable(world, game)
    state = export_plan.state_of(island)
    state["named"] = {good: 1 * QUANTITY_SCALE}
    state["plan"] = {}
    export_plan.update(world, island, game.defs, config, world.turn, no_seen(), {}, {}, {})
    assert state["plan"].get(good) == 1 * QUANTITY_SCALE  # 売れた量も圏の不足も 0 だが名指しの量
    assert any(
        item.get(trade_traders.PLAN_X) == 1 * QUANTITY_SCALE
        for f in traders_of(island, config)
        for g, item in trade_traders.goods_of(f).items()
        if g == good
    )
    state["named"] = {good: 0}
    export_plan.update(world, island, game.defs, config, world.turn, no_seen(), {}, {}, {})
    assert good not in state["plan"]


def test_set_export_plan_order(game, base):
    """命令 set_export_plan: 計画経済だけ。島の外へ運べる財だけ、重なりなし。表をまるごと置き換え、
    空の表で名指しをやめる。"""
    world = copy.deepcopy(base)
    planned = planned_islands(world)[0]
    market = next(i for i in world.islands.values() if i.regime["economy"] == "market")
    registry = game.registry
    ok = {"plans": [{"good": "wood", "quantity": 12}, {"good": "grain", "quantity": 0}]}
    assert check_order(game.defs, registry, planned, "set_export_plan", ok).ok
    result = check_order(game.defs, registry, market, "set_export_plan", ok)
    assert result.reason == "reason_not_planned"
    twice = {"plans": [{"good": "wood", "quantity": 1}, {"good": "wood", "quantity": 2}]}
    result = check_order(game.defs, registry, planned, "set_export_plan", twice)
    assert result.reason == "reason_duplicate_export_plan"
    ctx = context(game, world)
    registry.orders["set_export_plan"].execute(planned, ok, ctx)
    assert export_plan.named(planned) == {"grain": 0, "wood": 12 * QUANTITY_SCALE}
    registry.orders["set_export_plan"].execute(planned, {"plans": []}, ctx)
    assert export_plan.named(planned) == {}


def test_planned_islands_get_an_export_plan_in_a_short_run(game, base):
    """標準の計画の島でも、輸出の計画は命令なしに自動で動く。計画の財は生産目標に入り、商社が
    受け持つ。"""
    world = copy.deepcopy(base)
    run(game, world, 10)
    states = [export_plan.state_of(i) for i in planned_islands(world)]
    assert any(s.get("need") for s in states), "要る外貨を見積もっていない"
    for island in planned_islands(world):
        plan = export_plan.plans(island)
        goals = planning.state_of(island).get("goals", {})
        for good, qty in plan.items():
            assert goals.get(good, 0) >= qty
            assert (
                sum(
                    trade_traders.goods_of(f).get(good, {}).get(trade_traders.PLAN_X, 0)
                    for f in traders_of(island, game.config)
                )
                == qty
            )


def test_leaving_the_plan_drops_the_export_plan(game, base):
    """計画経済をやめると、輸出の計画は生産目標と一緒に消え、商社の受け持ちも外れる。"""
    world = copy.deepcopy(base)
    island = planned_islands(world)[0]
    export_plan.state_of(island)["plan"] = {"wood": 1000}
    planning.stop(island)
    export_plan.update(world, island, game.defs, game.config, world.turn, no_seen(), {}, {}, {})
    assert export_plan.plans(island) == {}
    for f in traders_of(island, game.config):
        for item in trade_traders.goods_of(f).values():
            assert trade_traders.PLAN_X not in item
