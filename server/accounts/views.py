"""登録とログイン。

同じ IP からの重複登録は game.yaml の accounts の設定に従って止めます。IP はそのまま残さず、
サーバーの秘密鍵で混ぜたハッシュにして、remember_days 日だけ持ちます（管理画面で消せます）。
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from server.accounts.forms import SignupForm
from server.accounts.models import SignupRecord
from server.game import get_game


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
