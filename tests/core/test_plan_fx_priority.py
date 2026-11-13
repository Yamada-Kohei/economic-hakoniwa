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
    """取り置きを 1 段（燃料と主食）にした設定。既定の game.yaml は 0（取り置かない。4 通りの比べで
    輸出の計画だけの方が停電・家計・崩れのどれもよかった）。load_game は毎回読み直すので、この組の
    中だけで変わる。"""
    from hakoniwa_core.game import load_game

    game = load_game()
    game.config.raw["trade"]["plan_fx_reserved"] = 1
    return game


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
    # 外貨を使う順だけを見る（輸出の計画の外貨の蓄え PL-62 は test_export_plan.py で見る）
    island.ext.get("plan", {}).pop("export", None)
    for good, qty in goods.items():
        item = trade_traders.entry(f, good)
        item.update({"dir": trade_traders.IMPORT, "bm": bloc["id"], "m": qty})
        bloc["prices"].setdefault(good, 1000)
        bloc.setdefault("supply", {})[good] = 10 * qty
        bloc.setdefault("volume", {})[good] = qty
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


def test_cut_for_the_queue_is_shown_as_its_own_reason(game, base):
    """取り置きがなければ買えた分は「外貨が足りない」でなく取り置きのわけを出す（貿易の画面）。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    want = 20 * QUANTITY_SCALE
    bloc, f, key = _importing(game, world, island, {"automobile": want, "coal": want})
    r = trade_stage._Round(world, _context(game, world))
    members = [m for m in bloc["members"] if m in r.islands]
    r.fx_queue = r._plan_fx_queue(bloc, members)
    _bought(r, bloc, members, "automobile", f, key, 10**15)
    assert "why" not in trade_traders.goods_of(f)["automobile"]
    # 自動車の全部の分はあるが、石炭の取り置きを引くと足りない
    car, _ = r._import_terms(island, "automobile", bloc)
    money = money_of(car.limit, want) + r.fx_queue[key]["coal"] // 2
    _bought(r, bloc, members, "automobile", f, key, money)
    assert trade_traders.goods_of(f)["automobile"]["why"] == trade_stage.WHY_PRIORITY
    # 取り置きを引かなくても足りなければ、今までどおり外貨が足りない
    _bought(r, bloc, members, "automobile", f, key, money_of(car.limit, want) // 2)
    assert trade_traders.goods_of(f)["automobile"]["why"] in (
        trade_stage.WHY_MONEY,
        trade_stage.WHY_FUNDS,
    )


@pytest.mark.parametrize("case", ["no_trade", "no_offer", "blockade"])
def test_no_fx_is_kept_for_goods_that_did_not_come_in(game, base, case):
    """前のターンに圏で約定がなかった・売りがなかった財と、封鎖で船出を断られた財のためには
    外貨を寝かせない。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    want = 20 * QUANTITY_SCALE
    bloc, f, key = _importing(game, world, island, {"automobile": want, "coal": want})
    if case == "no_trade":
        bloc["volume"].pop("coal")
    elif case == "no_offer":
        bloc["supply"].pop("coal")
    else:
        trade_traders.goods_of(f)["coal"]["why"] = trade_stage.WHY_BLOCKADE
    r = trade_stage._Round(world, _context(game, world))
    members = [m for m in bloc["members"] if m in r.islands]
    r.fx_queue = r._plan_fx_queue(bloc, members)
    assert "coal" not in r.fx_queue.get(key, {})
    assert r._fx_ahead(key, "automobile") == 0


