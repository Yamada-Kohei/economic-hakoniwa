"""輸出の儲けの幅で狙う量を上げる（trade.export_margin）と、参入の採算に輸出の値段を入れる
（firms.entry_export）の試験（#1307。どちらも試し中で既定は切る）。"""

from __future__ import annotations

import copy

from hakoniwa_core.modules.firms import EntryExit, trade_signals
from hakoniwa_core.state import MarketRecord
from hakoniwa_core.trade import traders as trade_traders
from hakoniwa_core.trade import traders_of
from tests.core.test_phase3_4 import base, game, island_of  # noqa: F401


def _export_case(game, base):  # noqa: F811
    """市場経済の島で、輸出が引き合う財を 1 つ選び、商社を輸出の向きにした世界。"""
    world = copy.deepcopy(base)
    island = island_of(world, "market")
    found = trade_traders.prospects(island, game.defs, game.config)
    prices = island.economy.prices
    good, p = next(
        (g, p)
        for g, p in sorted(found.items())
        if p.export_bloc is not None
        and trade_traders.exportable(p, trade_traders.home_price(island, game.config, g))
        and g in prices
    )
    return world, island, good, p


def _targets(game, island, good, p, unsold, unmet, sold=0):  # noqa: F811
    sellers = traders_of(island, game.config)
    assert sellers
    for f in sellers:
        trade_traders.goods_of(f).pop(good, None)
    island.markets = [m for m in island.markets if m.good != good]
    island.markets.append(
        MarketRecord(
            good=good, price=island.economy.prices[good], volume=0, demand=0, supply=unsold
        )
    )
    island.scratch.get(trade_traders.FOREIGN_SUPPLY, {}).pop(good, None)
    if sold:
        f = sellers[0]
        trade_traders.entry(f, good)["dir"] = trade_traders.EXPORT
        trade_traders.exported(f, good, sold)
    key = (p.export_bloc, good)
    trade_traders.update_targets(island, game.defs, game.config, {key: unmet}, {key: 10})
    items = [trade_traders.goods_of(f).get(good) or {} for f in sellers]
    return [i.get("x", 0) for i in items if i.get("dir") == trade_traders.EXPORT]


def test_export_margin_reaches_for_the_smaller_of_unsold_and_bloc_buyers(
    game,  # noqa: F811
    base,  # noqa: F811
    monkeypatch,
):
    """儲けの幅があれば、島の売れ残りと圏の買えなかった量の小さい方まで狙う量を上げる。切って
    いれば、圏の買えなかった量を圏の売り手の数で割った分け前だけ（今のまま）。"""
    world, island, good, p = _export_case(game, base)
    cap = trade_traders.capacity_units(game.defs, game.config, good)  # 商社 1 社の扱える量
    unsold, unmet = 5 * cap, cap // 2
    speed = game.config.get("trade.target_speed")
    off = _targets(game, island, good, p, unsold, unmet)
    monkeypatch.setitem(game.config.raw["trade"], "export_margin", True)
    world, island, good, p = _export_case(game, base)
    on = _targets(game, island, good, p, unsold, unmet)
    assert off and on
    # 圏の買えなかった量（売れ残りより小さい）へ target_speed の割合だけ近づく
    assert max(on) >= int(unmet * speed) - 1 > max(off)
    # 売れ残りが圏の買えなかった量より小さければ、売れ残りが上限
    world, island, good, p = _export_case(game, base)
    small = cap // 10
    assert max(_targets(game, island, good, p, small, 10 * unmet)) <= small


def test_export_margin_is_off_by_default(game):  # noqa: F811
    assert game.config.get("trade.export_margin") is False
    assert game.config.get("firms.entry_export") is False


def test_trade_signals_need_a_steady_history(game, monkeypatch):  # noqa: F811
    """輸出の値段と輸入して届く値段は、直近 price_turns ターンのどこにもあった財だけを
    平均で見る。"""
    from hakoniwa_core.state.generate import generate_world

    world = generate_world(1, 3, game.config, game.defs)
    island = next(iter(world.islands.values()))
    keep = game.config.get("firms.price_turns")
    finance = island.economy.finance
    finance[trade_traders.EXPORT_VALUE] = {"steel": [100] * keep, "coal": [0] + [50] * (keep - 1)}
    finance[trade_traders.EXPORT_ROOM] = {"steel": [10] * keep}
    finance[trade_traders.IMPORT_COST] = {"coke": [30] * keep, "ore": [40] * (keep - 1)}
    monkeypatch.setitem(game.config.raw["firms"], "entry_export", False)
    assert trade_signals(island, game.config) == ({}, {}, {})
    monkeypatch.setitem(game.config.raw["firms"], "entry_export", True)
    value, room, cost = trade_signals(island, game.config)
    assert value == {"steel": 100}  # 1 ターンでも 0 の財は見ない
    assert room == {"steel": 10}
    assert cost == {"coke": 30}  # 記録が price_turns ターンに足りない財は見ない


def test_export_fill_counts_bloc_buyers_like_the_home_signal():
    """輸出の値段が原価の cost_ratio 倍を超え、圏に買えなかった買い手がいれば、その量を今ある施設と
    新しい工場の全力で割った割合だけ売れると見る（島の合図 _fill と同じ形）。"""
    settings = {"cost_ratio": 1.2}
    recipe = {"outputs": {"steel": 1000}}

    def fill(value, room, capacity=2000):
        return EntryExit._export_fill(
            recipe, {"steel": capacity}, 100, settings, {"steel": value}, {"steel": room}
        )

    assert fill(130, 3000) == (1.0, {"steel": 130})
    assert fill(130, 1500) == (0.5, {"steel": 130})
    assert fill(110, 3000) == (0.0, {})  # 原価の 1.2 倍に届かない
    assert fill(130, 0) == (0.0, {})  # 圏に買えなかった買い手がいない
