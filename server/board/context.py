"""全部の画面で使う値（見出しの「掲示板」に出す、自分の書き込みへの返信の未読の数）。"""

from __future__ import annotations

from server.board.services import unread_count


def unread_replies(request) -> dict:
    if not request.user.is_authenticated:
        return {}
    return {"board_unread": unread_count(request.user)}
