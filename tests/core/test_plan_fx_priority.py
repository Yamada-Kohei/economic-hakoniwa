"""計画経済の国営の商社が少ない外貨を使う順（タブ 9 FX-76）と、傷んだ設備で縛られた施設の表示。

圏の市場は財の名前の順に回るので、前は自動車や衣類が石炭と燃料より先に外貨を使っていた（種1 の
島 6 は 1 ターンに 59 万を稼ぎ、石炭と燃料の分 19 万は払えたのに、石炭は狙いの 16%、燃料は 5%、
機械は 0）。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.economy import entity_of
from hakoniwa_core.modules import production
from hakoniwa_core.modules import trade as trade_stage
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import blocs, fx_priority, tradable_goods, traders_of
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


def island_of(world, economy):
    return next(i for i in world.islands.values() if i.regime["economy"] == economy)


def test_every_tradable_good_has_an_fx_priority(game):
    """島の外へ運べる財には外貨を使う順を書く。燃料と主食が先、消費財が後。"""
    for good in tradable_goods(game.defs):
        assert "fx_priority" in game.defs.goods[good], good
    rank = {g: fx_priority(game.defs, g) for g in tradable_goods(game.defs)}
    assert rank["coal"] == rank["fuel"] == rank["grain"] == 1
    assert rank["machinery"] == rank["steel"] == 2
    assert rank["iron_ore"] == 3
    assert rank["automobile"] == rank["clothing"] == 4
    assert fx_priority(game.defs, "no_such_good") == 3


def _importing(game, world, island, goods):
    """island の商社の輸入の狙いを goods（財 → 量）だけにして、圏と商社と取り置きの鍵を返す。"""
    [bloc] = blocs.member_of(world, island.id)
    [f, *_] = traders_of(island, game.config)
    trade_traders.goods_of(f).clear()
    for good, qty in goods.items():
        item = trade_traders.entry(f, good)
        item.update({"dir": trade_traders.IMPORT, "bm": bloc["id"], "m": qty})
        bloc["prices"].setdefault(good, 1000)
        bloc.setdefault("supply", {})[good] = 10 * qty
    key = (island.id, entity_of(game.config, f), bloc["currency"])
    return bloc, f, key


def _bought(r, bloc, members, good, f, key, money):
    buys, _, bids, _ = r._bloc_orders(bloc, members, good, {}, {key: money})
    return sum(o.quantity for o, b in zip(buys, bids, strict=True) if b.facility is f)


def test_planned_trader_keeps_fx_for_coal_before_buying_cars(game, base):
    """名前の順で先に来る自動車は、外貨を使う順で先の石炭の分を取り置いた残りでしか買わない。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    want = 20 * QUANTITY_SCALE
    bloc, f, key = _importing(game, world, island, {"automobile": want, "coal": want})
    r = trade_stage._Round(world, _context(game, world))
    members = [m for m in bloc["members"] if m in r.islands]
    r.fx_queue = r._plan_fx_queue(bloc, members)
    coal = r.fx_queue[key]["coal"]
    assert coal > 0
    # 石炭の分しかない: 自動車は買わず、石炭は全部買う
    assert _bought(r, bloc, members, "automobile", f, key, coal) == 0
    r.fx_queue[key].pop("coal")  # 石炭の板寄せが済んだ
    assert _bought(r, bloc, members, "coal", f, key, coal) > 0
    # 取り置きがなければ（前の作り）、同じ外貨で自動車を先に買っていた
    r.fx_queue = {}
    assert _bought(r, bloc, members, "automobile", f, key, coal) > 0


def test_no_fx_is_kept_for_goods_nobody_sold_last_turn(game, base):
    """前のターンに圏で売りに出なかった財のためには外貨を寝かせない。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    want = 20 * QUANTITY_SCALE
    bloc, f, key = _importing(game, world, island, {"automobile": want, "coal": want})
    bloc["supply"].pop("coal")
    r = trade_stage._Round(world, _context(game, world))
    members = [m for m in bloc["members"] if m in r.islands]
    r.fx_queue = r._plan_fx_queue(bloc, members)
    assert "coal" not in r.fx_queue.get(key, {})
    assert r._fx_ahead(key, "automobile") == 0


def test_market_and_mixed_traders_keep_no_fx(game, base):
    """市場経済・混合経済の島の商社は今までどおり（取り置きを数えない）。"""
    world = copy.deepcopy(base)
    want = 20 * QUANTITY_SCALE
    for economy in ("market", "mixed"):
        island = island_of(world, economy)
        bloc, _, key = _importing(game, world, island, {"automobile": want, "coal": want})
        r = trade_stage._Round(world, _context(game, world))
        members = [m for m in bloc["members"] if m in r.islands]
        assert key not in r._plan_fx_queue(bloc, members)


def test_queue_is_the_target_valued_at_the_bid(game, base):
    """取り置きの見込みは、狙う量の残りに圏の市場に出す指値を掛けた額（前のターンの売りまで）。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    want = 3 * QUANTITY_SCALE
    bloc, _, key = _importing(game, world, island, {"coal": want})
    r = trade_stage._Round(world, _context(game, world))
    members = [m for m in bloc["members"] if m in r.islands]
    terms, _ = r._import_terms(island, "coal", bloc)
    queue = r._plan_fx_queue(bloc, members)
    assert queue[key]["coal"] == money_of(terms.limit, want)


def test_worn_facility_shows_equipment_not_sales(game):
    """雇う人数は設備の割合までなので、傷んだ設備で縛られた施設は「売れ行きに合わせた」ではなく
    「設備の傷み」と出す（計画経済の傷んだ発電所が「売れ行きに合わせた」と出ていた）。"""
    world = generate_world(1, 1, game.config, game.defs)
    island = world.islands[1]
    plant = next(f for f in island.facilities if f.kind == "thermal_power_plant")
    stock.add(plant.stock, "coal", 100 * QUANTITY_SCALE, world.turn, game.defs.goods)
    plant.economy["target"] = fac.FULL
    plant.economy["condition"] = fac.FULL // 2
    ctx = context(game, world, "production")
    plant.workers = fac.labor_demand(game.defs, plant, ctx.turn, "electricity")
    run_ = production.Production._prepare(island, plant, ctx, "electricity")
    assert run_.cause == "equipment"
    assert run_.rate <= fac.FULL // 2
    # 操業目標が設備より低ければ、縛ったのは売れ行き
    plant.economy["target"] = fac.FULL // 4
    plant.workers = fac.labor_demand(game.defs, plant, ctx.turn, "electricity")
    run_ = production.Production._prepare(island, plant, ctx, "electricity")
    assert run_.cause == "target"
