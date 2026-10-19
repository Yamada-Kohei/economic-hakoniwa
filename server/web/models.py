"""トップの画面のアクセスカウンタ（個人サイトの伝統）。

数えるのはトップの画面を開いた人だけ。同じ人がその日のうちに開き直しても数えない。接続元は
そのまま残さず、日付とサーバーの秘密鍵で混ぜたハッシュにして、今日と昨日の分だけ持つ
（server/web/counter.py）。キリ番を踏んだ人（ログインしている人だけ）と、トップの画面の
更新履歴も持つ。ゲームの計算には関わらない。
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class AccessCounter(models.Model):
    """カウンタの合計（name ごとに1行。今はトップの画面の top だけ）。"""

    name = models.CharField("名前", max_length=32, primary_key=True)
    total = models.BigIntegerField("これまでのお客様の数", default=0)
    since = models.DateField("数え始めた日", auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = "アクセスカウンタ"

    def __str__(self) -> str:
        return f"{self.name}: {self.total}"


class Visit(models.Model):
    """その日に数えたお客様（同じ人の開き直しを数えないため）。今日と昨日の分だけ残す。"""

    day = models.DateField("日")
    digest = models.CharField("見分け（接続元のハッシュ）", max_length=64)
    number = models.BigIntegerField("何人目のお客様か")
    seen = models.DateTimeField("最後に開いた時刻", null=True)  # 閲覧中の人数を数えるため

    class Meta:
        verbose_name = verbose_name_plural = "その日のお客様"
        constraints = [
            models.UniqueConstraint(fields=["day", "digest"], name="web_visit_once_a_day")
        ]
        indexes = [models.Index(fields=["day"])]

    def __str__(self) -> str:
        return f"{self.day} {self.number} 人目"


class KiribanGetter(models.Model):
    """キリ番を踏んだ人（ログインしている人だけ。名前と国名は踏んだときのもの）。管理画面で消せる。"""

    number = models.BigIntegerField("番号", unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="アカウント", null=True, on_delete=models.SET_NULL
    )
    name = models.CharField("名前", max_length=150)
    island_name = models.CharField("国名", max_length=100, blank=True)
    at = models.DateTimeField("踏んだ時刻", auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = "キリ番ゲッター様"
        ordering = ["-number"]

    def __str__(self) -> str:
        return f"{self.number} {self.name}"


class SiteUpdate(models.Model):
    """トップの画面の更新履歴の1行。管理画面で足す・直す・隠すができる。

    遊ぶ人に見える変更のコミットに書いた「更新履歴: 〜」の行は、反映（migrate）のたびに
    ここへ写す（key で見分け、一度写した行は管理画面で直しても上書きしない）。"""

    date = models.DateField("日付")
    text = models.CharField("内容", max_length=200)
    hidden = models.BooleanField("隠す", default=False)
    key = models.CharField(
        "写した元", max_length=64, unique=True, null=True, blank=True, editable=False
    )

    class Meta:
        verbose_name = verbose_name_plural = "更新履歴"
        ordering = ["-date", "-id"]

    def __str__(self) -> str:
        return f"{self.date} {self.text}"
