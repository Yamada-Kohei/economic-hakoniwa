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
from tests.core.test_trade import _context, run

TURNS = 9


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
    item[traders.UNCAPPED_X] = 0
    offtake.propose(island, on.defs, on.config, turn)
    assert c["change"] == [turn + cancel, 0]
    before = c["qty"]
    offtake.propose(island, on.defs, on.config, turn + 1)
    assert c["qty"] == before  # 予告の間は今の量のまま
    # 狙う量が戻れば予告を取り消す
    item[traders.UNCAPPED_X] = before
    offtake.propose(island, on.defs, on.config, turn + 1)
    assert "change" not in c
    item[traders.UNCAPPED_X] = 0
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
    item[traders.UNCAPPED_X] = sum(x["qty"] for x in offtake.contracts(island)) + 5000
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
