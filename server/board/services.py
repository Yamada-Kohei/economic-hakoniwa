"""掲示板に書く（画面と JSON API の両方が使う）。"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from server.board.models import Post, Thread


def start_thread(user, title: str, body: str) -> Thread:
    """スレッドを立て、最初の書き込みを置く。"""
    with transaction.atomic():
        thread = Thread.objects.create(title=title, author=user)
        Post.objects.create(thread=thread, author=user, body=body)
    return thread


def add_post(thread: Thread, user, body: str) -> Post:
    """スレッドに書き込み、スレッドの「最後の書き込み」を今にする。"""
    post = Post.objects.create(thread=thread, author=user, body=body)
    Thread.objects.filter(pk=thread.pk).update(updated_at=timezone.now())
    return post
