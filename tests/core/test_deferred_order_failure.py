"""後の段で断られた命令は、「実行済み」でなく「失敗」になる（試し遊び2回目、W-185）。

外交・部隊・貿易の命令は、命令の段では世界の段への申し送りを積むだけで、断るかどうかは後の段が決める。
前は断られても命令が done のままで、遊ぶ人は効いたと読んでいた。
"""

from __future__ import annotations

import pytest

from hakoniwa_core.autoplan import queue_orders
from hakoniwa_core.game import load_game
from hakoniwa_core.state import ORDER_DONE, ORDER_FAILED
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    world = generate_world(7, 3, game.config, game.defs)
    for island in world.islands.values():
        island.regime["politics"] = "authoritarian"
    return world


def step(game, world, island_id, items):
    queue_orders(world.islands[island_id], items)
    game.engine.run_turn(world)
    return world.islands[island_id].orders[-len(items) :]


def kinds(island, kind):
    return [e for e in island.log if e.kind == kind]


def test_a_treaty_order_refused_later_is_marked_failed_with_the_reason(game, world):
    [order] = step(game, world, 1, [("denounce_treaty", {"treaty": 999})])
    assert order.status == ORDER_FAILED and order.reason == "reason_treaty_unknown"
    island = world.islands[1]
    assert kinds(island, "diplomacy_request_failed")  # ターンログの文は前のまま
    # 実行しましたとは書かない
    assert not [e for e in kinds(island, "order_done") if e.args["order"] == "denounce_treaty"]


def test_a_treaty_order_that_passes_stays_done(game, world):
    [proposed] = step(
        game,
        world,
        1,
        [("propose_treaty", {"partner": 2, "clauses": [{"type": "non_aggression"}]})],
    )
    assert proposed.status == ORDER_DONE and proposed.reason is None
    [signed] = step(game, world, 2, [("sign_treaty", {"treaty": 1})])
    assert signed.status == ORDER_DONE
    [denounced] = step(game, world, 1, [("denounce_treaty", {"treaty": 1})])
    assert denounced.status == ORDER_DONE
    [again] = step(game, world, 1, [("denounce_treaty", {"treaty": 1})])
    assert again.status == ORDER_FAILED and again.reason == "reason_treaty_denounced"


def test_two_orders_of_one_turn_are_told_apart(game, world):
    done, failed = step(
        game,
        world,
        1,
        [
            ("propose_treaty", {"partner": 2, "clauses": [{"type": "non_aggression"}]}),
            ("withdraw_treaty", {"treaty": 999}),
        ],
    )
    assert done.status == ORDER_DONE and failed.status == ORDER_FAILED
    assert failed.reason == "reason_treaty_unknown"


def test_a_unit_order_refused_later_is_marked_failed(game, world):
    [order] = step(
        game, world, 1, [("load_supplies", {"fleet": 99, "good": "food", "quantity": 1})]
    )
    assert order.status == ORDER_FAILED and order.reason == "reason_no_unit"
    assert kinds(world.islands[1], "unit_order_failed")


def test_a_trade_order_refused_later_is_marked_failed(game, world):
    [order] = step(game, world, 1, [("accept_contract", {"contract": 999})])
    assert order.status == ORDER_FAILED and order.reason.startswith("reason_")
    assert kinds(world.islands[1], "trade_request_failed")


def test_orders_refused_at_once_still_fail_as_before(game, world):
    [order] = step(
        game, world, 1, [("propose_treaty", {"partner": 1, "clauses": [{"type": "peace"}]})]
    )
    assert order.status == ORDER_FAILED and order.reason == "reason_treaty_partner"
