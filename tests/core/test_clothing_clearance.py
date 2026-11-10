"""衣料は持ち物（LB-44）と、売れ残りの値引き（MK-41）。"""

from __future__ import annotations

import copy

from hakoniwa_core import stock
from hakoniwa_core.economy import GENERAL, is_self_supporting, needs, retail
from hakoniwa_core.economy.households import move_people
from hakoniwa_core.economy.prices import USUAL, subsidized_floor
from hakoniwa_core.market import auction
from hakoniwa_core.modules.market import (
    CLEARANCE,
    Market,
    curve_cost,
    elastic_orders,
    inventory_turns,
)
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, scale_price
from tests.core.test_finance import context, island_of
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401

Q = QUANTITY_SCALE

# --- 衣料は持ち物（LB-44） --------------------------------------------------------------


def test_clothes_wear_out_and_households_replace_the_worn_part_and_some_of_the_gap(game):
    config = game.config
    target = needs.target_of(config, "clothing")
    full = round(1000 * target * Q)
    h = {"persons": 1000}
    assert needs.clothes_held(config, h, "clothing") == full  # 記録がなければ目安どおり持っている
    wear = needs.clothes_wear(config, full)
    # ふだんの買う量は前の毎月の目安（0.00004 / 人）と同じ
    assert wear == round(1000 * needs.per_turn_target(game.defs, config, "clothing") * Q)
    assert abs(needs.per_turn_target(game.defs, config, "clothing") - 0.00004) < 1e-9
    assert needs.clothes_wanted(config, h, "clothing") == (wear, wear)
    h[needs.CLOTHES] = {"clothing": full // 2}  # 半分しか持っていない
    want, whole = needs.clothes_wanted(config, h, "clothing")
    half_wear = needs.clothes_wear(config, full // 2)
    catch = round((full - full // 2) * config.get("household.clothing.catch_up"))
    assert want == half_wear + catch and whole == half_wear + full - full // 2
    # 人が減って目安より多く持っている層は、多い分が傷んで減るまで買い替えない
    h[needs.CLOTHES] = {"clothing": full * 2}
    assert needs.clothes_wanted(config, h, "clothing") == (0, 0)
    h[needs.CLOTHES] = {"clothing": full + wear // 2}
    assert (
        needs.clothes_wanted(config, h, "clothing")[0]
        == needs.clothes_wear(config, full + wear // 2) - wear // 2
    )


def test_clothes_kept_after_the_market_and_satisfaction_follows_what_is_held(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    config, defs = game.config, game.defs
    h = island.economy.households[GENERAL]
    full = round(h["persons"] * needs.target_of(config, "clothing") * Q)
    h[needs.CLOTHES] = {"clothing": full}
    island.scratch["household_goods"] = {GENERAL: {"clothing": 0}}
    needs.close_clothes(island, config, defs)
    held = h[needs.CLOTHES]["clothing"]
    assert held == full - needs.clothes_wear(config, full)  # 買えなかった月は傷んだ分だけ減る
    # 1か月買えなくても満足はほとんど下がらない（前は買えた量で見て、すぐ 0 になった）
    sat = needs.satisfaction(island, config, defs, GENERAL, h["persons"])
    assert sat["clothing"] >= Q * 95 // 100
    h[needs.CLOTHES]["clothing"] = full // 4
    assert needs.satisfaction(island, config, defs, GENERAL, h["persons"])["clothing"] <= Q // 4


def test_people_moving_between_layers_take_their_clothes(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    households = island.economy.households
    layer = next(n for n, x in households.items() if n != GENERAL and "share" in x)
    general, other = households[GENERAL], households[layer]
    general[needs.CLOTHES] = {"clothing": 1000 * Q}
    other[needs.CLOTHES] = {"clothing": 0}
    before = general["persons"]
    moved = before // 10
    general["persons"] -= moved
    other["persons"] += moved
    move_people(island, {layer: moved})
    assert other[needs.CLOTHES]["clothing"] == 1000 * Q * moved // before
    assert general[needs.CLOTHES]["clothing"] + other[needs.CLOTHES]["clothing"] == 1000 * Q
    # 記録のない層（新しく置いた層）へ移るときは、元の層と同じ1人あたりの量を持っていたことにする
    del other[needs.CLOTHES]
    general[needs.CLOTHES] = {"clothing": 1000 * Q}
    persons = general["persons"]
    general["persons"] -= moved
    other["persons"] += moved
    stayed = other["persons"] - moved
    move_people(island, {layer: moved})
    carried = 1000 * Q * moved // persons
    assert other[needs.CLOTHES]["clothing"] == 1000 * Q * stayed // persons + carried


def test_elastic_orders_buy_less_when_dearer_and_stay_in_the_budget():
    multiples = [0.5, 0.7, 0.85, 1.0, 1.2, 1.5, 2.0, 3.0]
    orders = elastic_orders(100 * Q, 300 * Q, 1000, 0.7, multiples, (1, 10_000), 10**9, 0, 0)

    def bought_at(price):
        return sum(q for q, lim in orders if lim >= price)

    assert bought_at(1000) == 100 * Q  # 真ん中の値段で欲しい量
    assert bought_at(2000) == round(100 * Q * 2**-0.7)  # 2倍なら約 6 割
    assert bought_at(3000) < bought_at(2000) < bought_at(1500) < bought_at(1000)
    assert bought_at(3001) == 0  # 一番上の段より高ければ買わない（買い替えを延ばす）
    assert bought_at(500) == round(100 * Q * 0.5**-0.7)  # 安ければ先に買う（目安までの量まで）
    # 指値は前の値段の値幅の上端まで（1ターンに値幅より高くは上がらない）
    capped = elastic_orders(100 * Q, 300 * Q, 1000, 0.7, multiples, (900, 1100), 10**9, 0, 0)
    assert max(lim for _, lim in capped) == 1100
    # 前の値段の値幅の下端より安い段は出さない（約定しないのに買えなかった量に数えられる）
    assert min(lim for _, lim in capped) >= 900
    # 予算: どの値段で約定しても払う額は予算を超えない
    tight = elastic_orders(100 * Q, 300 * Q, 1000, 0.7, multiples, (1, 10_000), 50_000, 0, 0)
    assert curve_cost(tight, 0, 0) <= 50_000
    assert sum(q for q, lim in tight if lim >= 3000) <= 50_000 * Q // 3000  # 予算で 16.6 単位まで


def _clothing_demand(game, world, island, price, usual):
    ctx = context(game, world, "market")
    economy = island.economy
    economy.prices["clothing"] = price
    economy.price_history["clothing"] = [usual] * 12
    economy.bought_history["clothing"] = [Q] * 12
    economy.ext.get(USUAL, {}).pop("clothing", None)
    island.scratch.clear()
    island.scratch["freight_household"] = 0
    retail.prepare(island, game.config)
    books = {}
    Market()._household_orders(island, ctx, books, 0, {"clothing": 10**12})
    return books["clothing"].buys if "clothing" in books else []


def test_households_buy_fewer_clothes_when_the_price_runs_away(game, warmed):
    """W-26: 前は値段が2倍でも 100 倍でも同じ量を買い、品薄の島で衣料が元の 194 倍になった。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    usual = 1000
    normal = _clothing_demand(game, world, island, usual, usual)
    dear = _clothing_demand(game, world, island, 3 * usual, usual)

    def at(buys, price):
        return sum(o.quantity for o, _ in buys if o.limit >= price)

    assert at(normal, usual) > 0
    assert at(dear, 3 * usual) < at(normal, usual) * 0.6
    assert at(dear, 3 * usual + 1) == 0
    # 取り置きは1つにまとめ、どの段の払いもそこから引く
    pools = [p for _, p in dear if p.reserved > 0]
    assert all(p.pool is not None for _, p in dear if p.reserved == 0)
    assert len({p.layer for p in pools}) == len(pools)


# --- 売れ残りの値引き（MK-41） ----------------------------------------------------------


def _stocked_seller(game, island):
    defs = game.defs
    for f in sorted(island.facilities, key=lambda f: f.id):
        if not is_self_supporting(f) or not f.recipe:
            continue
        outputs = [
            g
            for g in defs.facilities[f.kind]["recipes"][f.recipe]["outputs"]
            if g != game.config.get("power.good") and defs.goods[g].get("shelf_life") is None
        ]
        if outputs:
            return f, outputs[0]
    raise AssertionError("持ち越せる物を作る独立採算の施設がない")


def _clearance_order(game, world, island, f, good):
    books = {}
    Market()._facility_orders(island, context(game, world), books, {})
    mine = [
        (o, p) for o, p in books[good].sells if p.facility is not None and p.facility.id == f.id
    ]
    cut = [(o, p) for o, p in mine if p.clearance]
    return cut[0][0] if cut else None, [o for o, p in mine if not p.clearance]


def test_excess_stock_is_sold_off_step_by_step_down_to_half_the_cost(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f, good = _stocked_seller(game, island)
    ctx = context(game, world)
    config = game.config
    f.economy.setdefault("expected", {})[good] = 10 * Q
    f.stock.clear()
    stock.add(f.stock, good, 100 * Q, world.turn, game.defs.goods)
    normal = round(10 * Q * inventory_turns(ctx, good))
    line = normal * config.get("market.clearance_ratio")
    # 倍を超えても clearance_turns ターン続くまでは値引きしない
    for _ in range(config.get("market.clearance_turns") - 1):
        assert _clearance_order(game, world, island, f, good)[0] is None
    cut, rest = _clearance_order(game, world, island, f, good)
    assert cut is not None and cut.quantity == 100 * Q - line  # 超えた分だけ
    assert sum(o.quantity for o in rest) == line  # ふだんの在庫の分は今の指値のまま
    floor = subsidized_floor(
        island,
        game.defs,
        config,
        f,
        good,
        island.economy.prices,
        f.economy.get("wage", 0),
        0,
        ctx.turn,
    )
    first = cut.limit
    assert first <= max(o.limit for o in rest)
    # 売れ残るたびに値幅ずつ下げる
    entry = f.economy[CLEARANCE][good]
    entry["offered"], entry["sold"] = cut.quantity, 0
    second, _ = _clearance_order(game, world, island, f, good)
    assert second.limit < first
    for _ in range(40):
        entry = f.economy[CLEARANCE][good]
        entry["offered"], entry["sold"] = cut.quantity, 0
        last, _ = _clearance_order(game, world, island, f, good)
    # 変動費の clearance_floor 倍で止まる
    assert last.limit == -(-round(floor * config.get("market.clearance_floor")) * BASIS // BASIS)
    # 売れた月は下げない
    entry = f.economy[CLEARANCE][good]
    entry["offered"] = entry["sold"] = cut.quantity
    steps = entry["steps"]
    _clearance_order(game, world, island, f, good)
    assert f.economy[CLEARANCE][good]["steps"] == steps
    # 在庫が倍まで戻れば数え直し、今の指値に戻る
    f.stock.clear()
    stock.add(f.stock, good, line, world.turn, game.defs.goods)
    cut, _ = _clearance_order(game, world, island, f, good)
    assert cut is None and CLEARANCE not in f.economy


def test_state_run_and_price_fixed_sellers_do_not_sell_off(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f, good = _stocked_seller(game, island)
    f.economy.setdefault("expected", {})[good] = 10 * Q
    f.stock.clear()
    stock.add(f.stock, good, 100 * Q, world.turn, game.defs.goods)
    island.policy.price_floor[good] = scale_price(island.economy.prices.get(good, 1), 1.0)
    for _ in range(5):
        assert _clearance_order(game, world, island, f, good)[0] is None


def test_selling_off_keeps_the_line_it_started_with(game, warmed):
    """値引きの売りに先を越されてふだんの分が売れず、売れ行きの見込みが縮んでも、値引きに回す量は
    始めたときの線を超えた分のまま（在庫が全部値引きに回らない）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f, good = _stocked_seller(game, island)
    config = game.config
    f.economy.setdefault("expected", {})[good] = 10 * Q
    f.stock.clear()
    stock.add(f.stock, good, 100 * Q, world.turn, game.defs.goods)
    for _ in range(config.get("market.clearance_turns")):
        cut, _ = _clearance_order(game, world, island, f, good)
    f.economy["expected"][good] = Q  # 見込みが縮んだ
    again, rest = _clearance_order(game, world, island, f, good)
    assert (
        again.quantity == cut.quantity and sum(o.quantity for o in rest) == 100 * Q - cut.quantity
    )


def test_island_price_ignores_the_bargain_sale(game, warmed):
    """値引きの売りは約定には入るが、島の値段は値引きを除いて決まった値段を残す（ふだんの値段は
    残る。ほかの売り手の指値・採算の見方・家計のいつもの値段を在庫処分の安値で引き下げない）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f, good = _stocked_seller(game, island)
    config = game.config
    f.economy.setdefault("expected", {})[good] = 10 * Q
    f.stock.clear()
    stock.add(f.stock, good, 100 * Q, world.turn, game.defs.goods)
    for _ in range(config.get("market.clearance_turns") - 1):
        _clearance_order(game, world, island, f, good)
    f.economy[CLEARANCE][good]["start"] = 1  # 値引きの指値を 1 まで下げておく
    f.economy[CLEARANCE][good]["line"] = 0
    f.economy[CLEARANCE][good]["turns"] = config.get("market.clearance_turns")
    before = island.economy.prices.get(good)
    game.engine.run_turn(world)
    entry = f.economy.get(CLEARANCE, {}).get(good)
    record = next(m for m in island.markets if m.good == good)
    assert entry is not None and entry["sold"] > 0  # 値引きの分が売れた
    assert record.price < island.economy.prices[good]  # 約定は安値、島の値段はふだんの値段
    assert island.economy.prices[good] >= auction.band(before, 1000)[0]
