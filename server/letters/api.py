"""手紙の JSON API。画面と同じ決まり（services.py）で動き、試し遊びの道具
（tools/playtest/hk.py）も使う。

ログインしていなければ 403、自分の島がなければ 403、読めない手紙は 404。
書き込み（POST）には、ほかの API と同じく Django の CSRF の印が要る。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from django.core.paginator import Page, Paginator
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from server.letters import services
from server.letters.models import Letter
from server.world.models import Island

JSON = {"json_dumps_params": {"ensure_ascii": False}}


def _json(data: Any, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status=status, **JSON)


def _error(text: str, status: int) -> JsonResponse:
    return _json({"error": text}, status=status)


def _time(value: datetime | None) -> str | None:
    return timezone.localtime(value).isoformat(timespec="seconds") if value else None


def _page_info(page: Page) -> dict[str, int]:
    return {"page": page.number, "pages": page.paginator.num_pages, "count": page.paginator.count}


def _island(island: Island) -> dict[str, Any]:
    return {"id": island.id, "name": island.name}


def _letter_json(letter: Letter, body: bool = False) -> dict[str, Any]:
    """手紙1通。一覧では本文を省く。"""
    data = {
        "id": letter.id,
        "from": _island(letter.sender),
        "to": _island(letter.recipient),
        "author": letter.author.username if letter.author else "",
        "subject": letter.subject,
        "turn": letter.turn,
        "sent_at": _time(letter.sent_at),
        "read": letter.read_at is not None,
        "read_at": _time(letter.read_at),
    }
    if body:
        data["body"] = letter.body
    return data


@require_http_methods(["GET", "POST"])
def letters(request):
    """GET: 自分の島の手紙の一覧（?box=inbox|sent&page=）。新しい順。

    POST: 自分の島から送る（{"to": 島の番号, "subject", "body"}）。
    """
    if not request.user.is_authenticated:
        return _error("ログインしてください", 403)
    island = services.own_island(request.user)
    if island is None:
        return _error("手紙は自分の島を持つ人だけが使えます", 403)
    if request.method == "POST":
        return _send(request, island)
    box = request.GET.get("box") or "inbox"
    if box not in ("inbox", "sent"):
        return _error("box は inbox か sent です", 400)
    rows = services.inbox(island) if box == "inbox" else services.sent_box(island)
    page = Paginator(rows, services.PAGE_SIZE).get_page(request.GET.get("page"))
    return _json(
        {
            "island": _island(island),
            "box": box,
            "unread": services.unread_count(island),
            **_page_info(page),
            "letters": [_letter_json(letter) for letter in page],
        }
    )


def _send(request, island: Island) -> JsonResponse:
    try:
        data = json.loads(request.body or b"{}")
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return _error("JSON で送ってください", 400)
    to, subject, body = data.get("to"), data.get("subject", ""), data.get("body", "")
    if isinstance(to, str) and to.strip().isdigit():
        to = int(to)
    if not isinstance(to, int) or isinstance(to, bool):
        return _error("to に宛先の島の番号を入れてください", 400)
    if not isinstance(subject, str) or not isinstance(body, str):
        return _error("subject と body は文字列で送ってください", 400)
    try:
        letter = services.send_letter(request.user, island, to, subject, body)
    except services.LetterError as e:
        return _error(e.text, e.status)
    return _json(_letter_json(letter, body=True), status=201)


@require_GET
def letter(request, letter_id: int):
    """手紙1通（本文つき）。宛先の島の持ち主が読んだら既読にする。"""
    if not request.user.is_authenticated:
        return _error("ログインしてください", 403)
    found = services.visible_letter(request.user, letter_id)
    if found is None:
        return _error("手紙が見つかりません", 404)
    services.open_letter(request.user, found)
    return _json(_letter_json(found, body=True))
