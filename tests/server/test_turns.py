from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from server.game import get_game
from server.orders.models import Order
from server.turns.models import Backup, TurnLog
from server.turns.services import TurnError, rollback, run_turn
from server.world.models import Island, Tile, World

pytestmark = pytest.mark.django_db


def test_sample_world_has_full_islands(world):
    assert world.islands.count() == 3
    size = get_game().config.get("island.size")
    for island in world.islands.all():
        assert island.tiles.count() == size * size


def test_run_turn_advances_once_per_interval(world):
    assert run_turn().ran
    world.refresh_from_db()
    assert world.turn == 1 and world.next_turn_at > timezone.now()
    assert world.packs == {"base": "0.0.1"}
    # 次の更新時刻の前に呼んでも進まない
    assert not run_turn().ran
    assert World.objects.get().turn == 1
    assert run_turn(force=True).ran
    assert World.objects.get().turn == 2


def test_paused_world_does_not_advance(world):
    world.paused = True
    world.save()
    assert not run_turn().ran
    assert World.objects.get().turn == 0


def test_orders_are_executed_and_logged(world):
    island = Island.objects.order_by("id").first()
    Order.objects.create(island=island, seq=1, kind="rename_island", payload={"name": "新島"})
    Order.objects.create(island=island, seq=2, kind="rename_island", payload={"name": ""})
    run_turn(force=True)
    island.refresh_from_db()
    assert island.name == "新島"
    assert list(Order.objects.order_by("seq").values_list("status", flat=True)) == [
        "done",
        "failed",
    ]
    kinds = list(TurnLog.objects.filter(island=island, turn=1).values_list("kind", flat=True))
    assert "island_renamed" in kinds and "order_failed" in kinds


def test_orders_arriving_during_update_wait_for_next_turn(world):
    island = Island.objects.first()
    Order.objects.create(island=island, seq=1, kind="rename_island", payload={"name": "後から"})
    run_turn(force=True, now=timezone.now() - timedelta(seconds=5))
    assert Order.objects.get().status == "pending"
    run_turn(force=True)
    assert Order.objects.get().status == "done"


def test_failed_turn_writes_nothing(world, monkeypatch):
    island = Island.objects.first()
    Order.objects.create(island=island, seq=1, kind="rename_island", payload={"name": "X"})

    def boom(state):
        state.islands[island.id].name = "壊れた"
        raise RuntimeError("段の中で失敗")

    monkeypatch.setattr(get_game().engine, "run_turn", boom)
    with pytest.raises(RuntimeError):
        run_turn(force=True)
    world.refresh_from_db()
    assert world.turn == 0 and world.updating_since is None
    assert Island.objects.get(pk=island.pk).name != "壊れた"
    assert Order.objects.get().status == "pending"
    assert not Backup.objects.exists()


def test_removed_definition_stops_update(world):
    Tile.objects.filter(pk=Tile.objects.first().pk).update(terrain="lava")
    with pytest.raises(TurnError, match="地形 lava"):
        run_turn(force=True)
    assert World.objects.get().turn == 0


def test_backups_are_pruned(world, monkeypatch):
    monkeypatch.setitem(get_game().config.raw["backups"], "keep", 2)
    for _ in range(4):
        run_turn(force=True)
    assert sorted(Backup.objects.values_list("turn", flat=True)) == [2, 3]


def test_rollback_restores_state(world):
    island = Island.objects.first()
    original = island.name
    run_turn(force=True)  # バックアップ: ターン 1 の前（ターン 0）
    Order.objects.create(island=island, seq=1, kind="rename_island", payload={"name": "変更後"})
    tile = Tile.objects.filter(island=island).first()
    run_turn(force=True)  # ターン 1 → 2 で名前が変わる
    Tile.objects.filter(pk=tile.pk).update(population=99)
    assert Island.objects.get(pk=island.pk).name == "変更後"

    rollback(1)
    world.refresh_from_db()
    assert world.turn == 1
    assert Island.objects.get(pk=island.pk).name == original
    assert Tile.objects.get(pk=tile.pk).population == tile.population
    assert Order.objects.get().status == "pending"
    assert not TurnLog.objects.filter(turn__gt=1).exists()
    with pytest.raises(TurnError):
        rollback(5)


def test_commands(world):
    out = StringIO()
    call_command("run_turn", "--force", stdout=out)
    assert "ターン 1 に進めました" in out.getvalue()
    out = StringIO()
    call_command("rollback_turn", "0", stdout=out)
    assert "ターン 0 に戻しました" in out.getvalue()
    out = StringIO()
    call_command("check_defs", stdout=out)
    assert out.getvalue().startswith("OK: パック base")
