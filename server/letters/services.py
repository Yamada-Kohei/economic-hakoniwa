"""手紙を送る・読む決まり。画面（views.py）と JSON API（api.py）の両方が使う。

送れるのは自分の島を持つ人だけで、差出人は自分の島（1アカウント1島）。読めるのは差出人の島と
宛先の島の持ち主だけ。送りすぎの歯止めに、1つの島が 24 時間に送れる数を設定
（HAKONIWA_LETTERS_PER_DAY）で決める。
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from server.letters.models import Letter
from server.world.models import Island

SUBJECT_MAX = Letter._meta.get_field("subject").max_length
BODY_MAX = Letter._meta.get_field("body").max_length
PAGE_SIZE = 30  # 一覧の1ページの通数


class LetterError(Exception):
    """手紙を送れないわけ。text は画面と API にそのまま出し、status は API の HTTP の状態にする。"""

    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.text = text
        self.status = status


def own_island(user) -> Island | None:
    """ログインした人の島（1アカウント1島）。なければ None。"""
    if not user.is_authenticated:
        return None
    return Island.objects.select_related("world").filter(owner=user).order_by("id").first()


def _letters() -> QuerySet[Letter]:
    return Letter.objects.filter(hidden=False).select_related("sender", "recipient", "author")


def inbox(island: Island) -> QuerySet[Letter]:
    """島に届いた手紙（新しい順）。"""
    return _letters().filter(recipient=island)


def sent_box(island: Island) -> QuerySet[Letter]:
    """島から送った手紙（新しい順）。"""
    return _letters().filter(sender=island)


def unread_count(island: Island) -> int:
    return Letter.objects.filter(recipient=island, hidden=False, read_at__isnull=True).count()


def visible_letter(user, letter_id: int) -> Letter | None:
    """差出人の島か宛先の島の持ち主に見せる1通。ほかの人と、非表示の手紙は None。"""
    if not user.is_authenticated:
        return None
    letter = _letters().filter(pk=letter_id).first()
    if letter is None or user.id not in (letter.sender.owner_id, letter.recipient.owner_id):
        return None
    return letter


def open_letter(user, letter: Letter) -> None:
    """宛先の島の持ち主が開いたら既読にする（差出人が開いても変えない）。"""
    if letter.read_at is not None or letter.recipient.owner_id != user.id:
        return
    letter.read_at = timezone.now()
    Letter.objects.filter(pk=letter.pk, read_at__isnull=True).update(read_at=letter.read_at)


def send_letter(user, sender: Island, to: int, subject: str, body: str) -> Letter:
    """自分の島（sender）から、番号 to の島へ送る。送れなければ LetterError。"""
    subject, body = subject.strip(), body.strip()
    if to == sender.id:
        raise LetterError("自分の島には送れません")
    recipient = Island.objects.filter(pk=to).first()
    if recipient is None:
        raise LetterError(f"番号 {to} の島はありません")
    if not subject:
        raise LetterError("題名を書いてください")
    if len(subject) > SUBJECT_MAX:
        raise LetterError(f"題名は {SUBJECT_MAX} 字までです（いまは {len(subject)} 字）")
    if not body:
        raise LetterError("本文を書いてください")
    if len(body) > BODY_MAX:
        raise LetterError(f"本文は {BODY_MAX} 字までです（いまは {len(body)} 字）")
    limit = settings.HAKONIWA_LETTERS_PER_DAY
    since = timezone.now() - timedelta(days=1)
    if Letter.objects.filter(sender=sender, sent_at__gte=since).count() >= limit:
        raise LetterError(
            f"1つの島から送れる手紙は 24 時間に {limit} 通までです。"
            "しばらく待ってから送ってください",
            status=429,
        )
    return Letter.objects.create(
        sender=sender,
        recipient=recipient,
        author=user,
        subject=subject,
        body=body,
        turn=sender.world.turn,
    )


def reply_subject(subject: str) -> str:
    """返事の題名（「Re: 」を1つだけ付け、長さの上限に収める）。"""
    if subject.startswith("Re: "):
        return subject
    return ("Re: " + subject)[:SUBJECT_MAX]
