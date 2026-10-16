"""登録とログインと、AI 用の鍵の画面。

同じ IP からの重複登録は game.yaml の accounts の設定に従って止めます。IP はそのまま残さず、
サーバーの秘密鍵で混ぜたハッシュにして、remember_days 日だけ持ちます（管理画面で消せます）。
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from server.accounts import keys
from server.accounts.forms import SignupForm
from server.accounts.models import ApiKey, SignupRecord
from server.game import get_game
from server.world.models import Island


def client_ip(request) -> str:
    """利用者の IP。リバースプロキシの後ろでは HAKONIWA_CLIENT_IP_HEADER で見る場所を変える。"""
    value = request.META.get(settings.HAKONIWA_CLIENT_IP_HEADER, "") or ""
    return value.split(",")[0].strip()


def ip_hash(ip: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), ip.encode(), hashlib.sha256).hexdigest()


def signup_blocked(request) -> bool:
    config = get_game().config
    limit = config.get("accounts.signups_per_ip")
    if limit <= 0:
        return False
    since = timezone.now() - timedelta(days=config.get("accounts.remember_days"))
    SignupRecord.objects.filter(created_at__lt=since).delete()
    return SignupRecord.objects.filter(ip_hash=ip_hash(client_ip(request))).count() >= limit


def signup(request):
    blocked = signup_blocked(request)
    form = SignupForm(request.POST or None)
    if request.method == "POST" and not blocked and form.is_valid():
        with transaction.atomic():
            user = form.save()
            SignupRecord.objects.create(ip_hash=ip_hash(client_ip(request)))
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
