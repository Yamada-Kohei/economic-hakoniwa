"""MK-42・FM-69・FM-70（2026-11-12、W-261）: 店の付け値、店の参入、町の店の枠。"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import retail
from hakoniwa_core.economy.plan import is_planned
from hakoniwa_core.modules.firms import EntryExit
from hakoniwa_core.modules.market import Market
from hakoniwa_core.state import Facility
from hakoniwa_core.units import BASIS, to_basis
from tests.core.test_balance_round7 import base as base
from tests.core.test_balance_round7 import game as game
from tests.core.test_balance_round7 import market_island, town_of
from tests.core.test_phase3_3 import run_checked
from tests.core.test_retail_fix import entry_setup
from tests.core.test_retail_fix import sample as sample


def shop_of(island):
    return next(f for f in island.facilities if f.kind == "shop" and f.built)


# --- 店の付け値（MK-42） -----------------------------------------------------------------


def test_posted_price_marks_up_the_variable_cost_below_the_stall(game, sample):
    """付け値は 1 単位の変動費 ×（1 ＋ retail.markup）。屋台の値段 × retail.posted_cap を超えない
    （百貨店の運賃も上限の内）が、変動費は下回らない。変動費が分からなければ今の値段。"""
    island, _ = sample
    shop = shop_of(island)
    shop.economy.pop(retail.MARKUP, None)
    s = retail.settings(game.config)
    stall = 10_000
    assert retail.posted_price(island, game.config, shop, 1000, stall) == 1000 * (1 + s["markup"])
    assert shop.economy[retail.MARKUP] == to_basis(s["markup"])
    cap = stall * s["posted_cap"]
    assert retail.posted_price(island, game.config, shop, 8000, stall) == cap
    assert retail.posted_price(island, game.config, shop, 20_000, stall) == 20_000  # 変動費まで
    # 運賃 1 割の百貨店: 運賃を足した指値が上限に収まる
    assert retail.posted_price(island, game.config, shop, 8000, stall, 1000) == cap * 0.9
    island.economy.prices[retail.good_of(game.config)] = 1234
    assert retail.posted_price(island, game.config, shop, 0, stall) == 1234


def test_legacy_shops_ease_the_markup_from_todays_price(game, sample):
    """前の版から続く島の店は、上乗せを今の値段 ÷ 変動費 − 1 から retail.markup_step ずつ
    retail.markup へ寄せる（切り替えの日に小売りの値段が一度に下がらない）。印は1度だけ付ける。"""
    island, _ = sample
    good = retail.good_of(game.config)
    s = retail.settings(game.config)
    shop = shop_of(island)
    shop.economy.pop(retail.MARKUP, None)
    island.economy.ext.pop(retail.POSTED, None)
    retail.mark_legacy(island, game.defs, game.config)
    assert shop.economy[retail.MARKUP] == retail.LEGACY
    island.economy.prices[good] = 2400
    first = retail.posted_price(island, game.config, shop, 1000, 10**6)
    assert first == 2400  # 今の値段のまま始める
    assert shop.economy[retail.MARKUP] == 14 * BASIS // 10
    second = retail.posted_price(island, game.config, shop, 1000, 10**6)
    assert second == 2400 - 1000 * s["markup_step"]
    for _ in range(100):
        last = retail.posted_price(island, game.config, shop, 1000, 10**6)
    assert last == 1000 * (1 + s["markup"])
    # 2回目の移し替えでは印を付け直さない
    retail.mark_legacy(island, game.defs, game.config)
    assert shop.economy[retail.MARKUP] == to_basis(s["markup"])
    # 今の値段が目標より安い前の版の店は、今の値段から上乗せを寄せ上げる。変動費より安ければ
    # 上乗せ 0（変動費）から
    other = copy.deepcopy(shop)
    other.economy[retail.MARKUP] = retail.LEGACY
    island.economy.prices[good] = 1100
    assert retail.posted_price(island, game.config, other, 1000, 10**6) == 1100
    assert retail.posted_price(island, game.config, other, 1000, 10**6) == 1150
    other.economy[retail.MARKUP] = retail.LEGACY
    island.economy.prices[good] = 400
    assert retail.posted_price(island, game.config, other, 1000, 10**6) == 1000
    assert other.economy[retail.MARKUP] == 0


def test_households_pay_shops_no_more_than_the_highest_posted_price(game, base, monkeypatch):
    """市場経済と混合経済の島では、店（国営も）が小売りに付け値を付け、家計の店への指値は付け値の
    最高を超えない。計画経済の島の店には付け値の上乗せを持たせない。"""
    world = copy.deepcopy(base)
    good = retail.good_of(game.config)
    seen: dict[int, tuple[int, list[int]]] = {}
    buy = Market._household_buy

    def spy(self, island, books_by_good, g, qty, limit, *args):
        if g == good:
            book = books_by_good.get(good)
            asks = [o.limit for o, _ in book.sells if o.limit is not None] if book else []
            seen.setdefault(island.id, (limit, asks))
        return buy(self, island, books_by_good, g, qty, limit, *args)

    monkeypatch.setattr(Market, "_household_buy", spy)
    run_checked(game, world, 1)
    posted_islands = 0
    for i in world.island_ids():
        island = world.islands[i]
        shops = [
            f
            for f in island.facilities
            if f.built and retail.sells_retail(game.defs, game.config, f)
        ]
        if is_planned(island, game.defs):
            assert not any(retail.MARKUP in f.economy for f in shops)
            continue
        if i not in seen or not seen[i][1]:
            continue
        limit, asks = seen[i]
        assert limit <= max(asks)
        posted_islands += 1
    assert posted_islands > 0, "付け値の店のある島がない"


# --- 店の参入（FM-69） ---------------------------------------------------------------------


def test_shop_entry_does_not_wait_for_shops_being_built(game, sample):
    """同じ財の店の工事中や開いたばかりの店を待たず、足りない量からその店の全力を引いた残りで決める。"""
    island, ctx = sample
    good = retail.good_of(game.config)
    recipe = game.defs.facilities["shop"]["recipes"]["default"]
    qty = recipe["outputs"][good]
    island.facilities = [
        f for f in island.facilities if not retail.sells_retail(game.defs, game.config, f)
    ]
    building = Facility(id=island.next_facility_id(), kind="shop", recipe="default", x=-40, y=-40)
    building.built = False
    island.facilities.append(building)
    assert good not in EntryExit._pipeline(island, ctx)  # 店の工事は参入を止めない
    assert EntryExit._shop_pending(island, ctx) == [(qty, None)]
    turns = game.config.get("firms.price_turns")
    big = {good: [qty * 3] * turns}
    fill = EntryExit._shop_fill(island, ctx, recipe, big, {}, {}, {}, 1)
    assert fill == pytest.approx(1.0)  # 足りない 3 店分から工事中の 1 店を引いても 2 店分残る
    small = {good: [qty] * turns}
    assert EntryExit._shop_fill(island, ctx, recipe, small, {}, {}, {}, 1) == 0
    # 値段の合図: 買いたい量から動いている店と工事中の店の全力を引いた残り
    demand = {good: [qty * 5 // 2] * turns}
    prices = {good: 10**6}
    fill = EntryExit._shop_fill(island, ctx, recipe, {}, demand, {good: qty}, prices, 1)
    assert fill == pytest.approx(0.5)


def test_shop_entry_is_counted_apart_from_factories(monkeypatch, game, sample):
    """店だけの回は店だけを、工場の回は店のほかだけを建てる（1島1ターンに工場と店を1つずつ）。"""
    island, ctx, built = entry_setup(monkeypatch, game, sample)
    turns = game.config.get("firms.price_turns")
    island.economy.finance["shortages"] = {"retail": [10**9] * turns, "steel": [10**9] * turns}
    entry = EntryExit()
    assert entry._try_enter(island, ctx, [], shops=True)
    assert built and all(
        retail.sells_retail(game.defs, game.config, f) for f in island.facilities[-1:]
    )
    built.clear()
    assert entry._try_enter(island, ctx, [], shops=False)
    assert built and built[-1] not in ("shop", "department_store")
    calls = []
    monkeypatch.setattr(
        EntryExit, "_try_enter", lambda self, i, c, b, shops=None: calls.append(shops)
    )
    monkeypatch.setattr(EntryExit, "_sell_on_sale", lambda *a: 0)
    for name in ("_record_shortages", "_review_recipes", "_exits", "_foreclosures", "_invest"):
        monkeypatch.setattr(EntryExit, name, lambda *a: None)
    monkeypatch.setattr(EntryExit, "_raise_grades", lambda *a: None)
    monkeypatch.setattr(EntryExit, "_bank_entry", lambda *a: None)
    entry.run_island(island, ctx)
    assert calls == [False, True]  # 工場の回が建たなくても、店の回は別に1回


# --- 町の店の枠（FM-70） -------------------------------------------------------------------


def test_town_shop_slots_grow_with_people_but_villages_get_none(game, base):
    island = copy.deepcopy(market_island(base))
    town = town_of(game, island)
    level = town.ext.get("level") or game.defs.settlement_levels()[0]
    spec = game.defs.settlements[level]
    tile = island.tile(town.x, town.y)
    tile.population = 0
    assert fac.shop_slots(game.defs, town, island) == spec.get("shops", 0)
    tile.population = spec["people_per_shop"] * 30
    if spec.get("shops", 0) > 0:
        assert fac.shop_slots(game.defs, town, island) == max(spec["shops"], 30)
    assert fac.shop_slots(game.defs, town) == spec.get("shops", 0)
    village = copy.deepcopy(town)
    village.ext["level"] = game.defs.settlement_levels()[0]
    assert fac.shop_slots(game.defs, village, island) == 0


def test_price_floor_of_a_mixed_economy_holds_the_posted_price_up(game, base, monkeypatch):
    """混合経済の値段の下限（PL-32）は付け値より先に効く: 店の指値は下限を下回らず、家計の店への
    指値も下限に届くので、下限のある島でも店で売れる。"""
    from hakoniwa_core import politics

    world = copy.deepcopy(base)
    good = retail.good_of(game.config)
    island = next(
        world.islands[i]
        for i in world.island_ids()
        if world.islands[i].has_economy
        and politics.trait(world.islands[i], game.defs, "price_controls", False)
        and any(f.kind == "shop" and f.built for f in world.islands[i].facilities)
    )
    floor = 10 * island.economy.prices[good]
    island.policy.price_floor[good] = floor
    seen = []
    buy = Market._household_buy

    def spy(self, isl, books_by_good, g, qty, limit, *args):
        if g == good and isl is island:
            seen.append((limit, [o.limit for o, _ in books_by_good[good].sells]))
        return buy(self, isl, books_by_good, g, qty, limit, *args)

    monkeypatch.setattr(Market, "_household_buy", spy)
    run_checked(game, world, 1)
    assert seen
    limit, asks = seen[0]
    assert asks and min(asks) >= floor
    assert limit >= floor
