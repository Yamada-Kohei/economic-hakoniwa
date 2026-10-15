"""全部の画面で使う値（見出しの「外交」に出す未読の公電の数）。"""

from __future__ import annotations

from server.letters.models import Letter


def unread_letters(request) -> dict:
    """自分の島に届いた未読の手紙の数。ログインしていなければ出さない（島がなければ 0）。"""
    if not request.user.is_authenticated:
        return {}
    count = Letter.objects.filter(
        recipient__owner=request.user, hidden=False, read_at__isnull=True
    ).count()
    return {"letters_unread": count}
