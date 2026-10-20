"""フェーズ4-1: 記念碑（PO-47）。建てられる条件・建てたときの記録・国の歴史・倒れる像。

- モノリスは条件なしで建ち、碑文に足した文と建てたときの記録が施設に残る
- ほかの碑は条件を満たさないと断られ、満たすと建つ（しきい値は game.yaml の monuments）
- 国の歴史は、そのターンのログから段が書き足す（暴動・戦争・飢え・称号）
- 民主主義の国の元首の像は建てられず、体制が変わると倒される
"""

from __future__ import annotations

import pytest

from hakoniwa_core import monuments
from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.game import load_game
from hakoniwa_core.log import format_log
from hakoniwa_core.modules import monuments as stage
from hakoniwa_core.orders import check_order
from hakoniwa_core.state import LogEntry
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.turn.context import StageContext


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    world = generate_world(7, 2, game.config, game.defs)
    for island in world.islands.values():
        island.regime["politics"] = "authoritarian"
    game.engine.run_turn(world)  # 段が歴史としきい値を写す
    return world


def spot(island):
    return next(
        (t.x, t.y)
        for t in island.tiles
        if t.terrain == "plain" and island.facility_at(t.x, t.y) is None and not t.zone
    )


def build(game, island, kind, **extra):
    x, y = spot(island)
    return check_order(
        game.defs, game.registry, island, "build", {"facility": kind, "x": x, "y": y, **extra}
    )


def context(game, world, stage_name="monuments"):
    return StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.registry,
        formulas=game.engine.formulas,
        turn=world.turn + 1,
        seed=world.seed,
        stage=stage_name,
        before=world,
    )


def test_all_standard_monuments_are_defined(game):
    kinds = {k for k, f in game.defs.facilities.items() if f.get("monument")}
    assert kinds == {
        "monument_monolith",
        "monument_custom",
        "monument_founding",
        "monument_peace",
        "monument_battle",
        "monument_cenotaph",
        "monument_leader",
        "monument_worker",
        "monument_tower",
        "monument_ledger",
        "monument_verification",
    }


def test_monolith_builds_with_a_note_and_keeps_the_record(game, world):
    island = world.islands[1]
    x, y = spot(island)
    note = {"facility": "monument_monolith", "x": x, "y": y, "note": "海を見ている\n  ここで 待つ "}
    queue_orders(island, [("build", note)])
    game.engine.run_turn(world)
    f = island.facility_at(x, y)
    assert f is not None and f.kind == "monument_monolith"
    assert f.ext[monuments.MONUMENT]["note"] == "海を見ている\nここで 待つ"
    assert f.ext[monuments.MONUMENT]["turn"] == world.turn


def test_note_limits_and_only_monuments_take_a_note(game, world):
    island = world.islands[1]
    assert build(game, island, "monument_monolith", note="一行目\n二行目").ok
    long = "あ" * (monuments.NOTE_CHARS + 1)
    assert build(game, island, "monument_monolith", note=long).reason == "reason_monument_note"
    three = "a\nb\nc"
    assert build(game, island, "monument_monolith", note=three).reason == "reason_monument_note"
    assert build(game, island, "farm", note="x").reason == "reason_note_not_monument"


def test_conditions_gate_each_monument(game, world):
    island = world.islands[1]
    monuments.history(island)["titles"] = {}
    reasons = {
        "monument_founding": "reason_monument_age",
        "monument_ledger": "reason_monument_age",
        "monument_verification": "reason_monument_age",
        "monument_peace": "reason_monument_peace",
        "monument_battle": "reason_monument_battle",
        "monument_cenotaph": "reason_monument_cenotaph",
        "monument_worker": "reason_monument_worker",
        "monument_tower": "reason_monument_tower",
    }
    for kind, reason in reasons.items():
        check = build(game, island, kind)
        assert check.reason == reason, kind
        assert "{" not in format_log(game.defs, reason, check.args)
    assert build(game, island, "monument_leader").ok  # 権威主義
    world.islands[2].regime["politics"] = "democracy"
    assert build(game, world.islands[2], "monument_leader").reason == "reason_monument_leader"


def test_age_conditions_pass_when_enough_turns_have_passed(game, world):
    island = world.islands[1]
    island.ext["founded_turn"] = 1
    monuments.history(island)["turn"] = 60
    for kind in ("monument_founding", "monument_ledger", "monument_verification"):
        assert build(game, island, kind).ok, kind
    monuments.history(island)["turn"] = 11
    assert not build(game, island, "monument_founding").ok  # 建国から 10 ターン


