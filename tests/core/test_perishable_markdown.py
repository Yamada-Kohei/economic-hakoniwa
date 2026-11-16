"""傷む物の見切り売り（W-285、MK-41 の持ち越せる期間のある物）。"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import stock
from hakoniwa_core.economy import is_self_supporting
from hakoniwa_core.market import auction
from hakoniwa_core.modules.market import CLEARANCE, Market
from hakoniwa_core.units import QUANTITY_SCALE, scale_price, to_basis
from tests.core.test_finance import context, island_of
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401

Q = QUANTITY_SCALE
DEFS = {"food": {"shelf_life": 6}, "steel": {}}


@pytest.fixture
def clearance_on(game, monkeypatch):
    monkeypatch.setitem(game.config.get("market"), "clearance_ratio", 2)


# --- 傷む量の見込み（stock.doomed） ---------------------------------------------------


def test_nothing_is_doomed_when_sales_keep_up_with_the_oldest_first():
    s: dict = {}
    stock.add(s, "food", 10 * Q, 95, DEFS)  # ターン 101 の売りまで
    stock.add(s, "food", 10 * Q, 99, DEFS)  # ターン 105 の売りまで
    # ターン 100 から 1 ターンに 10 ずつ売れれば、どちらも間に合う
    assert stock.doomed(s, "food", 100, DEFS, 10 * Q) == 0


def test_the_part_that_cannot_be_sold_before_it_spoils_is_doomed():
    s: dict = {}
    stock.add(s, "food", 30 * Q, 95, DEFS)  # ターン 100・101 の 2 回しか売れない
    stock.add(s, "food", 10 * Q, 99, DEFS)
    # 古い組は 2 回で 20 しか売れず 10 が傷む。新しい組は後の 4 回で売れる
    assert stock.doomed(s, "food", 100, DEFS, 10 * Q) == 10 * Q
    # 売れ行きが 0 なら、このターンに捨てる組のほかは全部
    assert stock.doomed(s, "food", 100, DEFS, 0) == 40 * Q


def test_the_batch_thrown_away_tonight_uses_this_turns_sales_but_is_not_counted():
    s: dict = {}
    stock.add(s, "food", 5 * Q, 94, DEFS)  # 今夜捨てる（指値なしで売る組）
    stock.add(s, "food", 10 * Q, 100, DEFS)  # ターン 106 の売りまで
    # 今夜の組がこのターンの売れる分（1）を先に使い、新しい組は残りの 6 回で 6 しか売れず 4 が傷む。
    # 今夜の組の売れ残り 4 は数えない（指値なしで売る）
    assert stock.doomed(s, "food", 100, DEFS, 1 * Q) == 10 * Q - 6 * Q


def test_goods_that_keep_are_never_doomed():
    s: dict = {}
    stock.add(s, "steel", 100 * Q, 1, DEFS)
    assert stock.doomed(s, "steel", 100, DEFS, 0) == 0


# --- 見切り売りの注文 ------------------------------------------------------------------


def _perishable_seller(game, island):
    defs = game.defs
    for f in sorted(island.facilities, key=lambda f: f.id):
        if not is_self_supporting(f) or not f.recipe:
            continue
        outputs = [
            g
            for g in defs.facilities[f.kind]["recipes"][f.recipe]["outputs"]
            if defs.goods[g].get("shelf_life")
        ]
        if outputs:
            return f, outputs[0]
    raise AssertionError("傷む物を作る独立採算の施設がない")


def _orders(game, world, island, f, good):
    books: dict = {}
    Market()._facility_orders(island, context(game, world), books, {})
    mine = [
        (o, p) for o, p in books[good].sells if p.facility is not None and p.facility.id == f.id
    ]
    cut = [o for o, p in mine if p.clearance]
    return (cut[0] if cut else None), [o for o, p in mine if not p.clearance]


def _setup(game, warmed, pace, old, new):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f, good = _perishable_seller(game, island)
    life = game.defs.goods[good]["shelf_life"]
    f.economy.pop(CLEARANCE, None)
    f.economy.setdefault("expected", {})[good] = pace
    f.stock.clear()
    turn = world.turn + 1  # 市場の段のターン（context と同じ）
    if old:  # このターンと次のターンの売りの 2 回しか出せない組
        stock.add(f.stock, good, old, turn - life + 1, game.defs.goods)
    if new:
        stock.add(f.stock, good, new, turn, game.defs.goods)
    return world, island, f, good


def test_doomed_food_is_marked_down_at_once_from_the_band_low_to_one(game, warmed, clearance_on):
    world, island, f, good = _setup(game, warmed, 10 * Q, 50 * Q, 0)
    config = game.config
    # 待たずに、このターンから傷む分（2 回で 20 しか売れないので 30）だけを出す
    cut, rest = _orders(game, world, island, f, good)
    assert cut is not None and cut.quantity == 30 * Q
    assert sum(o.quantity for o in rest) == 50 * Q  # 在庫はみなふだんの板寄せにも出す
    # 指値は変動費でなく値幅の下端から（運賃を払った後で）
    width = to_basis(config.get("market.band"))
    low, _ = auction.band(island.economy.prices.get(good, 1), width)
    start = f.economy[CLEARANCE][good]["start"]
    assert start <= low and cut.limit <= max(o.limit for o in rest)
    # 売れ残るたびに値幅ずつ下げ、下限は 1（原価を下回っても捨てるよりよい）
    first = cut.limit
    for _ in range(300):
        entry = f.economy[CLEARANCE][good]
        entry["offered"], entry["sold"] = cut.quantity, 0
        cut, _ = _orders(game, world, island, f, good)
    assert cut.limit < first
    assert cut.limit == 1  # 試験は運賃 0


def test_no_markdown_when_sales_keep_up(game, warmed, clearance_on):
    world, island, f, good = _setup(game, warmed, 30 * Q, 20 * Q, 20 * Q)
    cut, _ = _orders(game, world, island, f, good)
    assert cut is None and CLEARANCE not in f.economy


def test_markdown_follows_the_doomed_amount_each_turn(game, warmed, clearance_on):
    world, island, f, good = _setup(game, warmed, 10 * Q, 50 * Q, 0)
    cut, _ = _orders(game, world, island, f, good)
    assert cut.quantity == 30 * Q
    f.economy["expected"][good] = 20 * Q  # 売れ行きの見込みが伸びた
    cut, _ = _orders(game, world, island, f, good)
    assert cut.quantity == 10 * Q


def test_price_floor_and_official_goods_are_not_marked_down_but_capped_ones_are(
    game, warmed, clearance_on
):
    world, island, f, good = _setup(game, warmed, 10 * Q, 50 * Q, 0)
    price = island.economy.prices.get(good, 1)
    island.policy.price_floor[good] = scale_price(price, 1.0)
    assert _orders(game, world, island, f, good)[0] is None
    del island.policy.price_floor[good]
    island.policy.price_cap[good] = scale_price(price, 1.0)
    cut, _ = _orders(game, world, island, f, good)
    assert cut is not None and cut.quantity == 30 * Q


def test_switch_off_stops_the_markdown_too(game, warmed, monkeypatch):
    monkeypatch.setitem(game.config.get("market"), "clearance_ratio", 0)
    world, island, f, good = _setup(game, warmed, 10 * Q, 50 * Q, 0)
    assert _orders(game, world, island, f, good)[0] is None
