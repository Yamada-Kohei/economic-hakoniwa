"""AI 用の鍵: 作る・確かめる・取り消すと、鍵での API の呼び出しと、AI の国の印。

AI（Claude や ChatGPT など）には、パスワードの代わりにアカウントの画面（/accounts/keys/）で作った
鍵を渡す。AI は API（/api/...）と MCP のつなぎ先（/mcp）に `Authorization: Bearer <鍵>` を付けて
呼ぶ。鍵で入った呼び出しは、そのアカウントがログインしたのと同じに扱う（見られるもの・出せる命令は
人と同じ決まり）。鍵は画面の cookie と違ってブラウザが勝手に付けないので、CSRF の印は要らない。

- 鍵そのものは作ったときに1度だけ見せ、DB には SHA-256 のハッシュだけを置く（鍵は推測できない長い
  乱数なので、ハッシュから戻せない）。
- 鍵が効くのは /api/ と /mcp だけ。画面・管理画面・ログインには使えない。
- 鍵には、1つの鍵が1分に使える処理の数の上限がある（設定 HAKONIWA_AI_CALLS_PER_MINUTE）。/api/ の
  呼び出しは1回に1、/mcp はまとめ送信に含まれるメッセージの数（1回の送信は HAKONIWA_MCP_MAX_BATCH
  件まで）。サーバーの負担と、人より速く何度も試す有利を抑えるため。数えは DB で、複数のワーカー
  が同じ数を見る（server/accounts/throttle.py）。
- AI の国の印: 取り消していない鍵を持つアカウントの島と、鍵を最後に使ってから MARK_TURNS ターンの
  間の島には、国の一覧と国の画面に「AI」の印を出す。持ち主は消せない（鍵を取り消しても、使った後
  しばらくは残る）。
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone

from server.accounts import throttle
from server.accounts.models import ApiKey

TOKEN_PREFIX = "hk_"
MAX_KEYS = 5  # 1つのアカウントが同時に持てる（取り消していない）鍵の数
MARK_TURNS = 12  # 鍵を取り消しても、最後に使ってからこのターン数の間は AI の国の印を出す（1年）
KEY_PATHS = ("/api/", "/mcp")  # 鍵が効く場所
WINDOW = 60  # 回数を数える区切り（秒）
NAME_MAX = ApiKey._meta.get_field("name").max_length


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def world_turn() -> int:
    from server.world.models import World

    turn = World.objects.order_by("id").values_list("turn", flat=True).first()
    return turn or 0


def active_keys(user) -> Any:
    return ApiKey.objects.filter(user=user, revoked_at__isnull=True)


def issue(user, name: str = "") -> tuple[ApiKey, str]:
    """鍵を作る。返す鍵そのもの（token）は、このときにしか分からない。"""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    key = ApiKey.objects.create(
        user=user,
        name=name.strip()[:NAME_MAX],
        prefix=token[: len(TOKEN_PREFIX) + 6],
        digest=digest(token),
        created_turn=world_turn(),
    )
    return key, token


def revoke(user, key_id: int) -> bool:
    """自分の鍵を取り消す。取り消せたら True。"""
    return bool(
        ApiKey.objects.filter(pk=key_id, user=user, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
    )


def find(token: str) -> ApiKey | None:
    """使える鍵（取り消していない、持ち主のアカウントが有効）。なければ None。"""
    if not token.startswith(TOKEN_PREFIX):
        return None
    key = ApiKey.objects.select_related("user").filter(digest=digest(token)).first()
    if key is None or key.revoked_at is not None or not key.user.is_active:
        return None
    return key


def touch(key: ApiKey) -> None:
    """最後に使った時刻とターンを残す（書き込みは1分に1度か、ターンが変わったときだけ）。"""
    now = timezone.now()
    turn = world_turn()
    if (
        key.last_used_turn == turn
        and key.last_used_at is not None
        and (now - key.last_used_at).total_seconds() < 60
    ):
        return
    key.last_used_at, key.last_used_turn = now, turn
    ApiKey.objects.filter(pk=key.pk).update(last_used_at=now, last_used_turn=turn)


def charge(key: ApiKey, cost: int = 1) -> int:
    """cost 件分の処理を、この鍵の 1 分の枠から使う。使えたら 0。足りなければ何も使わず、次の枠
    までの秒数。上限なし（0）なら常に 0。

    /api/ の呼び出しは 1 回に 1、/mcp は含まれるメッセージの数（server/web/mcp.py）。数えは DB で、
    どのワーカーも同じ回数を見る（server/accounts/throttle.py）。"""
    limit = settings.HAKONIWA_AI_CALLS_PER_MINUTE
    if limit <= 0:
        return 0
    return throttle.consume("ai-key", str(key.pk), limit=limit, window_seconds=WINDOW, cost=cost)


def over_limit_response(wait: int, cost: int = 1) -> JsonResponse:
    limit = settings.HAKONIWA_AI_CALLS_PER_MINUTE
    if cost > limit:
        text = (
            f"一度に {cost} 件の処理は送れません。AI 用の鍵で 1 分に使えるのは {limit} 件までです。"
            "件数を減らして送ってください"
        )
    else:
        text = f"AI 用の鍵で使えるのは 1 分に {limit} 件までです。{wait} 秒ほど待ってください"
    return _error(text, 429, **{"Retry-After": str(wait)})


def ai_owner_ids(turn: int | None = None) -> set[int]:
    """AI の国の印を出すアカウント（取り消していない鍵を持つか、鍵を最近使った）。"""
    turn = world_turn() if turn is None else turn
    recent = Q(revoked_at__isnull=True) | Q(last_used_turn__gte=turn - MARK_TURNS)
    return set(ApiKey.objects.filter(recent).values_list("user_id", flat=True))


def is_ai_island(island) -> bool:
    if island.owner_id is None:
        return False
    return island.owner_id in ai_owner_ids()


def _error(text: str, status: int, **headers: str) -> JsonResponse:
    response = JsonResponse(
        {"error": text}, status=status, json_dumps_params={"ensure_ascii": False}
    )
    for name, value in headers.items():
        response[name] = value
    return response


class KeyMiddleware:
    """/api/ と /mcp で `Authorization: Bearer <鍵>` を読み、鍵のアカウントとして扱う。

    AuthenticationMiddleware の後に置く（request.user を鍵のアカウントに置き換えるため）。
    鍵が正しくなければ 401、呼びすぎなら 429 を返し、ビューには渡さない。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.api_key = None
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if header and request.path.startswith(KEY_PATHS):
            scheme, _, token = header.partition(" ")
            if scheme.lower() != "bearer":
                return _error("Authorization は「Bearer <AI 用の鍵>」の形で送ってください", 401)
            key = find(token.strip())
            if key is None:
                return _error(
                    "AI 用の鍵が正しくないか、取り消されています",
                    401,
                    **{"WWW-Authenticate": 'Bearer realm="hakoniwa"'},
                )
            # POST /mcp は、本文のメッセージの数を見てから数える（server/web/mcp.py の endpoint）
            if not (request.method == "POST" and request.path == "/mcp"):
                wait = charge(key)
                if wait:
                    return over_limit_response(wait)
            touch(key)
            request.user = key.user
            request.api_key = key
            request._dont_enforce_csrf_checks = True  # 鍵はブラウザが勝手に付けない
        return self.get_response(request)
