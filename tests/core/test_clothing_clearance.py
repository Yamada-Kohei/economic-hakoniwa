"""衣料は持ち物（LB-44）と、売れ残りの値引き（MK-41）。"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import stock
from hakoniwa_core.economy import GENERAL, Payer, facility_entity, is_self_supporting, needs, retail
from hakoniwa_core.economy import freight as freight_rates
from hakoniwa_core.economy.households import move_people
from hakoniwa_core.economy.prices import SITE_USUAL, USUAL, subsidized_floor
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules.market import (
    CLEARANCE,
    CLEARING,
    FACILITY,
    SITE,
    Book,
    Market,
    Party,
    curve_cost,
    elastic_orders,
    inventory_turns,
)
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, money_of, round_money, scale_price
from tests.core.dismissal_rules import dismissal_rules
from tests.core.test_finance import WARMUP, context, island_of, run
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）

Q = QUANTITY_SCALE


@pytest.fixture(scope="module")
def warmed(game):
    """test_finance の warmed と同じ世界を、解雇の決まりを入れて作る。この試験は書いたときの
    世界の道（決まりのある世界）に頼るので、決まりを一時止めている間も同じ道で見る
    （tests/core/dismissal_rules.py）。"""
    with dismissal_rules(game.config):
        world = generate_world(1, 3, game.config, game.defs)
        run(game, world, WARMUP)
    return world


@pytest.fixture(autouse=True)
def _rules(game):
    with dismissal_rules(game.config):
        yield


@pytest.fixture
def clearance_on(game, monkeypatch):
    """値引き（MK-41）の試験は、線をふだんの在庫の 2 倍にして見る（設定が変わっても同じ線）。"""
    monkeypatch.setitem(game.config.get("market"), "clearance_ratio", 2)


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


def test_excess_stock_is_sold_off_step_by_step_down_to_half_the_cost(game, warmed, clearance_on):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f, good = _stocked_seller(game, island)
    ctx = context(game, world)
    config = game.config
    # 温めた世界で、もう値引きを始めている施設もある（数え始めからにする）
    f.economy.pop(CLEARANCE, None)
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
    # 在庫はみな今の指値でふだんの板寄せに出す（値引きの売りは、売れ残った超えた分の出し直し）
    assert sum(o.quantity for o in rest) == 100 * Q
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
    # （コードと同じく切り捨て。round だと変動費が奇数のとき 1 ずれる）
    least = round_money(floor * config.get("market.clearance_floor"))
    assert last.limit == -(-least * BASIS // BASIS)
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


def test_state_run_and_price_fixed_sellers_do_not_sell_off(game, warmed, clearance_on):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f, good = _stocked_seller(game, island)
    f.economy.setdefault("expected", {})[good] = 10 * Q
    f.stock.clear()
    stock.add(f.stock, good, 100 * Q, world.turn, game.defs.goods)
    island.policy.price_floor[good] = scale_price(island.economy.prices.get(good, 1), 1.0)
    for _ in range(5):
        assert _clearance_order(game, world, island, f, good)[0] is None


def test_selling_off_keeps_the_line_it_started_with(game, warmed, clearance_on):
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
    assert again.quantity == cut.quantity and sum(o.quantity for o in rest) == 100 * Q


def test_island_price_ignores_the_bargain_sale(game, warmed, clearance_on):
    """値引きの分は2回目（アウトレット）で売れ、島の値段はふだんの板寄せ（1回目）の値段を残す
    （ほかの売り手の指値・採算の見方・家計のいつもの値段を在庫処分の安値で引き下げない）。"""
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
    # 市場の値段（記録と島の値段）は、1回目の値段
    assert record.price == island.economy.prices[good]
    assert record.price >= auction.band(before, 1000)[0]
    assert island.economy.prices[good] >= auction.band(before, 1000)[0]
    # 値引きの売りで買い手が満たされたので、島の値段は前の値段より上がらない（値引きを除くと
    # 買い手が満たされないことになり、値引きのあいだ毎ターン値幅ずつ上がっていた。11-10）
    assert record.price <= before


def test_selling_off_stops_when_the_ratio_is_zero(game, warmed, monkeypatch):
    """market.clearance_ratio が 0 なら、在庫が目安を大きく超えても値引きの売りを出さない。"""
    monkeypatch.setitem(game.config.get("market"), "clearance_ratio", 0)
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f, good = _stocked_seller(game, island)
    f.economy.setdefault("expected", {})[good] = 10 * Q
    f.economy.pop(CLEARANCE, None)
    f.stock.clear()
    stock.add(f.stock, good, 100 * Q, world.turn, game.defs.goods)
    for _ in range(game.config.get("market.clearance_turns") + 1):
        assert _clearance_order(game, world, island, f, good)[0] is None
    assert CLEARANCE not in f.economy


# --- アウトレット形（MK-41、2026-11-11） ------------------------------------------------


def _outlet_book(normal_only=False):
    """買い 2 つ（指値 120 の 100 と 90 の 50）、売り手 a（指値 100 で 80）、売り手 b（在庫 40 を
    みな指値 130 で出し、超えた 30 を値引きの指値 60 で出し直す）の板。1回目は 120 で a が売り切れ、
    b は売れ残る。指値 120 の 20 と指値 90 の 50 が買えずに残る。"""
    shop = Party(FACILITY, "fac:a", Payer("fac:a"))
    dear = Party(FACILITY, "fac:b", Payer("fac:b"))
    cut = Party(FACILITY, "fac:b", Payer("fac:b"), clearance=True)
    buyers = [Party(FACILITY, f"fac:{n}", Payer(f"fac:{n}")) for n in ("x", "y")]
    book = Book(
        [(Order(100 * Q, 120), buyers[0]), (Order(50 * Q, 90), buyers[1])],
        [(Order(80 * Q, 100), shop), (Order(40 * Q, 130), dear)],
    )
    if not normal_only:
        book.sells.append((Order(30 * Q, 60), cut))
    return book


def _clear_captured(game, world, island, good, book):
    """Market._clear を回し、払い（_settle）に渡った板と結果を集める（払いはしない）。"""
    market, settled = Market(), []

    def settle(island, ctx, good, book, result, *rest):
        settled.append((book, copy.deepcopy(result)))
        return {}

    market._settle = settle  # type: ignore[method-assign]
    island.scratch[CLEARING] = (None, None)
    results, stale = {}, {}
    market._clear(island, context(game, world), good, {good: book}, {}, {}, results, stale)
    return settled, results[good]


def test_outlet_leaves_the_normal_market_as_it_was_without_the_sale(game, warmed):
    """(1) 値引きの売りは1回目に出さない: 1回目の値段と、ふだんの売りの約定量は値引きのない板と
    同じ。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    _, good = _stocked_seller(game, island)
    island.economy.prices[good] = 100
    plain, _ = _clear_captured(game, world, island, good, _outlet_book(normal_only=True))
    island.scratch.clear()
    both, merged = _clear_captured(game, world, island, good, _outlet_book())
    assert len(plain) == 1 and len(both) == 2
    (_, alone), (first_book, first) = plain[0], both[0]
    assert all(not p.clearance for _, p in first_book.sells)
    assert (first.price, first.volume, first.bought, first.sold) == (
        alone.price,
        alone.volume,
        alone.bought,
        alone.sold,
    )
    # まとめた結果の値段は1回目の値段、ふだんの売りの約定量も同じ
    assert merged.price == alone.price and merged.sold[:2] == alone.sold


