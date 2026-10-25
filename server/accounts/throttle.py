"""回数の上限を、DB で数える（複数のワーカーが同じ回数を見る）。

区切りごと（固定の窓）の回数を RateCounter に持ち、「空きがあれば、使う分をまとめて足す」を
1 つの UPDATE で行う。確かめと足し込みの間に別の送信が割り込めないので、同時に何本送られても
上限を超えて受けない。断った送信は数に足さない（断られ続けても、待ち時間は延びない）。

窓は固定（時刻を秒の幅で割った番号）。窓の変わり目に、続けて 2 回分まで通る場合があるが、実装が
単純で、どのワーカーが数えても同じ答えになる。
"""

from __future__ import annotations

import math
import time

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F

from server.accounts.models import RateCounter


def consume(
    scope: str,
    key: str,
    *,
    limit: int,
    window_seconds: int,
    cost: int = 1,
    now: float | None = None,
) -> int:
    """cost 回分を使う。使えたら 0。足りなければ何も使わず、次の窓までの秒数（1 以上）を返す。

    cost が limit より大きいときは、待っても通らない（呼ぶ側が先に見分ける）。
    """
    now = time.time() if now is None else now
    window = int(now // window_seconds)
    window_end = (window + 1) * window_seconds
    wait = max(1, math.ceil(window_end - now))
    if cost > limit:
        return wait
    rows = RateCounter.objects.filter(scope=scope, key=key, window=window)
    if rows.filter(count__lte=limit - cost).update(count=F("count") + cost):
        return 0
    if rows.exists():  # 窓の中で使い切っている
        return wait
    try:
        with transaction.atomic():  # PostgreSQL でも、重複で外側の取引が壊れないように
            RateCounter.objects.create(
                scope=scope, key=key, window=window, window_end=window_end, count=cost
            )
    except IntegrityError:  # 同時にだれかが先に作った
        if rows.filter(count__lte=limit - cost).update(count=F("count") + cost):
            return 0
        return wait
    purge(now)
    return 0


def purge(now: float | None = None) -> int:
    """保存の期限（HAKONIWA_THROTTLE_KEEP_HOURS）を過ぎた数えを消す。消した数。"""
    now = time.time() if now is None else now
    cutoff = now - settings.HAKONIWA_THROTTLE_KEEP_HOURS * 3600
    return RateCounter.objects.filter(window_end__lt=cutoff).delete()[0]
