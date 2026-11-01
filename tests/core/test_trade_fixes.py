"""貿易の直し（2026-11-01。見直しのスレッドの試作を入れた）。

- 圏の板寄せで約定しなかった買いは、取り置いたお金と運ぶ重さを戻す（FX-53）
- 建国の船団は、使い始めた年をずらす（FX-44）
- 船は実際に走る時間の分だけ使う（FX-08）
- 買い手のいない物の値段を下げるかは、売り手の作る費用で見る（FX-21）
- 運転資金の借入は配当に出さない（タブ 5 FM-06）
"""

from __future__ import annotations

import copy

from hakoniwa_core import ledger
from hakoniwa_core.economy import Payer, banks, books, entity_of
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules import accounts, market
from hakoniwa_core.modules import trade as trade_stage
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import port_of, shipping_of, ships, traders, traders_of, world_state
from hakoniwa_core.trade.setup import place_shipping
from hakoniwa_core.units import QUANTITY_SCALE, money_of, to_basis, to_milli
from tests.core.test_trade import _context, game, traded  # noqa: F401


def test_unfilled_bloc_bids_give_back_money_and_room(game, traded):  # noqa: F811
    """圏×財の板寄せの後、買い注文ごとに、取り置いた額（指値 × 量）から約定の値段 × 割り当てた量を
    引いた分を戻し、運ばなかった重さも戻す（FX-53。前は戻らず、財の名前の順で後の財の注文が、
    お金が足りずに出せなかった）。戻した後の残りは今の手元までにする。"""
    world = copy.deepcopy(traded[0])
    island = world.islands[1]
    [f, *_] = traders_of(island, game.config)
    entity = entity_of(game.config, f)
    stage = trade_stage._Round(world, _context(game, world))
    bloc = {"id": 1, "currency": island.id}
    good = "coal"
    filled, unfilled = Order(1000, 500), Order(2000, 400)
    placed = [
        (filled, trade_stage._Bid(island.id, f, entity, good)),
        (unfilled, trade_stage._Bid(island.id, f, entity, good)),
    ]
    key = (island.id, entity, island.id)
    slot = f.id + island.id * 10**6
    budgets = {slot: 0}
    money_left = {key: 0}
    stage._refund(bloc, good, placed, {id(filled): 600}, 300, budgets, money_left)
    back = money_of(500, 1000) - money_of(300, 600) + money_of(400, 2000)
    have = trade_stage.funds.available(island, Payer(entity))
    assert money_left[key] == min(back, have) > 0
    unused = trade_stage.weight(game.defs, good, 400) + trade_stage.weight(game.defs, good, 2000)
    assert budgets[slot] == unused > 0


def test_founding_fleet_has_ships_of_different_ages(game):  # noqa: F811
    """建国の輸送船は、使い始めた年を寿命の trade.start_ship_spread の割合の中でずらし、ずらしの
    初めは島ごとに変わる（FX-44。前はどの船も建国のターンに使い始め、世界じゅうの船が同じターンに
    寿命で消えた）。帳簿はその年数分の減価償却をしたのと同じにする。"""
    settings = game.config.get("trade")
    world = generate_world(7, 4, game.config, game.defs)
    life = settings["ship_life"]
    spread = life * settings["start_ship_spread"]
    starts = set()
    for island in world.islands.values():
        f = place_shipping(island, game.defs, game.config, 100)
        fleet = f.economy["fleet"]
        assert ships.fleet(f) == to_milli(settings["start_ships"])
        ages = [100 - s for s, _ in fleet]
        assert len(set(ages)) == len(fleet) == round(settings["start_ships"])
        assert all(0 <= a < spread for a in ages)
        assert 0 < f.economy["depreciated"] < f.economy["capital"]
        starts.add(tuple(sorted(ages)))
    assert len(starts) > 1  # 島ごとに違う