def test_outlet_sells_only_to_the_buyers_left_over_at_or_below_the_first_price(game, warmed):
    """(2) 値引きの売りは、1回目で買えなかった買い手の残りにだけ、1回目の値段以下で売れる。
    約定量は2回の合計、買いの量はもとのまま、売りの量は値引きの分を含めた合計。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    _, good = _stocked_seller(game, island)
    island.economy.prices[good] = 100
    book = _outlet_book()
    settled, merged = _clear_captured(game, world, island, good, book)
    (_, first), (second_book, second) = settled
    # 2回目の板: 買いは1回目の残り（指値と並びはそのまま）、売りは値引きの売りだけ
    left = [o.quantity - first.bought[i] for i, (o, _) in enumerate(book.buys)]
    assert [o.quantity for o, _ in second_book.buys] == [q for q in left if q > 0]
    assert [o.limit for o, _ in second_book.buys] == [
        o.limit for i, (o, _) in enumerate(book.buys) if left[i] > 0
    ]
    assert [p.clearance for _, p in second_book.sells] == [True]
    assert 0 < second.volume and second.price <= first.price
    assert second.high == first.price  # アウトレットはふだんの値段より高く売らない
    # まとめた結果（もとの注文の並び）
    assert merged.volume == first.volume + second.volume
    assert merged.demand == sum(o.quantity for o, _ in book.buys)
    # 売りの量は1回目に出した在庫（値引きの出し直しを2度数えない）
    assert merged.supply == sum(o.quantity for o, p in book.sells if not p.clearance)
    assert merged.sold == [*first.sold, second.sold[0]]
    assert merged.sold[1] + merged.sold[2] <= book.sells[1][0].quantity  # b の在庫まで
    assert sum(merged.bought) == merged.volume
    assert all(merged.bought[i] <= o.quantity for i, (o, _) in enumerate(book.buys))


def test_outlet_pays_each_round_at_its_own_price(game, warmed, clearance_on):
    """(2) 払いは回ごとにその回の値段: 値引きの売りの代金（測りの記録）は、1回目の値段より高く
    ならず、売り手の約定はどれも1回目の値段以下。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f, good = _stocked_seller(game, island)
    _force_clearance(game, world, island, f, good)
    game.engine.run_turn(world)
    record = next(m for m in island.markets if m.good == good)
    sold = island.stats.get(f"clearance.sold.{good}", 0)
    assert sold > 0  # 値引きの分が売れた
    paid = island.stats[f"clearance.paid.{good}"]
    top = max(record.price, auction.band(record.price, 1000)[1])
    assert paid <= money_of(top, round(sold * Q))
    name = facility_entity(f.id)
    for t in island.trades:
        if t.good == good and t.seller == name:
            assert t.amount <= money_of(top, t.quantity)


