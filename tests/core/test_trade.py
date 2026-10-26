"""フェーズ3-1: 貿易と為替。

- 貿易の仕組みは移し替えの段で置かれ、標準の計画の圏に入った島の商社が売り買いし、荷が船で届く
- 為替のレートは1ターンに値幅（fx.band）より動かない。中央銀行の受け身の注文で、片側だけの注文でも
  両替ができる
- 島をまたぐお金（外貨の持ち高と非居住者の預金）と運ぶ途中の荷は、毎ターン合う（trade_check の段）
- 二国間契約と海外の商人
"""

from __future__ import annotations

import copy
from dataclasses import replace
from functools import partial

import pytest

from hakoniwa_core import ledger, stock
from hakoniwa_core.autoplan import plan_trade, plan_turn, queue_orders
from hakoniwa_core.economy import Payer, entity_of
from hakoniwa_core.game import load_game
from hakoniwa_core.log import format_log
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules import market
from hakoniwa_core.modules.trade import check
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import (
    blocs,
    contracts,
    currency_label,
    currency_labels,
    merchant,
    port_of,
    send_to_market,
    shipping_of,
    ships,
    staff,
    started,
    swap,
    to_market,
    traders,
    traders_of,
    world_state,
)
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, to_basis

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
    assert ships.ships_wanted(game.config, f, world.turn) > 0
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


def _without_issuer(world, currency):
    """通貨を発行した島の中央銀行の受け身の注文を出さないようにする（島の中央銀行だけを確かめる）。"""
    world.islands[currency].economy.started = None


def test_central_bank_buys_when_there_are_only_sellers(game, traded):
    world, island, currency, seller = _pair(game, traded)
    _without_issuer(world, currency)
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
    _without_issuer(world, currency)
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


def test_central_bank_borrows_a_currency_nobody_on_the_island_holds(game, traded):
    """島の誰も持っていない通貨も、島の中央銀行が融通で借りて、前のレートより高い側で売る（3-3 の
    続き。前は発行した島の中央銀行が売り、受け取ったこの島の通貨をため込んでいた）。"""
    world, island, currency, buyer = _pair(game, traded)
    issuer = world.islands[currency]
    # 島の主体の持ち高を発行した島の中央銀行へ戻し、誰も持っていない形にする
    for entity in sorted(fxmoney.holdings(island)):
        have = fxmoney.holding(island, entity, currency)
        if have > 0:
            back = (currency, ledger.CB)
            fxmoney.transfer(world, currency, (island.id, entity), back, have, "trade")
    reserve = fxmoney.holding(issuer, ledger.CB, island.id)
    owed = swap.debt_in(island, currency)
    ledger.pay(island, ledger.CB, buyer, 10**9, "trade")
    width = fxrates.band_width(game.config)
    before = fxrates.rate(island, currency)
    book = fxrates.Book(
        depth=10_000,
        borrow_room=swap.available(world, game.config, island.id, currency),
        borrow=partial(swap.draw, world, game.config, island.id, currency),
    )
    fxrates.add_buy(island, book, buyer, Payer(buyer), 5_000, before, width)
    price, volume, _, _ = fxrates.clear(world, island, currency, book, width)
    assert volume > 0
    assert before < price <= before + before * width // BASIS  # 高い側でだけ売る
    assert fxmoney.holding(island, buyer, currency) == volume
    assert swap.debt_in(island, currency) == owed + volume  # 売った分を借りた
    assert fxmoney.holding(issuer, ledger.CB, island.id) == reserve  # 発行した国はため込まない
    fxrates.ensure(world, issuer, island.id)  # 同じ組のレートは1つ（使い始めるときも逆数）
    assert fxrates.rate(issuer, island.id) == fxrates.inverse(price)
    assert fxmoney.reconcile(world) == [] and swap.reconcile(world) == []


# --- 二国間契約 -----------------------------------------------------------------------------


def test_contract_delivers_goods_and_money(game, traded):
    """売り手が申し込み、買い手が受けると次のターンから履行される（代金は買い手の通貨。売り手の政府は
    受け取った外貨を為替の市場で売る）。"""
    world = copy.deepcopy(traded[0])
    seller, buyer = world.islands[1], world.islands[2]
    good = "building_materials"
    # 国の工事も倉庫の資材を先に使う（使う量は世界の流れで変わり、回3 で 100 を超えた）ので、契約の
    # 12 に多めに足して入れておく
    stock.add(seller.warehouse, good, 200 * QUANTITY_SCALE, world.turn, game.defs.goods)
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


