"""船を圏で貸し借りする（FX-56）・船が足りないと数えるのは載らなかったときだけ（FX-57）・中立国の
船は追い返す（MI-34）。見直しのスレッド「価格の決まり方を見直す」の試作（2026-11-02）を入れた。"""

from __future__ import annotations

import copy

from hakoniwa_core.diplomacy import war
from hakoniwa_core.economy import Payer
from hakoniwa_core.military import naval
from hakoniwa_core.modules import trade as trade_stage
from hakoniwa_core.modules.trade import check
from hakoniwa_core.trade import blocs, shipping_of, ships, world_state
from hakoniwa_core.trade import cells as route_cells
from hakoniwa_core.trade import money as fxmoney
from tests.core.test_trade import _context, game, traded  # noqa: F401
from tests.core.test_trade import run as run_world


def _round(game, world):  # noqa: F811
    return trade_stage._Round(world, _context(game, world))


def _empty(game, world, island_id):  # noqa: F811
    f = shipping_of(world.islands[island_id], game.config)
    f.economy["fleet"] = []
    f.economy["busy"] = {}


def test_bloc_cargo_goes_on_the_buyer_then_the_seller_then_the_nearest_member(game, traded):  # noqa: F811
    """圏の荷は、買い手の船、売り手の船、圏のほかの加盟国の船（売り手に近い順）の順に、空いている
    分から載せる。"""
    world = copy.deepcopy(traded[0])
    [bloc] = blocs.blocs(world)
    buyer, seller = world.islands[1], world.islands[2]
    qty = 1000
    assert [c.id for c, _ in _round(game, world)._carriage(bloc, buyer, seller, "coal", qty)] == [1]
    _empty(game, world, 1)
    [(carrier, got)] = _round(game, world)._carriage(bloc, buyer, seller, "coal", qty)
    assert (carrier.id, got) == (2, qty)
    _empty(game, world, 2)
    parts = _round(game, world)._carriage(bloc, buyer, seller, "coal", qty)
    nearest = min((world.islands[i] for i in (3, 4)), key=lambda o: (route_cells(o, seller), o.id))
    assert parts and parts[0][0].id == nearest.id
    for i in (3, 4):
        _empty(game, world, i)
    assert (
        _round(game, world)._carriage(bloc, buyer, seller, "coal", qty) == []
    )  # 載らない分は買わない
    assert _round(game, world)._reach(buyer, bloc, "coal") == 0


def test_ships_of_enemies_and_embargoing_countries_are_not_chartered(game, traded):  # noqa: F811
    world = copy.deepcopy(traded[0])
    [bloc] = blocs.blocs(world)
    for i in (1, 2):
        _empty(game, world, i)
    war.declare(world, 3, 2, world.turn)  # 次のターン（貿易の段のターン）から戦う
    world.islands[4].policy.embargoes["1"] = []  # 4 は 1 との取引を全部止めている
    r = _round(game, world)
    assert war.fighting(world, 3, 2, r.turn)
    assert r._carriage(bloc, world.islands[1], world.islands[2], "coal", 1000) == []


def test_chartered_freight_is_paid_in_the_settlement_currency(game, traded):  # noqa: F811
    """船のない島は、ほかの加盟国の船を借りて輸入し、運賃を決済通貨で船の国の海運業に払う（船の国は
    サービスの輸出、買い手はサービスの輸入）。外貨と帳簿の突き合わせは合う。"""
    world = copy.deepcopy(traded[0])
    _empty(game, world, 2)
    chartered = []
    paid = []

    def each(w):
        carriers = [
            s["carrier"]
            for s in world_state(w)["shipments"]
            if s["loaded"] == w.turn and s["to"] == 2 and s.get("carrier", 2) != 2
        ]
        chartered.extend(carriers)
        if carriers:  # 借りた船に載せたターンは、運賃を払い、船の国が受け取っている
            paid.append(
                (
                    w.islands[2].stats["trade.charter_paid"],
                    sum(w.islands[i].stats["trade.charter_income"] for i in (1, 3, 4)),
                )
            )
        assert check(w) == []
        assert fxmoney.reconcile(w) == []

    # 輸入するかどうかと、いつ載せるかは世界の道筋で変わる（2026-11-10 に飢えた家計の買い方を
    # 直した後は、1 ターン目に島 2 が輸入しなかった。売れ残りの値引きを止めた後は 2 ターンのうちに
    # 載せなかった）ので、半年回して、借りた船に載せたターンで見る
    run_world(game, world, 6, each)
    assert chartered and 2 not in chartered
    assert paid and all(p > 0 and income > 0 for p, income in paid)
    assert world.islands[2].stats["trade.charter_income"] == 0


