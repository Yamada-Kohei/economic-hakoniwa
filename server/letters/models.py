"""国どうしの手紙（非公開）。管理者は不適切な手紙を非表示にできる。"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from server.world.models import Island


class Letter(models.Model):
    """島から島への手紙。送るとすぐ届く（ターンを待たない）。

    読めるのは差出人の島と宛先の島の持ち主だけ。書いた人（author）も残すのは、
    島の持ち主が変わっても誰が書いたかが分かるようにするため。
    """

    sender = models.ForeignKey(
        Island, verbose_name="送った島", on_delete=models.CASCADE, related_name="letters_sent"
    )
    recipient = models.ForeignKey(
        Island,
        verbose_name="宛先の島",
        on_delete=models.CASCADE,
        related_name="letters_received",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="書いた人",
        on_delete=models.SET_NULL,
        null=True,
        related_name="letters",
    )
    subject = models.CharField("題名", max_length=100)
    body = models.TextField("本文", max_length=4000)
    turn = models.IntegerField("送ったターン")
    sent_at = models.DateTimeField("送った時刻", auto_now_add=True)
    read_at = models.DateTimeField("読んだ時刻", null=True, blank=True)
    hidden = models.BooleanField("非表示", default=False)

    class Meta:
        verbose_name = verbose_name_plural = "手紙"
        ordering = ["-sent_at", "-id"]

    def __str__(self) -> str:
        return f"{self.sender} → {self.recipient}: {self.subject}"
