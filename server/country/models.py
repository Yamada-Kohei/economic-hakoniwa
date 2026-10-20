"""国の公開ページに載せるもの: 国旗・紹介文と紀年・来訪者の一言。

どれもシミュレーションの外の、遊ぶ人が書く・上げるもの。管理者は不適切なものを非表示にできる
（admin.py）。世界の状態（island.ext）には置かない（ターンの処理が ext を書き換えるため）。
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from server.world.models import Island, World

ERA_KINDS = [
    ("world", "世界の暦のまま"),
    ("founding", "建国紀元"),
    ("regnal", "元号"),
]


class Flag(models.Model):
    """国が上げた国旗の PNG（検証して作り直したもの。png.py）。管理者が隠すと仮の旗に戻る。"""

    island = models.OneToOneField(
        Island, verbose_name="国", on_delete=models.CASCADE, related_name="flag"
    )
    png = models.BinaryField("PNG")
    width = models.IntegerField("幅")
    height = models.IntegerField("高さ")
    hidden = models.BooleanField("非表示", default=False)
    updated_at = models.DateTimeField("上げた時刻", auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = "国旗"

    def __str__(self) -> str:
        return f"{self.island} の国旗"


class Profile(models.Model):
    """国の紹介文と、国ごとの紀年の選び。紀年は表示だけで、ゲームの暦（ターン）は変わらない。"""

    island = models.OneToOneField(
        Island, verbose_name="国", on_delete=models.CASCADE, related_name="profile"
    )
    intro = models.TextField("紹介文", max_length=600, blank=True, default="")
    intro_hidden = models.BooleanField("紹介文を非表示", default=False)
    era_kind = models.CharField("紀年", max_length=16, choices=ERA_KINDS, default="world")
    # 元号の一覧 [{"name": 元号, "start_turn": 改元したターン}]（古い順）
    eras = models.JSONField("元号", default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = "国の紹介文と紀年"

    def __str__(self) -> str:
        return f"{self.island} の紹介"


class Note(models.Model):
    """ほかの国のページに残す一言（来訪者の帳）。島を持つ人が出す。"""

    world = models.ForeignKey(
        World, verbose_name="世界", on_delete=models.CASCADE, related_name="country_notes"
    )
    island = models.ForeignKey(
        Island, verbose_name="残された国", on_delete=models.CASCADE, related_name="visitor_notes"
    )
    visitor = models.ForeignKey(
        Island,
        verbose_name="残した国",
        on_delete=models.CASCADE,
        related_name="notes_left",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="書いた人",
        on_delete=models.SET_NULL,
        null=True,
        related_name="country_notes",
    )
    text = models.CharField("一言", max_length=80)
    turn = models.IntegerField("残したターン")
    posted_at = models.DateTimeField("残した時刻", auto_now_add=True)
    hidden = models.BooleanField("非表示", default=False)

    class Meta:
        verbose_name = verbose_name_plural = "来訪者の一言"
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"{self.visitor} → {self.island}: {self.text}"
