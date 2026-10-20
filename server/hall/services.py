"""国際会議場（公開の声明の場）の決まり。画面（views.py）と JSON API（api.py）の両方が使う。

載るのは次の 2 つで、新しい順に 1 本の並びにして見せる。

- 声明: 自分の島を持つ人が出す公開の文。出せる数は 1 つの島が 24 時間に HAKONIWA_HALL_PER_DAY まで
- 出来事: 条約の効き始め・破棄の予告・違反、宣戦・停戦・講和。当事国のターンログのうち
  ``hall`` の印のついた行から、表示のときに文にする（messages.yaml の ``hall_<文の種類>``）
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from hakoniwa_core.log import format_log
from server.game import get_game
from server.hall.models import Statement
from server.letters.services import own_island
from server.turns.models import TurnLog
from server.world.models import Island, World

TITLE_MAX = Statement._meta.get_field("title").max_length
BODY_MAX = Statement._meta.get_field("body").max_length
PAGE_SIZE = 30  # 一覧の 1 ページの数
SCAN = 300  # 並びに入れる、声明と出来事のそれぞれの新しい方の数

__all__ = ["HallError", "own_island", "post_statement", "timeline"]


class HallError(Exception):
    """声明を出せないわけ。text は画面と API にそのまま出し、status は API の HTTP の状態にする。"""

    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.text = text
        self.status = status


def post_statement(user, island: Island, title: str, body: str) -> Statement:
    """自分の島（island）から声明を出す。出せなければ HallError。"""
    title, body = title.strip(), body.strip()
    if not title:
        raise HallError("題名を書いてください")
    if len(title) > TITLE_MAX:
        raise HallError(f"題名は {TITLE_MAX} 字までです（いまは {len(title)} 字）")
    if not body:
        raise HallError("本文を書いてください")
    if len(body) > BODY_MAX:
        raise HallError(f"本文は {BODY_MAX} 字までです（いまは {len(body)} 字）")
    limit = settings.HAKONIWA_HALL_PER_DAY
    since = timezone.now() - timedelta(days=1)
    if Statement.objects.filter(island=island, posted_at__gte=since).count() >= limit:
        raise HallError(
            f"1つの島が国際会議場に出せる声明は 24 時間に {limit} 件までです。"
            "しばらく待ってから出してください",
            status=429,
        )
    return Statement.objects.create(
        world=island.world,
        island=island,
        author=user,
        title=title,
        body=body,
        turn=island.world.turn,
    )


def event_text(log: TurnLog) -> str:
    game = get_game()
    return format_log(game.defs, f"hall_{log.kind}", log.args or {}, calendar=game.calendar)


def timeline(world: World) -> list[dict[str, Any]]:
    """会議場の並び（新しい順。声明と出来事を、ターンごとに出来事のあとに声明）。"""
    out: list[dict[str, Any]] = []
    statements = (
        Statement.objects.filter(world=world, hidden=False)
        .select_related("island", "author")
        .order_by("-id")[:SCAN]
    )
    for s in statements:
        out.append(
            {
                "type": "statement",
                "id": s.id,
                "turn": s.turn,
                "island": {"id": s.island_id, "name": s.island.name},
                "author": s.author.username if s.author else "",
                "title": s.title,
                "text": s.body,
                "posted_at": timezone.localtime(s.posted_at).isoformat(timespec="seconds"),
            }
        )
    events = TurnLog.objects.filter(island__world=world, args__hall=True).order_by("-turn", "-id")
    for log in events[:SCAN]:
        out.append(
            {
                "type": "event",
                "id": log.id,
                "turn": log.turn,
                "kind": log.kind,
                "text": event_text(log),
            }
        )
    # 同じターンでは、出来事の後ろに声明（声明はそのターンの内に出たもの）
    out.sort(key=lambda e: (e["turn"], e["type"] == "statement", e["id"]), reverse=True)
    return out


def entries_since(world: World, since: int) -> list[dict[str, Any]]:
    """ターン since より後に会議場に載ったもの（古い順。国の差分に添える）。"""
    return [e for e in reversed(timeline(world)) if e["turn"] > since]
