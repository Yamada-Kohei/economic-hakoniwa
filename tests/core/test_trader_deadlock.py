"""取引の行き詰まりの直し（会議 #5303・#5306）。

- 向きを失った商社は、荷が残っている間は向きを付け直せず（update_targets）、向きが無い間は
  荷を売れなかった（home_orders）ので、荷が永遠に残って輸出に戻れなかった（#5301）
- 直し: 向きの無い商社でも、手元に残った荷を島の市場で売りに出せる（処分）。値段はほかの
  売り手と同じ決め方（輸入の荷の売りと同じ段）で、切り替えは付けない。残荷がある間は
  毎ターン出し直す（継続売り。#5314）
- #5301 で足した 2 件（w307-bloc-reentry 枝）は、直しの後の形に合わせてここへ移した
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


def _sells(island, game, f, good, turn):  # noqa: F811
    orders = traders.home_orders(island, game.defs, game.config, turn)
    return [o for o in orders if o.facility is f and o.good == good and o.side == "sell"]


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


def test_lost_orientation_sells_leftover_stock_then_returns(game, traded):  # noqa: F811
    # 決着回し（#5175）の形: 切では種 1 島 37（喪失 T194）・種 2 島 40（T272）・種 9 島 25
    # （T106）が、差が戻って売りがあっても戻らなかった。直しの後は、荷が残る間は向きは
    # 付かないが処分の売り注文が出て、売り切れば向きが付く
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
        # ただし処分の売りは毎ターン出る（残荷の全部。重複させない）
        sells = _sells(island, game, f, good, world.turn)
        assert sum(o.qty for o in sells) == qty
    # 荷が無くなれば、向きが付く
    stock.take(f.stock, good, qty)
    traders.take_cost(traders.goods_of(f)[good], qty)
    traders.update_targets(island, game.defs, game.config, {}, {}, world.turn)
    assert traders.goods_of(f)[good].get("dir") == traders.EXPORT


def test_late_fill_after_losing_orientation_is_sold_off(game, traded):  # noqa: F811
    # 種 9 島 25 の形の短い再現: 喪失のターン（記録 T106）は手元の荷がちょうど 0 で向きが
    # 落ち、次のターン（T107）に、向きを失う前に出していた買い注文が約定して荷 29,232 が
    # 残った。そのままでは 500 ターンまで戻らなかった
    world = copy.deepcopy(traded[0])
    island, f, good = _export_case(world, game)
    _drop_price(island, game, good, -1)  # 差が負になる
    traders.update_targets(island, game.defs, game.config, {}, {}, world.turn)
    assert traders.goods_of(f).get(good, {}).get("dir") is None
    # 遅れて買い注文が約定し、荷が手元に残る
    qty = 5000
    stock.add(f.stock, good, qty, world.turn, game.defs.goods)
    traders.bought_home(f, good, qty, qty * 100)
    # 差が戻っても向きは付かないが、処分の売りが出る。薄い市場で一部しか売れなくても、
    # 残りは次のターンに出し直す（継続売り）
    record = _drop_price(island, game, good, +1)
    record.supply = 100000  # 島の売りもある
    left = qty
    while left > 0:
        sells = _sells(island, game, f, good, world.turn)
        assert sum(o.qty for o in sells) == left
        sold = max(1, left // 2) if left > 1 else 1  # 一部だけ約定する
        stock.take(f.stock, good, sold)
        traders.sold_home(f, good, sold)
        left -= sold
        traders.update_targets(island, game.defs, game.config, {}, {}, world.turn)
        if left > 0:
            assert traders.goods_of(f)[good].get("dir") is None
    # 売り切ったら向きが付く
    traders.update_targets(island, game.defs, game.config, {}, {}, world.turn)
    assert traders.goods_of(f).get(good, {}).get("dir") == traders.EXPORT


def test_oriented_traders_orders_unchanged(game, traded):  # noqa: F811
    # 向きのある商社は従来どおり: 輸出向きで荷を持っていても、売れ残りの年数が
    # clearance の 2 倍に届くまでは島の市場で売らない（処分は向きの無い商社だけ）
    world = copy.deepcopy(traded[0])
    island, f, good, qty = _holding_case(world, game)
    assert traders.goods_of(f)[good].get("dir") == traders.EXPORT
    assert not _sells(island, game, f, good, world.turn)
