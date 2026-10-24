from __future__ import annotations

import threading
from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

from server.game import get_game
from server.orders.models import Order
from server.turns.models import Backup, TurnLog
from server.turns.services import LOCK_GRACE, TurnError, rollback, run_turn, update_running
from server.world.models import Island, Tile, World

pytestmark = pytest.mark.django_db


def _own_island(client) -> Island:
    user = User.objects.create_user("owner", password="a-long-pass-123")
    client.force_login(user)
    island = Island.objects.order_by("id").first()
    island.owner = user
    island.save(update_fields=["owner"])
    return island


def test_sample_world_has_full_islands(world):
    assert world.islands.count() == 3
    size = get_game().config.get("island.size")
    for island in world.islands.all():
        assert island.tiles.count() == size * size


def test_run_turn_advances_once_per_interval(world):
    assert run_turn().ran
    world.refresh_from_db()
    assert world.turn == 1 and world.next_turn_at > timezone.now()
    # 次の更新は設定の間隔だけ先。値はベータ版の決まり（4時間おき、台帳 2026-10-18）の見張りで、
    # 変えるときは設計書の台帳に行を足してからここも直す
    interval = timedelta(minutes=get_game().config.get("turn.interval_minutes"))
    assert interval == timedelta(hours=4)
    assert timezone.now() + interval - timedelta(minutes=1) < world.next_turn_at
    assert world.next_turn_at <= timezone.now() + interval
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


# --- 更新中の印（A03・A04） --------------------------------------------------


def _stale_limit(settings) -> float:
    """残りものの印とみなす古さ（分）。鎖を使える DB では鎖を取った側が短い猶予だけ見る。"""
    settings.HAKONIWA_UPDATE_STALE_MINUTES = 30
    return LOCK_GRACE.total_seconds() / 60 if connection.features.has_select_for_update else 30


def _mark_updating(world, minutes_ago: float):
    world.updating_since = timezone.now() - timedelta(minutes=minutes_ago)
    world.save(update_fields=["updating_since"])


def test_leftover_updating_mark_is_cleared_after_the_stale_limit(world, settings, mailoutbox):
    limit = _stale_limit(settings)
    settings.ADMINS = [("管理者", "admin@example.com")]
    _mark_updating(world, limit / 3)
    result = run_turn()
    assert not result.ran and "更新中です" in result.message  # 走っているかもしれない間は待つ
    _mark_updating(world, limit + 1)
    result = run_turn(force=False, now=timezone.now())
    assert result.ran and World.objects.get().updating_since is None
    assert "更新中の印が残っていた" in mailoutbox[0].subject


def test_rollback_ignores_a_stale_updating_mark(world, settings):
    limit = _stale_limit(settings)
    run_turn(force=True)
    _mark_updating(world, limit / 3)
    with pytest.raises(TurnError, match="更新中は巻き戻せません"):
        rollback(0)
    _mark_updating(world, limit + 1)
    rollback(0)
    assert World.objects.get().turn == 0


def test_orders_are_refused_while_an_update_is_running(client, world, settings):
    limit = _stale_limit(settings)
    island = _own_island(client)
    url = f"/api/islands/{island.id}/orders"
    body = {"orders": [{"kind": "rename_island", "payload": {"name": "新島"}}]}
    Order.objects.create(island=island, seq=1, kind="rename_island", payload={"name": "元"})
    _mark_updating(world, limit / 3)
    res = client.put(url, body, content_type="application/json")
    assert res.status_code == 409 and "更新中" in res.json()["error"]
    # 断られたので、待っていた命令はそのまま（更新が読んだ列と食い違わない）
    assert list(Order.objects.values_list("payload", flat=True)) == [{"name": "元"}]
    # 残りものの印は断る理由にならない
    _mark_updating(world, limit + 1)
    assert client.put(url, body, content_type="application/json").status_code == 200
    assert list(Order.objects.values_list("payload", flat=True)) == [{"name": "新島"}]


@pytest.mark.django_db(transaction=True)
def test_row_lock_tells_a_running_update_from_a_leftover_mark(world):
    """PostgreSQL では、更新が世界の行の鎖を持つ間だけ「更新中」（鎖のない DB では飛ばす）。"""
    if connection.vendor != "postgresql":
        pytest.skip("行の鎖は PostgreSQL だけ")
    holding, release, errors = threading.Event(), threading.Event(), []

    def update():  # 更新の本体（run_turn の 2 つ目のトランザクション）の鎖の持ち方
        try:
            with transaction.atomic():
                World.objects.select_for_update().get(pk=world.pk)
                holding.set()
                release.wait(10)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
            holding.set()
        finally:
            connection.close()

    _mark_updating(world, 10)  # 10 分前の印
    thread = threading.Thread(target=update)
    thread.start()
    assert holding.wait(10) and not errors
    try:
        with transaction.atomic():
            assert update_running()  # 鎖を持つ更新がいる間は、印が古くても更新中
    finally:
        release.set()
        thread.join(10)
    with transaction.atomic():
        assert not update_running()  # 更新がいなくなって 2 分以上たった印は残りもの
    _mark_updating(world, 0.5)
    with transaction.atomic():
        assert update_running()  # 印を立てた直後（まだ鎖を取る前かもしれない）は更新中
    _mark_updating(world, 10)
    assert run_turn().ran and World.objects.get().updating_since is None


def test_stale_mark_is_cleared_even_when_the_update_does_not_start(world, settings):
    limit = _stale_limit(settings)
    world.paused = True
    world.save(update_fields=["paused"])
    _mark_updating(world, limit + 1)
    assert not run_turn().ran
    assert World.objects.get().updating_since is None  # 一時停止中でも印は残さない


def test_stale_mark_mail_failure_does_not_block_the_update(world, settings, monkeypatch):
    limit = _stale_limit(settings)

    def boom(*args, **kwargs):
        raise OSError("SMTP が落ちている")

    monkeypatch.setattr("server.turns.services.mail_admins", boom)
    _mark_updating(world, limit + 1)
    assert run_turn().ran


def test_update_stops_if_its_mark_was_taken_over(world, monkeypatch):
    """古い印とみなされて外され、別の更新が印を取り直していたら、遅れてきた更新は何もしない。"""
    from server.turns import services

    real = services._begin_update

    def begin_then_steal(world, force, now):
        result = real(world, force, now)
        World.objects.filter(pk=world.pk).update(updating_since=now + timedelta(seconds=1))
        return result

    monkeypatch.setattr(services, "_begin_update", begin_then_steal)
    result = run_turn(force=True)
    assert not result.ran and "別の更新" in result.message
    assert World.objects.get().turn == 0