def _force_clearance(game, world, island, f, good):
    """f が good の在庫をみな値引きの売りに出すようにする（指値は変動費の clearance_floor 倍）。"""
    config = game.config
    f.economy.setdefault("expected", {})[good] = 10 * Q
    f.stock.clear()
    stock.add(f.stock, good, 1000 * Q, world.turn, game.defs.goods)
    entry = f.economy.setdefault(CLEARANCE, {}).setdefault(good, {"turns": 0, "steps": 0})
    entry.update(start=1, line=0, turns=config.get("market.clearance_turns"))


def test_island_price_does_not_climb_while_the_outlet_fills_the_shortage(
    game, warmed, clearance_on
):
    """(3) 値引きが続く何ターンかで、2回目が品薄を満たしているあいだ、島の値段は品薄で上がり続け
    ない（1回目の値段が上がっても、品薄を値引きの分がみな満たせば前の値段のまま）。上がってよい
    のは、ふだんの売りの指値そのもの（作る費用）が前の値段より上がったときだけ（値引きのないとき
    と同じ）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f, good = _stocked_seller(game, island)
    held = 0
    for _ in range(4):
        _force_clearance(game, world, island, f, good)
        # ふだんの売りの希望価格は島の値段のまま（品薄で売り手が指値を上げた分は見ない）
        rate = freight_rates.facility_rates(island, game.defs, game.config).get(f.id, 0)
        before = island.economy.prices[good]
        f.economy.setdefault("ask", {})[good] = before * (BASIS - rate) // BASIS
        cost = subsidized_floor(
            island,
            game.defs,
            game.config,
            f,
            good,
            island.economy.prices,
            f.economy.get("wage", 0),
            rate,
            world.turn + 1,
        )
        ask = -(-cost * BASIS // (BASIS - rate))  # 費用で決まるふだんの売りの指値
        game.engine.run_turn(world)
        record = next(m for m in island.markets if m.good == good)
        assert island.stats.get(f"clearance.sold.{good}", 0) > 0
        assert record.price == island.economy.prices[good]
        assert island.economy.prices[good] <= max(before, ask)
        held += island.economy.prices[good] == before
    assert held >= 1  # 品薄を値引きで満たし、前の値段のままのターンがある


def test_island_price_keeps_the_first_price_unless_the_outlet_filled_every_buyer(game, warmed):
    """(3) 1回目の値段が前の値段より高いとき: 2回目の後に1回目の値段で買いたい買い手が残れば
    1回目の値段、残らなければ前の値段。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    _, good = _stocked_seller(game, island)
    island.economy.prices[good] = 100
    # 値引きの分が 1 回目の残り（指値 120 の 20 と指値 90 の 50）をみな満たす
    settled, _ = _clear_captured(game, world, island, good, _outlet_book())
    assert settled[0][1].price > 100
    assert island.scratch[CLEARANCE][good] == 100
    # 値引きの分が少なく、指値 120 の買い手が残る
    island.scratch.clear()
    book = _outlet_book()
    order, party = book.sells[2]
    book.sells[2] = (Order(5 * Q, order.limit), party)
    settled, merged = _clear_captured(game, world, island, good, book)
    assert merged.bought[0] < book.buys[0][0].quantity
    assert island.scratch[CLEARANCE][good] == settled[0][1].price
    # 品薄がないまま1回目の値段が上がった（売り手の指値が上がった）ときは、値引きの売りがあっても
    # 値引きのないときと同じく上がる（2回目で何も売れない）
    island.scratch.clear()
    shop = Party(FACILITY, "fac:a", Payer("fac:a"))
    dear = Party(FACILITY, "fac:b", Payer("fac:b"))
    cut = Party(FACILITY, "fac:b", Payer("fac:b"), clearance=True)
    buyer = Party(FACILITY, "fac:x", Payer("fac:x"))
    book = Book(
        [(Order(5 * Q, 120), buyer), (Order(3 * Q, 95), buyer)],
        [(Order(10 * Q, 110), shop), (Order(10 * Q, 140), dear), (Order(10 * Q, 105), cut)],
    )
    settled, merged = _clear_captured(game, world, island, good, book)
    assert len(settled) == 1 and settled[0][1].price > 100  # 2回目は約定しない
    assert island.scratch[CLEARANCE][good] == settled[0][1].price


