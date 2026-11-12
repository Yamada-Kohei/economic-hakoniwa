"""商社の輸入した荷の売り値と持ち方（FX-63〜FX-67。商社の売り値の直し 案 1〜8）。

- 売り値の下限は帳簿の値と仕入れ直しの値の安い方（低価法）。圏で約定がなければ残した最後の値、
  一度も値がなければ帳簿の値、封鎖・禁輸では帳簿の値（FX-63）
- 借りた船の運賃（直近の実績の 1 単位あたり）を原価に入れる（FX-63）
- 持ちすぎた分だけアウトレットで値幅ずつ下げ、止まりは下限の半分と輸出し直す値の高い方。止まりでも
  売れなければ輸出の荷に替える（FX-64）
- 本当の品不足だけで積み増し、荷が余っている間は狙う量を据え置く（FX-65）
- 届くまでの分と手元の分を持ち、足りない分は market.inventory_adjust ずつ埋める（FX-66）
- 扱える重さと外貨は注文の割合で割り振る（FX-67）
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core.game import load_game
from hakoniwa_core.modules import trade as trade_stage
from hakoniwa_core.state import MarketRecord
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import blocs, island_state, merchant, traders, traders_of
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, QUANTITY_SCALE

from .test_trade import run

TURNS = 30


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture(scope="module")
def traded(game):
    world = generate_world(7, 4, game.config, game.defs)
    run(game, world, TURNS)
    return world


def _importer(game, world):
    """輸入している財がある、市場経済の島の商社と財。"""
    for island in world.islands.values():
        if traders.is_planned(island):
            continue
        for f in traders_of(island, game.config):
            for good, item in sorted(traders.goods_of(f).items()):
                if item.get("dir") == traders.IMPORT and item.get("bm") is not None:
                    return island, f, good, item
    pytest.skip("輸入している商社がない")


def _round(game, world):
    ctx = StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.registry,
        formulas=game.engine.formulas,
        turn=world.turn + 1,
        seed=world.seed,
        stage="trade",
        before=world,
    )
    return trade_stage._Round(world, ctx)


# --- FX-67: 割り振り -------------------------------------------------------------------------


def test_share_out_gives_the_flow_first_then_the_rest_in_proportion():
    units = [QUANTITY_SCALE, QUANTITY_SCALE]
    # 足りていればそのまま
    assert traders.share_out([10, 20], [5, 5], units, 100) == [15, 25]
    # 流れる分にも足りなければ、流れる分を同じ割合で（名前の順で前の財が取りきらない）
    assert traders.share_out([100, 300], [50, 50], units, 200) == [50, 150]
    # 流れる分を配った余りを、足りない分を埋める注文に同じ割合で
    assert traders.share_out([10, 30], [40, 120], units, 80) == [20, 60]
    assert traders.share_out([], [], [], 10) == []
    assert traders.share_out([10], [0], [QUANTITY_SCALE], 0) == [0]


# --- FX-66: 持つ量と注文の量 ----------------------------------------------------------------


def test_the_import_order_closes_a_quarter_of_the_gap_to_the_holding(game):
    adjust = game.config.get("market.inventory_adjust")
    item = {"m": 1000, "hold": 5000, "held": [1000, 0]}
    assert traders.import_want(game.config, item) == 1000 + round(4000 * adjust)
    item["held"][0] = 9000  # 持ちすぎ: 流れる分より少なく注文する
    assert traders.import_want(game.config, item) == max(0, 1000 + round(-4000 * adjust))
    item["held"][0] = 50_000
    assert traders.import_want(game.config, item) == 0
    assert traders.import_want(game.config, {"m": 0, "hold": 5000, "held": [0, 0]}) == 0
    # 持ちすぎの線を超えている間（値下げを数えている・輸出し直す荷がある）は注文しない
    busy = {"m": 1000, "hold": 5000, "held": [0, 0], traders.OUTLET: {"turns": 1, "line": 1}}
    assert traders.import_want(game.config, busy) == 0
    assert traders.import_want(game.config, {**busy, traders.OUTLET: None, "reexport": 5}) == 0


def test_lead_turns_start_from_the_round_trip_and_follow_the_record(game, traded):
    world = copy.deepcopy(traded)
    island, _, _, item = _importer(game, world)
    view = traders.views(island)[str(item["bm"])]
    item.pop("leads", None)
    speed = game.config.get("trade.ship_speed")
    oneway = max(1, -(-view["near"] // speed))
    assert traders.lead_turns(island, game.config, item) == 2 * oneway + 1
    assert traders.lead_turns(island, game.config, item, weightless=True) == oneway + 1
    for turns in (3, 5, 4):
        traders.keep_lead(game.config, item, turns)
    assert traders.lead_turns(island, game.config, item) == 4
    keep = game.config.get("trade.entry_turns")
    for _ in range(keep + 3):
        traders.keep_lead(game.config, item, 7)
    assert item["leads"] == [7] * keep


def test_targets_set_the_holding_to_the_flow_times_lead_and_stock_turns(game, traded):
    world = copy.deepcopy(traded)
    held = [
        (island, f, good, item)
        for island in world.islands.values()
        for f in traders_of(island, game.config)
        for good, item in traders.goods_of(f).items()
        # 引き合わなくなった財は、狙う量が 0 へ下がる途中でも持つ量はすぐ 0
        if item.get("dir") == traders.IMPORT and item.get("m", 0) > 0 and item.get("hold")
    ]
    assert held, "持つ量を持った輸入の荷がない"
    for island, _, good, item in held:
        turns = traders.holding_turns(island, game.defs, game.config, item, good)
        assert item["hold"] == pytest.approx(item["m"] * turns, abs=2)
        assert turns > game.config.get("market.inventory_turns") or (
            game.defs.goods[good].get("shelf_life") is not None
        )


# --- FX-63: 売り値の下限 --------------------------------------------------------------------


def _prospect(good, bloc, restock=None, blocked=False):
    p = traders.Prospect(good)
    if restock is not None:
        p.restock[bloc] = restock
    if blocked:
        p.no_restock.add(bloc)
    return p


def test_the_floor_is_the_lower_of_the_book_and_the_restock_value(game, traded):
    world = copy.deepcopy(traded)
    _, f, good, item = _importer(game, world)
    item["held"] = [2000, 2_000_000]  # 帳簿の値 1 単位 1,000,000
    item.pop("restock", None)
    bloc = item["bm"]
    book = traders.unit_cost(f, good)
    assert book == 1_000_000
    # 一度も仕入れ直しの値がなければ帳簿の値
    assert traders.sell_floor(f, good, item, _prospect(good, bloc)) == book
    # 仕入れ直しの値が安ければその値（低価法）。値は荷ごとに残す
    assert traders.sell_floor(f, good, item, _prospect(good, bloc, 300_000)) == 300_000
    assert item["restock"] == 300_000
    # 圏で約定がなかったターンは、残した最後の値
    assert traders.sell_floor(f, good, item, _prospect(good, bloc)) == 300_000
    # 仕入れ直しの値が帳簿より高ければ帳簿の値
    assert traders.sell_floor(f, good, item, _prospect(good, bloc, 5_000_000)) == book
    # 封鎖・禁輸で仕入れ直せない財は帳簿の値（残した値も捨てる）
    traders.sell_floor(f, good, item, _prospect(good, bloc, 300_000))
    assert traders.sell_floor(f, good, item, _prospect(good, bloc, blocked=True)) == book
    assert "restock" not in item
    # 港も圏もない（見込みがない）島も帳簿の値
    traders.sell_floor(f, good, item, _prospect(good, bloc, 300_000))
    assert traders.sell_floor(f, good, item, None) == book


def test_import_costs_include_the_chartered_freight_and_restock_needs_a_trade(game, traded):
    world = copy.deepcopy(traded)
    island, _, good, item = _importer(game, world)
    key = str(item["bm"])
    view = traders.views(island)[key]
    view.setdefault("traded", [])
    if good not in view["traded"]:
        view["traded"].append(good)
    view.setdefault("supply", [])
    if good not in view["supply"]:
        view["supply"].append(good)
    view.setdefault("charter", {}).pop(good, None)
    before = traders.prospects(island, game.defs, game.config)[good]
    assert int(key) in before.restock
    view["charter"][good] = 50_000  # 1 単位の借りた船の運賃（決済通貨）
    after = traders.prospects(island, game.defs, game.config)[good]
    extra = 50_000 * fxrates.rate(island, view["currency"]) // BASIS
    assert after.restock[int(key)] == before.restock[int(key)] + extra
    assert after.import_floor > before.import_floor
    if before.import_bloc == int(key) and after.import_bloc == int(key):
        assert after.import_limit < before.import_limit  # 運賃の分、圏で買う指値が下がる
    # 前のターンに約定していなければ、仕入れ直しの値は出さない（圏の気配は使わない）
    view["traded"].remove(good)
    assert int(key) not in traders.prospects(island, game.defs, game.config)[good].restock
    # 封鎖されていれば仕入れ直せない
    view["traded"].append(good)
    island_state(island)[merchant.BLOCKADE] = 5000
    found = traders.prospects(island, game.defs, game.config)[good]
    assert int(key) in found.no_restock and int(key) not in found.restock


def test_the_trade_stage_keeps_the_chartered_freight_for_the_cost_turns(game, traded):
    world = copy.deepcopy(traded)
    island, _, good, item = _importer(game, world)
    bloc = blocs.find(world, item["bm"])
    r = _round(game, world)
    here = trade_stage._IslandTurn()
    island_state(island).pop(traders.CHARTER, None)
    here.charter[(bloc["id"], good)] = [60_000, 2000]  # 2 単位で 6 万
    r._keep_charter(island, here)
    view = r._view(bloc, island.id, here)
    assert view["charter"][good] == 30_000
    # 買わなかったターンも数えて平均を残す（1 ターンで忘れない）
    quiet = trade_stage._IslandTurn()
    quiet.charter[(bloc["id"], good)] = [0, 2000]  # 自分の船で 2 単位
    r._keep_charter(island, quiet)
    assert r._view(bloc, island.id, quiet)["charter"][good] == 15_000
    empty = trade_stage._IslandTurn()
    for _ in range(game.config.get("trade.cost_turns")):
        r._keep_charter(island, empty)
    assert good not in r._view(bloc, island.id, empty).get("charter", {})


def test_goods_embargoed_with_every_other_member_cannot_be_restocked(game, traded):
    world = copy.deepcopy(traded)
    island, _, good, item = _importer(game, world)
    bloc = blocs.find(world, item["bm"])
    r = _round(game, world)
    assert good not in r._embargoed(bloc, island.id)
    others = [m for m in bloc["members"] if m != island.id]
    for m in others:
        island.policy.embargoes[str(m)] = [good]
    assert good in r._embargoed(bloc, island.id)
    island.policy.embargoes[str(others[0])] = []
    del island.policy.embargoes[str(others[0])]
    assert good not in r._embargoed(bloc, island.id)


# --- FX-64: 持ちすぎの分の値下げ ------------------------------------------------------------


def test_only_stock_beyond_the_line_is_marked_down_step_by_step(game, traded):
    world = copy.deepcopy(traded)
    island, f, good, item = _importer(game, world)
    config = game.config
    item["m"] = 1000
    item.pop(traders.OUTLET, None)
    item.pop(traders.REEXPORT, None)
    shelf = traders.stock.target_turns(config.get("market.inventory_turns"), game.defs.goods, good)
    line = round(1000 * shelf * config.get("market.clearance_ratio"))
    clearance = config.get("trade.clearance_turns")
    # 線の内側の売れ残りは数えない（ふだん持つ在庫は値下げしない）
    traders.record_home_sales(island, game.defs, config, f, good, (line, 0, 0, 0))
    assert traders.OUTLET not in item
    # 線を超えたまま clearance_turns 続けば、超えた分だけを値下げに出す
    offer = line + 5000
    for _ in range(clearance):
        assert traders._clearance_order(config, item, None, 1000, 900, offer) is None
        traders.record_home_sales(island, game.defs, config, f, good, (offer, 0, 0, 0))
    qty, limit = traders._clearance_order(config, item, None, 1000, 900, offer)
    assert qty == offer - item[traders.OUTLET]["line"]
    assert limit == 900  # ふだんの指値から始める
    # 値下げの分が売れ残るごとに値幅ずつ下げ、下限の clearance_floor 倍で止まる
    band = config.get("market.band")
    traders.record_home_sales(island, game.defs, config, f, good, (offer, 0, qty, 0))
    _, limit = traders._clearance_order(config, item, None, 1000, 900, offer)
    assert limit == round(900 * (1 - band))
    for _ in range(40):
        traders.record_home_sales(island, game.defs, config, f, good, (offer, 0, qty, 0))
        if traders.OUTLET not in item:
            break
        _, limit = traders._clearance_order(config, item, None, 1000, 900, offer)
        assert limit >= round(1000 * config.get("market.clearance_floor"))
    # 止まりの値で 2 × clearance_turns ターン売れ残れば、超えた分を輸出の荷に替える
    assert traders.OUTLET not in item
    assert item[traders.REEXPORT] == offer - line
    assert item[traders.REEXPORT_BLOC] == item["bm"]
    assert traders.surplus(item)


def test_the_markdown_stops_at_the_export_value_when_that_is_higher(game, traded):
    world = copy.deepcopy(traded)
    _, _, good, item = _importer(game, world)
    config = game.config
    item[traders.OUTLET] = {"turns": 5, "line": 0, "start": 900, "steps": 30}
    item.pop(traders.REEXPORT_BLOC, None)
    item["bm"] = 1
    # 輸出補助の入った輸出の見込み（export_limit）ではなく、荷を買う圏で輸出し直して手元に残る値
    p = traders.Prospect(good, export_bloc=2, export_limit=900)
    p.resale[1] = 700
    _, limit = traders._clearance_order(config, item, p, 1000, 900, 100)
    assert limit == 700  # ほかの商社が島で買って輸出し直す値より下げない
    p.resale.clear()
    _, limit = traders._clearance_order(config, item, p, 1000, 900, 100)
    assert limit == round(1000 * config.get("market.clearance_floor"))


def test_reexported_cargo_returns_to_the_island_when_the_bloc_does_not_buy(game):
    config = game.config
    clearance = config.get("trade.clearance_turns")
    item = {"bm": None, "bx": None, traders.OUTLET: {"turns": 9, "line": 0, "stuck": 99}}
    item.update(m=1000, held=[5000, 0], dir=traders.IMPORT)
    assert traders.resale_bloc(item) is None
    # 輸出し直す圏がなければ、止まりの値で島に出し続ける（輸出の荷にしない）
    item["bx"] = 3
    assert traders.resale_bloc(item) == 3
    item[traders.REEXPORT], item[traders.REEXPORT_BLOC] = 4000, 3
    traders.keep_reexport(config, item, 1000, 5000)  # 圏で売れた
    assert item[traders.REEXPORT_AGE] == 0
    for _ in range(2 * clearance - 1):
        traders.keep_reexport(config, item, 0, 5000)
        assert item[traders.REEXPORT] == 4000
    traders.keep_reexport(config, item, 0, 5000)  # 圏でも長く売れなければ島に戻す
    assert traders.REEXPORT not in item and traders.REEXPORT_BLOC not in item
    item[traders.REEXPORT] = 4000
    traders.keep_reexport(config, item, 0, 0)  # 荷がなくなれば外す
    assert traders.REEXPORT not in item
    # 荷より多くは輸出し直さない
    item[traders.REEXPORT] = 9000
    traders.keep_reexport(config, item, 0, 2500)
    assert item[traders.REEXPORT] == 2500


def test_planned_traders_order_as_before(game):
    item = {"m": 1000, "hold": 9000, "held": [300, 0]}
    assert traders.import_want(game.config, item, planned=True) == 700


def test_markdowns_stop_when_stock_falls_back_and_planned_islands_never_mark_down(game, traded):
    world = copy.deepcopy(traded)
    island, f, good, item = _importer(game, world)
    item[traders.OUTLET] = {"turns": 4, "line": 100, "start": 900, "steps": 2}
    traders.record_home_sales(island, game.defs, game.config, f, good, (150, 100, 0, 0))
    assert traders.OUTLET not in item
    item[traders.OUTLET] = {"turns": 4, "line": 100}
    island.ext.setdefault("plan", {})["prices"] = {good: 1}
    traders.record_home_sales(island, game.defs, game.config, f, good, (900, 0, 0, 0))
    assert traders.OUTLET not in item


def test_home_orders_send_the_markdown_to_the_outlet_and_sell_the_rest_as_usual(game, traded):
    world = copy.deepcopy(traded)
    island, f, good, item = _importer(game, world)
    stock_now = traders.stock.total(f.stock, good)
    if stock_now <= 0:
        traders.stock.add(f.stock, good, 10_000, world.turn)
        item["held"][0] += 10_000
        item["held"][1] += 10_000 * 1000
    item.pop(traders.REEXPORT, None)
    item[traders.OUTLET] = {"turns": 9, "line": 0}
    orders = [
        o
        for o in traders.home_orders(island, game.defs, game.config, world.turn + 1)
        if o.facility is f and o.good == good
    ]
    cut = [o for o in orders if o.clearance]
    usual = [o for o in orders if not o.clearance and o.limit is not None]
    assert cut and usual
    assert cut[0].limit <= usual[0].limit
    # 輸出の荷に替えた分は島で売らない
    item.pop(traders.OUTLET)
    total = sum(o.qty for o in orders if not o.clearance)
    item[traders.REEXPORT] = total
    left = [
        o
        for o in traders.home_orders(island, game.defs, game.config, world.turn + 1)
        if o.facility is f and o.good == good
    ]
    assert not left


def test_reexport_is_offered_in_the_bloc_at_the_bloc_price(game, traded):
    world = copy.deepcopy(traded)
    island, f, good, item = _importer(game, world)
    if traders.stock.total(f.stock, good) <= 0:
        traders.stock.add(f.stock, good, 10_000, world.turn)
    bloc = blocs.find(world, item["bm"])
    r = _round(game, world)
    item[traders.REEXPORT] = 10**12
    order = r._reexport_order(island, f, item, good, bloc, 10**12)
    reference = blocs.fixed_price(bloc, good) or blocs.reference(world, bloc, good)
    if reference is None:
        assert order is None
        return
    assert order is not None
    assert order.limit == reference
    assert order.quantity <= traders.stock.total(f.stock, good)
    item[traders.REEXPORT] = 0
    assert r._reexport_order(island, f, item, good, bloc, 10**12) is None


# --- FX-65: 本当の品不足 ---------------------------------------------------------------------


def test_only_buyers_at_or_above_the_price_count_as_a_shortage(game, traded):
    world = copy.deepcopy(traded)
    island = next(iter(world.islands.values()))
    record = MarketRecord("clothing", 100, 40, 100, 50, "trade")
    island.scratch.pop(traders.TRUE_SHORT, None)
    assert traders.true_shortage(island, record) == 60  # 市場の段の記録がなければ前の数え方
    island.scratch[traders.TRUE_SHORT] = {"clothing": 15}
    assert traders.true_shortage(island, record) == 15
    island.scratch[traders.TRUE_SHORT] = {}
    assert traders.true_shortage(island, record) == 0
    assert traders.true_shortage(island, None) == 0


def test_the_market_records_true_shortages_and_trader_sales(game, traded):
    """1 ターン回すと、市場の段が本当の品不足（買えなかった量以下）を残す。"""
    world = copy.deepcopy(traded)
    run(game, world, 1)
    for island in world.islands.values():
        short = island.scratch.get(traders.TRUE_SHORT)
        if short is None:
            continue
        records = {m.good: m for m in island.markets}
        for good, qty in short.items():
            record = records[good]
            assert 0 < qty <= record.demand - record.volume


def test_targets_hold_still_while_the_cargo_is_beyond_the_line(game, traded):
    world = copy.deepcopy(traded)
    island, f, good, item = _importer(game, world)
    if not item.get("m"):
        pytest.skip("狙う量がない")
    item[traders.OUTLET] = {"turns": 1, "line": 0}
    before = item["m"]
    # 島の市場で買えなかった量がたくさんあっても、荷が余っている間は積み増さない
    island.scratch[traders.TRUE_SHORT] = {good: 10**9}
    traders.update_targets(island, game.defs, game.config, {}, {}, world.turn)
    item = traders.goods_of(f).get(good)
    if item is not None and item.get("dir") == traders.IMPORT and item.get("m"):
        assert item["m"] == before