def test_ships_are_held_only_for_the_time_at_sea(game, traded):  # noqa: F811
    """船（と船員・燃料）は、荷の重さ × 区画 ÷（片道のターン数 × 船の速さ）の分だけ使う（FX-08。前は
    1 区画の航路でも往復のターンのあいだ荷の重さの分の船を占めた）。"""
    world = traded[0]
    speed = game.config.get("trade.ship_speed")
    held = {}
    for island in world.islands.values():
        f = shipping_of(island, game.config)
        assert f is not None
        held.update(f.economy.get("busy", {}))
    seen = 0
    for s in world_state(world)["shipments"]:
        if str(s["id"]) not in held or not s["ship"]:
            continue
        buyer, seller = world.islands[s["to"]], world.islands[s["from"]]
        cells, oneway = trade_stage.route_cells(buyer, seller), s["oneway"]
        assert held[str(s["id"])] == max(1, s["load"] * cells // (oneway * speed))
        assert held[str(s["id"])] <= s["load"]
        seen += 1
    assert seen > 0


def test_goods_nobody_buys_fall_to_what_exports_at_cost(game, traded, monkeypatch):  # noqa: F811
    """買い手のいない物の値段を輸出が引き合う値段まで下げるかは、施設の売りなら指値でなく作る費用
    （unit_floor に運賃を足した額）で見る（FX-21。造船所の売れ残った船の値段が、費用の 2〜3 倍の
    まま 100 ターン以上止まった）。"""
    world = copy.deepcopy(traded[0])
    island = world.islands[1]
    good, price, ask, cost = "coal", 10_000, 7_000, 5_000
    width = to_basis(game.config.get("market.band"))
    mine = next(f for f in island.facilities if f.built and f.x >= 0)  # 作る費用は下で決める
    seller = market.Party(market.FACILITY, f"fac:{mine.id}", Payer(f"fac:{mine.id}"), facility=mine)
    monkeypatch.setattr(market, "unit_floor", lambda *args: cost)

    def record(export_limit, party):
        island.economy.prices[good] = price
        island.scratch.pop("wanted", None)
        book = market.Book(sells=[(Order(1000, ask), party)])
        result = auction.clear([], [o for o, _ in book.sells], price, width)
        found = traders.Prospect(good, export_bloc=1, export_currency=1, export_limit=export_limit)
        monkeypatch.setattr(traders, "prospects", lambda *args: {good: found})
        market.Market._record(island, _context(game, world), {good: book}, {good: result}, {})
        return island.economy.prices[good]

    low = auction.band(price, width)[0]
    assert record(6_000, seller) == low  # 指値より安いが、作る費用では引き合う
    assert record(4_000, seller) == price  # 作る費用より安くしか輸出できない
    store = market.Party(market.STORE, ledger.GOV, Payer(ledger.GOV))
    assert record(6_000, store) == price  # 費用のわからない売り手（国の倉庫）は指値で見る


def test_working_loans_are_not_paid_out_as_dividends(game, traded, monkeypatch):  # noqa: F811
    """手元の資金の余りを配当（国営は納付）に回すとき、運転資金の借入の残りは除く（タブ 5 FM-06。
    海運業が船を買うために借りた運転資金が余りとして配当に出て、返済が遅れて倒産し、船団が消えた）。"""
    monkeypatch.setattr(accounts, "next_payments", lambda *args: 1)

    def paid(loan: int) -> int:
        world = copy.deepcopy(traded[0])
        island = world.islands[1]
        f = port_of(island, game.config)
        assert f is not None
        entity = entity_of(game.config, f)
        if loan:
            bank = banks.active_banks(island)[0]
            assert banks.lend(island, bank, entity, loan, banks.WORKING, 6, world.turn, 500)
        ctx = _context(game, world)
        _, dividend = accounts.Accounts()._settle(island, ctx, f, {books.PROFIT: 1000})
        return dividend

    assert paid(0) > 0
    assert paid(50 * QUANTITY_SCALE) == paid(0)
