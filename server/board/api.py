"""掲示板の JSON API。読むのは誰でも、書く・いいね・通報・NG・返信の知らせはログインした人だけ
（画面と同じ）。

書き込みには、書いた人のアカウントは出さない。名前（空なら「名無し」）・トリップ・ID（板ごと・日ごと
に変わる）と、国名で書いたときの島の番号と国名だけを付ける。非表示の書き込みは番号を残して本文を
null にする（あぼーん）。NG に当たった書き込みは本文を null にして ng を true にする（`?ng=show` で
本文も出す）。POST・DELETE には Django の CSRF の印が要る（AI 用の鍵なら要らない）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.core.paginator import Page, Paginator
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from server import jsonbody
from server.board import services
from server.board.models import NgRule, Post, Thread

JSON = {"json_dumps_params": {"ensure_ascii": False}}
DEFAULT_BOARD = "chat"


def _json(data: Any, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status=status, **JSON)


def _error(text: str, status: int) -> JsonResponse:
    return _json({"error": text}, status=status)


def _time(value: datetime | None) -> str | None:
    return timezone.localtime(value).isoformat(timespec="seconds") if value else None


def _page_info(page: Page) -> dict[str, int]:
    return {"page": page.number, "pages": page.paginator.num_pages, "count": page.paginator.count}


def _body(request) -> dict[str, Any]:
    try:
        data = jsonbody.loads(request.body or b"{}")
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise services.BoardError("JSON で送ってください")
    return data


def _text(data: dict[str, Any], key: str, default: str = "") -> str:
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise services.BoardError(f"{key} は文字で送ってください")
    return value


def _thread_json(thread: Thread) -> dict[str, Any]:
    data = services.thread_view(thread)
    for key in ("created_at", "updated_at", "bumped_at"):
        data[key] = _time(data[key])
    return data


def _post_json(post: dict[str, Any], show_ng: bool = False) -> dict[str, Any]:
    data = {**post, "created_at": _time(post["created_at"])}
    if data["ng"] and not show_ng:
        data["body"] = None
    return data


def _visible_thread(thread_id: int) -> Thread | None:
    return services.visible_thread(thread_id)


def _error_from(e: services.BoardError) -> JsonResponse:
    return _error(e.text, e.status)


@require_GET
def boards(request):
    """板の一覧。"""
    rows = services.board_summaries()
    for row in rows:
        row["updated_at"] = _time(row["updated_at"])
    return _json({"boards": rows})


@require_http_methods(["GET", "POST"])
def threads(request):
    """GET: スレッドの一覧（?board=板の名前、?archived=1 で過去ログ、?page=）。固定、上げた順。

    POST: スレッドを立てる（{"board"?: 板の名前（既定は chat）, "title", "body", "name"?,
    "identity"?: anon|country|staff, "sage"?}）。
    """
    if request.method == "POST":
        return _new_thread(request)
    board = None
    if request.GET.get("board"):
        board = services.get_board(request.GET["board"])
        if board is None:
            return _error("板が見つかりません", 404)
    archived = request.GET.get("archived") in ("1", "true")
    size = services.THREAD_LOG_PAGE_SIZE if archived else services.ACTIVE_THREADS_MAX
    page = Paginator(services.board_threads(board, archived=archived), size).get_page(
        request.GET.get("page")
    )
    return _json({**_page_info(page), "threads": [_thread_json(t) for t in page]})


def _new_thread(request) -> JsonResponse:
    if not request.user.is_authenticated:
        return _error("書き込むにはログインしてください", 403)
    try:
        data = _body(request)
        board = services.get_board(_text(data, "board", DEFAULT_BOARD) or DEFAULT_BOARD)
        if board is None:
            return _error("板が見つかりません", 404)
        thread = services.start_thread(
            request.user,
            board,
            _text(data, "title"),
            _text(data, "body"),
            name=_text(data, "name"),
            identity=_text(data, "identity", "anon") or "anon",
            sage=bool(data.get("sage")),
        )
    except services.BoardError as e:
        return _error_from(e)
    return _thread_page(request, thread, status=201)


@require_GET
def thread(request, thread_id: int):
    """スレッドと書き込み（番号順）。?page=（last で最後）・?latest=N（最後の N 件）・
    ?from=&to=（番号の範囲）。"""
    found = _visible_thread(thread_id)
    if found is None:
        return _error("スレッドが見つかりません", 404)
    return _thread_page(request, found)


def _int(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def _thread_page(request, found: Thread, status: int = 200) -> JsonResponse:
    query = request.GET
    read = services.read_posts(
        found,
        request.user,
        page=query.get("page"),
        latest=_int(query.get("latest")),
        start=_int(query.get("from")),
        end=_int(query.get("to")),
    )
    show_ng = query.get("ng") == "show"
    return _json(
        {
            "thread": _thread_json(found),
            "page": read["page"],
            "pages": read["pages"],
            "count": read["count"],
            "posts": [_post_json(p, show_ng) for p in read["posts"]],
        },
        status=status,
    )


@require_http_methods(["POST"])
def posts(request, thread_id: int):
    """スレッドに書き込む（{"body", "name"?, "identity"?: anon|country|staff, "sage"?}）。"""
    if not request.user.is_authenticated:
        return _error("書き込むにはログインしてください", 403)
    found = _visible_thread(thread_id)
    if found is None:
        return _error("スレッドが見つかりません", 404)
    try:
        data = _body(request)
        post = services.add_post(
            found,
            request.user,
            _text(data, "body"),
            name=_text(data, "name"),
            identity=_text(data, "identity", "anon") or "anon",
            sage=bool(data.get("sage")),
        )
    except services.BoardError as e:
        return _error_from(e)
    shown = services.one_post(found, post.number, request.user)
    return _json({"thread": found.id, **_post_json(shown, True)}, status=201)


@require_GET
def one_post(request, thread_id: int, number: int):
    """書き込み 1 件（`>>番号` の浮き出しに使う）。"""
    found = _visible_thread(thread_id)
    shown = services.one_post(found, number, request.user) if found else None
    if shown is None:
        return _error("書き込みが見つかりません", 404)
    return _json({"thread": found.id, **_post_json(shown, request.GET.get("ng") == "show")})


def _post_of(thread_id: int, number: int) -> tuple[Thread, Post]:
    found = _visible_thread(thread_id)
    if found is None:
        raise services.BoardError("スレッドが見つかりません", 404)
    return found, services.find_post(found, number)


@require_http_methods(["POST"])
def like(request, thread_id: int, number: int):
    """いいねを付ける・外す（付いていれば外れる）。自分の書き込みには付けられない。"""
    try:
        _, post = _post_of(thread_id, number)
        return _json(services.toggle_like(request.user, post))
    except services.BoardError as e:
        return _error_from(e)


@require_http_methods(["POST"])
def report(request, thread_id: int, number: int):
    """荒らし・不適切な書き込みを管理者へ通報する（{"reason"?}）。ゲームの不具合の報告ではない。"""
    try:
        _, post = _post_of(thread_id, number)
        data = _body(request)
        found = services.report_post(request.user, post, _text(data, "reason"))
    except services.BoardError as e:
        return _error_from(e)
    return _json({"report": found.id, "number": number}, status=201)


@require_http_methods(["GET", "POST"])
def notices(request):
    """自分の書き込みへの返信（新しい順）。GET は読むだけ、POST は見えたぶんを既読にして返す
    （ほかのサイトから GET で踏まれて既読にならないように）。"""
    if not request.user.is_authenticated:
        return _error("ログインしてください", 403)
    rows = services.notices(request.user, mark=request.method == "POST")
    unread = services.unread_count(request.user)
    for row in rows:
        row["post"] = _post_json(row["post"])
    return _json({"unread": unread, "notices": rows})


@require_http_methods(["GET", "POST"])
def ng(request):
    """GET: 自分の NG の設定。POST: 足す（{"kind": word|ident|person, "word"?, "thread"?,
    "number"?}。人（person）は書き込みを指して足し、誰かは返さない。ID は書き込みを指すか、
    ID の文字を直接）。"""
    if not request.user.is_authenticated:
        return _error("ログインしてください", 403)
    if request.method == "GET":
        return _json({"rules": services.ng_rules(request.user)})
    try:
        data = _body(request)
        kind = _text(data, "kind")
        if kind not in (NgRule.PERSON, NgRule.IDENT, NgRule.WORD):
            return _error("kind は word・ident・person のどれかです", 400)
        target_thread = None
        number = data.get("number")
        if data.get("thread") is not None:
            target_thread = _visible_thread(int(data["thread"]))
            if target_thread is None:
                return _error("スレッドが見つかりません", 404)
        rule = services.add_ng(
            request.user,
            kind,
            word=_text(data, "word"),
            thread=target_thread,
            number=int(number) if number is not None else None,
        )
    except (TypeError, ValueError):
        return _error("thread と number は数で送ってください", 400)
    except services.BoardError as e:
        return _error_from(e)
    return _json(
        {"id": rule.id, "kind": rule.kind, "word": rule.word, "label": rule.label}, status=201
    )


@require_http_methods(["DELETE"])
def ng_delete(request, rule_id: int):
    if not request.user.is_authenticated:
        return _error("ログインしてください", 403)
    if not services.delete_ng(request.user, rule_id):
        return _error("その NG はありません", 404)
    return _json({"deleted": rule_id})


@require_GET
def search(request):
    """題名か本文に言葉が入っている書き込み（?q=空白で分けた言葉・?board=・?page=）。過去ログも探す。"""
    query = request.GET.get("q", "").strip()
    if not query:
        return _error("q（探す言葉）を指定してください", 400)
    board = None
    if request.GET.get("board"):
        board = services.get_board(request.GET["board"])
        if board is None:
            return _error("板が見つかりません", 404)
    rows = services.search(query, board)
    page = Paginator(rows, services.SEARCH_PAGE_SIZE).get_page(request.GET.get("page"))
    ng_filter = services.NgFilter.of(request.user)
    hits = []
    for post in page:
        shown = _post_json(services.post_view(post, ng=ng_filter))
        hits.append({"thread": {"id": post.thread_id, "title": post.thread.title}, **shown})
    return _json({**_page_info(page), "query": query, "posts": hits})
