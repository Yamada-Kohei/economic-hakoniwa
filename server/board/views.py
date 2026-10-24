"""掲示板の画面。読むのは誰でも、書くのは登録して少したった人だけ（services.py の歯止め）。"""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from server.board import services
from server.board.models import Board, NgRule
from server.board.render import render_body
from server.letters.services import own_island


class WriteForm(forms.Form):
    """名前・名無しか国名か・sage・本文（スレッドを立てるときは題名も）。"""

    name = forms.CharField(
        label="名前", max_length=60, required=False, widget=forms.TextInput(attrs={"size": 24})
    )
    identity = forms.ChoiceField(
        label="書き方", choices=[("anon", "名無し（名前は自由）")], required=False, initial="anon"
    )
    sage = forms.BooleanField(label="sage（上げない）", required=False)
    body = forms.CharField(
        label="本文",
        max_length=services.BODY_MAX + 500,
        widget=forms.Textarea(attrs={"rows": 5}),
    )

    def __init__(self, *args, user=None, with_title: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("anon", "名無し（名前は自由）")]
        if user is not None and user.is_authenticated:
            if own_island(user) is not None:
                choices.append(("country", "国名で書く"))
            if user.is_staff:
                choices.append(("staff", "運営として書く"))
        self.fields["identity"].choices = choices
        if with_title:
            title = forms.CharField(label="題名", max_length=services.TITLE_MAX)
            self.fields = {"title": title, **self.fields}


def _href_for(thread_id: int):
    base = reverse("board_thread", args=[thread_id])
    return lambda number: f"{base}?p={services.page_of(number)}#p{number}"


def _with_html(posts: list[dict[str, Any]], thread_id: int) -> list[dict[str, Any]]:
    """画面に出す形にする: 本文の HTML（`>>番号` はリンク）と、返信された番号のリンク。"""
    href_for = _href_for(thread_id)
    for post in posts:
        post["html"] = (
            render_body(post["body"], thread_id, href_for) if post["body"] is not None else ""
        )
        post["thread_id"] = thread_id
        post["replies"] = [{"number": n, "href": href_for(n)} for n in post["replied_by"]]
    return posts


def _writer_args(form: forms.Form) -> dict[str, Any]:
    data = form.cleaned_data
    return {
        "name": data.get("name") or "",
        "identity": data.get("identity") or "anon",
        "sage": bool(data.get("sage")),
    }


def board_index(request):
    """板の一覧と、最近書き込みがあったスレッド。"""
    context = {
        "boards": services.board_summaries(),
        "recent": services.recent_threads(),
        "unread": services.unread_count(request.user),
    }
    return render(request, "board/index.html", context)


def board_forum(request, slug: str):
    """板のスレッドの一覧（固定が先、上げた順）。ログインした人にはスレッドを立てる欄も。"""
    board = get_object_or_404(Board, slug=slug)
    form = None
    if request.user.is_authenticated:
        form = WriteForm(request.POST or None, user=request.user, with_title=True)
        if request.method == "POST" and form.is_valid():
            data = form.cleaned_data
            try:
                thread = services.start_thread(
                    request.user, board, data["title"], data["body"], **_writer_args(form)
                )
            except services.BoardError as e:
                form.add_error(None, e.text)
            else:
                return redirect("board_thread", thread_id=thread.id)
    elif request.method == "POST":
        return redirect("login")
    context = {
        "board": board,
        "threads": services.board_threads(board),
        "form": form,
        "can_write": not board.staff_only or request.user.is_staff,
    }
    return render(request, "board/forum.html", context)


def board_log(request, slug: str):
    """板の過去ログ（スレッドの上限を超えて落ちたスレッド。読めるが書けない）。"""
    board = get_object_or_404(Board, slug=slug)
    rows = services.board_threads(board, archived=True)
    page = Paginator(rows, services.THREAD_LOG_PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "board/log.html", {"board": board, "page": page})


def board_thread(request, thread_id: int):
    """スレッド。?p=ページ・?latest=50（最後の 50 件）・?from=3&to=20（番号の範囲）。"""
    thread = services.visible_thread(thread_id)
    if thread is None:
        raise Http404
    form = None
    if request.user.is_authenticated:
        form = WriteForm(request.POST or None, user=request.user)
        if request.method == "POST" and form.is_valid():
            try:
                services.add_post(
                    thread, request.user, form.cleaned_data["body"], **_writer_args(form)
                )
            except services.BoardError as e:
                form.add_error(None, e.text)
            else:
                return redirect(f"{request.path}?p=last#bottom")
    elif request.method == "POST":
        return redirect("login")
    query = request.GET
    page = query.get("p")
    read = services.read_posts(
        thread,
        request.user,
        page=page,
        latest=int(query["latest"]) if query.get("latest", "").isdigit() else None,
        start=int(query["from"]) if query.get("from", "").isdigit() else None,
        end=int(query["to"]) if query.get("to", "").isdigit() else None,
    )
    can_write = not (thread.closed or thread.archived) and (
        not thread.board.staff_only or request.user.is_staff
    )
    context = {
        "thread": thread,
        "view": services.thread_view(thread),
        "posts": _with_html(read["posts"], thread.id),
        "page": read["page"],
        "pages": range(1, read["pages"] + 1),
        "num_pages": read["pages"],
        "ranged": bool(query.get("latest") or query.get("from")),
        "form": form,
        "can_write": can_write,
        "limit_left": services.THREAD_POST_LIMIT - thread.post_count,
    }
    return render(request, "board/thread.html", context)


def board_thread_old_url(request, thread_id: int):
    """以前の /board/<番号>/ の住所。"""
    return redirect("board_thread", thread_id=thread_id, permanent=True)


def board_search(request):
    """題名と本文の検索（過去ログも）。"""
    query = request.GET.get("q", "").strip()
    slug = request.GET.get("board", "")
    board = Board.objects.filter(slug=slug).first() if slug else None
    page = None
    if query:
        rows = services.search(query, board)
        page = Paginator(rows, services.SEARCH_PAGE_SIZE).get_page(request.GET.get("page"))
        ng = services.NgFilter.of(request.user)
        page.hits = [
            _with_html([services.post_view(p, ng=ng)], p.thread_id)[0]
            | {"thread": p.thread, "href": _href_for(p.thread_id)(p.number)}
            for p in page.object_list
        ]
    context = {"q": query, "board": board, "boards": services.boards(), "page": page}
    return render(request, "board/search.html", context)


@login_required
def board_notices(request):
    """自分の書き込みへの返信の知らせ。開くと既読になる（開く前の未読は印をつけて見せる）。"""
    rows = services.notices(request.user, mark=True)
    for row in rows:
        _with_html([row["post"]], row["thread"]["id"])
    return render(request, "board/notices.html", {"notices": rows})


@login_required
def board_ng(request):
    """NG の設定（人・ID・言葉）。言葉と ID はここで足し、人は書き込みの「NG」から足す。"""
    error = ""
    if request.method == "POST":
        if request.POST.get("delete", "").isdigit():
            services.delete_ng(request.user, int(request.POST["delete"]))
        else:
            kind = request.POST.get("kind", "")
            if kind not in (NgRule.WORD, NgRule.IDENT):
                error = "種類を選んでください"
            else:
                try:
                    services.add_ng(request.user, kind, word=request.POST.get("word", ""))
                except services.BoardError as e:
                    error = e.text
        if not error:
            return redirect("board_ng")
    return render(
        request, "board/ng.html", {"rules": services.ng_rules(request.user), "error": error}
    )
