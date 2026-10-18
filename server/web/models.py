"""トップの画面のアクセスカウンタ（個人サイトの伝統）。

数えるのはトップの画面を開いた人だけ。同じ人がその日のうちに開き直しても数えない。接続元は
そのまま残さず、日付とサーバーの秘密鍵で混ぜたハッシュにして、今日と昨日の分だけ持つ
（server/web/counter.py）。ゲームの計算には関わらない。
"""

from __future__ import annotations

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

    class Meta:
        verbose_name = verbose_name_plural = "その日のお客様"
        constraints = [
            models.UniqueConstraint(fields=["day", "digest"], name="web_visit_once_a_day")
        ]
        indexes = [models.Index(fields=["day"])]

    def __str__(self) -> str:
        return f"{self.day} {self.number} 人目"


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
