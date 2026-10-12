from __future__ import annotations

from hakoniwa_core.game import load_game
from hakoniwa_core.orders import check_order
from hakoniwa_core.state import ORDER_DONE, ORDER_FAILED, ORDER_PENDING, OrderState
from hakoniwa_core.state.generate import generate_world


def setup(orders):
    game = load_game()
    world = generate_world(1, 1, game.config)
    island = world.islands[1]
    island.orders = [
        OrderState(id=i + 1, seq=i + 1, kind=k, payload=p) for i, (k, p) in enumerate(orders)
    ]
    return game, world, island


def test_check_order_reports_shape_errors():
    game, _, island = setup([])
    ok = check_order(game.defs, game.registry, island, "rename_island", {"name": "新島"})
    assert ok.ok
    bad = check_order(game.defs, game.registry, island, "rename_island", {"nam": "x"})
    assert not bad.ok and bad.reason == "reason_invalid_payload" and bad.details
    unknown = check_order(game.defs, game.registry, island, "launch_rocket", {})
    assert unknown.reason == "reason_unknown_order"
    same = check_order(game.defs, game.registry, island, "rename_island", {"name": island.name})
    assert same.reason == "reason_same_name"


def test_orders_run_in_sequence_and_failures_are_logged():
    game, world, island = setup(
        [
            ("rename_island", {"name": "A"}),
            ("rename_island", {"name": ""}),
            ("rename_island", {"name": "B"}),
        ]
    )
    game.engine.run_turn(world)
    assert island.name == "B"
    assert [o.status for o in island.orders] == [ORDER_DONE, ORDER_FAILED, ORDER_DONE]
    assert island.orders[1].reason == "reason_invalid_payload"
    assert [e.kind for e in island.log].count("order_failed") == 1


def _land_tiles(island, count):
    return [(t.x, t.y) for t in island.tiles if t.terrain in ("plain", "forest")][:count]


def test_orders_stop_when_admin_power_runs_out():
    power = load_game().config.get("orders.base_admin_power")
    game, world, island = setup([])
    tiles = _land_tiles(island, power + 2)
    island.orders = [
        OrderState(id=i + 1, seq=i + 1, kind="build_power_line", payload={"tiles": [list(p)]})
        for i, p in enumerate(tiles)
    ]
    game.engine.run_turn(world)
    statuses = [o.status for o in island.orders]
    assert statuses.count(ORDER_DONE) == power
    assert statuses[power:] == [ORDER_PENDING, ORDER_PENDING]
    assert island.log[-1].kind == "order_postponed"
    game.engine.run_turn(world)  # 残りは次のターン以降に実行される
    game.engine.run_turn(world)
    assert all(o.status == ORDER_DONE for o in island.orders)


def test_renames_do_not_use_admin_power():
    power = load_game().config.get("orders.base_admin_power")
    orders = [("rename_island", {"name": f"N{i}"}) for i in range(power + 3)]
    game, world, island = setup(orders)
    game.engine.run_turn(world)
    assert all(o.status == ORDER_DONE for o in island.orders)
    assert island.name == f"N{power + 2}"


def test_log_formatting():
    from hakoniwa_core.log import format_log

    defs = load_game().defs
    text = format_log(
        defs, "order_failed", {"order": "rename_island", "reason": "reason_same_name"}
    )
    assert text == "命令「国名の変更」を実行できませんでした（現在と同じ名称です）"
    spoiled = format_log(defs, "goods_spoiled", {"good": "grain", "amount": 2.5})
    assert spoiled == "穀物 2.5 トンが劣化により廃棄されました"
