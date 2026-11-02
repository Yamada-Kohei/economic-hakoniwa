"""Web Push の入力の守りと、任意の送信部品の読み込み。"""

from __future__ import annotations

import base64
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import CommandError

ENDPOINT_MAX = 2048
PUSH_HOSTS = {"fcm.googleapis.com", "web.push.apple.com", "updates.push.services.mozilla.com"}
TOPIC = "hakoniwa-letters"
TTL = 86400
TIMEOUT = 10
MAX_ATTEMPTS = 3


def configured() -> bool:
    return bool(
        settings.HAKONIWA_VAPID_PUBLIC_KEY
        and settings.HAKONIWA_VAPID_PRIVATE_KEY
        and settings.HAKONIWA_VAPID_CONTACT
    )


def validate_endpoint(endpoint: object) -> str:
    """許した中継所だけ。別ポート・認証情報・曖昧な URL も受け付けない。"""
    if (
        not isinstance(endpoint, str)
        or not endpoint
        or len(endpoint) > ENDPOINT_MAX
        or any(ord(c) <= 32 or ord(c) >= 127 for c in endpoint)
        or "\\" in endpoint
    ):
        raise ValueError("通知の宛先が正しくありません")
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ""
        allowed = host in PUSH_HOSTS or (
            host.endswith(".notify.windows.com")
            and re.fullmatch(r"[a-z0-9.-]+", host)
            and all(
                part and not part.startswith("-") and not part.endswith("-")
                for part in host.split(".")
            )
        )
        if (
            url.scheme != "https"
            or not allowed
            or url.port not in (None, 443)
            or url.username is not None
            or url.password is not None
            or url.fragment
        ):
            raise ValueError
    except ValueError:
        raise ValueError("この通知の中継所は使えません") from None
    return endpoint


def validate_key(value: object, size: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("通知の鍵が正しくありません")
    if len(value) > 128:
        raise ValueError("通知の鍵が長すぎます")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(raw) != size or (size == 65 and raw[0] != 4):
        raise ValueError("通知の鍵が正しくありません")
    return value


def load_sender():
    """起動時には import せず、send_push の実行時だけ必要にする。"""
    try:
        from pywebpush import webpush
        from requests import Session
    except ImportError as exc:
        raise CommandError(
            '通知の送信には pip install ".[push]" で pywebpush を入れてください'
        ) from exc

    class NoRedirectSession(Session):
        def request(self, method, url, **kwargs):
            # 許した中継所から任意の URL へ転送されても、追いかけない。
            kwargs["allow_redirects"] = False
            return super().request(method, url, **kwargs)

    def send(**kwargs):
        with NoRedirectSession() as session:
            response = webpush(**kwargs, requests_session=session)
            if not 200 <= response.status_code < 300:
                error = RuntimeError("通知の中継所への送信に失敗しました")
                error.response = response
                raise error

    return send
