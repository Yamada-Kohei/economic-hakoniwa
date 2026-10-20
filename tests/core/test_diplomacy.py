"""フェーズ4-1: 戦争の状態と条約（docs/design/15-military.md MI-02〜MI-06）。

- 宣戦布告は次のターンから戦争を始め、結んでいる不可侵・同盟を破れば違反として記録する
- 条約は両国の署名で効き、提案は流れ・断られ・取り下げられる
- 停戦は戦闘を止め、予告つきの破棄で戦争に戻る。講和は戦争を終え、賠償が期日までに届かなければ違反
- 違反は信用の傷（国債の利回りへの上乗せ）になり、だんだん消える
"""

from __future__ import annotations

import pytest

from hakoniwa_core import politics
from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.diplomacy import clauses as cl
from hakoniwa_core.diplomacy import credit, treaties, war, world_state
from hakoniwa_core.game import load_game
from hakoniwa_core.log import format_log
from hakoniwa_core.modules.bonds import default_premium
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.turn.context import StageContext


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    """3 島。議会のない体制にして、命令がそのまま通るようにする（民主主義の採決は別に確かめる）。"""
    world = generate_world(7, 3, game.config, game.defs)
    for island in world.islands.values():
        island.regime["politics"] = "authoritarian"
    return world


def step(game, world, orders=None):
    """島ごとの命令（島の番号 → [(種類, 中身)]）を並べて、1ターン進める。"""
    for island_id, items in (orders or {}).items():
        queue_orders(world.islands[island_id], items)
    game.engine.run_turn(world)


def logs(world, island_id, kind):
    return [e for e in world.islands[island_id].log if e.kind == kind]


def hall_entries(world, kind):
    """国際会議場に載せる印の付いたログ（出来事 1 つにつき、当事国のどちらか 1 件だけ）。"""
    return [
        e
        for island in world.islands.values()
        for e in island.log
        if e.kind == kind and e.args.get("hall")
    ]


def sign(game, world, treaty_id, island_id):
    step(game, world, {island_id: [("sign_treaty", {"treaty": treaty_id})]})


def propose(game, world, a, b, clauses, **extra):
    step(game, world, {a: [("propose_treaty", {"partner": b, "clauses": clauses, **extra})]})
    return treaties.treaties(world)[-1]


def test_declared_war_starts_next_turn(game, world):
    step(game, world, {1: [("declare_war", {"target": 2, "reason": "国境の争い"})]})
    w = war.open_war(world, 1, 2)
    assert w is not None and w["attacker"] == 1 and w["defender"] == 2
    assert w["declared"] == world.turn and w["start"] == world.turn + 1
    assert war.status(world, 1, 2, world.turn) == war.DECLARED
    assert not war.fighting(world, 1, 2, world.turn)
    assert war.status(world, 2, 1, world.turn + 1) == war.WAR
    assert war.enemies(world, 1, world.turn + 1) == [2]
    assert war.status(world, 1, 3, world.turn) == war.PEACE
    # 当事国のログと、国際会議場に載せる印（出来事 1 つに 1 件）
    [declared] = logs(world, 1, "war_declared")
    assert declared.args["hall"] is True and declared.args["text"] == "国境の争い"
    assert "宣戦を布告しました" in format_log(game.defs, "war_declared", declared.args)
    assert "宣戦" in format_log(game.defs, "hall_war_declared", declared.args)
    [on] = logs(world, 2, "war_declared_on")
    assert "hall" not in on.args


def test_cannot_declare_twice_or_on_self(game, world):
    step(game, world, {1: [("declare_war", {"target": 2})]})
    step(game, world, {2: [("declare_war", {"target": 1})]})
    [failed] = logs(world, 2, "diplomacy_request_failed")
    assert failed.args["reason"] == "reason_war_exists"
    assert len(war.wars(world)) == 1
    step(game, world, {1: [("declare_war", {"target": 1})]})
    assert len(war.wars(world)) == 1
    step(game, world, {1: [("declare_war", {"target": 99})]})
    assert len(war.wars(world)) == 1


def test_treaty_needs_both_signatures(game, world):
    t = propose(game, world, 1, 2, [{"type": "non_aggression"}])
    assert t["state"] == treaties.PROPOSED and t["parties"] == [1, 2]
    assert t["title"] == "不可侵条約" and t["signed"] == {"1": world.turn}
    assert logs(world, 2, "treaty_offer")
    sign(game, world, 1, 1)  # 自分の提案には署名できない
    assert treaties.find(world, 1)["state"] == treaties.PROPOSED
    sign(game, world, 1, 3)  # 当事国でない
    assert treaties.find(world, 1)["state"] == treaties.PROPOSED
    sign(game, world, 1, 2)
    t = treaties.find(world, 1)
    assert t["state"] == treaties.IN_FORCE and t["start"] == world.turn
    assert treaties.in_force(world, 1, 2, cl.NON_AGGRESSION) is t
    [in_force] = logs(world, 1, "treaty_in_force")
    assert (
        in_force.args["hall"] is True
        and logs(world, 2, "treaty_in_force")[0].args.get("hall") is None
    )
    assert "不可侵" in format_log(game.defs, "treaty_in_force", in_force.args)


