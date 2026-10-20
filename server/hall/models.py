"""国際会議場の声明（公開）。管理者は不適切な声明を非表示にできる。

条約の効き始め・宣戦・停戦・講和のような出来事は、この表には持たず、当事国のターンログ（`hall` の
印のついた行）から表示のときに作る（services.py）。巻き戻しでターンログが消えれば、会議場の出来事も
消える。
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from server.world.models import Island, World


class Statement(models.Model):
    """島が国際会議場に出す公開の声明。送るとすぐ載る（ターンを待たない）。"""

    world = models.ForeignKey(
        World, verbose_name="世界", on_delete=models.CASCADE, related_name="statements"
    )
    island = models.ForeignKey(
        Island, verbose_name="出した島", on_delete=models.CASCADE, related_name="statements"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="書いた人",
        on_delete=models.SET_NULL,
        null=True,
        related_name="statements",
    )
    title = models.CharField("題名", max_length=100)
    body = models.TextField("本文", max_length=2000)
    turn = models.IntegerField("出したターン")
    posted_at = models.DateTimeField("出した時刻", auto_now_add=True)
    hidden = models.BooleanField("非表示", default=False)

    class Meta:
        verbose_name = verbose_name_plural = "国際会議場の声明"
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"{self.island}: {self.title}"
