"""国の公開ページの書き込みの JSON API。読むのは GET /api/islands/<id>/country
（server/web/api.py）。

書けるのは、紹介文・紀年・国旗は国の持ち主だけ、一言は自分の国を持つ人（ほかの国のページに）だけ。
書き込みには、ほかの API と同じく Django の CSRF の印（AI 用の鍵なら要らない）が要る。
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from server import jsonbody
from server.country import services
from server.web import country as country_views
from server.web import monuments as monuments_web
from server.world.models import Island

JSON = {"json_dumps_params": {"ensure_ascii": False}}


def _json(data: Any, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status=status, **JSON)


def _error(text: str, status: int) -> JsonResponse:
    return _json({"error": text}, status=status)


def _body(request) -> dict[str, Any]:
    try:
        data = jsonbody.loads(request.body or b"{}")
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise services.CountryError("JSON で送ってください")
    return data


def _owned(request, island_id: int) -> Island:
    island = get_object_or_404(Island.objects.select_related("world"), pk=island_id)
    if not request.user.is_authenticated:
        raise services.CountryError("ログインしてください", 403)
    if island.owner_id != request.user.id:
        raise services.CountryError("この国の持ち主だけが書き換えられます", 403)
    return island


@require_http_methods(["PUT"])
def edit(request, island_id: int):
    """紹介文と紀年を書き換える。{"intro"?: 文, "new_era"?: 元号（改元は今のターンから）,
    "era"?: {"kind": world|founding|regnal, "name"?: 元号}}。送った項目だけ書き換わる。"""
    try:
        island = _owned(request, island_id)
        data = _body(request)
        unknown = set(data) - {"intro", "era", "new_era"}
        if unknown or not data:
            raise services.CountryError("intro・era・new_era のどれかを送ってください")
        services.edit(island, data)
    except services.CountryError as e:
        return _error(e.text, e.status)
    return _json(country_views.country_data(island, True, island))


@require_http_methods(["PUT", "DELETE"])
def flag(request, island_id: int):
    """国旗を上げる（PUT。{"png_base64": …}、または本体が PNG で Content-Type が image/png）。
    外す（DELETE）。"""
    try:
        island = _owned(request, island_id)
        if request.method == "DELETE":
            services.clear_flag(island)
        else:
            services.set_flag(island, _png_of(request))
    except services.CountryError as e:
        return _error(e.text, e.status)
    return _json({"flag": country_views.flag_info(island, True)})


@require_http_methods(["PUT", "DELETE"])
def monument_image(request, island_id: int, number: int):
    """自作の記念碑の絵を上げる・外す（国旗と同じ。PUT は png_base64 か image/png）。"""
    try:
        island = _owned(request, island_id)
        if request.method == "DELETE":
            services.clear_monument_image(island, number)
        else:
            services.set_monument_image(island, number, _png_of(request))
    except services.CountryError as e:
        return _error(e.text, e.status)
    return _json({"monuments": monuments_web.listing(island)})


def _png_of(request) -> bytes:
    limit = settings.HAKONIWA_FLAG_MAX_KB * 1024
    if request.content_type == "image/png":
        return request.body
    text = _body(request).get("png_base64")
    if not isinstance(text, str):
        raise services.CountryError("png_base64 に PNG を base64 で入れて送ってください")
    if len(text) > limit * 4 // 3 + 16:
        raise services.CountryError(f"国旗の PNG は {limit // 1024} KB までです")
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as e:
        raise services.CountryError("png_base64 が base64 ではありません") from e


@require_http_methods(["DELETE"])
def note_hide(request, island_id: int, note_id: int):
    """自分の国のページの一言を隠す（持ち主だけ）。"""
    try:
        services.hide_note(_owned(request, island_id), note_id)
    except services.CountryError as e:
        return _error(e.text, e.status)
    return _json({"hidden": note_id})


@require_http_methods(["POST"])
def note(request, island_id: int):
    """ほかの国のページに一言を残す（{"text"}）。"""
    island = get_object_or_404(Island.objects.select_related("world"), pk=island_id)
    if not request.user.is_authenticated:
        return _error("ログインしてください", 403)
    try:
        n = services.post_note(request.user, island, _body(request).get("text"))
    except services.CountryError as e:
        return _error(e.text, e.status)
    return _json({"id": n.id, "turn": n.turn, "text": n.text}, status=201)


@require_http_methods(["GET", "PUT"])
def diary(request, island_id: int):
    """国の日誌（持ち主だけ）。GET は {"text", "turn", "updated_at"}、PUT {"text"} は全文を
    置き換える。"""
    try:
        island = _owned(request, island_id)
        if request.method == "PUT":
            services.set_diary(island, _body(request).get("text"))
    except services.CountryError as e:
        if e.text == "この国の持ち主だけが書き換えられます":
            return _error("国の日誌は持ち主だけが読めます", 403)
        return _error(e.text, e.status)
    return _json({"island": island.id, **services.diary_data(island)})
