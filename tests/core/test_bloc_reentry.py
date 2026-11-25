"""W-307 別件: 輸出の抜けの粘りと戻り（会議 #4731。trade.bloc_reentry、既定は切り）。

- 粘り: 輸出の差（圏で払える上限 − 島の値段）が負のターンが trade.exit_turns（3）だけ続くまで、
  輸出の向きを「なし」にしない。1 ターンだけの値段の跳ねでは抜けない
- 戻り: 荷を持っていない商社でも、exportable なら島の市場に売りが無くても輸出に向け、島の市場へ
  圏で払える上限までの指値で買い注文を出す（売りが消えた島で、差が戻っても戻れなかった卵と鶏の直し）
"""

from __future__ import annotations

import copy

from hakoniwa_core import stock
from hakoniwa_core.trade import traders, traders_of
from tests.core.test_trade import game, traded  # noqa: F401


def _export_case(world, game):  # noqa: F811
    """輸出向きで荷を持たず、輸出が引き合い輸入が引き合わない商社と財（市場経済の島）。"""
    for island in world.islands.values():
        if not island.has_economy or traders.is_planned(island):
            continue
        found = traders.prospects(island, game.defs, game.config)
        for f in traders_of(island, game.config):
            for good, item in sorted(traders.goods_of(f).items()):
                if item.get("dir") != traders.EXPORT or item["held"][0] > 0:
                    continue
                p = found.get(good)
                home = traders.home_price(island, game.config, good)
                if not traders.exportable(p, home) or traders.importable(p):
                    continue
                if home != island.economy.prices.get(good, 0):
                    continue  # 備蓄の指値で島の値段が動く財は除く（値段を直接いじれる物だけ）
                return island, f, good
    raise AssertionError("輸出向きで荷のない商社が見つからない")


def test_one_negative_turn_keeps_export_orientation(game, traded, monkeypatch):  # noqa: F811
    # 切り替えが切の間は、差が負になった 1 ターンで向きが「なし」になる（直す前の形）
    world = copy.deepcopy(traded[0])
    island, f, good = _export_case(world, game)
    record = next(m for m in island.markets if m.good == good)
    record.demand = 0  # 島に買い手がいない物にする（輸入にも向けない）
    limit = traders.prospects(island, game.defs, game.config, good)[good].export_limit
    island.economy.prices[good] = limit + 10000  # 島の値段が跳ねて差が負になる（T4 の形）
    p = traders.prospects(island, game.defs, game.config, good)[good]
    assert not traders.exportable(p, traders.home_price(island, game.config, good))
    traders.update_targets(island, game.defs, game.config, {}, {}, world.turn)
    assert traders.goods_of(f).get(good, {}).get("dir") is None

    # 入れると、exit_turns（3）続くまでは向きが残り、3 ターン続いたら「なし」になる
    monkeypatch.setitem(game.config.raw["trade"], "bloc_reentry", True)
    world = copy.deepcopy(traded[0])
    island, f, good = _export_case(world, game)
    record = next(m for m in island.markets if m.good == good)
    record.demand = 0
    limit = traders.prospects(island, game.defs, game.config, good)[good].export_limit
    island.economy.prices[good] = limit + 10000
    exit_turns = game.config.get("trade.exit_turns")
    assert exit_turns == 3
    for turn in range(1, exit_turns + 1):
        traders.update_targets(island, game.defs, game.config, {}, {}, world.turn)
        item = traders.goods_of(f).get(good, {})
        if turn < exit_turns:
            assert item.get("dir") == traders.EXPORT
            assert item.get("xneg") == turn  # 負のターンが続いた数
        else:
            assert item.get("dir") is None


def _holding_case(world, game):  # noqa: F811
    """輸出向きの商社に、輸出する荷を持たせる（買った荷が手元にある状態）。"""
    island, f, good = _export_case(world, game)
    qty = 5000
    stock.add(f.stock, good, qty, world.turn, game.defs.goods)
    traders.bought_home(f, good, qty, qty * 100)
    assert traders.goods_of(f)[good]["held"][0] == qty
    return island, f, good, qty


def _drop_price(island, game, good, sign):  # noqa: F811
    """島の値段を動かして輸出の差を負（sign=-1）・正（sign=+1）にする。"""
    record = next(m for m in island.markets if m.good == good)
    record.demand = 0  # 島に買い手がいない物にする（輸入にも向けない）
    limit = traders.prospects(island, game.defs, game.config, good)[good].export_limit
    island.economy.prices[good] = limit - sign * 10000
    return record