def test_contract_in_a_currency_nobody_holds_is_paid_through_its_issuer(game, traded):
    """買い手の島で誰も持っていない通貨の契約も、政府が発行した島の中央銀行から買って払う（発行した島の
    中央銀行が買い手の通貨を受け取るか、買い手の島の中央銀行に融通で貸して、そこから買う。フェーズ3-3）。
    市場の厚みより多くは買えず足りないときは、お金ではなく外貨を買えなかったことを不足のわけに書く。"""
    world = copy.deepcopy(traded[0])
    # 買い手は自分の通貨の圏にだけ入っている島（ほかの通貨を誰も持っていない）
    buyer = next(
        world.islands[i] for i in world.island_ids() if blocs.currencies_of(world, i) == set()
    )
    seller = next(world.islands[i] for i in world.island_ids() if i != buyer.id)
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
    lent_before = swap.lent(seller).get(str(seller.id), {}).get(str(buyer.id), 0)
    run(game, world, 2)
    assert c["delivered"] >= c["qty"]  # 止まらずに届く
    lent_after = swap.lent(seller).get(str(seller.id), {}).get(str(buyer.id), 0)
    # 発行した島の中央銀行の外貨準備か、買い手の島の中央銀行に貸した自国の通貨
    assert fxmoney.holding(seller, ledger.CB, buyer.id) > 0 or lent_after > lent_before
    for island in (seller, buyer):
        reasons = {e.args["reason"] for e in island.log if e.kind == "contract_short"}
        assert reasons <= {"contract_short_fx"}
    assert check(world) == []


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


# --- 試し遊び（ターン150）の直し -----------------------------------------------------------


def test_trade_facilities_hiring_for_their_cargo_are_not_short_of_workers(game, traded):
    """港と商社は扱う量に合わせて少なく雇うので、雇いたい人数がそろっていれば「労働者不足」ではなく
    「扱う荷に合わせて操業」と出す。

    港はその月に扱った量を、雇った後（貿易の段）に足すので、雇ったときに見た前の月までの量で
    比べる（値幅をなくした後の流れで、最後の月に荷が増えて1人足りなく見えた）。"""
    for island in traded[0].islands.values():
        port = port_of(island, game.config)
        assert port is not None
        for f in [port, *traders_of(island, game.config)]:
            seen = f
            if f is port:
                seen = replace(f, economy={**f.economy, "handled": f.economy["handled"][:-1]})
            else:  # 商社も同じ: 雇ったときに見た前の月までの量で比べる
                state = traders.state_of(f)
                if len(state["volume"]) >= game.config.get("trade.entry_turns"):
                    # 残す月の数がいっぱいなら、雇ったときに見ていた一番古い月はもう消えているので、
                    # 雇いたい人数は比べ直せない（操業の知らせだけ見る。W-186 回4 で量が変わり、
                    # 消えた月の量で雇いたい人数が変わる島が出た）。賃金が払えずに減らした
                    # （wages）のは人手の足りなさではないのでよい
                    assert f.status in ("", "cargo", "wages")
                    continue
                seen = replace(
                    f, economy={**f.economy, "trade": {**state, "volume": state["volume"][:-1]}}
                )
            assert f.workers >= staff.wanted(game.config, game.defs, seen)
            assert f.status in ("", "cargo")
    assert format_log(game.defs, "status_cargo", {}) == "扱う荷に合わせて操業"


def test_new_port_and_trading_company_start_rather_than_restart(game):
    """建国で置いた港と商社が初めて動いたときは「稼働を開始しました」（前は「再開しました」）。"""
    world = generate_world(7, 4, game.config, game.defs)
    first: dict[tuple[int, str], str] = {}

    def keep(w):
        for island in w.islands.values():
            for e in island.log:
                if e.kind in ("facility_started", "facility_restarted"):
                    first.setdefault((island.id, e.args["facility"]), e.kind)

    run(game, world, 3, keep)
    for island in world.islands.values():
        for f in (port_of(island, game.config), traders_of(island, game.config)[0]):
            assert first.get((island.id, f.kind)) == "facility_started"


