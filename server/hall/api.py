"""国際会議場の JSON API。読むのは誰でも、書くのはログインして自分の島を持つ人だけ（画面と同じ）。

書き込み（POST）には、ほかの API と同じく Django の CSRF の印が要る。
"""

from __future__ import annotations

import json
from typing import Any

from django.core.paginator import Paginator
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from server.hall import services
from server.turns.services import get_world
from server.web import diplomacy

JSON = {"json_dumps_params": {"ensure_ascii": False}}


def _json(data: Any, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status=status, **JSON)


def _error(text: str, status: int) -> JsonResponse:
    return _json({"error": text}, status=status)


@require_http_methods(["GET", "POST"])
def hall(request):
    """GET: 国際会議場（?page=）。戦争の状態・効いている条約・声明と出来事の新しい順の並び。

    POST: 自分の島から声明を出す（{"title", "body"}）。
    """
    if request.method == "POST":
        return _post(request)
    world = get_world()
    page = Paginator(services.timeline(world), services.PAGE_SIZE).get_page(request.GET.get("page"))
    return _json(
        {
            "turn": world.turn,
            "page": page.number,
            "pages": page.paginator.num_pages,
            "count": page.paginator.count,
            **diplomacy.world_view(world),
            "entries": list(page),
        }
    )


def _post(request) -> JsonResponse:
    if not request.user.is_authenticated:
        return _error("ログインしてください", 403)
    island = services.own_island(request.user)
    if island is None:
        return _error("声明は自分の島を持つ人だけが出せます", 403)
    try:
        data = json.loads(request.body or b"{}")
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return _error("JSON で送ってください", 400)
    title, body = data.get("title", ""), data.get("body", "")
    if not isinstance(title, str) or not isinstance(body, str):
        return _error("title と body は文字列で送ってください", 400)
    try:
        s = services.post_statement(request.user, island, title, body)
    except services.HallError as e:
        return _error(e.text, e.status)
    where = {"id": island.id, "name": island.name}
    return _json({"id": s.id, "turn": s.turn, "island": where, "title": s.title}, status=201)
