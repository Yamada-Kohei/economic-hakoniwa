"""遊ぶ人が画面から送る不具合の報告。ゲームの計算には関わらない。

書いてもらうのは「何が起きたか」の 1 欄だけ（種類は任意）。世界・島・ターン・開いていた画面の URL・
ブラウザと画面の大きさ・ゲームの版・直前に出した命令・画面で起きた JavaScript のエラーは、
送るときに自動で添える（services.py）。管理画面で一覧・状態・メモを扱う。
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from server.world.models import Island


class BugReport(models.Model):
    STATUS_CHOICES = [("new", "未読"), ("working", "対応中"), ("done", "済み")]
    KIND_CHOICES = [
        ("bug", "動かない・おかしい"),
        ("display", "表示・使いにくさ"),
        ("rule", "ゲームの数字・仕組み"),
        ("other", "そのほか"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="報告した人",
        on_delete=models.SET_NULL,
        null=True,
        related_name="bug_reports",
    )
    island = models.ForeignKey(
        Island,
        verbose_name="島",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bug_reports",
    )
    world_turn = models.IntegerField("ターン", null=True, blank=True)
    kind = models.CharField("種類", max_length=16, choices=KIND_CHOICES, blank=True, default="")
    text = models.TextField("何が起きたか", max_length=2000)
    status = models.CharField("状態", max_length=16, choices=STATUS_CHOICES, default="new")
    note = models.TextField("メモ（管理者だけが読む）", blank=True, default="")
    url = models.CharField("開いていた画面", max_length=300, blank=True, default="")
    version = models.CharField("ゲームの版", max_length=40, blank=True, default="")
    via = models.CharField("送った入り口", max_length=8, default="web")
    context = models.JSONField("添えた情報", default=dict, blank=True)
    created_at = models.DateTimeField("送った時刻", auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = "不具合の報告"
        ordering = ["-id"]
        indexes = [models.Index(fields=["user", "created_at"])]

    def __str__(self) -> str:
        return f"#{self.pk} {self.text[:30]}"
