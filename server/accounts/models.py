"""アカウントまわりの保存: 登録の記録と、AI 用の鍵。

登録の記録は、同じ IP からの重複登録を止めるために使う。IP はそのまま残さず、サーバーの秘密鍵で
混ぜたハッシュ（照合用の形）にして、決めた期間だけ持つ。
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class SignupRecord(models.Model):
    ip_hash = models.CharField("IP のハッシュ", max_length=64, db_index=True)
    created_at = models.DateTimeField("登録した時刻", auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = "登録の記録"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.ip_hash[:12]}… {self.created_at:%Y-%m-%d %H:%M}"


class ApiKey(models.Model):
    """AI 用の鍵（server/accounts/keys.py）。AI にパスワードを渡さずに、このアカウントとして API と
    MCP を使わせるためのもの。

    鍵そのものは作ったときに1度だけ見せ、DB にはハッシュだけを置く。取り消した鍵は消さずに残す
    （AI の国の印を、最後に使ってからしばらく出し続けるため）。
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="アカウント",
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField("名前", max_length=40, blank=True, default="")
    prefix = models.CharField("鍵の頭", max_length=16)  # 見分けるために画面に出す最初の数文字
    digest = models.CharField("鍵のハッシュ", max_length=64, unique=True)
    created_at = models.DateTimeField("作った時刻", auto_now_add=True)
    created_turn = models.IntegerField("作ったターン", null=True, blank=True)
    last_used_at = models.DateTimeField("最後に使った時刻", null=True, blank=True)
    last_used_turn = models.IntegerField("最後に使ったターン", null=True, blank=True)
    revoked_at = models.DateTimeField("取り消した時刻", null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "AI 用の鍵"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user} {self.prefix}… {self.name}".rstrip()
