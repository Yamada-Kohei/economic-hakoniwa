"""トップの画面のアクセスカウンタとキリ番（個人サイトの伝統。ゲームの得はない）。

- 数えるのはトップの画面を開いた人だけ。同じ人がその日のうちに開き直しても数えない（その日の
  番号をもう一度見せる）。日付が変わると、また1人として数える（当時のカウンタと同じ）。
- 「同じ人」は、ログインしていればアカウント、していなければ接続元の IP とブラウザの名前の組。
  そのまま残さず、日付とサーバーの秘密鍵で混ぜたハッシュにする。日をまたいで同じ人を
  たどれないよう、ハッシュに日付を混ぜ、今日と昨日の分だけ持つ。
- 数えるのはブラウザ（User-Agent に Mozilla とあるもの）だけ。試し遊びの道具（tools/playtest/hk.py）
  が cookie を取りに開くときや、curl などは数えない（今の数を見せるだけ）。
- 数字は 7 セグメントの表示器の絵（SVG）で出す。絵はここで作る（外の素材は使わない）。
- 閲覧中の人数は、ONLINE_MINUTES 分のうちにトップの画面を開いた人の数（当時の「現在の閲覧者数」）。
- キリ番を踏んだ人は、ログインしていればアカウントの名前と国名を KiribanGetter に残す（ログインして
  いない人は誰か分からないので残さない）。
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from server.accounts.clientip import client_ip
from server.web.models import AccessCounter, KiribanGetter, Visit
from server.world.models import Island

NAME = "top"
DIGITS = 6  # カウンタの桁（足りなくなったら桁を増やして出す）
ONLINE_MINUTES = 5  # 閲覧中と数える時間
GETTERS = 10  # キリ番ゲッター様の一覧に出す人数


@dataclass(frozen=True)
class Count:
    number: int  # この人が何人目のお客様か
    total: int  # これまでのお客様の数
    today: int
    yesterday: int
    new: bool  # 今日はじめて数えたか
    since: date | None
    visitor: bool = True  # 数える相手（ブラウザ）か
    online: int = 0  # 閲覧中の人数（ONLINE_MINUTES 分のうちに開いた人）
    getter: bool = False  # キリ番ゲッター様の一覧に名前を残したか

    @property
    def kiriban(self) -> bool:
        return self.visitor and is_kiriban(self.number)


def _digest(request, day: date) -> str:
    if request.user.is_authenticated:
        who = f"user:{request.user.pk}"
    else:
        agent = request.META.get("HTTP_USER_AGENT", "")[:200]
        who = f"ip:{client_ip(request)}|{agent}"
    message = f"{day.isoformat()}|{who}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def count_visit(request) -> Count:
    """トップの画面を開いた人を数える（その日に2回目なら数えない）。"""
    now = timezone.now()
    day = timezone.localdate()
    Visit.objects.filter(day__lt=day - timedelta(days=1)).delete()
    if "Mozilla" not in request.META.get("HTTP_USER_AGENT", ""):
        counter, _ = AccessCounter.objects.get_or_create(name=NAME)
        return Count(
            number=counter.total,
            total=counter.total,
            today=Visit.objects.filter(day=day).count(),
            yesterday=Visit.objects.filter(day=day - timedelta(days=1)).count(),
            new=False,
            since=counter.since,
            visitor=False,
            online=online(now),
        )
    digest = _digest(request, day)
    visit = Visit.objects.filter(day=day, digest=digest).first()
    new = False
    if visit is None:
        try:
            with transaction.atomic():
                counter, _ = AccessCounter.objects.get_or_create(name=NAME)
                AccessCounter.objects.filter(pk=counter.pk).update(total=F("total") + 1)
                number = AccessCounter.objects.values_list("total", flat=True).get(pk=NAME)
                visit = Visit.objects.create(day=day, digest=digest, number=number, seen=now)
            new = True
        except IntegrityError:  # 同じ人の同時の2回目（先に数えたほうの番号を見せる）
            visit = Visit.objects.get(day=day, digest=digest)
    if not new:
        Visit.objects.filter(pk=visit.pk).update(seen=now)
    getter = new and is_kiriban(visit.number) and _record_getter(request, visit.number)
    counter = AccessCounter.objects.get(pk=NAME)
    return Count(
        number=visit.number,
        total=counter.total,
        today=Visit.objects.filter(day=day).count(),
        yesterday=Visit.objects.filter(day=day - timedelta(days=1)).count(),
        new=new,
        since=counter.since,
        online=online(now),
        getter=getter,
    )


def online(now) -> int:
    """閲覧中の人数（ONLINE_MINUTES 分のうちにトップの画面を開いた人）。"""
    return Visit.objects.filter(seen__gte=now - timedelta(minutes=ONLINE_MINUTES)).count()


def _record_getter(request, number: int) -> bool:
    """キリ番を踏んだのがログインしている人なら、名前と国名を残す。"""
    user = request.user
    if not user.is_authenticated:
        return False
    island = Island.objects.filter(owner=user).values_list("name", flat=True).first()
    _, created = KiribanGetter.objects.get_or_create(
        number=number, defaults={"user": user, "name": user.username, "island_name": island or ""}
    )
    return created


def getters() -> list[dict[str, Any]]:
    """キリ番ゲッター様の一覧（新しい番号から GETTERS 人）。"""
    rows = KiribanGetter.objects.order_by("-number")[:GETTERS]
    return [
        {
            "number": r.number,
            "name": r.name,
            "island_name": r.island_name,
            "date": timezone.localdate(r.at).isoformat(),
        }
        for r in rows
    ]


def is_kiriban(n: int) -> bool:
    """キリ番（切りのよい番号）か: 100 の倍数（1000 未満）・1000 の倍数・ゾロ目（777・1111 など）・
    連番（123・1234・12345・123456）。"""
    if n < 100:
        return False
    text = str(n)
    if n % 1000 == 0 or (n < 1000 and n % 100 == 0):
        return True
    if len(set(text)) == 1:
        return True
    return text == "123456789"[: len(text)]


# 7 セグメントの表示器: 桁ごとに a〜g の 7 本の線
# （a 上・b 右上・c 右下・d 下・e 左下・f 左上・g 中）
_SEGMENTS = {
    "a": "6,3 8,1 16,1 18,3 16,5 8,5",
    "b": "20,4 22,6 22,17 20,19 18,17 18,6",
    "c": "20,21 22,23 22,34 20,36 18,34 18,23",
    "d": "6,37 8,35 16,35 18,37 16,39 8,39",
    "e": "4,21 6,23 6,34 4,36 2,34 2,23",
    "f": "4,4 6,6 6,17 4,19 2,17 2,6",
    "g": "6,20 8,18 16,18 18,20 16,22 8,22",
}
_LIT = {
    "0": "abcdef",
    "1": "bc",
    "2": "abdeg",
    "3": "abcdg",
    "4": "bcfg",
    "5": "acdfg",
    "6": "acdefg",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
}
_WIDTH, _HEIGHT = 26, 40


def seven_segment(number: int, digits: int = DIGITS) -> str:
    """数を 7 セグメントの表示器の絵（SVG）にする。点いていない線も薄く描く（本物と同じ）。"""
    text = str(max(number, 0)).zfill(digits)
    pad = 4
    width = pad * 2 + _WIDTH * len(text)
    height = pad * 2 + _HEIGHT
    parts = []
    for i, ch in enumerate(text):
        x = pad + _WIDTH * i
        for seg, points in _SEGMENTS.items():
            lit = seg in _LIT[ch]
            parts.append((x, "on" if lit else "off", points))
    shapes = format_html_join(
        "",
        '<polygon class="seg-{}" transform="translate({},{})" points="{}"/>',
        ((state, x, pad, points) for x, state, points in parts),
    )
    return format_html(
        '<svg class="seven-seg" viewBox="0 0 {w} {h}" width="{w2}" height="{h2}" role="img" '
        'aria-label="{label}"><rect width="{w}" height="{h}" rx="3" class="seg-bg"/>{shapes}</svg>',
        w=width,
        h=height,
        w2=width // 2,
        h2=height // 2,
        label=text,
        shapes=shapes,
    )
