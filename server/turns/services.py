"""ターン更新の実行、バックアップ、巻き戻し。"""

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.core.mail import mail_admins
from django.db import OperationalError, connection, transaction
from django.utils import timezone

from hakoniwa_core import ledger
from hakoniwa_core.economy.start import start_economy
from hakoniwa_core.founding import refound
from hakoniwa_core.modules.trade import check as trade_check
from hakoniwa_core.state import IslandState, WorldState
from hakoniwa_core.state.serialize import world_from_dict, world_to_dict
from server.game import get_game
from server.orders.models import Order
from server.turns.models import Backup, TurnLog
from server.world import models as m
from server.world.bulk import update_rows
from server.world.repository import (
    ORDER_FIELDS,
    WorldRepository,
    delete_records_after,
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


# 更新中の印を立てた直後から、更新が行の鎖を取るまでの間（別の更新が割り込んで見るかもしれない）
LOCK_GRACE = timedelta(minutes=2)


def update_is_stale(world: m.World, now: datetime | None = None) -> bool:
    """更新中の印が、強制終了などで残ったままの古いものか。

    更新は世界の行の鎖を取ったまま走るので、鎖を取れた側が印を見つけたなら、更新していた側は
    もういない。鎖を使えない DB（SQLite）では見分けられないので、
    HAKONIWA_UPDATE_STALE_MINUTES より古い印を残りものとみなす。ターンの計算がそれより長くかかる
    世界では、その分だけ大きくする。鎖を取れた側（select_for_update の後）か、取れない DB で呼ぶ。
    """
    since = world.updating_since
    if since is None:
        return False
    limit = (
        LOCK_GRACE
        if connection.features.has_select_for_update
        else timedelta(minutes=settings.HAKONIWA_UPDATE_STALE_MINUTES)
    )
    return (now or timezone.now()) - since > limit


def update_running() -> bool:
    """今、更新が走っているか（残りものの印は数えない）。命令の書き換えを断るかどうかに使う。

    呼ぶ側のトランザクションの中で呼ぶ。世界の行の鎖を、待たずに取りにいく。取れなければ更新が
    走っている。取れたら、そのトランザクションが終わるまで更新は始まらない（始まる側が待つ）ので、
    その間に命令を書き換えても更新が読んだ命令とぶつからない。
    """
    try:
        with transaction.atomic():  # 鎖が取れない失敗で呼び出し側のトランザクションを壊さない
            world = m.World.objects.select_for_update(nowait=True).get(pk=get_world().pk)
    except OperationalError:
        return True
    return world.updating_since is not None and not update_is_stale(world)


def run_turn(force: bool = False, now: datetime | None = None) -> TurnResult:
    """次の更新時刻になっていれば1ターン進める。なっていなければ何もしない。

    短い間隔で何度呼んでも二重に進まない。force=True なら時刻と一時停止を無視する。
    """
    now = now or timezone.now()
    game = get_game()

    # 1. 更新中の印を立てる（同時に2つ走らないように）
    cleared: str | None = None
    with transaction.atomic():
        world = m.World.objects.select_for_update().get(pk=get_world().pk)
        if world.updating_since is not None and update_is_stale(world, now):
            # 外すのはその場で保存する（一時停止中などで更新を始めずに戻っても、印を残さない）
            logger.warning("更新中の印が残っていたので外します（%s から）", world.updating_since)
            cleared = f"{world.updating_since} に始まった更新が終わらないまま止まっていました"
            world.updating_since = None
            world.save(update_fields=["updating_since"])
        result = _begin_update(world, force, now)
    if cleared:  # メールは保存の後に。送れなくても更新は止めない
        try:
            mail_admins(
                "更新中の印が残っていたので外しました",
                f"{cleared}（強制終了など）。ターン {world.turn} から更新をやり直します。",
            )
        except Exception:
            logger.exception("更新中の印を外したことのメールを送れませんでした")
    if result is not None:
        return result
    return _run_update(world, game, now)


def _begin_update(world: m.World, force: bool, now: datetime) -> TurnResult | None:
    """更新を始めてよければ印を立てて None。だめなら、そのわけの結果。"""
    if world.updating_since is not None and not force:
        return TurnResult(False, f"更新中です（{world.updating_since} から）", world.turn)
    if world.paused and not force:
        return TurnResult(False, "更新は一時停止中です", world.turn)
    if not force and world.next_turn_at is not None and now < world.next_turn_at:
        return TurnResult(False, f"次の更新は {world.next_turn_at} です", world.turn)
    world.updating_since = now
    world.save(update_fields=["updating_since"])
    return None


def _run_update(world: m.World, game, now: datetime) -> TurnResult:
    started = timezone.now()
    try:
        # 2. 読み込み → バックアップ → 計算 → 書き戻しを1つのトランザクションで行う。
        #    途中で失敗したら何も書かれず、ターンも進まない。
        with transaction.atomic():
            world = m.World.objects.select_for_update().get(pk=world.pk)
            if world.updating_since != now:
                # 古い印とみなされて外され、別の更新が印を取り直した。その更新に任せる
                text = "別の更新が始まっていたので、この更新はやめました"
                return TurnResult(False, text, world.turn)
            repo = WorldRepository(world)
            state = repo.load(orders_until=now)
            _check_defs_cover(state)
            _make_backup(world, state)
            _start_economies(state)
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


def _start_economies(state: WorldState) -> None:
    """フェーズ1のままの島（お金の経済がまだない島）に、お金と経済を始める（世界の移し替え）。

    今の人口と施設から額を決め、施設の会計の形は経済体制の既定にする。バックアップの後に
    行うので、このターンより前に巻き戻せばフェーズ1の島に戻り、次の更新でもう一度移し替える。
    """
    game = get_game()
    for island_id in state.island_ids():
        island = state.islands[island_id]
        if not island.has_economy:
            start_economy(island, game.defs, game.config, state.turn, migrated=True)
            logger.info("島 %s にお金と経済を始めました", island_id)


# --- バックアップと巻き戻し ---------------------------------------------------


def _make_backup(world: m.World, state: WorldState) -> None:
    # 圧縮の強さは 6（既定の 9 より1割ほど大きくなるが、時間は4分の1ほど。W-35）
    data = gzip.compress(
        json.dumps(world_to_dict(state), ensure_ascii=False).encode(), compresslevel=6
    )
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

    世界全体を同じ時点にそろえる。バックアップの後に建国した島は消さずに、建国したときの姿に戻して
    ターン to_turn に建国したことにする（ほかの島とのお金や荷のやり取りは、相手の島が戻ると消える
    ので、その島にだけ残すと帳簿が合わなくなる）。戻した先より後の更新で実行した命令は待ちに戻し、
    後から届いた命令も残す。書く前に帳簿と島をまたぐお金・荷を確かめ、合わなければ何も変えずに
    TurnError。
    """
    world = m.World.objects.select_for_update().get(pk=get_world().pk)
    if world.updating_since is not None and not update_is_stale(world):
        raise TurnError("更新中は巻き戻せません")
    backup = Backup.objects.filter(world=world, turn=to_turn).order_by("-id").first()
    if backup is None:
        turns = list(Backup.objects.filter(world=world).values_list("turn", flat=True))
        raise TurnError(f"ターン {to_turn} のバックアップがありません（あるもの: {turns}）")
    state = load_backup(backup)

    existing = set(m.Island.objects.filter(world=world).values_list("id", flat=True))
    islands = {i.id: i for i in state.islands.values() if i.id in existing}
    later = _founding_states(world, backup, sorted(existing - set(islands)))
    founded: dict[int, int] = {}  # 後から建国した島 → 建国のログと統計を書いたターン
    for island in later.values():
        refound(island, to_turn, get_game().defs)
        founded[island.id] = island.ext.get("founded_turn", to_turn)
        island.ext["founded_turn"] = min(founded[island.id], to_turn)
    state.islands = {**islands, **later}
    problems = _world_problems(state)
    if problems:
        shown = " / ".join(problems[:5])
        more = f"（ほかに {len(problems) - 5} 件）" if len(problems) > 5 else ""
        raise TurnError(f"戻した世界の帳簿が合わないので、巻き戻しませんでした: {shown}{more}")

    replace_island_contents([state.islands[i] for i in state.island_ids()])
    order_ids = set(Order.objects.filter(island__world=world).values_list("id", flat=True))
    update_rows(
        Order,
        [order_row(o) for i in state.islands.values() for o in i.orders if o.id in order_ids],
        ORDER_FIELDS,
    )
    Order.objects.filter(island__world=world, executed_turn__gt=to_turn).update(
        status="pending", reason="", reason_args={}, executed_turn=None
    )
    for island_id, turn in founded.items():
        # 建国のときのログと統計を、建国したことにしたターンに移す（後の記録は下で消える）
        if turn > to_turn:
            TurnLog.objects.filter(island_id=island_id, turn=turn).update(turn=to_turn)
            m.StatRecord.objects.filter(island_id=island_id, turn=turn).update(turn=to_turn)
    TurnLog.objects.filter(island__world=world, turn__gt=to_turn).delete()
    m.StatRecord.objects.filter(island__world=world, turn__gt=to_turn).delete()
    delete_records_after(world, to_turn)
    Backup.objects.filter(world=world, turn__gt=to_turn).delete()
    world.turn = state.turn
    world.ext = state.ext
    world.save(update_fields=["turn", "ext"])
    return state


def _founding_states(world: m.World, backup: Backup, ids: list[int]) -> dict[int, IslandState]:
    """バックアップ backup の後に建国した島の、建国したときの姿（島の番号 → 状態）。

    その島が入ったいちばん古いバックアップ（建国の後の最初の更新の前）から取る。まだ一度も更新して
    いない島は、今の姿が建国したときの姿。
    """
    found: dict[int, IslandState] = {}
    if not ids:
        return found
    # 建国したターンより前のバックアップには入っていないので読まない
    founded = [
        (ext or {}).get("founded_turn", 0)
        for ext in m.Island.objects.filter(id__in=ids).values_list("ext", flat=True)
    ]
    first = max(backup.turn, min(founded))
    later = Backup.objects.filter(world=world, turn__gte=first).exclude(pk=backup.pk)
    for b in later.order_by("turn", "id"):
        state = load_backup(b)
        for island_id in ids:
            if island_id not in found and island_id in state.islands:
                found[island_id] = state.islands[island_id]
        if len(found) == len(ids):
            return found
    current = WorldRepository(world).load()
    for island_id in ids:
        if island_id not in found:
            found[island_id] = current.islands[island_id]
    return found


def _world_problems(state: WorldState) -> list[str]:
    """世界の確かめ: 島ごとの帳簿（持ち物ごとの合計が 0、預金と国庫がマイナスでない）と、島をまたぐ
    お金（外貨・外国の国債・融通）と運ぶ途中の荷。"""
    kinds = get_game().defs.journal_kinds()
    problems = []
    for island_id in state.island_ids():
        island = state.islands[island_id]
        problems += [f"島 {island_id}: {p}" for p in ledger.check(island, island.accounts, kinds)]
    return problems + trade_check(state)