def test_off_map_facility_logs_say_on_the_island(game, traded):
    shipping = shipping_of(traded[0].islands[1], game.config)
    args = {"facility": shipping.kind, "x": shipping.x, "y": shipping.y}
    text = format_log(game.defs, "state_facility_losses", args)
    assert text.startswith("島の国営の") and "(-" not in text
    on_map = format_log(game.defs, "state_facility_losses", {**args, "x": 3, "y": 4})
    assert on_map.startswith("(3, 4) の")


def test_currencies_with_the_same_name_carry_the_country():
    names = {1: ("共和国", "クレジット"), 2: ("連合", "クレジット"), 3: ("公国", "ドル")}
    labels = currency_labels(names)
    assert labels == {1: "共和国のクレジット", 2: "連合のクレジット", 3: "ドル"}


def test_bloc_changes_are_told_to_every_member(game, traded):
    world = copy.deepcopy(traded[0])
    world_state(world)["blocs"] = []
    a, b, c = (world.islands[i] for i in (1, 2, 3))
    blocs.create(world, a, "試験の圏", True, world.turn)
    (bloc,) = blocs.blocs(world)
    for island in (b, c):
        assert blocs.join(world, island, bloc) is None
    for island in (a, b, c):
        island.log.clear()
    assert blocs.settings(world, bloc, False, b.id) is None
    assert blocs.transfer(world, bloc, c.id) is None
    for island in (a, b, c):
        kinds = [e.kind for e in island.log]
        assert "bloc_currency_changed" in kinds and "bloc_closed" in kinds
        assert ("bloc_rep_received" if island is c else "bloc_rep_changed") in kinds
    (log,) = [e for e in a.log if e.kind == "bloc_currency_changed"]
    assert log.args["currency"] == currency_label(world, b.id)


def _sold_by_government(island, good):
    """このターンに国の倉庫が島の市場で売った量。"""
    return sum(t.quantity for t in island.trades if t.good == good and t.seller == ledger.GOV)


def _context(game, world):
    return StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.registry,
        formulas=game.engine.formulas,
        turn=world.turn + 1,
        seed=world.seed,
        stage="market",
        before=None,
    )


def test_goods_nobody_buys_fall_toward_the_export_price(game, traded, monkeypatch):
    """島に買い手がなく売りだけの財は、圏で輸出が引き合う値段（売りの指値以上）まで値幅ずつ下がる。
    輸出が引き合わないときは、前のとおり動かない。"""
    world = copy.deepcopy(traded[0])
    island = world.islands[1]
    good, price, ask = "coal", 10_000, 7_000
    width = to_basis(game.config.get("market.band"))
    seller = market.Party(market.STORE, ledger.GOV, Payer(ledger.GOV))

    def record(export_limit):
        island.economy.prices[good] = price
        island.scratch.pop("wanted", None)
        book = market.Book(sells=[(Order(1000, ask), seller)])
        result = auction.clear([], [o for o, _ in book.sells], price, width)
        found = traders.Prospect(good, export_bloc=1, export_currency=1, export_limit=export_limit)
        monkeypatch.setattr(traders, "prospects", lambda *args: {good: found})
        market.Market._record(island, _context(game, world), {good: book}, {good: result}, {})
        return island.economy.prices[good]

    assert record(8_000) == auction.band(price, width)[0]
    assert record(6_000) == price  # 売りの指値より安くしか輸出できない
    assert record(12_000) == price  # 今の値段のままで輸出が引き合う


def test_perishables_arriving_in_the_warehouse_go_to_the_market(game, traded):
    """国の倉庫に船で届いた腐る物は、売り切れるまで毎ターン島の市場に売りに出す。前からある倉庫の
    在庫には手を付けない。"""
    world = copy.deepcopy(traded[0])
    island = world.islands[2]
    good = "food"
    stock.add(island.warehouse, good, 3 * QUANTITY_SCALE, world.turn, game.defs.goods)  # 備蓄
    stock.add(island.warehouse, good, 2 * QUANTITY_SCALE, world.turn, game.defs.goods)
    send_to_market(island, good, 2 * QUANTITY_SCALE)
    sold = []
    run(game, world, 3, lambda w: sold.append(_sold_by_government(w.islands[2], good)))
    assert 0 < sum(sold) <= 2 * QUANTITY_SCALE
    assert to_market(island).get(good, 0) == 2 * QUANTITY_SCALE - sum(sold)
    assert stock.total(island.warehouse, good) >= 3 * QUANTITY_SCALE  # 備蓄は売らない