def test_same_treaty_cannot_be_proposed_twice(game, world):
    propose(game, world, 1, 2, [{"type": "alliance"}])
    step(game, world, {2: [("propose_treaty", {"partner": 1, "clauses": [{"type": "alliance"}]})]})
    assert len(treaties.treaties(world)) == 1
    assert logs(world, 2, "diplomacy_request_failed")[-1].args["reason"] == "reason_treaty_exists"


def test_proposal_can_be_refused_withdrawn_or_lapse(game, world):
    propose(game, world, 1, 2, [{"type": "alliance"}])
    step(game, world, {2: [("refuse_treaty", {"treaty": 1})]})
    assert treaties.find(world, 1)["state"] == treaties.REFUSED
    propose(game, world, 1, 2, [{"type": "alliance"}])
    step(game, world, {1: [("withdraw_treaty", {"treaty": 2})]})
    assert treaties.find(world, 2)["state"] == treaties.WITHDRAWN
    t = propose(game, world, 1, 3, [{"type": "non_aggression"}])
    for _ in range(game.config.get("diplomacy.proposal_turns")):
        step(game, world)
    assert treaties.find(world, t["id"])["state"] == treaties.LAPSED
    assert logs(world, 1, "treaty_lapsed")


def refused_why(game, world, island_id, kind, payload):
    """命令を 1 ターンで試して、断られたわけ（命令の段か外交の段で断られたもの）を返す。"""
    step(game, world, {island_id: [(kind, payload)]})
    log = world.islands[island_id].log
    for e in reversed(log):
        if e.kind in ("order_failed", "diplomacy_request_failed"):
            return e.args["reason"]
    return None


def test_clause_rules(game, world):
    def propose_why(clauses, partner=2):
        return refused_why(
            game, world, 1, "propose_treaty", {"partner": partner, "clauses": clauses}
        )

    # 平時に停戦や講和は結べない
    assert propose_why([{"type": "ceasefire"}]) == "reason_clause_no_war"
    assert propose_why([{"type": "peace"}]) == "reason_clause_no_war"
    # 重なった条項・停戦と講和の同時・予告の範囲外・相手がいない
    assert propose_why([{"type": "alliance"}, {"type": "alliance"}]) == "reason_clause_duplicate"
    assert propose_why([{"type": "non_aggression", "notice_turns": 99}]) == "reason_invalid_payload"
    assert propose_why([{"type": "alliance"}], partner=99) == "reason_treaty_partner"
    assert propose_why([{"type": "alliance"}], partner=1) == "reason_treaty_partner"
    assert propose_why([{"type": "friendship"}]) == "reason_invalid_payload"
    assert not treaties.treaties(world)
    # 戦争のあいだは、停戦と講和しか結べない
    step(game, world, {1: [("declare_war", {"target": 2})]})
    assert propose_why([{"type": "alliance"}]) == "reason_clause_wartime"
    assert (
        propose_why([{"type": "ceasefire"}, {"type": "peace"}]) == "reason_clause_ceasefire_peace"
    )
    bad = {"payer": 3, "amount": 10, "due_turns": 5}
    assert propose_why([{"type": "peace", "reparations": bad}]) == "reason_clause_reparations"
    ok = {"payer": 1, "amount": 10, "due_turns": 5}
    assert propose_why([{"type": "peace", "reparations": ok}]) is None
    assert treaties.treaties(world)[-1]["clauses"][0]["reparations"]["payee"] == 2


