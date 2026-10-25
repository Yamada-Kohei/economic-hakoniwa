"""登録とログインと、AI 用の鍵の画面。

同じ IP からの重複登録は game.yaml の accounts の設定に従って止めます。IP はそのまま残さず、
サーバーの秘密鍵で混ぜたハッシュにして、remember_days 日だけ持ちます（管理画面で消せます）。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from server.accounts import keys
from server.accounts.clientip import client_ip
from server.accounts.forms import SignupForm
from server.accounts.models import ApiKey, SignupRecord
from server.game import get_game
from server.world.models import Island

logger = logging.getLogger(__name__)


def ip_hash(ip: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), ip.encode(), hashlib.sha256).hexdigest()


def signup_blocked(request) -> bool:
    """この接続元から登録できる数に達しているか。接続元が分からないときは止めない（全員を同じ
    接続元として数えて、誰も登録できなくなるのを避ける。読む場所の設定違いはログに残す）。"""
    config = get_game().config
    limit = config.get("accounts.signups_per_ip")
    if limit <= 0:
        return False
    ip = client_ip(request)
    if not ip:
        logger.warning(
            "接続元の IP が読めないので、登録の数を数えません（HAKONIWA_CLIENT_IP_HEADER=%s）",
            settings.HAKONIWA_CLIENT_IP_HEADER,
        )
        return False
    mine = SignupRecord.objects.filter(ip_hash=ip_hash(ip))
    allowed = limit + (mine.aggregate(total=Sum("extra"))["total"] or 0)  # 管理者が認めた分
    return mine.filter(created_at__gte=_since()).count() >= allowed


def _since():
    return timezone.now() - timedelta(days=get_game().config.get("accounts.remember_days"))


def signup(request):
    if request.method == "POST":  # 古い記録は、登録を受けるときにまとめて消す
        SignupRecord.objects.filter(created_at__lt=_since(), extra=0).delete()
    blocked = signup_blocked(request)
    form = SignupForm(request.POST or None)
    if request.method == "POST" and not blocked and form.is_valid():
        with transaction.atomic():
            user = form.save()
            ip = client_ip(request)
            if ip:
                SignupRecord.objects.create(ip_hash=ip_hash(ip), user=user)
        login(request, user)
        return redirect("found")
    return render(request, "accounts/signup.html", {"form": form, "blocked": blocked})


@login_required
def api_keys(request):
    """AI 用の鍵の画面: 作る・取り消す・一覧（server/accounts/keys.py）。

    鍵そのものは作ったときに1度だけ出す。
    """
    token = None
    error = ""
    if request.method == "POST":
        if request.POST.get("action") == "revoke":
            try:
                keys.revoke(request.user, int(request.POST.get("key", "")))
            except ValueError:
                pass
            return redirect("api_keys")
        if keys.active_keys(request.user).count() >= keys.MAX_KEYS:
            error = (
                f"鍵は {keys.MAX_KEYS} つまでです。使っていない鍵を取り消してから作ってください。"
            )
        else:
            _, token = keys.issue(request.user, request.POST.get("name", ""))
    context = {
        "keys": ApiKey.objects.filter(user=request.user),
        "token": token,
        "error": error,
        "name_max": keys.NAME_MAX,
        "max_keys": keys.MAX_KEYS,
        "mark_turns": keys.MARK_TURNS,
        "calls_per_minute": settings.HAKONIWA_AI_CALLS_PER_MINUTE,
        "base_url": request.build_absolute_uri("/").rstrip("/"),
        "island": Island.objects.filter(owner=request.user).order_by("id").first(),
    }
    return render(request, "accounts/api_keys.html", context)
