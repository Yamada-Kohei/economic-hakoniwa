"""アカウントまわりの保存: 登録の記録と、AI 用の鍵と、回数制限の記録。

登録の記録は、同じ IP からの重複登録を止めるために使う。IP はそのまま残さず、サーバーの秘密鍵で
混ぜたハッシュ（照合用の形）にして、決めた期間だけ持つ。
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class SignupRecord(models.Model):
    ip_hash = models.CharField("IP のハッシュ", max_length=64, db_index=True)
    created_at = models.DateTimeField("登録した時刻", auto_now_add=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="登録したアカウント",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signup_records",
    )
    extra = models.PositiveSmallIntegerField(
        "追加で登録できる人数",
        default=0,
        help_text="管理者が決める。この接続元から、上限に加えてこの人数まで登録できる"
        "（家族・学校など、特定の人に複数の登録を認めるとき）。"
        "0 より大きい記録は古くなっても消さない。",
    )

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


class RateCounter(models.Model):
    """区切りごとの回数（server/accounts/throttle.py）。複数のワーカーが同じ DB で数えるための記録。

    scope は何の制限か（ai-key: AI 用の鍵の処理数、login-ip: 接続元ごとのログインの送信数）、key は
    その対象（鍵の番号、接続元のハッシュ）。区切り（window）は時刻を秒の幅で割った番号。
    古い区切りは、新しい区切りを作るときについでに消す（保存の期限 HAKONIWA_THROTTLE_KEEP_HOURS）。
    """

    scope = models.CharField("何の制限か", max_length=24)
    key = models.CharField("対象", max_length=64)
    window = models.BigIntegerField("区切りの番号")
    window_end = models.BigIntegerField("区切りの終わり（UNIX 秒）", db_index=True)
    count = models.PositiveIntegerField("回数", default=0)

    class Meta:
        verbose_name = verbose_name_plural = "回数制限の数え"
        constraints = [
            models.UniqueConstraint(fields=["scope", "key", "window"], name="uniq_rate_counter")
        ]

    def __str__(self) -> str:
        return f"{self.scope} {self.key[:12]} #{self.window} {self.count}"


class LoginAttempt(models.Model):
    """ログインの失敗の記録（server/accounts/loginguard.py）。接続元とアカウント名の組み合わせごと。

    IP もアカウント名も、そのまま残さずサーバーの秘密鍵で混ぜたハッシュにする（アカウント名の欄に
    パスワードを打ち込む人がいるので、打たれた文字を残さない）。消すと、その組み合わせの停止が解ける。
    """

    ip_hash = models.CharField("接続元のハッシュ", max_length=64)
    user_hash = models.CharField("アカウント名のハッシュ", max_length=64)
    failures = models.PositiveIntegerField("数えた回数（失敗か、確かめ中）", default=0)
    window_start = models.DateTimeField("数え始めた時刻", null=True, blank=True)
    locked_until = models.DateTimeField("停止の終わり", null=True, blank=True)
    touched_at = models.DateTimeField("最後に動いた時刻", db_index=True)

    class Meta:
        verbose_name = verbose_name_plural = "ログインの失敗の記録"
        ordering = ["-touched_at"]
        constraints = [
            models.UniqueConstraint(fields=["ip_hash", "user_hash"], name="uniq_login_attempt")
        ]

    def __str__(self) -> str:
        return f"{self.ip_hash[:8]}…/{self.user_hash[:8]}… {self.failures}回"