def test_outlet_keeps_pooled_household_tiers_within_what_they_reserved(game, warmed):
    """家計の値段の曲線の段（取り置きを分け合う）は、1回目に高い段を払った残りの取り置きで、
    2回目にどの値段で約定しても払える量まで減らして出し直す（取り置きより多く払わない）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    _, good = _stocked_seller(game, island)
    island.economy.prices[good] = 100
    tiers = [(Q, 100), (Q, 90)]
    head = Party("household", "hh:general", Payer("hh:general"), layer=GENERAL)
    head.reserved = curve_cost(tiers, 0, 0)  # どれか1つの値段で約定したときの上限（180）
    member = Party("household", "hh:general", Payer("hh:general"), layer=GENERAL, pool=head)
    shop = Party(FACILITY, "fac:a", Payer("fac:a"))
    dear = Party(FACILITY, "fac:b", Payer("fac:b"))
    cut = Party(FACILITY, "fac:b", Payer("fac:b"), clearance=True)
    book = Book(
        [(Order(Q, 100), head), (Order(Q, 90), member)],
        [(Order(Q, 100), shop), (Order(5 * Q, 200), dear), (Order(5 * Q, 85), cut)],
    )
    market, paid = Market(), []

    def settle(island, ctx, good, book, result, *rest):  # 払った分だけ取り置きを減らす
        for i, (_, party) in enumerate(book.buys):
            cost = money_of(result.price, result.bought[i])
            (party.pool or party).reserved -= cost
            paid.append(cost)
        return {}

    market._settle = settle  # type: ignore[method-assign]
    island.scratch[CLEARING] = (None, None)
    results = {}
    market._clear(island, context(game, world), good, {good: book}, {}, {}, results, {})
    assert results[good].bought[0] == Q  # 1回目に高い段を 100 で買った
    assert 0 < results[good].bought[1] < Q  # 2回目（85）は残りの取り置き（80）で買える分だけ
    assert head.reserved >= 0 and sum(paid) <= curve_cost(tiers, 0, 0)


def test_outlet_asks_do_not_lower_the_usual_price_or_the_materials_estimate(game, warmed):
    """(4) 値引きの売りの指値で、家計のいつもの値段と工事の資材の積算が下がらない（ふだんの売りの
    指値なら下がる）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    _, good = _stocked_seller(game, island)
    island.economy.bought_history[good] = [0]
    island.economy.ext.setdefault(USUAL, {})[good] = {"stall": 1, "low": 10**6}
    island.economy.ext.setdefault(SITE_USUAL, {})[good] = {"stall": 1, "low": 10**6}
    seller = Party(FACILITY, "fac:a", Payer("fac:a"))
    site = Party(SITE, "site", Payer("gov"))

    def adapt(extra):
        here = copy.deepcopy(island)
        book = Book([(Order(Q, 500), site)], [(Order(Q, 400), seller), *extra])
        Market._adapt_usual(here, ctx, {good: book})
        Market._adapt_materials(here, ctx, {good: book})
        ext = here.economy.ext
        return ext.get(USUAL, {}).get(good), ext.get(SITE_USUAL, {}).get(good)

    plain = adapt([])
    outlet = adapt([(Order(Q, 3), Party(FACILITY, "fac:b", Payer("fac:b"), clearance=True))])
    cheap = adapt([(Order(Q, 3), Party(FACILITY, "fac:b", Payer("fac:b")))])
    assert outlet == plain
    assert cheap != plain  # ふだんの売りの安い指値なら数える（試験が効いていることの確かめ）
