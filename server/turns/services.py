"""ターン更新の実行、バックアップ、巻き戻し。"""

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.mail import mail_admins
from django.db import transaction
from django.utils import timezone

from hakoniwa_core.state import WorldState
from hakoniwa_core.state.serialize import world_from_dict, world_to_dict
from server.game import get_game
from server.orders.models import Order
from server.turns.models import Backup, TurnLog
from server.world import models as m
from server.world.bulk import update_rows
from server.world.repository import (
    ORDER_FIELDS,
    WorldRepository,
    order_row,
    replace_island_contents,
)

logger = logging.getLogger(__name__)


class TurnError(Exception):
    pass


@dataclass
class TurnResult:
    ran: bool
    message: str
    turn: int
    seconds: float = 0.0


def get_world() -> m.World:
    world = m.World.objects.order_by("id").first()
    if world is None:
        raise TurnError(
            "世界がまだありません（manage.py create_sample_world などで作ってください）"
        )
    return world


def run_turn(force: bool = False, now: datetime | None = None) -> TurnResult:
    """次の更新時刻になっていれば1ターン進める。なっていなければ何もしない。

    短い間隔で何度呼んでも二重に進まない。force=True なら時刻と一時停止を無視する。
    """
    now = now or timezone.now()
    game = get_game()

    # 1. 更新中の印を立てる（同時に2つ走らないように）
    with transaction.atomic():
        world = m.World.objects.select_for_update().get(pk=get_world().pk)
        if world.updating_since is not None and not force:
            return TurnResult(False, f"更新中です（{world.updating_since} から）", world.turn)
        if world.paused and not force:
            return TurnResult(False, "更新は一時停止中です", world.turn)
        if not force and world.next_turn_at is not None and now < world.next_turn_at:
            return TurnResult(False, f"次の更新は {world.next_turn_at} です", world.turn)
        world.updating_since = now
        world.save(update_fields=["updating_since"])

    started = timezone.now()
    try:
        # 2. 読み込み → バックアップ → 計算 → 書き戻しを1つのトランザクションで行う。
        #    途中で失敗したら何も書かれず、ターンも進まない。
        with transaction.atomic():
            world = m.World.objects.select_for_update().get(pk=world.pk)
            repo = WorldRepository(world)
            state = repo.load(orders_until=now)
            _check_defs_cover(state)
            _make_backup(world, state)
            report = game.engine.run_turn(state)
            logger.info("ターン %s の段ごとの時間（秒）: %s", report.turn, report.seconds)
            repo.save(state)
            world.packs = game.defs.pack_versions()
            world.next_turn_at = _next_turn_time(world.next_turn_at, now)
            world.updating_since = None
            world.save(update_fields=["packs", "next_turn_at", "updating_since"])
    except Exception as e:
        m.World.objects.filter(pk=world.pk).update(updating_since=None)
        logger.exception("ターン更新に失敗しました")
        mail_admins("ターン更新に失敗しました", f"ターン {world.turn} → {world.turn + 1}: {e!r}")
        raise
    seconds = (timezone.now() - started).total_seconds()
    return TurnResult(True, f"ターン {state.turn} に進めました", state.turn, seconds)


def _next_turn_time(previous: datetime | None, now: datetime) -> datetime:
    interval = timedelta(minutes=get_game().config.get("turn.interval_minutes"))
    candidate = (previous or now) + interval
    return candidate if candidate > now else now + interval


def _check_defs_cover(state: WorldState) -> None:
    """保存されている名前が、今の定義データに残っているか。

    パックの版が変わって定義が消えたのに移し替えがない場合は、更新を止める。
    """
    defs = get_game().defs
    missing: set[str] = set()
    for island in state.islands.values():
        missing.update(f"地形 {t.terrain}" for t in island.tiles if t.terrain not in defs.terrain)
        missing.update(f"命令 {o.kind}" for o in island.orders if o.kind not in defs.orders)
        for f in island.facilities:
            if f.kind not in defs.facilities:
                missing.add(f"施設 {f.kind}")
            elif f.recipe not in defs.facilities[f.kind]["recipes"]:
                missing.add(f"施設 {f.kind} の作り方 {f.recipe}")
            missing.update(f"財 {g}" for g in f.stock if g not in defs.goods)
        missing.update(f"財 {g}" for g in island.warehouse if g not in defs.goods)
    if missing:
        raise TurnError("定義データから消えたものがあります: " + "、".join(sorted(missing)))


# --- バックアップと巻き戻し ---------------------------------------------------


def _make_backup(world: m.World, state: WorldState) -> None:
    data = gzip.compress(json.dumps(world_to_dict(state), ensure_ascii=False).encode())
    Backup.objects.create(world=world, turn=state.turn, data=data)
    keep = get_game().config.get("backups.keep")
    old = (
        Backup.objects.filter(world=world)
        .order_by("-turn", "-id")
        .values_list("id", flat=True)[keep:]
    )
    Backup.objects.filter(id__in=list(old)).delete()


def load_backup(backup: Backup) -> WorldState:
    return world_from_dict(json.loads(gzip.decompress(bytes(backup.data))))


@transaction.atomic
def rollback(to_turn: int) -> WorldState:
    """ターン to_turn の更新前の状態に戻す。

    バックアップの後に作られた島と、後から届いた命令はそのまま残す。
    """
    world = m.World.objects.select_for_update().get(pk=get_world().pk)
    if world.updating_since is not None:
        raise TurnError("更新中は巻き戻せません")
    backup = Backup.objects.filter(world=world, turn=to_turn).order_by("-id").first()
    if backup is None:
        turns = list(Backup.objects.filter(world=world).values_list("turn", flat=True))
        raise TurnError(f"ターン {to_turn} のバックアップがありません（あるもの: {turns}）")
    state = load_backup(backup)

    existing = set(m.Island.objects.filter(world=world).values_list("id", flat=True))
    islands = [i for i in state.islands.values() if i.id in existing]
    replace_island_contents(islands)
    order_ids = set(Order.objects.filter(island__world=world).values_list("id", flat=True))
    update_rows(
        Order,
        [order_row(o) for i in islands for o in i.orders if o.id in order_ids],
        ORDER_FIELDS,
    )
    TurnLog.objects.filter(island__world=world, turn__gt=to_turn).delete()
    m.StatRecord.objects.filter(island__world=world, turn__gt=to_turn).delete()
    Backup.objects.filter(world=world, turn__gt=to_turn).delete()
    world.turn = state.turn
    world.ext = state.ext
    world.save(update_fields=["turn", "ext"])
    return state