def test_orientation_is_frozen_while_trader_holds_stock(game, traded):  # noqa: F811
    # 荷を持っている間は向きを変えない（売り切ってから）: 差が負になっても向きは残り、
    # 荷が無くなったターンに落ちる。決着回し（#5175）で喪失がこの形で起きていた
    world = copy.deepcopy(traded[0])
    island, f, good, qty = _holding_case(world, game)
    _drop_price(island, game, good, -1)
    traders.update_targets(island, game.defs, game.config, {}, {}, world.turn)
    assert traders.goods_of(f)[good].get("dir") == traders.EXPORT
    stock.take(f.stock, good, qty)
    traders.take_cost(traders.goods_of(f)[good], qty)
    assert traders.goods_of(f)[good]["held"][0] == 0
    traders.update_targets(island, game.defs, game.config, {}, {}, world.turn)
    assert traders.goods_of(f).get(good, {}).get("dir") is None


def test_lost_orientation_does_not_return_while_stock_remains(game, traded, monkeypatch):  # noqa: F811
    # 決着回し（#5175）の形の固定: 切では種 1 島 37（喪失 T194・残り 307 ターン）・
    # 種 2 島 40（T272・残り 229 ターン）・種 9 島 25（T106・残り 395 ターン）が、差が戻って
    # 売りがあっても戻らなかった。入でも種 2 島 40（T374・残り 127 ターン）と種 9 島 32
    # （T389・残り 112 ターン）に同じ形が残った。5 件とも、向きを失った商社の財の状態が
    # 消えずに残り続けた（狙う量 0・向きなし）。その状態は「荷が残っている間は向きを
    # 変えない」決まりで説明が付く: 向きのない商社は荷を売る注文を出さないので荷が
    # 無くならず、荷がある間は向きの付け直し自体が走らない。
    for on in (False, True):
        if on:
            monkeypatch.setitem(game.config.raw["trade"], "bloc_reentry", True)
        world = copy.deepcopy(traded[0])
        island, f, good, qty = _holding_case(world, game)
        item = traders.goods_of(f)[good]
        item["dir"] = None  # 向きを失った後にする
        item["x"] = 0
        record = _drop_price(island, game, good, +1)  # 差は戻っている
        record.supply = 100000  # 島の売りもある
        assert traders.exportable(
            traders.prospects(island, game.defs, game.config, good)[good],
            traders.home_price(island, game.config, good),
        )
        for _ in range(3):  # 何ターン見直しても、荷が残る間は向きは付かない
            traders.update_targets(island, game.defs, game.config, {}, {}, world.turn)
            assert traders.goods_of(f)[good].get("dir") is None
        # 荷が無くなれば、向きが付く（切・入とも。入は売りが無くても戻れるのは
        # test_reentry_places_a_buy_order_without_supply の分）
        stock.take(f.stock, good, qty)
        traders.take_cost(traders.goods_of(f)[good], qty)
        traders.update_targets(island, game.defs, game.config, {}, {}, world.turn)
        assert traders.goods_of(f)[good].get("dir") == traders.EXPORT
        monkeypatch.undo()


def test_reentry_places_a_buy_order_without_supply(game, traded, monkeypatch):  # noqa: F811
    # 向きを失い、島に売りも無い状態にする（抜けた後の島）
    def lost(world):
        island, f, good = _export_case(world, game)
        item = traders.goods_of(f)[good]
        item["dir"] = None
        item["x"] = 0
        record = next(m for m in island.markets if m.good == good)
        record.supply = 0  # 島の売り手が売りに出していない
        supply, _ = traders.home_supply(island, record)
        assert supply == 0
        return island, f, good

    # 切り替えが切の間は、差が正でも輸出に向けず、買い注文も出ない（直す前の形）
    world = copy.deepcopy(traded[0])
    island, f, good = lost(world)
    traders.update_targets(island, game.defs, game.config, {}, {}, world.turn)
    item = traders.goods_of(f).get(good, {})
    assert item.get("dir") != traders.EXPORT
    orders = traders.home_orders(island, game.defs, game.config, world.turn)
    assert not [o for o in orders if o.facility is f and o.good == good and o.side == "buy"]

    # 入れると、輸出に向き、圏で払える上限までの指値で買い注文が出る
    monkeypatch.setitem(game.config.raw["trade"], "bloc_reentry", True)
    world = copy.deepcopy(traded[0])
    island, f, good = lost(world)
    p = traders.prospects(island, game.defs, game.config, good)[good]
    unmet = {(p.export_bloc, good): 100000}
    sellers = {(p.export_bloc, good): 1}
    traders.update_targets(island, game.defs, game.config, unmet, sellers, world.turn)
    item = traders.goods_of(f)[good]
    assert item.get("dir") == traders.EXPORT
    assert item["x"] > 0  # 売りが無くても狙う量が残る（買い注文の元）
    orders = traders.home_orders(island, game.defs, game.config, world.turn)
    buys = [o for o in orders if o.facility is f and o.good == good and o.side == "buy"]
    assert buys and all(o.qty > 0 for o in buys)
    assert all(o.limit == p.export_limit for o in buys)  # 圏で払える上限までの指値
