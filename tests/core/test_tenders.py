"""入札で結ぶ契約と、その罰金（W-268、タブ 9 FX-71・FX-72）。"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger, stock
from hakoniwa_core.autoplan import plan_trade, plan_turn, queue_orders
from hakoniwa_core.game import load_game
from hakoniwa_core.modules.trade import check
from hakoniwa_core.orders import check_order
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import contracts, started, tenders, world_state
from hakoniwa_core.trade import money as fxmoney

GOOD = "building_materials"


@pytest.fixture(scope="module")
def game():
    return load_game()


def run(game, world, turns, extra=None):
    """標準の計画で turns ターン回す。extra(island) の命令は、ほかの命令より先に実行する。"""
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


@pytest.fixture(scope="module")
def base(game):
    """4島を貿易が始まるまで回した世界。"""
    world = generate_world(7, 4, game.config, game.defs)
    run(game, world, 13)
    assert all(started(i) for i in world.islands.values())
    return world


def _orders(world, by_island):
    """{島の番号: [(命令, 中身)]} を、そのターンの extra にする。"""
    return lambda island: by_island.get(island.id, [])


def _open(game, world, buyer, cap, deadline=1, qty=2, turns=4):
    payload = {"good": GOOD, "quantity": qty, "turns": turns, "cap": cap, "deadline": deadline}
    run(game, world, 1, _orders(world, {buyer.id: [("open_tender", payload)]}))
    t = tenders.tenders(world)[-1]
    assert t["buyer"] == buyer.id and t["state"] == tenders.OPEN
    return t


def test_the_lowest_bid_wins_and_other_bids_stay_hidden(game, base):
    world = copy.deepcopy(base)
    a, b, c, d = (world.islands[i] for i in (1, 2, 3, 4))
    cap = 1000
    t = _open(game, world, a, cap, deadline=1)
    assert t["deadline"] == world.turn + 1  # 次のターンの貿易の段で落とす
    low = tenders.floor_price(game.config, cap)
    assert low == 700
    run(
        game,
        world,
        1,
        _orders(
            world,
            {
                b.id: [("bid_tender", {"tender": t["id"], "good": GOOD, "price": 900})],
                c.id: [("bid_tender", {"tender": t["id"], "good": GOOD, "price": 800})],
                d.id: [
                    ("bid_tender", {"tender": t["id"], "good": GOOD, "price": low - 1})
                ],  # 下限より安い
            },
        ),
    )
    t = tenders.find(world, t["id"])
    assert t["state"] == tenders.AWARDED and t["winner"] == c.id and t["price"] == 800
    assert t["bids"] == {}  # 落ちなかった札は残さない
    assert any(
        e.kind == "tender_request_failed" and e.args["reason"] == "reason_tender_price"
        for e in d.log
    )
    k = contracts.find(world, t["contract"])
    assert k["tender"] == t["id"] and k["seller"] == c.id and k["buyer"] == a.id
    assert k["currency"] == a.id and k["price"] == 800
    assert k["start"] == world.turn + 1 and k["end"] == world.turn + 1 + t["turns"]
    for island in (a, c):
        assert any(e.kind == "tender_awarded" for e in island.log)
    # 落ちなかった国には書かない（公開のログから誰が札を入れたかが分からないように）
    assert not any(e.kind == "tender_awarded" for e in b.log)
    assert check(world) == []


def test_ties_are_drawn_by_lot_and_withdrawals_go_first(game, base):
    world = copy.deepcopy(base)
    a, b, c, d = (world.islands[i] for i in (1, 2, 3, 4))
    t = _open(game, world, a, 1000, deadline=1)
    # 締め切りのターンに、b と c が同じ値段、d は札を入れてから同じターンに取り下げる
    t["bids"][str(d.id)] = 750
    orders = {
        b.id: [("bid_tender", {"tender": t["id"], "good": GOOD, "price": 800})],
        c.id: [("bid_tender", {"tender": t["id"], "good": GOOD, "price": 800})],
        d.id: [("bid_tender", {"tender": t["id"], "good": GOOD})],
    }
    twin = copy.deepcopy(world)
    run(game, world, 1, _orders(world, orders))
    run(game, twin, 1, _orders(twin, orders))
    t = tenders.find(world, t["id"])
    assert t["winner"] in (b.id, c.id)
    assert tenders.find(twin, t["id"])["winner"] == t["winner"]  # くじは世界の種で決まる


def test_the_issuer_can_withdraw_before_the_deadline(game, base):
    world = copy.deepcopy(base)
    a, b = world.islands[1], world.islands[2]
    t = _open(game, world, a, 1000, deadline=3)
    run(
        game,
        world,
        1,
        _orders(
            world,
            {
                a.id: [("cancel_tender", {"tender": t["id"]})],
                b.id: [("bid_tender", {"tender": t["id"], "good": GOOD, "price": 900})],
            },
        ),
    )
    t = tenders.find(world, t["id"])
    assert t["state"] == tenders.WITHDRAWN and t["bids"] == {}
    # 取り下げを先に処理するので、同じターンの札は締め切られた入札への札になる
    assert any(
        e.kind == "tender_request_failed" and e.args["reason"] == "reason_tender_closed"
        for e in b.log
    )


def test_a_tender_without_bids_lapses(game, base):
    world = copy.deepcopy(base)
    a = world.islands[1]
    t = _open(game, world, a, 1000, deadline=1)
    run(game, world, 1)
    assert tenders.find(world, t["id"])["state"] == tenders.LAPSED


def test_order_checks(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    good = {"good": GOOD, "quantity": 1, "turns": 3, "cap": 10, "deadline": 2}

    def ok(kind, payload):
        return check_order(game.defs, game.registry, island, kind, payload).ok

    assert ok("open_tender", good)
    assert not ok("open_tender", {**good, "turns": 0})
    assert not ok("open_tender", {**good, "indefinite": True})
    assert ok("bid_tender", {"tender": 1, "good": GOOD, "price": 5})
    assert ok("bid_tender", {"tender": 1, "good": GOOD})
    assert not ok("bid_tender", {"tender": 1, "price": 5})
    assert ok("settle_tender_default", {"contract": 1, "choice": "forgive"})
    assert not ok("settle_tender_default", {"contract": 1, "choice": "later"})
    late = max(1, game.config.get("trade.tender.max_open_turns") + 1)
    run(game, world, 1, _orders(world, {island.id: [("open_tender", {**good, "deadline": late})]}))
    assert any(
        e.kind == "tender_request_failed" and e.args["reason"] == "reason_tender_deadline"
        for e in island.log
    )


# --- 罰金 ---------------------------------------------------------------------------------


def test_short_is_split_by_the_order_of_limits():
    split = tenders.split_short
    assert split(100, {"goods": 30, "port": 50, "money": 10, "ships": 100}) == {
        "goods": 70,
        "money": 20,
    }
    assert split(100, {"blockade": 0, "goods": 0, "money": 0}) == {"blockade": 100}
    assert split(100, {"goods": 100, "money": 100, "ships": 100, "port": 100}) == {}


def _awarded(game, world, buyer, seller, price=None, qty=2, turns=6):
    """入札を出して seller に落とさせ、契約を返す（落とした次のターンから効く）。"""
    cap = buyer.economy.prices[GOOD] * 2
    t = _open(game, world, buyer, cap, deadline=1, qty=qty, turns=turns)
    bid = {"tender": t["id"], "good": GOOD, "price": price or cap}
    run(game, world, 1, _orders(world, {seller.id: [("bid_tender", bid)]}))
    t = tenders.find(world, t["id"])
    assert t["winner"] == seller.id
    return contracts.find(world, t["contract"])


def _empty(island, good):
    stock.take_batches(island.warehouse, good, stock.total(island.warehouse, good))


def test_a_contract_too_big_to_deliver_leaves_penalties_paid_when_collected(game, base):
    """1ターンの量が大きすぎて届かない入札の契約は、わけのある国への罰金が残り、損をした国が取ると
    決めると、次のターンにわけのある国の通貨で払われる（受ける国は外貨として持つ）。"""
    world = copy.deepcopy(base)
    buyer, seller = world.islands[1], world.islands[2]
    _empty(seller, GOOD)
    k = _awarded(game, world, buyer, seller, qty=50_000)
    run(game, world, 1)
    assert k["short"] > 0
    claims = [c for c in tenders.claims(world) if c["contract"] == k["id"]]
    assert claims
    for claim in claims:
        side = "seller" if claim["why"] in tenders.SHORT_BY_SELLER else "buyer"
        assert claim["payer"] == k[side]
        assert claim["amount"] == tenders.penalty_of(game.config, k, claim["qty"])
    claim = claims[0]
    payer, payee = world.islands[claim["payer"]], world.islands[claim["payee"]]
    held = fxmoney.holding(payee, ledger.GOV, payer.id)
    settle = {"contract": k["id"], "choice": "collect"}
    run(game, world, 1, _orders(world, {payee.id: [("settle_tender_default", settle)]}))
    assert not [c for c in tenders.claims(world) if c["id"] == claim["id"]]
    assert any(e.kind == "tender_penalty_charged" for e in payer.log)
    run(game, world, 1)
    assert any(e.kind in ("tender_penalty_paid", "tender_penalty_unpaid") for e in payee.log)
    assert fxmoney.holding(payee, ledger.GOV, payer.id) > held
    assert check(world) == []


def test_an_unpaid_penalty_bars_the_country_from_tenders(game, base):
    world = copy.deepcopy(base)
    a, b, c = (world.islands[i] for i in (1, 2, 3))
    tenders.debts(world).append(
        {
            "id": 99,
            "contract": 1,
            "payer": b.id,
            "payee": c.id,
            "amount": 10**15,  # 払いきれない
            "due": world.turn + 1,
            "late": False,
            "turn": world.turn,
        }
    )
    t = _open(game, world, a, 1000, deadline=1)
    debt = next(d for d in tenders.debts(world) if d["id"] == 99)
    assert debt["late"] and tenders.barred(world, b.id)
    assert any(e.kind == "tender_penalty_unpaid" for e in b.log)
    run(
        game,
        world,
        1,
        _orders(world, {b.id: [("bid_tender", {"tender": t["id"], "good": GOOD, "price": 900})]}),
    )
    assert any(
        e.kind == "tender_request_failed" and e.args["reason"] == "reason_tender_barred"
        for e in b.log
    )
    assert tenders.find(world, t["id"])["state"] == tenders.LAPSED
    # 島が世界から消えたら、その国の未払いは消える
    del world.islands[c.id]
    tenders.close(world, world.turn)
    assert not tenders.barred(world, b.id)


def test_forgive_terminate_and_claims_left_undecided(game, base):
    world = copy.deepcopy(base)
    buyer, seller = world.islands[1], world.islands[2]
    k = _awarded(game, world, buyer, seller, turns=20)
    turn = world.turn
    tenders.record_short(world, game.config, k, {"goods": k["qty"]}, turn)
    tenders.record_short(world, game.config, k, {"ships": k["qty"]}, turn)
    by_buyer = [c for c in tenders.claims(world) if c["payer"] == buyer.id]
    assert by_buyer and by_buyer[0]["why"] == "ships"
    # 許すと消え、払いは出ない
    assert tenders.settle(world, buyer, k, tenders.FORGIVE, turn) is None
    assert not tenders.pending_for(world, buyer.id, k["id"])
    assert not [d for d in tenders.debts(world) if d["payer"] == seller.id]
    assert tenders.settle(world, buyer, k, tenders.FORGIVE, turn) == "reason_tender_no_claim"
    # 売り手は、買い手の船の不足の罰金を取ったうえで契約をやめる
    assert tenders.settle(world, seller, k, tenders.TERMINATE, turn) is None
    assert k["end"] == turn + 1 and k["terminated_by"] == seller.id
    assert [d for d in tenders.debts(world) if d["payer"] == buyer.id]
    # 決めないまま 12 ターンたった罰金は取る
    tenders.record_short(world, game.config, k, {"goods": k["qty"]}, turn)
    tenders.lapse_claims(world, turn + tenders.CLAIM_TURNS - 1)
    assert tenders.pending_for(world, buyer.id, k["id"])
    tenders.lapse_claims(world, turn + tenders.CLAIM_TURNS)
    assert not tenders.pending_for(world, buyer.id, k["id"])
    assert [d for d in tenders.debts(world) if d["payer"] == seller.id]


def test_a_third_party_blockade_carries_no_penalty(game, base):
    from hakoniwa_core.diplomacy import war

    world = copy.deepcopy(base)
    buyer, seller, third = world.islands[1], world.islands[2], world.islands[3]
    k = _awarded(game, world, buyer, seller)
    war.declare(world, third.id, buyer.id, world.turn - 5)
    tenders.record_short(world, game.config, k, {"blockade": k["qty"]}, world.turn)
    assert not [c for c in tenders.claims(world) if c["contract"] == k["id"]]
    # 当事者どうしの戦争の封鎖は、宣戦した国のわけ
    war.declare(world, seller.id, buyer.id, world.turn - 5)
    tenders.record_short(world, game.config, k, {"blockade": k["qty"]}, world.turn)
    (claim,) = [c for c in tenders.claims(world) if c["contract"] == k["id"]]
    assert claim["payer"] == seller.id and claim["why"] == "blockade"


def test_quitting_a_tender_contract_carries_a_penalty_unless_both_quit(game, base):
    world = copy.deepcopy(base)
    buyer, seller = world.islands[1], world.islands[2]
    k = _awarded(game, world, buyer, seller, turns=30)
    twin = copy.deepcopy(world)
    stop = {"contract": k["id"]}
    run(game, world, 1, _orders(world, {seller.id: [("cancel_contract", stop)]}))
    (claim,) = [c for c in tenders.claims(world) if c["why"] == "cancel"]
    turns = game.config.get("trade.contract_cancel_turns")
    assert claim["payer"] == seller.id and claim["payee"] == buyer.id
    assert claim["amount"] == tenders.penalty_of(game.config, k, k["qty"] * turns)
    both = {seller.id: [("cancel_contract", stop)], buyer.id: [("cancel_contract", stop)]}
    run(game, twin, 1, _orders(twin, both))
    assert not [c for c in tenders.claims(twin) if c["why"] == "cancel"]


def test_nothing_is_kept_when_nobody_uses_tenders(game, base):
    world = copy.deepcopy(base)
    run(game, world, 2)
    state = world_state(world)
    assert not state.get("tenders") and not state.get("tender_claims")
    assert not state.get("tender_debts")


def test_quitting_while_the_other_side_terminates_still_carries_the_penalty(game, base):
    """売り手のやめる命令と、買い手の「取ったうえでやめる」が同じターンでも、やめる罰金のあるなしは
    島の順番によらない（買い手の島 1 が先に処理されても、売り手にやめる罰金がかかる。FX-01）。"""
    world = copy.deepcopy(base)
    buyer, seller = world.islands[1], world.islands[2]
    k = _awarded(game, world, buyer, seller, turns=30)
    tenders.record_short(world, game.config, k, {"goods": k["qty"]}, world.turn)
    orders = {
        buyer.id: [("settle_tender_default", {"contract": k["id"], "choice": "terminate"})],
        seller.id: [("cancel_contract", {"contract": k["id"]})],
    }
    run(game, world, 1, _orders(world, orders))
    assert [c for c in tenders.claims(world) if c["why"] == "cancel" and c["payer"] == seller.id]
    assert k["terminated_by"] == buyer.id


def test_an_inactive_country_is_not_charged(game, base):
    world = copy.deepcopy(base)
    buyer, seller = world.islands[1], world.islands[2]
    k = _awarded(game, world, buyer, seller)
    tenders.record_short(world, game.config, k, {"goods": k["qty"]}, world.turn)
    assert tenders.pending_for(world, buyer.id, k["id"])
    seller.ext["inactive"] = True  # 請求の後に休止した
    tenders.lapse_claims(world, world.turn + tenders.CLAIM_TURNS)
    assert not [d for d in tenders.debts(world) if d["payer"] == seller.id]
    tenders.record_short(world, game.config, k, {"goods": k["qty"]}, world.turn)
    assert not tenders.pending_for(world, buyer.id, k["id"])  # 休止中は請求も残さない