def test_food_bought_by_contract_reaches_the_market(game, traded):
    world = copy.deepcopy(traded[0])
    seller, buyer = world.islands[1], world.islands[2]
    good = "food"
    stock.add(seller.warehouse, good, 20 * QUANTITY_SCALE, world.turn, game.defs.goods)
    payload = {
        "partner": buyer.id,
        "side": "sell",
        "good": good,
        "quantity": 1,
        "price": buyer.economy.prices[good],
        "currency": buyer.id,
        "turns": 3,
    }
    assert contracts.propose(world, seller, payload, world.turn) is None
    c = contracts.contracts(world)[-1]
    assert contracts.accept(world, buyer, c, world.turn) is None
    sold, told = [], []

    def keep(w):
        sold.append(_sold_by_government(w.islands[2], good))
        told.extend(e for e in w.islands[2].log if e.kind == "cargo_to_market")

    run(game, world, 8, keep)
    assert c["delivered"] == 3 * QUANTITY_SCALE
    assert told and told[0].args["good"] == good
    assert sum(sold) > 0  # 国の倉庫から島の市場に出て、住民が買った


def test_bloc_buyer_in_the_settlement_currency_pays_only_what_it_has(game, traded, monkeypatch):
    """決済通貨の島の商社は運賃と使用料も同じお金から払う。注文の見込みより払えるお金が少なくても、
    払える分だけ約定し、ターンは止まらない（前は資金不足の例外でターンが止まることがあった）。"""
    from hakoniwa_core.modules import trade as trade_stage

    world = copy.deepcopy(traded[0])
    original = trade_stage._Round._import_order
    inflated = []

    def import_order(self, island, f, entity, item, good, bloc, left, money_left):
        order = original(self, island, f, entity, item, good, bloc, left, money_left)
        if order is None or island.id != bloc["currency"]:
            return order
        inflated.append(good)
        return Order(order.quantity * 50, order.limit)  # 払えるお金を超える注文

    monkeypatch.setattr(trade_stage._Round, "_import_order", import_order)
    run(game, world, 6)
    assert inflated
    assert check(world) == []


# --- W-140: 建国の瞬間に置く・商社が戻る道 ----------------------------------------------------


def _market_island(game, seed=7):
    from hakoniwa_core.state.generate import generate_world

    world = generate_world(seed, 1, game.config, game.defs)
    island = world.islands[1]
    assert island.regime["economy"] == "market"
    return world, island


def test_open_for_business_places_port_and_trader_at_founding(game):
    """プレイヤーの建国は、次の更新を待たずに港・海運業・商社を置く。更新の段は重ねて置かない。"""
    from hakoniwa_core.founding import open_for_business

    world, island = _market_island(game)
    assert not started(island) and port_of(island, game.config) is None
    open_for_business(island, game.defs, game.config, world.turn)
    assert started(island)
    assert port_of(island, game.config) is not None
    assert shipping_of(island, game.config) is not None
    assert len(traders_of(island, game.config)) == 1
    before = sorted((f.kind, f.x, f.y) for f in island.facilities)
    game.engine.run_turn(world)
    after = sorted((f.kind, f.x, f.y) for f in island.facilities)
    assert [f for f in after if f[0] in ("port", "trading")] == [
        f for f in before if f[0] in ("port", "trading")
    ]


def test_refound_moves_the_turns_of_the_trade_setup(game):
    import random

    from hakoniwa_core.founding import found_island, open_for_business, refound
    from hakoniwa_core.state.generate import random_plan
    from hakoniwa_core.state.serialize import island_to_dict

    land = {(x, y) for x in range(8, 24) for y in range(8, 24)}
    plan = random_plan(random.Random(5), "T", game.config, game.defs, land, {"economy": "market"})
    early = found_island(game.defs, game.config, plan, 1, 0, 0, turn=3)
    open_for_business(early, game.defs, game.config, 3)
    late = found_island(game.defs, game.config, plan, 1, 0, 0, turn=9)
    open_for_business(late, game.defs, game.config, 9)
    assert island_to_dict(late) != island_to_dict(early)
    refound(late, 3, game.defs)
    assert island_to_dict(late) == island_to_dict(early)