def test_ceasefire_stops_fighting_and_notice_resumes_war(game, world):
    step(game, world, {1: [("declare_war", {"target": 2})]})
    step(game, world)
    assert war.fighting(world, 1, 2, world.turn)
    propose(game, world, 2, 1, [{"type": "ceasefire", "notice_turns": 2}])
    sign(game, world, treaties.treaties(world)[-1]["id"], 1)
    w = war.open_war(world, 1, 2)
    assert w["state"] == war.CEASEFIRE and w["ceasefire"] is not None
    assert not war.fighting(world, 1, 2, world.turn)
    assert war.status(world, 1, 2, world.turn) == war.CEASEFIRE
    assert war.enemies(world, 1, world.turn) == []
    [started] = logs(world, 1, "ceasefire_started")
    assert started.args.get("hall") is None  # 提案した島 2 の記録が国際会議場の元
    assert logs(world, 2, "ceasefire_started")[0].args["hall"] is True
    # 予告つきの破棄: 予告のターンがたつと戦争に戻る
    tid = w["ceasefire"]
    step(game, world, {1: [("denounce_treaty", {"treaty": tid})]})
    assert treaties.find(world, tid)["notice"]["until"] == world.turn + 2
    assert war.open_war(world, 1, 2)["state"] == war.CEASEFIRE
    step(game, world)
    assert war.open_war(world, 1, 2)["state"] == war.CEASEFIRE
    step(game, world)
    assert war.open_war(world, 1, 2)["state"] == war.WAR
    assert treaties.find(world, tid)["state"] == treaties.ENDED
    assert logs(world, 1, "war_resumed") and logs(world, 2, "war_resumed")
    assert war.fighting(world, 1, 2, world.turn)
    # もう一度停戦できる
    propose(game, world, 1, 2, [{"type": "ceasefire"}])
    assert treaties.treaties(world)[-1]["state"] == treaties.PROPOSED


def test_peace_ends_the_war_and_sets_withdrawal(game, world):
    step(game, world, {1: [("declare_war", {"target": 2})]})
    clause = {"type": "peace", "withdraw_turns": 4, "prisoners": True}
    t = propose(game, world, 1, 2, [clause])
    sign(game, world, t["id"], 2)
    w = war.wars(world)[0]
    assert w["state"] == war.ENDED and w["ended_by"] == t["id"]
    assert w["withdraw_until"] == w["ended"] + 4 and w["prisoners"] is True
    assert war.status(world, 1, 2, world.turn) == war.PEACE
    assert war.must_withdraw(world, 1, 2, world.turn)
    assert not war.must_withdraw(world, 1, 2, w["ended"] + 5)
    assert logs(world, 1, "war_ended")[0].args["hall"] is True
    # 賠償のない講和は、効いた次のターンの見張りで済んで終わる
    assert treaties.find(world, t["id"])["state"] == treaties.ENDED
    assert treaties.find(world, t["id"])["end_reason"] == "fulfilled"
    # 戦争が終わったので、もう一度宣戦できる
    step(game, world, {2: [("declare_war", {"target": 1})]})
    assert war.open_war(world, 1, 2)["attacker"] == 2


def peace_with_reparations(game, world, due=3):
    step(game, world, {1: [("declare_war", {"target": 2})]})
    clause = {"type": "peace", "reparations": {"payer": 2, "amount": 1000, "due_turns": due}}
    t = propose(game, world, 1, 2, [clause])
    sign(game, world, t["id"], 2)
    return treaties.find(world, t["id"])


def test_reparations_paid_in_time_is_not_a_violation(game, world):
    t = peace_with_reparations(game, world)
    rep = t["clauses"][0]["reparations"]
    assert t["state"] == treaties.IN_FORCE and rep["state"] == "open"
    assert rep["due"] == t["start"] + 3
    assert t["notice"] is None
    step(game, world, {2: [("denounce_treaty", {"treaty": t["id"]})]})
    assert (
        logs(world, 2, "diplomacy_request_failed")[-1].args["reason"]
        == "reason_treaty_not_terminable"
    )
    treaties.note_payment(world, 2, 1, 2, 600, world.turn)
    treaties.note_payment(world, 2, 1, 1, 9999, world.turn)  # 別の通貨は数えない
    treaties.note_payment(world, 1, 2, 2, 9999, world.turn)  # 払う向きが逆
    assert rep["paid"] == 600
    treaties.note_payment(world, 2, 1, 2, 400, world.turn)
    step(game, world)
    assert rep["state"] == "paid" and t["state"] == treaties.ENDED
    assert not t["violations"]
    assert credit.premium(world.islands[2], world.turn, game.config) == 0


def test_unpaid_reparations_are_a_violation_that_dents_credit(game, world):
    t = peace_with_reparations(game, world)
    rep = t["clauses"][0]["reparations"]
    for _ in range(3):
        step(game, world)
    assert rep["state"] == "open"  # 期日のターンまでは待つ
    step(game, world)
    assert rep["state"] == "defaulted"
    [v] = t["violations"]
    assert v["by"] == 2 and v["reason"] == "reparations" and v["clause"] == cl.PEACE
    assert t["state"] == treaties.ENDED  # 違反として記録して、条約は済んだことにする
    [violated] = hall_entries(world, "treaty_violated")
    assert "賠償" in format_log(game.defs, "treaty_violated", violated.args)
    assert "賠償" in format_log(game.defs, "hall_treaty_violated", violated.args)
    # 破った国の国債の利回りに上乗せが付き、だんだん消える。相手の国には付かない
    ctx = StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.registry,
        formulas=game.engine.formulas,
        turn=world.turn,
        seed=world.seed,
        stage="bonds",
        before=None,
    )
    cfg = game.config.get("diplomacy")
    assert default_premium(world.islands[2], ctx) == pytest.approx(cfg["violation_premium"])
    assert default_premium(world.islands[1], ctx) == 0
    later = credit.premium(
        world.islands[2], world.turn + cfg["violation_decay_turns"] // 2, game.config
    )
    assert 0 < later < cfg["violation_premium"]
    assert (
        credit.premium(world.islands[2], world.turn + cfg["violation_decay_turns"], game.config)
        == 0
    )


