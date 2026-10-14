"""フェーズ3-1: 貿易と為替。

- 貿易の仕組みは移し替えの段で置かれ、標準の計画の圏に入った島の商社が売り買いし、荷が船で届く
- 為替のレートは1ターンに値幅（fx.band）より動かない。中央銀行の受け身の注文で、片側だけの注文でも
  両替ができる
- 島をまたぐお金（外貨の持ち高と非居住者の預金）と運ぶ途中の荷は、毎ターン合う（trade_check の段）
- 二国間契約と海外の商人
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger, stock
from hakoniwa_core.autoplan import plan_trade, plan_turn, queue_orders
from hakoniwa_core.economy import Payer, entity_of
from hakoniwa_core.game import load_game
from hakoniwa_core.modules.trade import check
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import (
    blocs,
    contracts,
    merchant,
    port_of,
    shipping_of,
    ships,
    started,
    traders_of,
    world_state,
)
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.units import BASIS, QUANTITY_SCALE

TURNS = 36


@pytest.fixture(scope="module")
def game():
    return load_game()


def run(game, world, turns, each_turn=None, extra=None):
    """標準の計画（島ごとの計画と貿易圏の計画）で turns ターン回す。extra の命令は、前のターンから
    残っている命令より先に実行する（行政力が足りずに次のターンに回らないように）。"""
    for _ in range(turns):
        for island in world.islands.values():
            first = extra(island) if extra is not None else []
            if first:
                queue_orders(island, first)
                start = min(o.seq for o in island.orders) - len(first)
                for i, order in enumerate(island.orders[-len(first) :]):
                    order.seq = start + i
            queue_orders(island, plan_turn(island, game.defs, game.config, world.turn + 1))
        for island_id, orders in plan_trade(world, game.defs, game.config).items():
            queue_orders(world.islands[island_id], orders)
        game.engine.run_turn(world)
        if each_turn is not None:
            each_turn(world)


@pytest.fixture(scope="module")
def traded(game):
    """4島を TURNS ターン回した世界と、ターンごとのレートと海外の商人の売上の記録。"""
    world = generate_world(7, 4, game.config, game.defs)
    history: list[dict[int, dict[str, int]]] = []
    merchant_sales = []

    def keep(w):
        history.append({i: dict(fxrates.rates(w.islands[i])) for i in w.island_ids()})
        merchant_sales.append(sum(i.stats.get("trade.merchant", 0) for i in w.islands.values()))
        assert check(w) == []  # trade_check の段も毎ターン確かめている

    run(game, world, TURNS, keep)
    return world, history, merchant_sales


def test_trade_is_set_up_on_every_island(game, traded):
    world, _, _ = traded
    for island in world.islands.values():
        assert started(island)
        assert port_of(island, game.config) is not None
        shipping = shipping_of(island, game.config)
        assert shipping is not None and shipping.x < 0  # マスを持たない
        assert ships.fleet(shipping) >= game.config.get("trade.start_ships") * QUANTITY_SCALE // 2
        assert traders_of(island, game.config)


def test_blocs_form_and_goods_are_shipped(game, traded):
    world, _, _ = traded
    made = blocs.blocs(world)
    assert len(made) == 2  # 北と南（真ん中の行の島は両方）
    for bloc in made:
        assert len(bloc["members"]) >= 2
        assert bloc["currency"] in bloc["members"]
        assert bloc["volume"] or any(bloc["history"].values())
    cargo = world_state(world)["cargo"]
    assert sum(cargo["loaded"].values()) > 0
    assert sum(cargo["unloaded"].values()) > 0
    handled = [i.stats.get("port.handled", 0) for i in world.islands.values()]
    assert max(handled) > 0


def test_freight_rate_is_per_ton_and_cell(game, traded):
    """運賃は荷 1 トン × 区画 1 つあたり: 運んだ量（1000 分の 1）と下限（船を半分使った量）を同じ
    単位で数える。"""
    world = copy.deepcopy(traded[0])
    island = world.islands[1]
    f = shipping_of(island, game.config)
    for key in ("costs", "carried", "used"):
        f.economy.pop(key, None)
    f.workers = 0
    settings = game.config.get("trade")
    half = ships.fleet(f) * settings["ship_capacity"] * settings["ship_speed"] // 4  # 1000 分の 1
    for carried in (half // 10, 2 * half):
        f.economy.pop("costs", None)
        f.economy.pop("carried", None)
        ships.close_turn(island, game.config, carried, 0)
        cost = f.economy["costs"][-1]
        tons = max(carried, half) / QUANTITY_SCALE
        assert abs(f.economy["rate"] - cost / tons * (1 + settings["cost_markup"])) <= 1


def test_shipping_keeps_its_ship_shortages_apart(game, traded):
    """船が足りなかった記録（ships_short）は、市場の買えなかったわけ（economy["short"]）と別に持つ
    （海運業が輸送船や燃料を買えなかったターンに、市場の段が止まらない）。"""
    world = copy.deepcopy(traded[0])
    island = world.islands[1]
    f = shipping_of(island, game.config)
    turns = game.config.get("trade.cost_turns")
    f.economy["ships_short"] = [1] * turns  # 輸送船を1隻買いに出る
    assert ships.ships_wanted(game.config, f) > 0
    run(game, world, 1)
    f = shipping_of(island, game.config)
    assert isinstance(f.economy["ships_short"], list)
    assert len(f.economy["ships_short"]) <= turns


def test_fx_moves_at_most_the_band_each_turn(game, traded):
    _, history, _ = traded
    band = game.config.get("fx.band")
    moved = 0
    for before, after in zip(history, history[1:], strict=False):
        for island_id, rates in after.items():
            for key, rate in rates.items():
                old = before[island_id].get(key)
                if old is None:
                    continue
                assert abs(rate / old - 1) <= band + 1 / BASIS * 2, (island_id, key, old, rate)
                moved += rate != old
    assert moved > 0  # レートは動いている


def test_foreign_merchant_sells_at_the_start(traded):
    _, _, merchant_sales = traded
    assert sum(merchant_sales) > 0


# --- 為替の部品 ---------------------------------------------------------------------------


def test_one_sided_orders_move_by_the_unmatched_share():
    assert fxrates._step(10000, 10200, 100, 900) == 10020
    assert fxrates._step(10000, 9800, 300, 0) == 9800  # 厚みがなければ指値まで
    assert fxrates._step(10000, 10200, 0, 900) == 10000


def _pair(game, traded):
    """決済通貨を使っている島と、その通貨と、その島の商社の主体（写した世界で）。"""
    world = copy.deepcopy(traded[0])
    for bloc in blocs.blocs(world):
        for member in bloc["members"]:
            if member != bloc["currency"]:
                island = world.islands[member]
                trader = traders_of(island, game.config)[0]
                return world, island, bloc["currency"], entity_of(game.config, trader)
    raise AssertionError("決済通貨を使う島がない")


def test_central_bank_buys_when_there_are_only_sellers(game, traded):
    world, island, currency, seller = _pair(game, traded)
    # 商社に外貨を持たせる（発行した島の中央銀行が非居住者の預金を作る）
    fxmoney.transfer(world, currency, (currency, ledger.CB), (island.id, seller), 50_000, "trade")
    width = fxrates.band_width(game.config)
    before = fxrates.rate(island, currency)
    held = fxmoney.holding(island, seller, currency)
    cb_before = fxmoney.holding(island, ledger.CB, currency)
    money_before = ledger.money(island, seller)
    book = fxrates.Book(depth=1_000_000)
    fxrates.add_sell(book, seller, Payer(seller), 50_000, before, width)
    price, volume, _, _ = fxrates.clear(world, island, currency, book, width)
    assert volume > 0  # 買い手がいなくても、中央銀行が引き受ける
    assert fxmoney.holding(island, ledger.CB, currency) == cb_before + volume
    assert fxmoney.holding(island, seller, currency) == held - volume
    assert ledger.money(island, seller) == money_before + volume * price // BASIS
    low = before - before * width // BASIS
    assert low <= price < before  # 前のレートより安い側でだけ買う
    assert fxmoney.reconcile(world) == []


def test_rate_moves_toward_buyers_without_sellers(game, traded):
    world, island, currency, buyer = _pair(game, traded)
    fxmoney.holdings(island).get(ledger.CB, {}).pop(str(currency), None)  # 中央銀行は売る外貨がない
    ledger.pay(island, ledger.CB, buyer, 10**9, "trade")  # 払う自国通貨は足りている
    width = fxrates.band_width(game.config)
    before = fxrates.rate(island, currency)
    depth = 10_000
    book = fxrates.Book(depth=depth)
    fxrates.add_buy(island, book, buyer, Payer(buyer), depth, before, width)
    price, volume, _, supply = fxrates.clear(world, island, currency, book, width)
    high = before + before * width // BASIS
    assert volume == 0 and supply == 0
    # 相手のない量と市場の厚みが同じなので、値幅の上端までの半分だけ上がる
    assert price == before + (high - before) * depth // (depth + depth)


# --- 二国間契約 -----------------------------------------------------------------------------


def test_contract_delivers_goods_and_money(game, traded):
    """売り手が申し込み、買い手が受けると次のターンから履行される（代金は買い手の通貨。売り手の政府は
    受け取った外貨を為替の市場で売る）。"""
    world = copy.deepcopy(traded[0])
    seller, buyer = world.islands[1], world.islands[2]
    good = "building_materials"
    stock.add(seller.warehouse, good, 50 * QUANTITY_SCALE, world.turn, game.defs.goods)
    price = buyer.economy.prices[good]
    proposal = {
        "partner": buyer.id,
        "side": "sell",
        "good": good,
        "quantity": 2,
        "price": price,
        "currency": buyer.id,
        "turns": 6,
    }

    def offer(island):
        return [("propose_contract", proposal)] if island is seller else []

    run(game, world, 1, extra=offer)
    (c,) = [c for c in contracts.of_island(world, seller.id) if c["proposer"] == seller.id]
    assert c["state"] == contracts.PROPOSED and c["currency"] == buyer.id
    assert any(e.kind == "contract_offer" for e in buyer.log)

    def accept(island):
        return [("accept_contract", {"contract": c["id"]})] if island is buyer else []

    run(game, world, 1, extra=accept)
    c = contracts.find(world, c["id"])
    assert c["state"] == contracts.ACTIVE
    sent = []
    run(game, world, 6, lambda w: sent.append(contracts.find(w, c["id"])["delivered"]))
    c = contracts.find(world, c["id"])
    assert c["state"] == contracts.ENDED
    assert c["delivered"] == 6 * 2 * QUANTITY_SCALE  # 物も船もお金も足りている
    assert abs(c["paid"] - c["delivered"] * price // QUANTITY_SCALE) <= 6
    shipped = [s for s in world_state(world)["shipments"] if s["kind"] == "contract"]
    assert sum(sum(q for _, q in s["batches"]) for s in shipped) <= c["delivered"]
    assert check(world) == []


def test_contract_in_a_currency_nobody_sells_is_short_of_money(game, traded):
    """買い手の島で誰も売らない通貨の契約は払えず、不足のわけを両方のログに書く（罰はない）。"""
    world = copy.deepcopy(traded[0])
    seller, buyer = world.islands[1], world.islands[2]
    assert blocs.currencies_of(world, buyer.id) == set()  # 島2は自分の通貨の圏にだけ入っている
    good = "building_materials"
    stock.add(seller.warehouse, good, 50 * QUANTITY_SCALE, world.turn, game.defs.goods)
    payload = {
        "partner": buyer.id,
        "side": "sell",
        "good": good,
        "quantity": 2,
        "price": seller.economy.prices[good],
        "turns": 3,
    }
    assert contracts.propose(world, seller, payload, world.turn) is None
    c = contracts.contracts(world)[-1]
    assert contracts.accept(world, buyer, c, world.turn) is None
    run(game, world, 2)
    assert c["delivered"] == 0 and c["short"] == c["qty"]
    for island in (seller, buyer):
        (log,) = [e for e in island.log if e.kind == "contract_short"]
        assert log.args["reason"] == "contract_short_money"


def test_contract_cannot_be_accepted_by_the_proposer(game, traded):
    world = copy.deepcopy(traded[0])
    a, b = world.islands[1], world.islands[2]
    payload = {
        "partner": b.id,
        "side": "buy",
        "good": "coal",
        "quantity": 1,
        "price": 10,
        "turns": 3,
    }
    assert contracts.propose(world, a, payload, world.turn) is None
    c = contracts.contracts(world)[-1]
    assert c["seller"] == b.id and c["buyer"] == a.id
    assert contracts.accept(world, a, c, world.turn) == "reason_contract_party"
    assert contracts.accept(world, b, c, world.turn) is None
    assert contracts.accept(world, b, c, world.turn) == "reason_contract_state"
    wait = game.config.get("trade.contract_cancel_turns")
    assert contracts.cancel(world, a, c, world.turn, wait) is None
    assert c["end"] == world.turn + 1 + wait


# --- 貿易圏 ---------------------------------------------------------------------------------


def test_closed_bloc_needs_approval_and_leaving_moves_the_currency(game, traded):
    world = copy.deepcopy(traded[0])
    world_state(world)["blocs"] = []
    a, b, c = (world.islands[i] for i in (1, 2, 3))
    blocs.create(world, a, "試験の圏", False, world.turn)
    (bloc,) = blocs.blocs(world)
    assert blocs.join(world, b, bloc) is None
    assert b.id in bloc["applicants"] and b.id not in bloc["members"]
    assert blocs.approve(world, bloc, c.id) == "reason_bloc_no_application"
    assert blocs.approve(world, bloc, b.id) is None
    assert bloc["members"] == [a.id, b.id]
    assert fxrates.has_rate(b, a.id)
    # 代表で決済通貨の島が抜けると、残った島が代表になり、その通貨に変わる
    bloc["prices"]["coal"] = 1000
    assert blocs.leave(a, bloc) is None
    blocs.settle_leaves(world)
    (bloc,) = blocs.blocs(world)
    assert bloc["members"] == [b.id] and bloc["rep"] == b.id and bloc["currency"] == b.id
    assert bloc["prices"]["coal"] == max(1, 1000 * fxrates.rate(b, a.id) // BASIS)


def test_take_over_needs_an_inactive_representative(game, traded):
    world = copy.deepcopy(traded[0])
    world_state(world)["blocs"] = []
    a, b = world.islands[1], world.islands[2]
    blocs.create(world, a, "試験の圏", True, world.turn)
    (bloc,) = blocs.blocs(world)
    blocs.join(world, b, bloc)
    assert blocs.take_over(world, b, bloc) == "reason_bloc_rep_active"
    a.ext["inactive"] = True
    assert blocs.take_over(world, b, bloc) is None
    assert bloc["rep"] == b.id


# --- 海外の商人 -----------------------------------------------------------------------------


def test_foreign_merchant_comes_for_a_limited_time(game, traded):
    island = copy.deepcopy(traded[0].islands[1])
    config = game.config
    since = 10
    island.ext["trade"]["merchant_from"] = since
    turns = config.get("foreign_merchant.turns")
    assert merchant.offers(island, game.defs, config, since)
    assert merchant.multiplier(island, config, since) == config.get("foreign_merchant.multiplier")
    last = since + turns - 1
    assert merchant.multiplier(island, config, last) == pytest.approx(
        config.get("foreign_merchant.last_multiplier")
    )
    assert merchant.offers(island, game.defs, config, since + turns) == []
    assert merchant.offers(island, game.defs, config, since - 1) == []