def test_peace_needs_a_long_quiet_spell_since_the_last_unrest(game, world):
    island = world.islands[1]
    island.ext["founded_turn"] = 1
    h = monuments.history(island)
    h["turn"], h["unrest"] = 150, 100
    assert not build(game, island, "monument_peace").ok  # 50 ターン
    h["turn"] = 230
    assert build(game, island, "monument_peace").ok
    assert monuments.facts(game.defs, island, "monument_peace") == {"turns": 130}


def test_stage_records_unrest_war_hunger_and_titles(game, world):
    island = world.islands[1]
    island.log = [
        LogEntry("politics_riot", {}),
        LogEntry("war_declared", {"country": "隣国", "turn": 9, "war": 3}),
        LogEntry("population_hunger_deaths", {"count": 700, "months": 2.0}),
    ]
    world.ext.setdefault("rankings", {})["titles"] = {"agriculture": 1}
    stage.update(world, context(game, world))
    h = monuments.history(island)
    assert h["unrest"] == world.turn + 1
    assert h["war"] == {"country": "隣国", "turn": 9, "id": 3}
    assert h["dead"] == {"hunger": 700}
    assert h["titles"] == {"agriculture": world.turn + 1}
    # 称号を失えば消える（建てた碑は残る）。亡くなった人は積み上がる
    world.ext["rankings"]["titles"] = {}
    island.log = [LogEntry("population_hunger_deaths", {"count": 400, "months": 2.0})]
    stage.update(world, context(game, world))
    assert h["titles"] == {} and h["dead"] == {"hunger": 1100}


def test_stage_keeps_the_first_journal_entry_for_the_ledger_monument(game, world):
    island = world.islands[1]
    first = monuments.history(island)["first_entry"]
    assert first["turn"] == 1 and first["kind"] == "capital_injection" and first["amount"] > 0
    game.engine.run_turn(world)
    assert monuments.history(island)["first_entry"] == first  # 後のターンで上書きしない
    assert monuments.facts(game.defs, island, "monument_ledger") == first


def test_cenotaph_battle_and_worker_facts_come_from_the_history(game, world):
    island = world.islands[1]
    h = monuments.history(island)
    h["dead"] = {"hunger": 1500, "war": 300}
    h["war"] = {"country": "隣国", "turn": 9, "id": 3}
    h["titles"] = {"agriculture": 40}
    assert build(game, island, "monument_cenotaph").ok
    assert monuments.facts(game.defs, island, "monument_cenotaph") == {
        "cause": "hunger",
        "dead": 1500,
    }
    assert monuments.facts(game.defs, island, "monument_battle") == {
        "country": "隣国",
        "turn": 9,
        "dead": 300,
    }
    assert build(game, island, "monument_worker").ok
    assert monuments.facts(game.defs, island, "monument_worker") == {
        "industry": "agriculture",
        "turn": 40,
    }


def test_leader_statue_falls_when_the_country_becomes_a_democracy(game, world):
    island = world.islands[1]
    x, y = spot(island)
    queue_orders(island, [("build", {"facility": "monument_leader", "x": x, "y": y})])
    game.engine.run_turn(world)
    assert island.facility_at(x, y) is not None
    island.regime["politics"] = "democracy"
    game.engine.run_turn(world)
    assert island.facility_at(x, y) is None
    assert any(e.kind == "monument_toppled" for e in island.log)


def test_a_market_economy_state_does_not_run_a_monument_as_a_business(game, world):
    island = world.islands[1]
    island.regime["economy"] = "market"  # 国営の施設は独立採算が既定の体制
    x, y = spot(island)
    queue_orders(island, [("build", {"facility": "monument_monolith", "x": x, "y": y})])
    game.engine.run_turn(world)
    assert island.facility_at(x, y).accounting == "treasury"
    result = check_order(
        game.defs,
        game.registry,
        island,
        "set_accounting",
        {"x": x, "y": y, "accounting": "self_supporting"},
    )
    assert result.reason == "reason_monument_accounting"


def test_monuments_stay_out_of_firms_and_cost_nothing_to_run(game, world):
    island = world.islands[1]
    x, y = spot(island)
    queue_orders(island, [("build", {"facility": "monument_monolith", "x": x, "y": y})])
    for _ in range(12):
        game.engine.run_turn(world)
    f = island.facility_at(x, y)
    assert f is not None and f.built and f.workers == 0
    assert f.owner == "state" and f.accounting == "treasury"  # 売り上げがないので国庫の直轄
    assert not any(e.kind.endswith("bankrupt") for e in island.log)
