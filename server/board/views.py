"""掲示板の画面。読むのは誰でも、書くのは登録した人だけ。"""

from __future__ import annotations

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from server.board.models import Post, Thread


class ThreadForm(forms.Form):
    title = forms.CharField(label="題名", max_length=100)
    body = forms.CharField(label="本文", max_length=4000, widget=forms.Textarea(attrs={"rows": 5}))


class PostForm(forms.Form):
    body = forms.CharField(label="本文", max_length=4000, widget=forms.Textarea(attrs={"rows": 4}))


def thread_list(request):
    threads = Thread.objects.filter(hidden=False).select_related("author")
    page = Paginator(threads, 30).get_page(request.GET.get("page"))
    form = ThreadForm() if request.user.is_authenticated else None
    return render(request, "board/list.html", {"page": page, "form": form})


@login_required
def new_thread(request):
    form = ThreadForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            thread = Thread.objects.create(title=form.cleaned_data["title"], author=request.user)
            Post.objects.create(thread=thread, author=request.user, body=form.cleaned_data["body"])
        return redirect("board_thread", thread_id=thread.id)
    return render(request, "board/new.html", {"form": form})


def thread_page(request, thread_id: int):
    thread = get_object_or_404(Thread, pk=thread_id, hidden=False)
    form = PostForm(request.POST or None) if request.user.is_authenticated else None
    if request.method == "POST":
        if form is None:
            return redirect("login")
        if form.is_valid():
            Post.objects.create(thread=thread, author=request.user, body=form.cleaned_data["body"])
            Thread.objects.filter(pk=thread.pk).update(updated_at=timezone.now())
            return redirect("board_thread", thread_id=thread.id)
    posts = thread.posts.filter(hidden=False).select_related("author")
    page = Paginator(posts, 50).get_page(request.GET.get("page"))
    return render(request, "board/thread.html", {"thread": thread, "page": page, "form": form})
