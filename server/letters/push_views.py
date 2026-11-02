"""通知の購読（ブラウザのログインのみ）と、ルートで返す PWA のファイル。"""

from __future__ import annotations

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_http_methods

from server import jsonbody
from server.accounts import keys as account_keys
from server.letters import push, services
from server.letters.models import PushSubscription


@require_http_methods(["POST", "DELETE"])
def subscription(request) -> JsonResponse:
    if not request.user.is_authenticated or getattr(request, "api_key", None):
        return JsonResponse({"error": "ブラウザでログインしてください"}, status=403)
    island = services.own_island(request.user)
    if island is None:
        return JsonResponse({"error": "自分の国を持つ人だけが使えます"}, status=403)
    try:
        data = jsonbody.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError("JSON のオブジェクトで送ってください")
        endpoint = push.validate_endpoint(data.get("endpoint"))
        if request.method == "DELETE":
            PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
            return JsonResponse({"ok": True})
        if account_keys.is_ai_island(island):
            return JsonResponse({"error": "AI の国は API の未読数を使ってください"}, status=403)
        if not push.configured():
            return JsonResponse({"error": "ブラウザ通知はまだ設定されていません"}, status=503)
        keys = data.get("keys")
        if not isinstance(keys, dict):
            raise ValueError("通知の鍵を送ってください")
        p256dh = push.validate_key(keys.get("p256dh"), 65)
        auth = push.validate_key(keys.get("auth"), 16)
    except ValueError as exc:
        return JsonResponse({"error": str(exc) or "JSON で送ってください"}, status=400)
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={"user": request.user, "p256dh": p256dh, "auth": auth, "last_outbox_id": 0},
    )
    return JsonResponse({"ok": True})


@require_GET
@cache_control(max_age=60, public=True)
def service_worker(request) -> HttpResponse:
    return render(
        request,
        "letters/sw.js",
        {"inbox_url": reverse("letters")},
        content_type="application/javascript",
    )


@require_GET
@cache_control(max_age=60, public=True)
def manifest(request) -> JsonResponse:
    return JsonResponse(
        {
            "name": "経済版箱庭諸国",
            "short_name": "経済箱庭",
            "start_url": reverse("letters"),
            "display": "standalone",
            "icons": [
                {
                    "src": static(f"img/push/icon-{size}.png"),
                    "sizes": f"{size}x{size}",
                    "type": "image/png",
                }
                for size in (192, 512)
            ],
        },
        content_type="application/manifest+json",
        json_dumps_params={"ensure_ascii": False},
    )