def _foreclose_all_traders(island, config, turn):
    for f in traders_of(island, config):
        f.economy["foreclosed"] = {"since": turn}


def test_a_foreclosed_trader_does_not_keep_the_island_without_a_trader(game):
    """倒産して差し押さえ中の商社が残っていても、entry_turns ターンたつと民間が新しい商社を開く。"""
    from hakoniwa_core.trade import setup

    world, island = _market_island(game)
    game.engine.run_turn(world)
    assert traders_of(island, game.config)
    old = traders_of(island, game.config)[0]
    _foreclose_all_traders(island, game.config, world.turn)
    assert traders_of(island, game.config) == []
    wait = game.config.get("trade.entry_turns")
    first = world.turn + 1  # 動いている商社がいないと気づいたターンから数える
    for turn in range(first, first + wait):
        assert setup.entry(island, game.defs, game.config, False, turn) is None
    opened = setup.entry(island, game.defs, game.config, False, first + wait)
    assert opened is not None and opened is not old
    assert traders_of(island, game.config) == [opened]


def test_the_state_opens_a_trading_company_when_private_owners_cannot(game, monkeypatch):
    """民間に開業の資金がないまま、さらに entry_turns ターンたつと、国が国営の商社を開く。"""
    from hakoniwa_core.trade import setup

    world, island = _market_island(game)
    game.engine.run_turn(world)
    _foreclose_all_traders(island, game.config, world.turn)
    monkeypatch.setattr(setup, "owners_can_fund", lambda island, config: False)
    wait = game.config.get("trade.entry_turns")
    first = world.turn + 1
    for turn in range(first, first + 2 * wait):
        assert setup.entry(island, game.defs, game.config, False, turn) is None
    opened = setup.entry(island, game.defs, game.config, False, first + 2 * wait)
    assert opened is not None and opened.owner == "state"
    assert any(e.kind == "trader_opened_state" for e in island.log)


# --- 商社の採算（W-157） -----------------------------------------------------------------


def test_a_trader_without_cargo_keeps_only_a_caretaker_and_grows_with_its_cargo(game):
    """荷がない商社（圏に入れない島など）は 1 人だけ置き、扱う荷が増えれば直近の量に合わせて増やす。
    前は荷が 0 でも定義の人数の 1 割（5 人）を雇い続け、収入がないまま賃金だけ出ていた。"""
    world, island = _market_island(game)
    game.engine.run_turn(world)
    f = traders_of(island, game.config)[0]
    full = game.defs.facilities[f.kind]["workers"]
    capacity = game.config.get("trade.trader_capacity") * QUANTITY_SCALE
    assert game.config.get("trade.staff_floor") < 0.1
    traders.state_of(f)["volume"] = []
    assert traders.workers_wanted(game.config, game.defs, f) == 1
    traders.state_of(f)["volume"] = [0] * 12
    assert traders.workers_wanted(game.config, game.defs, f) == 1
    traders.state_of(f)["volume"] = [capacity // 10] * 12
    assert traders.workers_wanted(game.config, game.defs, f) == round(full * 0.1)
    traders.state_of(f)["volume"] = [capacity] * 12
    assert traders.workers_wanted(game.config, game.defs, f) == full
    traders.state_of(f)["volume"] = [capacity * 3] * 12  # 扱える重さを超えても定義の人数まで
    assert traders.workers_wanted(game.config, game.defs, f) == full


def test_an_island_outside_every_bloc_pays_its_trader_one_wage(game):
    """貿易圏に入っていない島の商社は、取引量 0 のまま、賃金は 1 人分だけ。"""
    world = generate_world(7, 2, game.config, game.defs)
    for _ in range(20):
        for island in world.islands.values():
            queue_orders(island, plan_turn(island, game.defs, game.config, world.turn + 1))
        game.engine.run_turn(world)  # 貿易圏の計画は出さない
    for island in world.islands.values():
        f = traders_of(island, game.config)[0]
        assert not traders.views(island)
        assert f.workers == 1
        # 賃金はそのターンの払った後に動くことがあるので、1 人分の賃金と 1% の内で合えばよい
        assert -f.economy["ebitda"][-1] == pytest.approx(f.economy["wage"] * f.workers, rel=0.01)
        assert sum(traders.state_of(f)["volume"]) == 0