def test_goods_of_the_same_rank_keep_nothing_for_each_other(game, base):
    """同じ順の財（石炭と燃料）は互いに取り置かない（名前の順のまま）。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    want = 20 * QUANTITY_SCALE
    bloc, _, key = _importing(game, world, island, {"coal": want, "fuel": want})
    r = trade_stage._Round(world, _context(game, world))
    members = [m for m in bloc["members"] if m in r.islands]
    r.fx_queue = r._plan_fx_queue(bloc, members)
    assert r.fx_queue[key]["fuel"] > 0
    assert r._fx_ahead(key, "coal") == 0


def test_queue_is_capped_by_what_the_bloc_offered(game, base):
    """見込みは前のターンに圏で売りに出た量まで。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    want = 20 * QUANTITY_SCALE
    bloc, _, key = _importing(game, world, island, {"coal": want})
    bloc["supply"]["coal"] = want // 4
    r = trade_stage._Round(world, _context(game, world))
    members = [m for m in bloc["members"] if m in r.islands]
    terms, _ = r._import_terms(island, "coal", bloc)
    assert r._plan_fx_queue(bloc, members)[key]["coal"] == money_of(terms.limit, want // 4)


def test_bloc_round_clears_the_queue(game, base):
    """圏の市場を回し終えたら、取り置きは残らない（どの財の板寄せも済んだ）。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    want = 20 * QUANTITY_SCALE
    _importing(game, world, island, {"automobile": want, "coal": want})
    r = trade_stage._Round(world, _context(game, world))
    r._blocs()
    assert all(not queue for queue in r.fx_queue.values())


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


def test_worn_facility_buying_less_by_plan_shows_sales(game):
    """原料を計画どおり少なく買って、原料が設備の割合より少ないなら、縛ったのは売れ行き。"""
    world = generate_world(1, 1, game.config, game.defs)
    island = world.islands[1]
    plant = next(f for f in island.facilities if f.kind == "thermal_power_plant")
    plant.stock.clear()
    need = fac.recipe_of(game.defs, plant)["inputs"]["coal"]
    stock.add(plant.stock, "coal", fac.scale(need, fac.FULL // 4), world.turn, game.defs.goods)
    plant.economy["target"] = fac.FULL
    plant.economy["condition"] = fac.FULL // 2
    have, short = fac.input_ratio(game.defs, plant, "electricity")
    assert short is not None and have < fac.FULL // 2
    plant.economy["input_plan"] = have
    ctx = context(game, world, "production")
    plant.workers = fac.labor_demand(game.defs, plant, ctx.turn, "electricity")
    run_ = production.Production._prepare(island, plant, ctx, "electricity")
    assert run_.cause == "target"


def test_measure_counts_worn_equipment_on_its_own():
    """測りの稼働の内訳は、設備の傷みを止まった施設と分けて数える。"""
    from hakoniwa_core import measure

    assert measure.CAUSES["equipment"] == "equipment"
    assert "equipment" in measure.CAUSE_GROUPS


def test_only_the_first_ranks_are_kept(game, base):
    """取り置くのは順が trade.plan_fx_reserved までの財だけ（機械の分は取り置かない）。"""
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    want = 20 * QUANTITY_SCALE
    bloc, _, key = _importing(game, world, island, {"automobile": want, "machinery": want})
    r = trade_stage._Round(world, _context(game, world))
    members = [m for m in bloc["members"] if m in r.islands]
    r.fx_queue = r._plan_fx_queue(bloc, members)
    assert r.fx_queue[key]["machinery"] > 0
    assert game.config.get("trade.plan_fx_reserved") == 1
    assert r._fx_ahead(key, "automobile") == 0


def test_nothing_is_kept_by_default(game, base):
    """既定（game.yaml の trade.plan_fx_reserved: 0）では、燃料と主食の分も取り置かない。"""
    from hakoniwa_core.config import default_config_path, load_config

    assert load_config(default_config_path()).get("trade.plan_fx_reserved") == 0
    world = copy.deepcopy(base)
    island = island_of(world, "planned")
    want = 20 * QUANTITY_SCALE
    bloc, _, key = _importing(game, world, island, {"automobile": want, "coal": want})
    r = trade_stage._Round(world, _context(game, world))
    members = [m for m in bloc["members"] if m in r.islands]
    r.fx_queue = r._plan_fx_queue(bloc, members)
    assert r.fx_queue[key]["coal"] > 0
    game.config.raw["trade"]["plan_fx_reserved"] = 0
    try:
        assert r._fx_ahead(key, "automobile") == 0
    finally:
        game.config.raw["trade"]["plan_fx_reserved"] = 1
