"""フェーズ4-1 の②: 経済の取り決めを条約の条項にする（docs/design/10-politics.md PL-45〜PL-50）。

- 貿易圏・契約・固定相場・融通・援助・移民の条項は、署名で効くと約束どおりの状態になり、毎ターン
  見張られる
- 約束を破ると違反として記録され（破った国が分かる）、予告つきの破棄のあとは罰がない
- 契約と援助は済む条項で、期間か回数を終えると条約が終わる
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger, stock
from hakoniwa_core.diplomacy import clauses as cl
from hakoniwa_core.diplomacy import treaties, war
from hakoniwa_core.game import load_game
from hakoniwa_core.log import format_log
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import blocs, contracts, swap
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.units import BASIS, QUANTITY_SCALE
from tests.core.test_diplomacy import logs, propose, sign, step
from tests.core.test_diplomacy import refused_why as order_refused_why
from tests.core.test_trade import run

GOOD = "building_materials"


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """標準の計画で 12 ターン回した 3 島（お金の経済が始まっている）。議会のない体制にする。"""
    world = generate_world(7, 3, game.config, game.defs)
    run(game, world, 12)
    for island in world.islands.values():
        island.regime["politics"] = "authoritarian"
    return world


@pytest.fixture
def world(base):
    return copy.deepcopy(base)


def refused_why(game, world, a, b, clauses):
    return order_refused_why(game, world, a, "propose_treaty", {"partner": b, "clauses": clauses})


def violations(treaty):
    return [(v["by"], v["clause"], v["reason"]) for v in treaty["violations"]]


def contract_clause(**extra):
    return {
        "type": "contract",
        "seller": 1,
        "good": GOOD,
        "quantity": 2,
        "price": 10,
        "currency": 2,
        "turns": 4,
        **extra,
    }


def stock_up(game, world, island_id, amount=100):
    stock.add(
        world.islands[island_id].warehouse,
        GOOD,
        amount * QUANTITY_SCALE,
        world.turn,
        game.defs.goods,
    )


def test_economic_clauses_need_a_started_economy(game):
    world = generate_world(7, 3, game.config, game.defs)
    for island in world.islands.values():
        island.regime["politics"] = "authoritarian"
    world.islands[1].economy.started = None  # 建国したての、お金の経済が始まっていない島
    assert not world.islands[1].has_economy
    step(game, world, {1: [("propose_treaty", {"partner": 2, "clauses": [{"type": "swap"}]})]})
    assert not treaties.treaties(world)
    assert logs(world, 1, "diplomacy_request_failed")[-1].args["reason"] == "reason_clause_economy"


def test_clause_forms_are_checked(game, world):
    bad = {
        "reason_clause_bloc": {"type": "bloc"},  # 圏の名前も番号もない
        "reason_clause_contract": contract_clause(seller=3),
        "reason_contract_currency": contract_clause(currency=3),
        "reason_clause_peg": {"type": "peg", "pegger": 3, "rate": 1.5},
        "reason_clause_aid": {"type": "aid", "payer": 3, "amount": 100},
        "reason_contract_currency ": {"type": "aid", "payer": 1, "amount": 100, "currency": 3},
        "reason_clause_refugees": {"type": "refugees", "host": 3},
        "reason_clause_duplicate": None,
    }
    for reason, clause in bad.items():
        clauses = [{"type": "swap"}, {"type": "swap"}] if clause is None else [clause]
        assert refused_why(game, world, 1, 2, clauses) == reason.strip(), reason
    # 契約と援助は何本でも書ける
    t = propose(game, world, 1, 2, [contract_clause(), contract_clause(seller=2, currency=1)])
    assert [c["type"] for c in t["clauses"]] == ["contract", "contract"]
    assert t["title"] == "貿易の契約・貿易の契約"


def test_clauses_wait_for_peace_in_wartime(game, world):
    step(game, world, {1: [("declare_war", {"target": 2})]})
    step(game, world)
    assert war.open_war(world, 1, 2) is not None
    assert refused_why(game, world, 1, 2, [{"type": "swap"}]) == "reason_clause_wartime"
    t = propose(game, world, 1, 2, [{"type": "peace"}, {"type": "swap"}])  # 講和に添えるなら結べる
    assert [c["type"] for c in t["clauses"]] == ["peace", "swap"]


def test_bloc_clause_creates_a_bloc_and_leaving_is_a_violation(game, world):
    t = propose(game, world, 1, 2, [{"type": "bloc", "name": "海の同盟圏"}])
    sign(game, world, t["id"], 2)
    t = treaties.find(world, t["id"])
    assert t["state"] == treaties.IN_FORCE
    bloc = blocs.find(world, t["clauses"][0]["bloc_id"])
    assert bloc["name"] == "海の同盟圏" and bloc["rep"] == 1 and bloc["members"] == [1, 2]
    assert violations(t) == []
    # 予告なしに抜けると、抜けた国の違反として 1 回だけ記録する
    step(game, world, {2: [("leave_bloc", {"bloc": bloc["id"]})]})
    step(game, world)
    t = treaties.find(world, t["id"])
    assert violations(t) == [(2, "bloc", "bloc_left")]
    assert world.islands[2].ext["diplomacy"]["violations"]
    [event] = [e for e in logs(world, 1, "treaty_violated") if e.args.get("hall")]
    assert "貿易圏" in format_log(game.defs, "hall_treaty_violated", event.args)
    assert "予告なしに貿易圏を抜けた" in format_log(game.defs, "hall_treaty_violated", event.args)


def test_bloc_clause_adds_the_partner_to_a_bloc_the_proposer_leads(game, world):
    step(game, world, {1: [("create_bloc", {"name": "北の圏", "open": False})]})
    [bloc] = [b for b in blocs.blocs(world) if b["name"] == "北の圏"]
    assert bloc["members"] == [1]
    t = propose(game, world, 1, 2, [{"type": "bloc", "bloc": bloc["id"]}])
    sign(game, world, t["id"], 2)
    assert 2 in blocs.find(world, bloc["id"])["members"]  # 承認の要る圏にも、条約で入る
    # 代表でない国は、その圏の番号を書けない
    assert refused_why(game, world, 2, 3, [{"type": "bloc", "bloc": bloc["id"]}]) == (
        "reason_clause_bloc"
    )


def denounced_bloc(game, world, notice_turns=4):
    """1 が貿易圏の条約を破棄の予告にした直後の (条約の番号, 圏の番号)。"""
    t = propose(
        game, world, 1, 2, [{"type": "bloc", "name": "短い圏", "notice_turns": notice_turns}]
    )
    sign(game, world, t["id"], 2)
    bloc_id = treaties.find(world, t["id"])["clauses"][0]["bloc_id"]
    step(game, world, {1: [("denounce_treaty", {"treaty": t["id"]})]})
    return t["id"], bloc_id


def test_leaving_after_notice_period_is_not_a_violation(game, world):
    tid, bloc_id = denounced_bloc(game, world)
    until = treaties.find(world, tid)["notice"]["until"]
    while world.turn < until - 1:
        step(game, world)
    step(game, world, {2: [("leave_bloc", {"bloc": bloc_id})]})
    step(game, world)
    t = treaties.find(world, tid)
    assert t["state"] == treaties.ENDED and t["end_reason"] == "notice"
    assert violations(t) == []


def test_leaving_during_notice_is_a_violation(game, world):
    tid, bloc_id = denounced_bloc(game, world)
    assert world.turn < treaties.find(world, tid)["notice"]["until"] - 1
    step(game, world, {2: [("leave_bloc", {"bloc": bloc_id})]})
    step(game, world)
    t = treaties.find(world, tid)
    assert t["state"] == treaties.IN_FORCE  # 予告のターンがたつまでは条約が効いている
    assert violations(t) == [(2, "bloc", "bloc_left")]
    while t["state"] == treaties.IN_FORCE:
        step(game, world)
        t = treaties.find(world, tid)
    assert t["end_reason"] == "notice"
    assert violations(t) == [(2, "bloc", "bloc_left")]  # 終わるときに重ねて数えない


def test_closing_the_doors_during_notice_is_a_violation(game, world):
    world.islands[1].policy.immigration = ""
    world.islands[2].policy.immigration = ""
    t = propose(game, world, 1, 2, [{"type": "free_movement"}])
    sign(game, world, t["id"], 2)
    step(game, world, {1: [("denounce_treaty", {"treaty": t["id"]})]})
    assert treaties.find(world, t["id"])["notice"]["until"] > world.turn + 1
    step(game, world, {2: [("set_immigration", {"mode": "closed"})]})
    step(game, world)
    assert violations(treaties.find(world, t["id"])) == [(2, "free_movement", "migration_closed")]


def test_contract_clause_starts_a_contract_and_the_treaty_ends_when_it_does(game, world):
    stock_up(game, world, 1)
    t = propose(game, world, 1, 2, [contract_clause()])
    sign(game, world, t["id"], 2)
    t = treaties.find(world, t["id"])
    c = contracts.find(world, t["clauses"][0]["contract"])
    assert c["state"] == contracts.ACTIVE and c["treaty"] == t["id"]
    assert (c["seller"], c["buyer"], c["good"], c["price"], c["currency"]) == (1, 2, GOOD, 10, 2)
    assert c["start"] == world.turn + 1 and c["end"] == c["start"] + 4
    assert logs(world, 1, "contract_started") and logs(world, 2, "contract_started")
    # 契約を結んでいる間は、予告つきの破棄で抜けられない
    step(game, world, {1: [("denounce_treaty", {"treaty": t["id"]})]})
    assert logs(world, 1, "diplomacy_request_failed")[-1].args["reason"] == (
        "reason_treaty_term_open"
    )
    for _ in range(5):
        step(game, world)
    t = treaties.find(world, t["id"])
    c = contracts.find(world, c["id"])
    assert c["state"] == contracts.ENDED and c["delivered"] == 4 * 2 * QUANTITY_SCALE
    assert t["state"] == treaties.ENDED and t["end_reason"] == "fulfilled"
    assert violations(t) == []


def test_cancelling_a_treaty_contract_is_a_violation_by_the_canceller(game, world):
    stock_up(game, world, 1)
    t = propose(game, world, 1, 2, [contract_clause(turns=8)])
    sign(game, world, t["id"], 2)
    c = contracts.find(world, treaties.find(world, t["id"])["clauses"][0]["contract"])
    step(game, world, {2: [("cancel_contract", {"contract": c["id"]})]})
    step(game, world)
    step(game, world)
    assert violations(treaties.find(world, t["id"])) == [(2, "contract", "contract_cancelled")]


def test_a_short_delivery_is_a_violation_by_the_one_who_failed_and_not_repeated(game, world):
    """売る国が品物を届けられない・買う国が代金を払えないターンは、足りなかった側の違反として
    1 回だけ記録する（輸送や港のせいは違反にしない）。"""
    t = propose(game, world, 1, 2, [contract_clause(turns=40)])
    sign(game, world, t["id"], 2)
    c = contracts.find(world, treaties.find(world, t["id"])["clauses"][0]["contract"])

    def fall_short(why):
        # このターンの履行で足りなかったことにする（外交の段は次のターンに読む）
        c.update(short=c["qty"], short_why=why, short_turn=world.turn)
        step(game, world)

    fall_short("ships")  # 船が足りない: 約束した国のせいではない
    assert violations(treaties.find(world, t["id"])) == []
    fall_short("goods")
    fall_short("goods")  # 続けても、同じ条項で 12 ターンは重ねない
    assert violations(treaties.find(world, t["id"])) == [(1, "contract", "contract_short_goods")]
    repeat = game.config.get("diplomacy.violation_repeat_turns")
    for _ in range(repeat):
        step(game, world)
    fall_short("money")
    assert violations(treaties.find(world, t["id"])) == [
        (1, "contract", "contract_short_goods"),
        (2, "contract", "contract_short_money"),
    ]


def test_peg_clause_pegs_the_currency_and_dropping_it_is_a_violation(game, world):
    pegger = world.islands[1]
    pegger.policy.cb_independent = False
    t = propose(game, world, 1, 2, [{"type": "peg", "pegger": 1, "rate": 1.25}])
    sign(game, world, t["id"], 2)
    assert pegger.policy.fx_peg == {"2": round(1.25 * BASIS)}
    assert logs(world, 1, "fx_peg_set")
    step(game, world)
    assert violations(treaties.find(world, t["id"])) == []
    pegger.policy.fx_peg.clear()  # 外れた（準備が尽きた）か、やめた
    step(game, world)
    step(game, world)
    assert violations(treaties.find(world, t["id"])) == [(1, "peg", "peg_dropped")]


def test_independent_central_bank_cannot_promise_a_peg(game, world):
    world.islands[1].policy.cb_independent = True
    assert (
        refused_why(game, world, 1, 2, [{"type": "peg", "pegger": 1, "rate": 1.2}])
        == "reason_peg_independent"
    )


def test_swap_clause_makes_the_partner_a_lender(game, world):
    borrower, lender, issuer = world.islands[1], world.islands[2], world.islands[3]
    value = fxrates.to_own(lender, issuer.id, 10**8)
    fxrates.ensure(world, lender, issuer.id)
    fxmoney.transfer(
        world,
        issuer.id,
        (issuer.id, ledger.CB),
        (lender.id, ledger.CB),
        10**8,
        "fx_exchange",
        value,
    )
    lenders = lambda: dict(swap.lenders(world, game.config, borrower.id, issuer.id))  # noqa: E731
    before = lenders()
    t = propose(game, world, 1, 2, [{"type": "swap"}])
    sign(game, world, t["id"], 2)
    assert treaties.partners_with(world, 1, "swap") == {2}
    assert lender.id in lenders() and lender.id not in before
    assert violations(treaties.find(world, t["id"])) == []


def test_aid_clause_counts_payments_and_missing_one_is_a_violation(game, world):
    t = propose(
        game,
        world,
        1,
        2,
        [{"type": "aid", "payer": 1, "amount": 1000, "currency": 1, "period": 2, "count": 2}],
    )
    sign(game, world, t["id"], 2)
    assert logs(world, 1, "treaty_aid_owed") and logs(world, 2, "treaty_aid_due")
    step(game, world, {1: [("send_aid", {"partner": 2, "amount": 1000})]})
    clause = treaties.find(world, t["id"])["clauses"][0]
    assert clause["paid"] == 1000
    for _ in range(5):
        step(game, world)
    t = treaties.find(world, t["id"])
    # 1 回目は届き、2 回目（累計 2000）は届かなかった
    assert violations(t) == [(1, "aid", "aid_missed")]
    assert t["state"] == treaties.ENDED and t["end_reason"] == "fulfilled"


def test_one_aid_payment_counts_for_reparations_first(game, world):
    step(game, world, {1: [("declare_war", {"target": 2})]})
    step(game, world)
    peace = {
        "type": "peace",
        "reparations": {"payer": 2, "amount": 600, "currency": 2, "due_turns": 10},
    }
    aid = {"type": "aid", "payer": 2, "amount": 500, "currency": 2, "period": 3, "count": 1}
    t = propose(game, world, 1, 2, [peace, aid])
    sign(game, world, t["id"], 2)
    step(game, world, {2: [("send_aid", {"partner": 1, "amount": 1000})]})
    peace_clause, aid_clause = treaties.find(world, t["id"])["clauses"]
    assert peace_clause["reparations"]["paid"] == 600  # 賠償の残りに先に数える
    assert aid_clause["paid"] == 400  # 余りが援助の約束に


def test_migration_clauses_open_the_doors_and_closing_them_is_a_violation(game, world):
    world.islands[1].policy.immigration = ""
    world.islands[2].policy.immigration = "jobs"
    t = propose(game, world, 1, 2, [{"type": "free_movement"}])
    sign(game, world, t["id"], 2)
    assert world.islands[1].policy.immigration == "open"
    assert world.islands[2].policy.immigration == "open"
    assert logs(world, 2, "treaty_immigration_set")
    step(game, world, {2: [("set_immigration", {"mode": "closed"})]})
    step(game, world)
    assert violations(treaties.find(world, t["id"])) == [(2, "free_movement", "migration_closed")]


def test_refugee_clause_only_asks_the_host(game, world):
    world.islands[1].policy.immigration = ""
    world.islands[2].policy.immigration = ""
    t = propose(game, world, 1, 2, [{"type": "refugees", "host": 2}])
    sign(game, world, t["id"], 2)
    assert world.islands[2].policy.immigration == "refugees"
    assert world.islands[1].policy.immigration == ""
    step(game, world)
    assert violations(treaties.find(world, t["id"])) == []
    world.islands[2].policy.immigration = "engineers"
    step(game, world)
    assert violations(treaties.find(world, t["id"])) == [(2, "refugees", "migration_closed")]


def test_clause_kinds_have_messages(game):
    for kind in cl.ECONOMIC:
        assert game.defs.name("messages", f"clause_{kind}", "ja")


def test_a_missed_aid_instalment_is_forgiven_for_the_next_ones(game, world):
    t = propose(
        game,
        world,
        1,
        2,
        [{"type": "aid", "payer": 1, "amount": 1000, "currency": 1, "period": 2, "count": 3}],
    )
    sign(game, world, t["id"], 2)
    pay = {1: [("send_aid", {"partner": 2, "amount": 1000})]}
    for _ in range(3):
        step(game, world)  # 1 回目の期日（効いて 2 ターン後）の次のターンに、届いていないのを見る
    assert len(treaties.find(world, t["id"])["violations"]) == 1
    step(game, world, pay)  # 2 回目の分
    step(game, world)
    step(game, world, pay)  # 3 回目の分
    step(game, world)
    t = treaties.find(world, t["id"])
    assert violations(t) == [(1, "aid", "aid_missed")]  # 遅れを数え直さない
    assert t["state"] == treaties.ENDED


def test_one_payment_fills_aid_clauses_in_order(game, world):
    aid = {"type": "aid", "payer": 1, "amount": 500, "currency": 1, "period": 5, "count": 1}
    t = propose(game, world, 1, 2, [aid, aid])
    sign(game, world, t["id"], 2)
    step(game, world, {1: [("send_aid", {"partner": 2, "amount": 700})]})
    first, second = treaties.find(world, t["id"])["clauses"]
    assert (first["paid"], second["paid"]) == (500, 200)


def test_tiny_contract_quantities_are_refused(game, world):
    assert refused_why(game, world, 1, 2, [contract_clause(quantity=0.0001)]) == (
        "reason_clause_contract"
    )


def test_contract_shortage_on_the_last_turn_is_still_seen(game, world):
    t = propose(game, world, 1, 2, [contract_clause(turns=2)])
    sign(game, world, t["id"], 2)
    c = contracts.find(world, treaties.find(world, t["id"])["clauses"][0]["contract"])
    step(game, world)
    step(game, world)  # 契約の最後のターン
    c.update(short=c["qty"], short_why="goods", short_turn=world.turn)
    c["state"] = contracts.ENDED  # 履行のあと、その段の終わりに終わる
    step(game, world)
    assert violations(treaties.find(world, t["id"])) == [(1, "contract", "contract_short_goods")]


def test_cancelling_inside_the_wait_does_not_end_it_earlier_so_it_is_not_a_violation(game, world):
    stock_up(game, world, 1)
    t = propose(game, world, 1, 2, [contract_clause(turns=3)])
    sign(game, world, t["id"], 2)
    c = contracts.find(world, treaties.find(world, t["id"])["clauses"][0]["contract"])
    step(game, world, {2: [("cancel_contract", {"contract": c["id"]})]})
    assert "cancelled_by" not in c  # 残りは解約の待ち（3 ターン）より短い
    step(game, world)
    assert violations(treaties.find(world, t["id"])) == []


def test_both_cancelling_blames_the_first_canceller(game, world):
    stock_up(game, world, 1)
    t = propose(game, world, 1, 2, [contract_clause(turns=30)])
    sign(game, world, t["id"], 2)
    c = contracts.find(world, treaties.find(world, t["id"])["clauses"][0]["contract"])
    step(
        game,
        world,
        {
            1: [("cancel_contract", {"contract": c["id"]})],
            2: [("cancel_contract", {"contract": c["id"]})],
        },
    )
    step(game, world)
    assert violations(treaties.find(world, t["id"])) == [(1, "contract", "contract_cancelled")]


def test_watching_survives_a_deleted_island_and_skips_inactive_payers(game, world):
    pegger = world.islands[1]
    pegger.policy.cb_independent = False
    t = propose(game, world, 1, 2, [{"type": "peg", "pegger": 1, "rate": 1.25}])
    sign(game, world, t["id"], 2)
    aid = {"type": "aid", "payer": 3, "amount": 100, "currency": 3, "period": 1, "count": 1}
    t2 = propose(game, world, 2, 3, [aid])
    sign(game, world, t2["id"], 3)
    world.islands[3].ext["inactive"] = True  # 払う国が休止中: 動けない国の違反にはしない
    step(game, world)
    step(game, world)
    assert violations(treaties.find(world, t2["id"])) == []
    del world.islands[1]  # 島が世界から消えても、外交の見張りは止まらない
    treaties.upkeep(world, game.config, world.turn + 1)
    assert treaties.find(world, t["id"])["state"] == treaties.IN_FORCE


def test_violation_reasons_have_messages(game):
    for reason in (
        "war_declared",
        "reparations",
        "contract_cancelled",
        "contract_short_goods",
        "contract_short_money",
        "aid_missed",
        "bloc_left",
        "peg_dropped",
        "migration_closed",
    ):
        assert game.defs.name("messages", f"why_{reason}", "ja"), reason
