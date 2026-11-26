"""ターンの新聞（案31）: 1 つのターンに起きた主な出来事を、新聞の一面の形で見せる。

材料はターンログの見出しになる出来事（top.HEADLINES と外交の出来事）だけ。
地図の切り抜きは書き出し基盤（案30）で描く今の地図なので、出せるのは
いちばん新しいターンの新聞だけ（それ以外のターンは地図なしで出す）。
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q

from server.country import services
from server.country.models import Profile
from server.turns.models import TurnLog
from server.web import country, display, top
from server.world import models as m


def _headline_logs(island: m.Island, mine: bool):
    logs = TurnLog.objects.filter(island=island)
    return display.visible_logs(logs, mine, island).filter(
        top.headline_filter() | Q(kind__in=country.DIPLOMACY_KINDS)
    )


def paper_data(island: m.Island, turn: int | None = None, mine: bool = False) -> dict[str, Any]:
    """その島の、そのターンの新聞。turn が無ければいちばん新しい見出しのあるターン。"""
    profile = Profile.objects.filter(island=island).first()
    logs = _headline_logs(island, mine)
    available = list(logs.order_by("turn").values_list("turn", flat=True).distinct())
    if not available:
        return {"empty": True, "turn": None}
    if turn is None or turn not in available:
        turn = available[-1]
    rows = list(logs.filter(turn=turn).order_by("id"))
    entries = display.log_entries(rows)
    lead_at = next(
        (i for i, e in enumerate(entries) if e["kind"] in country.MILESTONE_KINDS),
        0 if entries else None,
    )
    lead = entries[lead_at] if lead_at is not None else None
    articles = [e for i, e in enumerate(entries) if i != lead_at]
    prev_turn = max((t for t in available if t < turn), default=None)
    next_turn = min((t for t in available if t > turn), default=None)
    return {
        "empty": False,
        "turn": turn,
        "date": services.date_text(island, profile, turn),
        "lead": lead,
        "articles": articles,
        # 地図は今の姿だけが正しい。いちばん新しいターンの新聞にだけ付ける
        "map_shown": turn == available[-1],
        "prev_turn": prev_turn,
        "next_turn": next_turn,
    }