def test_neutral_chartered_ships_are_turned_back_not_sunk(game, traded, monkeypatch):  # noqa: F811
    """戦っている国の艦は、届け先の国の船なら沈めるが、借りた中立国の船は、艦を出す国が決めて
    いなければ追い返す（2026-11-02）。"""
    world = copy.deepcopy(traded[0])
    s = next(s for s in world_state(world)["shipments"] if s["kind"] == "bloc")
    victim = s["to"]
    [attacker, neutral] = [i for i in world.islands if i not in (victim, s["from"])][:2]
    war.declare(world, attacker, victim, world.turn - 1)
    turn = world.turn
    cell, _ = naval.position(world, s, turn)
    monkeypatch.setattr(naval, "blockade_share", lambda config, ships_: 1.0)
    groups = {cell: {attacker: 5}}
    outcome = naval.shipment_outcome(world, game.defs, game.config, turn, s, [], groups)
    assert outcome == naval.SUNK
    s["carrier"] = neutral
    outcome = naval.shipment_outcome(world, game.defs, game.config, turn, s, [], groups)
    assert outcome == naval.TURNED
    # 艦を出す国が命令 set_neutral_ships で「沈める」にすると、中立国の船も沈める（11-02 本人）
    from hakoniwa_core.modules import military

    military.set_neutral_ships(world.islands[attacker], {"sink": True}, _context(game, world))
    assert world.islands[attacker].log[-1].kind == "neutral_ships_sink"
    outcome = naval.shipment_outcome(world, game.defs, game.config, turn, s, [], groups)
    assert outcome == naval.SUNK


def test_lost_and_sunk_chartered_ships_belong_to_the_carrier(game, traded):  # noqa: F811
    """届け先がなくなった荷の借りた船は持ち主の船団に戻り、沈んだら持ち主の船団から除いて、沈んだ
    数は持ち主に数える。両方の国に知らせる。"""
    world = copy.deepcopy(traded[0])
    r = _round(game, world)
    f3 = shipping_of(world.islands[3], game.config)
    ships.occupy(f3, 999, 500)
    r._lost({"id": 999, "to": 1, "carrier": 3, "good": "coal", "batches": [[0, 1000]]})
    assert "999" not in f3.economy.get("busy", {})
    before = ships.fleet(f3)
    s = {"id": 998, "to": 1, "carrier": 3, "good": "coal", "batches": [[0, 1000]], "load": 1000}
    ships.occupy(f3, 998, 500)
    r._sink_ship(world.islands[1], s)
    assert ships.fleet(f3) < before
    assert world.islands[3].stats.get("trade.ships_sunk", 0) > 0
    assert world.islands[1].stats.get("trade.ships_sunk", 0) == 0
    assert any(e.kind == "lent_ship_sunk" for e in world.islands[3].log)
    assert any(e.kind == "chartered_ship_sunk_out" for e in world.islands[1].log)
    assert not any(e.kind == "ship_sunk_out" for e in world.islands[1].log)


def test_the_issuer_pays_its_own_freight_from_the_same_money(game, traded):  # noqa: F811
    """決済通貨の島は、自分の船の運賃も品物の代金と同じお金から払うので、払えるかの確かめに足す。
    ほかの島は、自分の船の運賃を自国通貨で払うので足さない。借りた船の運賃はどちらも足す。"""
    world = copy.deepcopy(traded[0])
    r = _round(game, world)
    buyer, seller, other = world.islands[1], world.islands[2], world.islands[3]
    plain = r._settle_cost(buyer, seller, "coal", 10_000, buyer, 1000, 1, None)
    assert r._settle_cost(buyer, seller, "coal", 10_000, buyer, 1000, 1, Payer("x")) > plain
    assert r._settle_cost(buyer, seller, "coal", 10_000, other, 1000, 1, None) > plain


def test_ship_shortage_is_counted_only_when_cargo_finds_no_ship(game, traded):  # noqa: F811
    """どの国の船にも載らないときだけ、海運業の「船が足りない」に数える（S2。前は狙う量と自分の空いた
    船を比べ、使わない船まで買っていた）。"""
    world = copy.deepcopy(traded[0])
    run_world(game, world, 1)
    assert not any(
        shipping_of(i, game.config).economy["ships_short"][-1] for i in world.islands.values()
    )
    world = copy.deepcopy(traded[0])
    for i in world.islands:
        _empty(game, world, i)
    run_world(game, world, 1)
    assert any(
        shipping_of(i, game.config).economy["ships_short"][-1] for i in world.islands.values()
    )


def test_countries_that_do_not_lend_keep_their_ships(game, traded):  # noqa: F811
    """命令 set_ship_lending で貸さないと決めた国の船は、売り手でも借りない（11-02 本人カード
    「貸すかどうか国が選べる」）。"""
    from hakoniwa_core.trade import policy

    world = copy.deepcopy(traded[0])
    [bloc] = blocs.blocs(world)
    _empty(game, world, 1)
    buyer, seller = world.islands[1], world.islands[2]
    for i in (2, 3, 4):
        assert world.islands[i].policy.lend_ships  # 初めは貸す
        policy.set_ship_lending(world.islands[i], {"lend": False}, _context(game, world))
    assert world.islands[2].log[-1].kind == "ship_lending_off"
    r = _round(game, world)
    assert r._carriage(bloc, buyer, seller, "coal", 1000) == []
    assert r._reach(buyer, bloc, "coal") == 0
    policy.set_ship_lending(world.islands[4], {"lend": True}, _context(game, world))
    parts = _round(game, world)._carriage(bloc, buyer, seller, "coal", 1000)
    assert [c.id for c, _ in parts] == [4]
