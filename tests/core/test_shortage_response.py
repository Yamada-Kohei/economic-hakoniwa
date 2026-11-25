"""要望B（家計が品切れで代わりを買ったとき、本来ほしかった量を値段と作る量に伝える）の最終形。

切り替え market.shortage_response（既定は切）:
- 買い手: 前のターンに supply で買えなかった原料も、price のときと同じく指値を1ターンに値幅ずつ
  上げる（上限は input_limit）。
- 売り手: 島の買えなかった量を、採算条件を満たし（値段が変動費の下限の1.2倍超）まだ増やせる
  施設だけに、「増やせる余地＝全力の産出量 − 見直す前の操業目標に当たる産出量」の割合で配る。
  余地は全戸の目標更新前にまとめて計算し、再開の分岐も含め、加算の島の合計は買えなかった量を
  超えない。1ターンの変化上限（market.max_target_change）はそのまま。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy.prices import subsidized_floor
from hakoniwa_core.modules import market as market_mod
from hakoniwa_core.state import MarketRecord
from hakoniwa_core.state.generate import generate_world
from tests.core.test_finance import context, island_of, run
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）


@pytest.fixture
def on(game, monkeypatch):
    monkeypatch.setitem(game.config.raw["market"], "shortage_response", True)
    return game


def _two_camps(game, world):
    """同じ島に材木場を2つ（片方は写し）置き、木材の値段を採算条件の上にした場面。"""
    island = island_of(world, "mixed")
    f = next(f for f in island.facilities if f.kind == "lumber_camp")
    f.economy["wage"] = game.config.get("labor.initial_wage")
    ctx = context(game, world)
    full = fac.outputs_of(game.defs, f)["wood"]
    floor = subsidized_floor(
        island,
        game.defs,
        game.config,
        f,
        "wood",
        island.economy.prices,
        f.economy["wage"],
        0,
        ctx.turn,
    )
    island.economy.prices["wood"] = floor * 2 + 10
    other = copy.deepcopy(f)
    other.id = 9999
    island.facilities = [f, other]
    for camp in (f, other):
        camp.stock.clear()
    island.markets = [MarketRecord("wood", island.economy.prices["wood"], full, full * 2, full)]
    return island, ctx, f, other, full


def test_shares_go_only_to_farms_with_headroom(on, game):
    """(i)(ii) 全力の畑には足されず、休止の畑に配られ、合計は買えなかった量を超えない。"""
    world = generate_world(1, 3, game.config, game.defs)
    island, ctx, full_farm, idle, full = _two_camps(game, world)
    unmet = full  # MarketRecord の demand − volume
    full_farm.economy.update(
        target=fac.FULL, expected={"wood": full}, sales={"wood": [full, full, 0]}
    )
    idle.economy.update(target=0, expected={"wood": 0}, sales={"wood": [0, 0, 0]})
    shares = market_mod.shortage_shares(island, ctx)
    assert shares.get((full_farm.id, "wood"), 0) == 0
    assert shares[(idle.id, "wood")] == unmet
    assert sum(shares.values()) <= unmet
    # 操業目標にも届く: 休止の畑は上がり（1ターンの上限まで）、全力の畑は全力のまま
    assert market_mod.operating_target(island, idle, ctx) > 0
    assert market_mod.operating_target(island, full_farm, ctx) == fac.FULL


def test_shares_use_target_not_production(on, game):
    """#5423: 余地は目標で引く。目標100%・生産0の畑には足さず、目標0%の畑の目標が上がる。"""
    world = generate_world(1, 3, game.config, game.defs)
    island, ctx, busy, idle, full = _two_camps(game, world)
    busy.economy.update(target=fac.FULL, expected={"wood": full}, sales={"wood": [0, 0, 0]})
    idle.economy.update(target=0, expected={"wood": 0}, sales={"wood": [0, 0, 0]})
    shares = market_mod.shortage_shares(island, ctx)
    assert shares.get((busy.id, "wood"), 0) == 0
    assert shares[(idle.id, "wood")] > 0
    assert market_mod.operating_target(island, idle, ctx) > 0


def test_shares_skip_unprofitable_farms(on, game):
    """(iii) 値段が変動費の下限の1.2倍を超えない畑には足されず、目標も上がらない。"""
    world = generate_world(1, 3, game.config, game.defs)
    island, ctx, full_farm, idle, full = _two_camps(game, world)
    island.economy.prices["wood"] = 1
    full_farm.economy.update(
        target=fac.FULL, expected={"wood": full}, sales={"wood": [full, full, 0]}
    )
    idle.economy.update(target=0, expected={"wood": 0}, sales={"wood": [0, 0, 0]})
    assert market_mod.shortage_shares(island, ctx) == {}
    assert market_mod.operating_target(island, idle, ctx) == 0


def test_shares_are_empty_when_switch_is_off(game):
    """切り替えが切（既定）なら配分はなく、操業目標の見直しも今までどおり。"""
    world = generate_world(1, 3, game.config, game.defs)
    island, ctx, full_farm, idle, full = _two_camps(game, world)
    idle.economy.update(target=0, expected={"wood": 0}, sales={"wood": [0, 0, 0]})
    assert market_mod.shortage_shares(island, ctx) == {}


def test_buyer_raises_bid_after_supply_shortage(on, game):
    """supply で買えなかった原料も指値が上がる（切ではふだんの指値のまま）。"""
    world = generate_world(1, 3, game.config, game.defs)
    run(game, world, 10)
    base = island_of(world, "market")
    ctx = context(game, world)
    limits = {}
    for enabled in (False, True):
        island = copy.deepcopy(base)
        game.config.raw["market"]["shortage_response"] = enabled
        f = next(f for f in island.facilities if f.kind == "thermal_power_plant")
        f.economy["short"] = {"coal": "supply"}
        f.stock.clear()
        usual, most = market_mod.Market._bid(ctx, f, "coal", island.economy.prices, 0)
        last = usual + max(1, (most - usual) // 2)
        f.economy[market_mod.BIDS] = {"turn": ctx.turn - 1, "limits": {"coal": last}}
        books: dict = {}
        market_mod.Market()._facility_orders(island, ctx, books, {})
        buys = [o.limit for o, p in books.get("coal", market_mod.Book()).buys if p.facility is f]
        assert len(buys) == 1
        limits[enabled] = (buys[0], usual, most)
    off_limit, usual, most = limits[False]
    on_limit = limits[True][0]
    assert off_limit == usual  # 切: supply では買い上がらない
    assert usual < on_limit <= most  # 入: 上がるが、採算の上限は超えない
