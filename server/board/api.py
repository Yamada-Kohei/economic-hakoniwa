"""掲示板の JSON API。読むのは誰でも、書くのはログインした人だけ（画面と同じ）。

書き込みとスレッドには、書いた人の名前と、その人の島があれば島の番号と国名を付ける。
非表示のスレッドと書き込みは返さない。書き込み（POST）には Django の CSRF の印が要る。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from django import forms
from django.core.paginator import Page, Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from server.board.models import Post, Thread
from server.board.services import add_post, start_thread
from server.board.views import POSTS_PER_PAGE, THREADS_PER_PAGE, PostForm, ThreadForm
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


def _body(request) -> dict[str, Any] | None:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _form_error(form: forms.Form) -> JsonResponse:
    parts = []
    for name, messages in form.errors.items():
        label = form.fields[name].label if name in form.fields else ""
        text = " ".join(messages)
        parts.append(f"{label}: {text}" if label else text)
    return _error(" / ".join(parts), 400)


def _islands_of(user_ids: set[int]) -> dict[int, dict[str, Any]]:
    """人ごとの島（番号と国名）。島のない人は入らない。"""
    rows = (
        Island.objects.filter(owner_id__in=user_ids).order_by("id").values("owner_id", "id", "name")
    )
    islands: dict[int, dict[str, Any]] = {}
    for row in rows:
        islands.setdefault(row["owner_id"], {"id": row["id"], "name": row["name"]})
    return islands


def _who(author_id: int | None, author, islands: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {
        "author": author.username if author else "",
        "island": islands.get(author_id) if author_id else None,
    }


def _thread_json(thread: Thread, islands: dict[int, dict[str, Any]]) -> dict[str, Any]:
    data = {
        "id": thread.id,
        "title": thread.title,
        **_who(thread.author_id, thread.author, islands),
        "created_at": _time(thread.created_at),
        "updated_at": _time(thread.updated_at),
    }
    if hasattr(thread, "post_count"):
        data["posts"] = thread.post_count
    return data


def _post_json(post: Post, islands: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": post.id,
        **_who(post.author_id, post.author, islands),
        "body": post.body,
        "created_at": _time(post.created_at),
    }


def _visible_thread(thread_id: int) -> Thread | None:
    return Thread.objects.filter(pk=thread_id, hidden=False).select_related("author").first()


@require_http_methods(["GET", "POST"])
def threads(request):
    """GET: スレッドの一覧（最後の書き込みの新しい順。?page=）。

    POST: スレッドを立てる（{"title", "body"}）。
    """
    if request.method == "POST":
        return _new_thread(request)
    rows = (
        Thread.objects.filter(hidden=False)
        .select_related("author")
        .annotate(post_count=Count("posts", filter=Q(posts__hidden=False)))
        .order_by("-updated_at", "-id")  # 集計を付けると Meta の並びが使われないので書く
    )
    page = Paginator(rows, THREADS_PER_PAGE).get_page(request.GET.get("page"))
    islands = _islands_of({t.author_id for t in page if t.author_id})
    return _json({**_page_info(page), "threads": [_thread_json(t, islands) for t in page]})


def _new_thread(request) -> JsonResponse:
    if not request.user.is_authenticated:
        return _error("書き込むにはログインしてください", 403)
    data = _body(request)
    if data is None:
        return _error("JSON で送ってください", 400)
    form = ThreadForm(data)
    if not form.is_valid():
        return _form_error(form)
    thread = start_thread(request.user, form.cleaned_data["title"], form.cleaned_data["body"])
    return _thread_page(_visible_thread(thread.id), "1", status=201)


@require_GET
def thread(request, thread_id: int):
    """スレッドと書き込みの一覧（古い順。?page=、page=last で最後のページ）。"""
    found = _visible_thread(thread_id)
    if found is None:
        return _error("スレッドが見つかりません", 404)
    return _thread_page(found, request.GET.get("page"))


def _thread_page(found: Thread, number: str | None, status: int = 200) -> JsonResponse:
    rows = found.posts.filter(hidden=False).select_related("author")
    paginator = Paginator(rows, POSTS_PER_PAGE)
    page = paginator.get_page(paginator.num_pages if number == "last" else number)
    authors = {p.author_id for p in page if p.author_id}
    if found.author_id:
        authors.add(found.author_id)
    islands = _islands_of(authors)
    return _json(
        {
            "thread": _thread_json(found, islands),
            **_page_info(page),
            "posts": [_post_json(p, islands) for p in page],
        },
        status=status,
    )


@require_http_methods(["POST"])
def posts(request, thread_id: int):
    """スレッドに書き込む（{"body"}）。"""
    if not request.user.is_authenticated:
        return _error("書き込むにはログインしてください", 403)
    found = _visible_thread(thread_id)
    if found is None:
        return _error("スレッドが見つかりません", 404)
    data = _body(request)
    if data is None:
        return _error("JSON で送ってください", 400)
    form = PostForm(data)
    if not form.is_valid():
        return _form_error(form)
    post = add_post(found, request.user, form.cleaned_data["body"])
    islands = _islands_of({request.user.id})
    return _json({"thread": found.id, **_post_json(post, islands)}, status=201)
