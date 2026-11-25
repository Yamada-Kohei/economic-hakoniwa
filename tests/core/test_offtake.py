"""W-307: 商社と作り手の引き取り契約（掲示板 #3591。trade/offtake.py）。

- 既定は切り。入れると、市場経済の島の商社が石炭の作り手と契約を結び、板寄せの前に荷を受け取り、
  島の市場の値段で精算する（帳簿と島をまたぐお金が合う）
- 作り手は操業目標に契約の量（前のターンに商社が市場で買った分を引く）を足す
- 狙う量が契約を下回れば予告して縮め、作り手が続けて渡せなければ渡せた量の平均まで縮める
- 引き受けられなかった量は、作り手の売り逃し（設備投資の合図）と島の品切れの記録に足す
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.game import load_game
from hakoniwa_core.modules import market
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.trade import offtake, traders
from hakoniwa_core.units import QUANTITY_SCALE
from tests.core.test_trade import _context, run

TURNS = 12


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """引き取り契約を入れて TURNS ターン回した 6 島。試験ごとに写す。"""
    raw = game.config.raw["trade"]["offtake"]
    assert raw["enabled"] is False and raw["goods"] == ["coal"]  # 既定は切り、初版は石炭
    raw["enabled"] = True
    try:
        world = generate_world(1, 6, game.config, game.defs)
        run(game, world, TURNS)
    finally:
        raw["enabled"] = False
    return world


@pytest.fixture
def on(game, monkeypatch):
    monkeypatch.setitem(game.config.raw["trade"]["offtake"], "enabled", True)
    return game


def aim(item, qty, cancel):
    """商社の頭打ち前の狙う量を、直近 contract_cancel_turns ターンずっと qty だったことにする。"""
    item[traders.UNCAPPED_X] = item[traders.EXPORTED] = qty
    item[offtake.SEEN] = [qty] * (cancel - 1)
    item["x"] = qty  # 頭打ちで抑えた分（投資の合図）は見ない


def with_contract(world):
    for island in world.islands.values():
        if island.has_economy and offtake.contracts(island):
            return island, offtake.contracts(island)[0]
    pytest.skip("契約を結んだ島がない")


def test_default_is_off_and_leaves_no_contracts(game):
    world = generate_world(1, 6, game.config, game.defs)
    run(game, world, 3)
    for island in world.islands.values():
        assert "offtake" not in island.ext.get("trade", {})
        assert not any(k.startswith("offtake.") for k in island.stats)


def test_trader_takes_coal_before_the_market_and_pays_the_market_price(on, base):
    world = copy.deepcopy(base)
    island, c = with_contract(world)
    assert island.regime["economy"] != "planned" and c["good"] == "coal"
    by_id = {f.id: f for f in island.facilities}
    trader, maker = by_id[c["trader"]], by_id[c["maker"]]
    assert traders.is_planned(island) is False
    # 契約の量は、頭打ちにする前の狙う量まで
    item = traders.goods_of(trader)["coal"]
    assert (
        sum(x["qty"] for x in offtake.contracts(island) if x["trader"] == trader.id)
        <= max(item[traders.UNCAPPED_X], item["x"]) + c["qty"]
    )
    run(on, world, 1)
    stats = island.stats
    delivered = stats.get("offtake.delivered.coal", 0)
    assert 0 < delivered <= stats["offtake.qty.coal"]
    price = island.economy.prices["coal"]
    paid = stats["offtake.paid.coal"]
    # このターンの島の市場の値段で精算する（値段の記録は板寄せの後に見直すので、値幅の中）
    assert paid > 0 and abs(paid * 1000 // delivered - price) <= price
    assert not fxmoney.reconcile(world)
    # 作り手の操業目標に足す量は、契約の量から前のターンに商社が市場で買った分を引いた分
    extra = offtake.committed_extra(island, maker, "coal")
    spot = maker.economy.get(offtake.SPOT, {}).get("coal", 0)
    assert extra == max(0, offtake.committed(island, maker, "coal") - spot)


def test_lower_target_cuts_the_newest_contract_after_notice(on, base):
    world = copy.deepcopy(base)
    island, c = with_contract(world)
    trader = next(f for f in island.facilities if f.id == c["trader"])
    item = traders.goods_of(trader)["coal"]
    turn = world.turn + 1
    cancel = on.config.get("trade.contract_cancel_turns")
    for x in offtake.contracts(island):
        x.pop("change", None)
    aim(item, 0, cancel)
    offtake.propose(island, on.defs, on.config, turn)
    assert c["change"] == [turn + cancel, 0]
    before = c["qty"]
    offtake.propose(island, on.defs, on.config, turn + 1)
    assert c["qty"] == before  # 予告の間は今の量のまま
    # 狙う量が一度でも下がった間は、戻っても（直近の一番小さい値が下がったまま）取り消さない
    item[traders.UNCAPPED_X] = before
    offtake.propose(island, on.defs, on.config, turn + 1)
    assert "change" in c
    # 狙う量が戻れば予告を取り消す
    aim(item, before, cancel)
    offtake.propose(island, on.defs, on.config, turn + 1)
    assert "change" not in c
    aim(item, 0, cancel)
    offtake.propose(island, on.defs, on.config, turn + 2)
    offtake.propose(island, on.defs, on.config, turn + 2 + cancel)
    assert c not in offtake.contracts(island)


def test_maker_that_cannot_deliver_shrinks_to_what_it_gave(on, base):
    world = copy.deepcopy(base)
    island, c = with_contract(world)
    maker = next(f for f in island.facilities if f.id == c["maker"])
    cancel = on.config.get("trade.contract_cancel_turns")
    c["qty"] = 1000
    c["recent"], c["short"] = [], 0
    given = []
    for k in range(cancel):
        maker.stock.pop("coal", None)
        stock.add(maker.stock, "coal", 400, world.turn, on.defs.goods)
        offtake.deliver(island, on.defs, on.config, world.turn + 1 + k)
        given.append(400)
        offtake.settle(island, on.defs, on.config, {}, ("gov", None))
    assert c["qty"] == sum(given) // len(given)
    assert c["short"] == 0


def test_refused_offtake_counts_as_missed_sales_and_shortage(on, base, monkeypatch):
    world = copy.deepcopy(base)
    island, c = with_contract(world)
    trader = next(f for f in island.facilities if f.id == c["trader"])
    maker = next(f for f in island.facilities if f.id == c["maker"])
    item = traders.goods_of(trader)["coal"]
    aim(
        item,
        sum(x["qty"] for x in offtake.contracts(island)) + 5000,
        on.config.get("trade.contract_cancel_turns"),
    )
    monkeypatch.setattr(offtake, "spare", lambda *a: 0)
    monkeypatch.setattr(traders, "exportable", lambda *a: True)
    island.scratch.pop(offtake.UNMET, None)
    offtake.propose(island, on.defs, on.config, world.turn + 1)
    assert island.scratch[offtake.UNMET]["coal"] == 5000
    refused = maker.economy[offtake.REFUSED]["coal"]
    assert 0 < refused <= 5000
    full = fac.outputs_of(on.defs, maker)["coal"]
    maker.economy[market.MISSED] = []
    market.record_missed(island, maker, _context(on, world))
    assert maker.economy[market.MISSED][-1] >= fac.ratio(refused, full)


def test_contract_follows_sustained_exports_and_the_capped_part_signals_investment(
    on, base, monkeypatch
):
    """契約の量は、圏で売れた輸出の量（頭打ち前の狙う量まで）の直近 contract_cancel_turns ターンの
    一番小さい値まで（2026-11-23 の比べで、頭打ち前の狙う量で結んだ契約の荷が商社に積み上がり、
    作り手の売れ行きが減った）。島の売り手の売りたい量で抑えた分（頭打ち前 − 頭打ち後）は契約に
    せず、作り手の売り逃しと島の品切れの記録に足す。"""
    cancel = on.config.get("trade.contract_cancel_turns")
    item = {"dir": traders.EXPORT, "x": 100}
    for sold in [500] * (cancel - 1):
        item[traders.UNCAPPED_X], item[traders.EXPORTED] = 900, sold
        assert offtake._target(item, cancel) == 0  # まだ予告の長さの分ない
    item[traders.EXPORTED] = 300
    assert offtake._target(item, cancel) == 300  # 一番小さい値
    item[traders.EXPORTED] = 2000
    assert offtake._target(item, cancel) == 300
    for _ in range(cancel):
        item[traders.EXPORTED] = 2000
        last = offtake._target(item, cancel)
    assert last == 900  # 頭打ち前の狙う量まで
    item["dir"] = traders.IMPORT
    for _ in range(cancel):
        offtake._target(item, cancel)
    assert offtake._target(item, cancel) == 0  # 輸出しない間は 0

    world = copy.deepcopy(base)
    island, c = with_contract(world)
    trader = next(f for f in island.facilities if f.id == c["trader"])
    item = traders.goods_of(trader)["coal"]
    total = sum(x["qty"] for x in offtake.contracts(island) if x["trader"] == trader.id)
    aim(item, total, cancel)
    item[traders.UNCAPPED_X] = item["x"] + 700  # 島の売り手の売りたい量で 700 抑えた
    monkeypatch.setattr(traders, "exportable", lambda *a: True)
    island.scratch.pop(offtake.UNMET, None)
    offtake.propose(island, on.defs, on.config, world.turn + 1)
    assert island.scratch[offtake.UNMET]["coal"] >= 700


def test_contract_cargo_is_not_dumped_into_the_bloc(on, base):
    """契約を結んだ商社の輸出の荷は、圏で売れ残りが続いても指値なしで売らず、仕入れ値を下限に売る
    （会議 #4536 の案 a。契約の余りを投げ売りすると、圏の値段が下がって契約のない輸出の島が
    抜けた）。契約のない商社は今までどおり、売れ残りが clearance_turns 続けば指値を外す。"""
    from hakoniwa_core.modules import trade as trade_stage
    from hakoniwa_core.trade import blocs
    from tests.core.test_trade import _context

    world = copy.deepcopy(base)
    island, c = with_contract(world)
    f = next(x for x in island.facilities if x.id == c["trader"])
    [bloc, *_] = blocs.member_of(world, island.id)
    item = traders.entry(f, "coal")
    clearance = on.config.get("trade.clearance_turns")
    item.update({"dir": traders.EXPORT, "bx": bloc["id"], "age": clearance})
    if stock.total(f.stock, "coal") <= 0:
        stock.add(f.stock, "coal", 10 * QUANTITY_SCALE, world.turn, on.defs.goods)
    item["held"] = [stock.total(f.stock, "coal"), item["held"][1] or 1]

    def order_of():
        r = trade_stage._Round(world, _context(on, world))
        members = [m for m in bloc["members"] if m in r.islands]
        _, sells, _, asks = r._bloc_orders(bloc, members, "coal", {}, {})
        return [o for o, a in zip(sells, asks, strict=True) if a.facility is f]

    [order] = order_of()
    assert order.limit is not None and order.limit > 0
    offtake.contracts(island)[:] = [x for x in offtake.contracts(island) if x["trader"] != f.id]
    [order] = order_of()
    assert order.limit is None


def _craft_bloc_imports(on, world):
    """圏の石炭の輸入の場面: 島 2（石炭を作る施設がない）の商社はもともと輸入していて、島 1・5
    （建った炭鉱がある）の商社を輸入に仕立てる。指値が圏の値幅の上端に届くよう、島の石炭の
    値段と決済通貨の持ちをそろえる。"""
    from hakoniwa_core import ledger
    from hakoniwa_core.economy import entity_of
    from hakoniwa_core.trade import blocs

    [bloc, *_] = blocs.member_of(world, 2)
    for iid in (1, 5):
        island = world.islands[iid]
        assert any(
            g.built and "coal" in fac.outputs_of(on.defs, g) for g in island.facilities
        )  # 作る施設のある島
        f = next(x for x in island.facilities if x.kind == "trading")
        item = traders.entry(f, "coal")
        item.update(
            {"dir": traders.IMPORT, "bm": bloc["id"], "m": 10_000 * iid, "hold": 10_000 * iid}
        )
        item["held"] = [0, 0]
        island.economy.prices["coal"] = 93_190
        fxmoney.transfer(
            world,
            bloc["currency"],
            (bloc["currency"], ledger.CB),
            (iid, entity_of(on.config, f)),
            2_000_000_000,
            "trade",
        )
    assert not any(
        g.built and "coal" in fac.outputs_of(on.defs, g) for g in world.islands[2].facilities
    )  # 島 2 は作る施設のない島（輸入に頼る島）
    return bloc


def _bloc_buy_orders(on, world, bloc):
    from hakoniwa_core.modules import trade as trade_stage

    r = trade_stage._Round(world, _context(on, world))
    members = [m for m in bloc["members"] if m in r.islands]
    buys, _, bids, _ = r._bloc_orders(bloc, members, "coal", {}, {})
    return buys, bids


def test_import_first_lifts_priority_of_islands_without_a_maker(on, base, monkeypatch):
    """trade.offtake.import_first（W-307 の cfB。会議 #4742）: 切では輸入注文の優先は変わらない。
    入れると、その財を作る建った施設のない島（輸入に頼る島）の輸入注文だけ、優先が 1 上がる。"""
    world = copy.deepcopy(base)
    bloc = _craft_bloc_imports(on, world)
    buys, bids = _bloc_buy_orders(on, world, bloc)
    orders = {b.island: o for o, b in zip(buys, bids, strict=True)}
    assert {1, 2, 5} <= set(orders)
    assert all(o.priority == 0 for o in orders.values())  # 切（既定）
    monkeypatch.setitem(on.config.raw["trade"]["offtake"], "import_first", True)
    buys, bids = _bloc_buy_orders(on, world, bloc)
    orders = {b.island: o for o, b in zip(buys, bids, strict=True)}
    assert orders[2].priority == 1  # 作る施設のない島だけ上がる
    assert orders[1].priority == 0 and orders[5].priority == 0


def test_import_first_fills_import_dependent_islands_first(on, base, monkeypatch):
    """入のとき、圏の売りが足りなければ、作る施設のない島の輸入注文が先に全部約定し、作る施設の
    ある島は残りを量に比例で分ける（板寄せは優先の高い順、同じ優先の中は量に比例）。"""
    from hakoniwa_core.market import auction
    from hakoniwa_core.market.auction import Order
    from hakoniwa_core.trade import blocs
    from hakoniwa_core.units import to_basis

    monkeypatch.setitem(on.config.raw["trade"]["offtake"], "import_first", True)
    world = copy.deepcopy(base)
    bloc = _craft_bloc_imports(on, world)
    buys, bids = _bloc_buy_orders(on, world, bloc)
    qty = {b.island: o.quantity for o, b in zip(buys, bids, strict=True)}
    # 売りは、作る施設のない島の分と、作る施設のある島の分の半分だけ
    supply = qty[2] + (qty[1] + qty[5]) // 2
    result = auction.clear(
        buys,
        [Order(supply, None)],
        blocs.reference(world, bloc, "coal"),
        to_basis(on.config.get("trade.bloc_band")),
    )
    bought = {b.island: got for b, got in zip(bids, result.bought, strict=True)}
    assert bought[2] == qty[2]  # 輸入に頼る島が先に全部
    rest = supply - bought[2]
    assert bought[1] + bought[5] == rest  # 残りは作る施設のある島で
    total = qty[1] + qty[5]
    for iid in (1, 5):  # 量に比例（割り切れない余りは 1 まで）
        share = qty[iid] * rest // total
        assert share <= bought[iid] <= share + 1
