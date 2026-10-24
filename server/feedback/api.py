"""不具合の報告の JSON API。送れるのはログインした人（画面と AI の入り口で同じ）。

POST /api/bug-reports  {"text", "kind"?, "url"?, "client"?}  → 201 {"id": 報告番号, ...}
GET  /api/bug-reports  自分が送った報告の一覧（状態つき）
書き込み（POST）には、ほかの API と同じく Django の CSRF の印が要る（AI 用の鍵は要らない）。
"""

from __future__ import annotations

import json
from typing import Any

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from server.feedback import services
from server.feedback.models import BugReport

JSON = {"json_dumps_params": {"ensure_ascii": False}}
LIST_MAX = 50


def _json(data: Any, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status=status, **JSON)


def _error(text: str, status: int) -> JsonResponse:
    return _json({"error": text}, status=status)


@require_http_methods(["GET", "POST"])
def bug_reports(request):
    if not request.user.is_authenticated:
        return _error("ログインしてください", 403)
    if request.method == "GET":
        rows = BugReport.objects.filter(user=request.user)[:LIST_MAX]
        return _json({"reports": [services.view(r) for r in rows]})
    try:
        data = json.loads(request.body or b"{}")
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return _error("JSON で送ってください", 400)
    text, kind, url = data.get("text", ""), data.get("kind", ""), data.get("url", "")
    if not all(isinstance(v, str) for v in (text, kind, url)):
        return _error("text・kind・url は文字列で送ってください", 400)
    via = str(data.get("via") or ("api" if getattr(request, "api_key", None) else "web"))
    try:
        report = services.submit(request.user, text, kind, url, data.get("client"), via=via)
    except services.ReportError as e:
        return _error(e.text, e.status)
    return _json(
        {
            "id": report.pk,
            "message": f"ありがとうございます。報告番号は {report.pk} です",
            "version": report.version,
        },
        status=201,
    )
