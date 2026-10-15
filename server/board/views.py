"""掲示板の画面。読むのは誰でも、書くのは登録した人だけ。"""

from __future__ import annotations

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from server.board.models import Thread
from server.board.services import add_post, start_thread

THREADS_PER_PAGE = 30
POSTS_PER_PAGE = 50


class ThreadForm(forms.Form):
    title = forms.CharField(label="題名", max_length=100)
    body = forms.CharField(label="本文", max_length=4000, widget=forms.Textarea(attrs={"rows": 5}))


class PostForm(forms.Form):
    body = forms.CharField(label="本文", max_length=4000, widget=forms.Textarea(attrs={"rows": 4}))


def thread_list(request):
    threads = Thread.objects.filter(hidden=False).select_related("author")
    page = Paginator(threads, THREADS_PER_PAGE).get_page(request.GET.get("page"))
    form = ThreadForm() if request.user.is_authenticated else None
    return render(request, "board/list.html", {"page": page, "form": form})


@login_required
def new_thread(request):
    form = ThreadForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        thread = start_thread(request.user, form.cleaned_data["title"], form.cleaned_data["body"])
        return redirect("board_thread", thread_id=thread.id)
    return render(request, "board/new.html", {"form": form})


def thread_page(request, thread_id: int):
    thread = get_object_or_404(Thread, pk=thread_id, hidden=False)
    form = PostForm(request.POST or None) if request.user.is_authenticated else None
    if request.method == "POST":
        if form is None:
            return redirect("login")
        if form.is_valid():
            add_post(thread, request.user, form.cleaned_data["body"])
            return redirect("board_thread", thread_id=thread.id)
    posts = thread.posts.filter(hidden=False).select_related("author")
    page = Paginator(posts, POSTS_PER_PAGE).get_page(request.GET.get("page"))
    return render(request, "board/thread.html", {"thread": thread, "page": page, "form": form})