def test_declaring_war_on_a_treaty_partner_is_a_violation(game, world):
    for kind in ("non_aggression", "alliance"):
        w = generate_world(7, 3, game.config, game.defs)
        t = propose(game, w, 1, 2, [{"type": kind}])
        sign(game, w, t["id"], 2)
        step(game, w, {1: [("declare_war", {"target": 2})]})
        assert war.open_war(w, 1, 2) is not None  # 宣戦は止めない
        [v] = treaties.find(w, t["id"])["violations"]
        assert v["by"] == 1 and v["clause"] == kind and v["reason"] == "war_declared"
        assert credit.premium(w.islands[1], w.turn, game.config) > 0
        assert credit.premium(w.islands[2], w.turn, game.config) == 0


def test_denounced_non_aggression_ends_without_penalty(game, world):
    t = propose(game, world, 1, 2, [{"type": "non_aggression", "notice_turns": 2}])
    sign(game, world, t["id"], 2)
    step(game, world, {2: [("denounce_treaty", {"treaty": t["id"]})]})
    assert t["notice"]["by"] == 2
    step(game, world, {1: [("denounce_treaty", {"treaty": t["id"]})]})
    assert (
        logs(world, 1, "diplomacy_request_failed")[-1].args["reason"] == "reason_treaty_denounced"
    )
    # 予告の間に宣戦すれば違反、予告が済んだ後の宣戦は違反でない
    step(game, world)
    assert t["state"] == treaties.ENDED and t["end_reason"] == "notice"
    assert len(hall_entries(world, "treaty_ended")) == 1
    step(game, world, {1: [("declare_war", {"target": 2})]})
    assert not t["violations"]
    assert credit.premium(world.islands[1], world.turn, game.config) == 0


def test_democracy_votes_on_war_and_treaties(game):
    world = generate_world(7, 3, game.config, game.defs)
    assert world.islands[1].regime["politics"] == "democracy"

    def seats(island_id, share):
        state = politics.state_of(world.islands[island_id])
        state.update({"seats": share, "elected": 50.0, "support": 50.0})

    seats(1, 0.4)
    step(game, world, {1: [("declare_war", {"target": 2})]})
    assert war.open_war(world, 1, 2) is None
    assert logs(world, 1, "order_failed")[-1].args["reason"] == "reason_parliament_rejected"
    seats(1, 0.8)
    t = propose(game, world, 1, 2, [{"type": "alliance"}])  # 提案は採決しない
    assert t["state"] == treaties.PROPOSED
    seats(2, 0.4)
    step(game, world, {2: [("sign_treaty", {"treaty": t["id"]})]})
    assert t["state"] == treaties.PROPOSED  # 否決
    seats(2, 0.8)
    step(game, world, {2: [("sign_treaty", {"treaty": t["id"]})]})
    assert t["state"] == treaties.IN_FORCE
    seats(1, 0.4)
    step(game, world, {1: [("denounce_treaty", {"treaty": t["id"]})]})
    assert t["notice"] is None  # 破棄の予告も採決する


def test_orders_are_public_and_democracies_vote(game):
    orders = game.defs.orders
    for kind in ("declare_war", "sign_treaty", "denounce_treaty"):
        assert orders[kind]["parliament"] == "majority", kind
    for kind in (
        "declare_war",
        "propose_treaty",
        "sign_treaty",
        "refuse_treaty",
        "denounce_treaty",
    ):
        assert not orders[kind].get("secret"), kind  # 宣戦・停戦・講和は伏せない（MI-27）
    assert orders["declare_war"]["admin_cost"] > orders["propose_treaty"]["admin_cost"]


def test_state_survives_a_json_round_trip(game, world):
    from hakoniwa_core.state.serialize import world_from_dict, world_to_dict

    step(game, world, {1: [("declare_war", {"target": 2})]})
    t = propose(game, world, 2, 1, [{"type": "ceasefire"}])
    again = world_from_dict(world_to_dict(world))
    assert world_state(again) == world_state(world)
    assert treaties.find(again, t["id"])["clauses"] == t["clauses"]
    sign(game, again, t["id"], 1)
    assert war.open_war(again, 1, 2)["state"] == war.CEASEFIRE
