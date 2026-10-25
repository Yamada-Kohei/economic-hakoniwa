"""ログインの試しすぎの上限（通常のログイン /accounts/login/ と管理画面 /admin/login/ の両方）。

2 つの上限を、この順に調べる。どちらも断るときは、パスワードの照合まで進まず、429 と
`Retry-After`（待つ秒数）を返す。画面の文は 2 つとも同じで、アカウントがあるかどうかは分からない。

1. 接続元ごとの送信の数: 同じ接続元がログインの画面に送れるのは、HAKONIWA_LOGIN_POST_WINDOW_SECONDS
   の間に HAKONIWA_LOGIN_POSTS_PER_WINDOW 回まで。アカウント名を変えた送信も、2 つの画面の送信も
   合算する。成功しても戻さない。数えるのは server/accounts/throttle.py（断った送信は足さない）。
2. 接続元とアカウント名の組み合わせごとの失敗: HAKONIWA_LOGIN_FAILURE_WINDOW_MINUTES の間に
   HAKONIWA_LOGIN_MAX_FAILURES 回失敗したら、その組み合わせだけ HAKONIWA_LOGIN_LOCK_MINUTES 分
   断る。アカウント名だけでは止めないので、別の接続元から本人が入れる。通常と管理画面は同じ記録を
   使う。成功するとその組み合わせの記録を消す。停止中の送信は記録に触らないので、送り続けても
   停止は延びない。

パスワードの照合の前に「1 回分を先に取る」ので、同時に何本送られても、照合できるのは上限の
回数までに収まる。照合の後で、成功なら記録を消し、失敗なら（上限に達していれば）停止を始め、
照合に進まなかった送信（CSRF の印の誤りなど）なら取った分を返す。

接続元は server/accounts/clientip.py。読めない接続元は、1 つにまとめて数える（対象外にしない）。
アカウント名も IP も、そのまま残さずハッシュにして持つ（models.LoginAttempt）。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import unicodedata
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import DateTimeField, F, Q, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone

from server.accounts import throttle
from server.accounts.clientip import UNKNOWN_BUCKET, client_ip
from server.accounts.models import LoginAttempt

logger = logging.getLogger(__name__)

LOGIN_PATHS = ("/accounts/login/", "/admin/login/")
USERNAME_MAX = 256  # 照合に使う文字数（Django のアカウント名は 150 字まで。それより長い入力は切る）


def _digest(kind: str, text: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(), f"{kind}:{text}".encode(), hashlib.sha256
    ).hexdigest()


def _normalize(username: str) -> str:
    """大文字小文字・全角半角・前後の空白の違いで別のアカウント名として数えさせない。"""
    return unicodedata.normalize("NFKC", username).strip().casefold()[:USERNAME_MAX]


def wait_text(seconds: int) -> str:
    return f"{seconds} 秒" if seconds < 60 else f"約 {math.ceil(seconds / 60)} 分"


def _pair(ip_key: str, user_key: str):
    return LoginAttempt.objects.filter(ip_hash=ip_key, user_hash=user_key)


def reserve(ip_key: str, user_key: str, now=None) -> int:
    """パスワードを照合してよいか。よければ、その 1 回分を先に数えて 0。停止中なら残りの秒数。"""
    now = now or timezone.now()
    limit = settings.HAKONIWA_LOGIN_MAX_FAILURES
    window = timedelta(minutes=settings.HAKONIWA_LOGIN_FAILURE_WINDOW_MINUTES)
    rows = _pair(ip_key, user_key)
    # 停止が終わった記録と、数え始めから期間を過ぎた記録は、0 に戻す
    rows.filter(
        Q(locked_until__lte=now) | Q(locked_until__isnull=True, window_start__lte=now - window)
    ).update(failures=0, window_start=None, locked_until=None, touched_at=now)

    def take() -> int:
        return rows.filter(failures__lt=limit).update(
            failures=F("failures") + 1,
            window_start=Coalesce("window_start", Value(now, output_field=DateTimeField())),
            touched_at=now,
        )

    if take():
        return 0
    if not rows.exists():
        try:
            with transaction.atomic():
                LoginAttempt.objects.create(
                    ip_hash=ip_key, user_hash=user_key, failures=1, window_start=now, touched_at=now
                )
        except IntegrityError:  # 同時にだれかが先に作った
            if take():
                return 0
        else:
            purge_attempts(now)
            return 0
    row = rows.first()
    if row is None:  # 取ろうとする間に消えた（成功して記録が消えた）
        return 0
    if row.locked_until is not None:
        return max(1, math.ceil((row.locked_until - now).total_seconds()))
    return settings.HAKONIWA_LOGIN_LOCK_MINUTES * 60  # 最後の 1 回の照合の結果待ち


def settle_success(ip_key: str, user_key: str) -> None:
    _pair(ip_key, user_key).delete()


def settle_failure(ip_key: str, user_key: str, now=None) -> int:
    """失敗の後。上限に達していれば停止を始めて（すでに始まっていれば延ばさず）残りの秒数。"""
    now = now or timezone.now()
    rows = _pair(ip_key, user_key)
    until = now + timedelta(minutes=settings.HAKONIWA_LOGIN_LOCK_MINUTES)
    rows.filter(
        failures__gte=settings.HAKONIWA_LOGIN_MAX_FAILURES, locked_until__isnull=True
    ).update(locked_until=until, touched_at=now)
    row = rows.filter(failures__gte=settings.HAKONIWA_LOGIN_MAX_FAILURES).first()
    if row is None or row.locked_until is None:
        return 0
    return max(1, math.ceil((row.locked_until - now).total_seconds()))


def refund(ip_key: str, user_key: str) -> None:
    """照合に進まなかった送信の分を戻す。"""
    rows = _pair(ip_key, user_key)
    rows.filter(failures__gt=0, locked_until__isnull=True).update(failures=F("failures") - 1)
    rows.filter(failures=0).update(window_start=None)


def purge_attempts(now=None) -> int:
    """保存の期限（HAKONIWA_THROTTLE_KEEP_HOURS）を過ぎた失敗の記録を消す。消した数。"""
    now = now or timezone.now()
    cutoff = now - timedelta(hours=settings.HAKONIWA_THROTTLE_KEEP_HOURS)
    return LoginAttempt.objects.filter(touched_at__lt=cutoff).delete()[0]


def blocked_response(request, wait: int):
    response = render(
        request,
        "accounts/login_blocked.html",
        {"wait": wait, "wait_text": wait_text(wait)},
        status=429,
    )
    response["Retry-After"] = str(wait)
    return response


class LoginGuardMiddleware:
    """LOGIN_PATHS への POST に、上の 2 つの上限を掛ける。AuthenticationMiddleware の後に置く。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method != "POST" or request.path not in LOGIN_PATHS:
            return self.get_response(request)

        ip = client_ip(request)
        if not ip:
            ip = UNKNOWN_BUCKET
            logger.warning(
                "ログインの送信の接続元が読めないので、まとめて数えます"
                "（HAKONIWA_CLIENT_IP_HEADER=%s、HAKONIWA_TRUSTED_PROXIES を確かめてください）",
                settings.HAKONIWA_CLIENT_IP_HEADER,
            )
        ip_key = _digest("login-ip", ip)

        posts = settings.HAKONIWA_LOGIN_POSTS_PER_WINDOW
        if posts > 0:
            wait = throttle.consume(
                "login-ip",
                ip_key,
                limit=posts,
                window_seconds=settings.HAKONIWA_LOGIN_POST_WINDOW_SECONDS,
            )
            if wait:
                logger.warning(
                    "ログインの制限: 接続元の送信が多すぎます 接続元=%s 待ち=%d秒 画面=%s",
                    ip_key[:12],
                    wait,
                    request.path,
                )
                return blocked_response(request, wait)

        if settings.HAKONIWA_LOGIN_MAX_FAILURES <= 0:
            return self.get_response(request)

        user_key = _digest("login-user", _normalize(str(request.POST.get("username", ""))))
        wait = reserve(ip_key, user_key)
        if wait:
            logger.info(
                "ログインの制限: 停止中の組み合わせです 接続元=%s アカウント=%s 待ち=%d秒 画面=%s",
                ip_key[:12],
                user_key[:12],
                wait,
                request.path,
            )
            return blocked_response(request, wait)

        response = self.get_response(request)
        if response.status_code == 302 and request.user.is_authenticated:
            settle_success(ip_key, user_key)
        elif response.status_code == 200:  # ログインの画面にもどった = 失敗
            wait = settle_failure(ip_key, user_key)
            if wait:
                logger.warning(
                    "ログインの制限: 失敗が続いたので停止します 接続元=%s アカウント=%s "
                    "回数=%d 停止=%d秒 画面=%s",
                    ip_key[:12],
                    user_key[:12],
                    settings.HAKONIWA_LOGIN_MAX_FAILURES,
                    wait,
                    request.path,
                )
                return blocked_response(request, wait)
        else:
            refund(ip_key, user_key)
        return response
