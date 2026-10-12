"""登録の記録。同じ IP からの重複登録を止めるために使う。

IP はそのまま残さず、サーバーの秘密鍵で混ぜたハッシュ（照合用の形）にして、決めた期間だけ持つ。
"""

from __future__ import annotations

from django.db import models


class SignupRecord(models.Model):
    ip_hash = models.CharField("IP のハッシュ", max_length=64, db_index=True)
    created_at = models.DateTimeField("登録した時刻", auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = "登録の記録"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.ip_hash[:12]}… {self.created_at:%Y-%m-%d %H:%M}"
