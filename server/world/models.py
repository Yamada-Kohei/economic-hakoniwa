"""世界・島・マスの保存。ルールはコア（hakoniwa_core）にあり、ここは保存だけを受け持つ。

定義データ（地形や施設の種類）は文字列の名前で持ち、外部キーにしない。
追加パックで種類が増えても DB の移行が要らないようにするため。
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class World(models.Model):
    """1サーバーに1つ。"""

    turn = models.IntegerField("ターン", default=0)
    seed = models.BigIntegerField("乱数の種")
    next_turn_at = models.DateTimeField("次の更新時刻", null=True, blank=True)
    paused = models.BooleanField("更新の一時停止", default=False)
    updating_since = models.DateTimeField("更新中（開始時刻）", null=True, blank=True)
    packs = models.JSONField("読み込んだパックの版", default=dict, blank=True)
    ext = models.JSONField("拡張欄", default=dict, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "世界"

    def __str__(self) -> str:
        return f"世界（ターン {self.turn}）"


class Island(models.Model):
    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="islands")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="持ち主",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="islands",
    )
    name = models.CharField("国名", max_length=32)
    wx = models.IntegerField("世界マップの x")
    wy = models.IntegerField("世界マップの y")
    size = models.IntegerField("1辺のマス数")
    inactive = models.BooleanField("休止中", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    ext = models.JSONField("拡張欄", default=dict, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "島"
        constraints = [
            models.UniqueConstraint(fields=["world", "wx", "wy"], name="unique_island_cell"),
        ]

    def __str__(self) -> str:
        return self.name


class Tile(models.Model):
    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="tiles")
    x = models.SmallIntegerField()
    y = models.SmallIntegerField()
    terrain = models.CharField("地形", max_length=64)
    population = models.IntegerField("住民の数", default=0)
    ext = models.JSONField("拡張欄", default=dict, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "マス"
        constraints = [
            models.UniqueConstraint(fields=["island", "x", "y"], name="unique_tile_xy"),
        ]

    def __str__(self) -> str:
        return f"{self.island} ({self.x}, {self.y})"
