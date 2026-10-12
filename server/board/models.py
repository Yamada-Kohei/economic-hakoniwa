"""掲示板: スレッドと書き込み。管理者は不適切な書き込みを非表示にできる。"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class Thread(models.Model):
    title = models.CharField("題名", max_length=100)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="立てた人", on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField("立てた時刻", auto_now_add=True)
    updated_at = models.DateTimeField("最後の書き込み", auto_now_add=True)
    hidden = models.BooleanField("非表示", default=False)

    class Meta:
        verbose_name = verbose_name_plural = "スレッド"
        ordering = ["-updated_at", "-id"]

    def __str__(self) -> str:
        return self.title


class Post(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="posts")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="書いた人", on_delete=models.SET_NULL, null=True
    )
    body = models.TextField("本文", max_length=4000)
    created_at = models.DateTimeField("書いた時刻", auto_now_add=True)
    hidden = models.BooleanField("非表示", default=False)

    class Meta:
        verbose_name = verbose_name_plural = "書き込み"
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.thread} #{self.pk}"
