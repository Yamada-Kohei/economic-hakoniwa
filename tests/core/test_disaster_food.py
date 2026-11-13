"""災害で島の食料が止まったときの備え（W-239）の試験。

- 国の穀物の備蓄（設計書 6 財政 FS-25）: 穀物を作らない島では、買い足しの指値を輸入が引き合う値段
  まで上げ、商社はその指値を島の値段として輸入を決める。この形の間の値上がりでの放出の線は買い足し
  の指値から数え、海外の食料商人が来ている間は買い足さない。
- 物がないときの海外の食料商人（設計書 9 貿易 FX-73。仮の決まり）。
"""

from __future__ import annotations

import copy

from hakoniwa_core import stock
from hakoniwa_core.economy import funds
from hakoniwa_core.economy.prices import initial_prices, usual_price
from hakoniwa_core.log import format_log
from hakoniwa_core.modules.market import RESERVE_SEEN, Market
from hakoniwa_core.modules.population import FED
from hakoniwa_core.trade import food_merchant, island_state, merchant
from hakoniwa_core.trade import traders as trade_traders
from hakoniwa_core.units import QUANTITY_SCALE, scale_price
from tests.core.test_finance import context, island_of
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401

FOODS = ("grain", "fish", "food", "meat")


def _reserve_orders(game, world, island):
    """_reserve_orders が出す売り（財, 量, 指値）と買いの本（板）を返す。"""
    sells = []
    offered: dict[str, int] = {}

    def sell(good, qty, limit, layer=None, priority=None):
        qty = min(qty, stock.total(island.warehouse, good) - offered.get(good, 0))
        if qty > 0:
            offered[good] = offered.get(good, 0) + qty
            sells.append((good, qty, limit))

    books_by_good: dict = {}
    Market()._reserve_orders(island, context(game, world), books_by_good, sell, offered)
    for book in books_by_good.values():  # 取り置いたお金を戻す（同じ島で続けて呼ぶため）
        for _, party in book.buys:
            funds.release(island, party.payer, party.reserved)
    return sells, books_by_good


def _grain_island(game, warmed, bloc_price):
    """市場経済の島に、前のターンに圏の市場で穀物が bloc_price で売られていた様子を置き、倉庫の
    穀物を半分にする（買い足しが要る）。島のふだんの穀物の売り手は見ない（前のターンの様子で決める）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    view = {
        "name": "圏",
        "currency": island.id,
        "members": 2,
        "rep": island.id,
        "cells": 2,
        "prices": {"grain": bloc_price},
        "supply": ["grain"],
        "short": [],
    }
    island_state(island)["blocs"] = {"1": view}
    held = stock.total(island.warehouse, "grain")
    stock.take(island.warehouse, "grain", held // 2)
    for state in island.economy.households.values():
        state.pop(FED, None)
        state.pop("hunger", None)
    island.policy.budget["warehouse"] = 10**12
    return world, island


def _usual(game, island):
    return usual_price(island, "grain", game.config.get("household.usual_price_turns"))


def test_reserve_imports_grain_when_no_island_seller_fills_the_refill(game, warmed):
    """前のターンの買い足しが埋まらず、島の施設が穀物を売りに出さず、島の売り手に穀物の売れ残りが
    なく、圏に穀物の売りがあれば、
    買い足しの指値を輸入が引き合う値段まで上げ、商社はそれを島の値段として輸入する（W-239）。"""
    usual_now = initial_prices(game.defs, game.config)["grain"]
    world, island = _grain_island(game, warmed, usual_now * 3)
    usual = _usual(game, island)
    island.economy.ext[RESERVE_SEEN] = {"short": True, "home_unsold": 0, "made": 0}
    _, books_by_good = _reserve_orders(game, world, island)
    floor = trade_traders.prospects(island, game.defs, game.config, only="grain")["grain"]
    assert floor.import_floor > usual  # 輸入の原価のほうが高い島
    ((order, _),) = books_by_good["grain"].buys
    bid = floor.import_floor + -(-floor.import_floor // 1000)  # 丸めの分だけ上
    assert order.limit == bid
    assert island.economy.ext[trade_traders.RESERVE_IMPORT] == bid
    # 商社は倉庫の指値を島の値段として見て、輸入が引き合う
    assert trade_traders.home_price(island, game.config, "grain") >= floor.import_floor
    found = trade_traders.prospects(island, game.defs, game.config)["grain"]
    assert trade_traders.importable(found)
    # 一度入ったら、島の売り手に売れ残りが出るまで続ける（買い足しが埋まっても）
    island.economy.ext[RESERVE_SEEN] = {"short": False, "home_unsold": 0, "made": 0}
    _, books_by_good = _reserve_orders(game, world, island)
    assert books_by_good["grain"].buys[0][0].limit == bid
    # 島の施設が穀物を売りに出していれば（穀物を作る島）、島の売り手から買う（いつもの値段）
    island.economy.ext[RESERVE_SEEN] = {"short": True, "home_unsold": 0, "made": 1}
    _, books_by_good = _reserve_orders(game, world, island)
    assert books_by_good["grain"].buys[0][0].limit == usual
    assert trade_traders.RESERVE_IMPORT not in island.economy.ext
    # 島の売り手（施設と倉庫）に穀物の売れ残りがあれば、いつもの値段に戻す
    island.economy.ext[RESERVE_SEEN] = {"short": True, "home_unsold": 1, "made": 0}
    _, books_by_good = _reserve_orders(game, world, island)
    assert books_by_good["grain"].buys[0][0].limit == usual
    assert trade_traders.RESERVE_IMPORT not in island.economy.ext
    assert trade_traders.home_price(island, game.config, "grain") == island.economy.prices["grain"]


def test_reserve_import_bid_clears_rounding_with_another_currency(game, warmed):
    """圏の通貨が島の通貨と違い、レートが 1 でなくても、輸入で積む形の指値なら商社は輸入する
    （輸入が引き合う値段ちょうどだと、通貨の換算の切り捨てで買いの指値が圏の値段に届かない）。"""
    usual_now = initial_prices(game.defs, game.config)["grain"]
    world, island = _grain_island(game, warmed, 1)
    other = next(i for i in world.islands if i != island.id)
    view = island_state(island)["blocs"]["1"]
    view["currency"] = other
    for rate in (3333, 7777, 9999, 12345, 18888, 27001):
        for price in range(usual_now * 2, usual_now * 2 + 40, 7):
            view["prices"]["grain"] = price
            island_state(island).setdefault("rates", {})[str(other)] = rate
            island.economy.ext.pop(trade_traders.RESERVE_IMPORT, None)
            island.economy.ext[RESERVE_SEEN] = {"short": True, "home_unsold": 0, "made": 0}
            island.economy.prices["grain"] = 1
            _reserve_orders(game, world, island)
            found = trade_traders.prospects(island, game.defs, game.config)["grain"]
            assert found.import_floor > 0, (rate, price)
            assert trade_traders.importable(found), (rate, price)


def test_reserve_does_not_import_without_a_seller_in_the_bloc(game, warmed):
    """圏の市場に前のターンに穀物の売りがなければ、いつもの値段のまま（輸入が引き合う値段がない）。"""
    world, island = _grain_island(game, warmed, 1)
    island_state(island)["blocs"]["1"]["supply"] = []
    island.economy.ext[RESERVE_SEEN] = {"short": True, "home_unsold": 0, "made": 0}
    _, books_by_good = _reserve_orders(game, world, island)
    assert books_by_good["grain"].buys[0][0].limit == _usual(game, island)
    assert trade_traders.RESERVE_IMPORT not in island.economy.ext


def test_release_line_follows_the_import_bid(game, warmed):
    """輸入で積む形の間は、値上がりでの放出の線を買い足しの指値 ×(1 + release) にする（輸入の値段で
    買うと島の値段がその値段になり、いつもの値段の線ではすぐ放出して買い直すため）。"""
    usual_now = initial_prices(game.defs, game.config)["grain"]
    world, island = _grain_island(game, warmed, usual_now * 3)
    usual = _usual(game, island)
    release = game.config.get("food_reserve.release")
    island.economy.ext[RESERVE_SEEN] = {"short": True, "home_unsold": 0, "made": 0}
    island.economy.prices["grain"] = scale_price(usual, 1 + release)  # いつもの値段の線の上
    sells, _ = _reserve_orders(game, world, island)
    limit = island.economy.ext[trade_traders.RESERVE_IMPORT]
    assert limit > island.economy.prices["grain"]
    assert not any(qty for g, qty, lim in sells if lim == scale_price(usual, 1 + release))
    island.economy.prices["grain"] = scale_price(limit, 1 + release)
    sells, _ = _reserve_orders(game, world, island)
    assert any(lim == scale_price(limit, 1 + release) for _, _, lim in sells)


def test_reserve_does_not_refill_while_the_food_merchant_is_here(game, warmed):
    """海外の食料商人が来ている間は、国の倉庫は穀物を買い足さない（商人の穀物を備蓄に買わない）。"""
    world, island = _grain_island(game, warmed, 1)
    food_merchant.state_of(island)["active"] = True
    _, books_by_good = _reserve_orders(game, world, island)
    assert "grain" not in books_by_good


def _hungry_seen(island, unmet, cpi=1.2):
    state = food_merchant.state_of(island)
    state.clear()
    state["seen"] = {"unmet": unmet, "others_unsold": False, "sold": 0, "cpi": cpi}


def test_food_merchant_comes_when_food_runs_out_and_brings_the_missing_grain(game, warmed):
    """飢えた家計があり、お金のある買いが食料を買えず、ほかの売り手に売れ残りがなければ、次の
    ターンに海外の商人が足りない栄養の分の穀物を、来る前の物価で止めた割高な値段で売りに来る。
    国の倉庫が放出する分は引く。上限は港の積み下ろしの量の設定（港の有無によらない）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    defs, config = game.defs, game.config
    unit = food_merchant.nutrition(defs, "grain")
    _hungry_seen(island, 10 * QUANTITY_SCALE)
    assert food_merchant.decide(island, True, world.turn + 1)
    good, qty, price = food_merchant.offer(island, defs, config, 0)
    assert good == "grain"
    assert qty == -(-10 * QUANTITY_SCALE * QUANTITY_SCALE // unit)
    base = initial_prices(defs, config, island.resources)["grain"]
    assert price == scale_price(base, config.get("foreign_merchant.multiplier") * 1.2)
    _, less, _ = food_merchant.offer(island, defs, config, 4 * QUANTITY_SCALE)
    assert less == qty - 4 * QUANTITY_SCALE  # 国の倉庫の放出の分を引く
    _hungry_seen(island, 10**12)
    food_merchant.decide(island, True, world.turn + 1)
    _, most, _ = food_merchant.offer(island, defs, config, 0)
    cap = config.get("trade.port_capacity") * QUANTITY_SCALE
    assert most == cap * QUANTITY_SCALE // (defs.goods["grain"]["freight_weight"] * QUANTITY_SCALE)
    # 封鎖された島には、その強さの割合だけ少なく来る
    island_state(island)[merchant.BLOCKADE] = 5000
    _, blocked, _ = food_merchant.offer(island, defs, config, 0)
    assert blocked == most // 2


def test_food_merchant_stays_while_its_grain_sells_and_leaves_when_food_is_back(game, warmed):
    """来た商人は、前のターンに穀物が売れ、ほかの売り手に売れ残りがない間は来続ける（飢えが解けた
    ターンに去ると、次のターンにまた飢えるため）。穀物が売れなくなるか、ほかの売り手に売れ残りが
    出たら来ない。飢えていない島、お金のない飢え（買えなかった栄養がない）、ほかの売り手に売れ残りの
    ある島には来ない。前のターンの記録のない島にも来ない。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    turn = world.turn + 1
    assert not food_merchant.decide(island, True, turn)  # 記録がない
    _hungry_seen(island, 1000)
    assert not food_merchant.decide(island, False, turn)  # 飢えていない
    _hungry_seen(island, 0)
    assert not food_merchant.decide(island, True, turn)  # お金がなくて買えなかっただけ
    _hungry_seen(island, 1000)
    food_merchant.state_of(island)["seen"]["others_unsold"] = True
    assert not food_merchant.decide(island, True, turn)  # 売れ残りがある（値段かお金のせい）
    _hungry_seen(island, 1000, cpi=1.5)
    assert food_merchant.decide(island, True, turn)
    state = food_merchant.state_of(island)
    assert state["since"] == turn and state["cpi"] == 1.5
    # 次のターン: 飢えは解けたが、商人の穀物が売れていた → 来続ける（物価は来始めの値のまま）
    food_merchant.remember(island, 0, False, 500, 2.0, (800, 100), False)
    assert food_merchant.decide(island, False, turn + 1)
    assert state["since"] == turn and state["cpi"] == 1.5
    # 島の売り手に売れ残りが出た → 来ない
    food_merchant.remember(island, 0, True, 500, 2.0, (800, 100), False)
    assert not food_merchant.decide(island, False, turn + 2)
    assert not food_merchant.active(island) and "since" not in state
    # 商人の穀物が売れなかった → 来ない
    _hungry_seen(island, 1000)
    food_merchant.decide(island, True, turn)
    food_merchant.remember(island, 0, False, 0, 2.0, (800, 100), False)
    assert not food_merchant.decide(island, False, turn + 1)


def test_food_merchant_sells_on_the_market_after_the_food_runs_out(game, warmed):
    """島の食料の施設がみな止まり、在庫も国の備蓄もない島に、飢えた次のターンから海外の商人が
    穀物を運び、市場で売れる（代金は島の外へ出る）。ログとターンの記録に残る。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    for f in island.facilities:
        for good in FOODS:
            stock.take(f.stock, good, stock.total(f.stock, good))
        recipe = game.defs.facilities[f.kind]["recipes"].get(f.recipe, {})
        if any(g in recipe.get("outputs", {}) for g in FOODS):
            f.ext["halted_until"] = world.turn + 10
    for good in FOODS:
        stock.take(island.warehouse, good, stock.total(island.warehouse, good))
    island.policy.budget["warehouse"] = 0
    came = []
    for _ in range(4):
        game.engine.run_turn(world)
        came += [e for e in island.log if e.kind == "food_merchant_came"]
        if came:
            break
    assert came, "食料がなくなっても海外の商人が来なかった"
    state = food_merchant.state_of(island)
    assert state["last"]["qty"] > 0 and state["last"]["sold"] > 0
    assert island.stats.get("trade.merchant", 0) > 0
    text = format_log(game.defs, "food_merchant_came", came[0].args)
    assert "海外の商人" in text and "{" not in text
