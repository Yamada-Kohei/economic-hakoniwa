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
    capital_x = models.SmallIntegerField("首都の x", null=True, blank=True)
    capital_y = models.SmallIntegerField("首都の y", null=True, blank=True)
    resources = models.JSONField("鉱業資源", default=list, blank=True)
    regime = models.JSONField("体制", default=dict, blank=True)
    names = models.JSONField("首都名・通貨名と記号・国家元首", default=dict, blank=True)
    # 島の共同倉庫は在庫 Stock の行（facility が空の行）で持つ
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
    zone = models.CharField("指定", max_length=32, blank=True, default="")
    infra = models.JSONField("インフラ", default=dict, blank=True)
    pollution = models.IntegerField("汚染", default=0)
    ext = models.JSONField("拡張欄", default=dict, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "マス"
        constraints = [
            models.UniqueConstraint(fields=["island", "x", "y"], name="unique_tile_xy"),
        ]

    def __str__(self) -> str:
        return f"{self.island} ({self.x}, {self.y})"


class Facility(models.Model):
    """マスに建つ施設（住宅も施設の一種）。number は島の中での番号（コアの Facility.id）。"""

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="facilities")
    number = models.IntegerField("島の中の番号")
    kind = models.CharField("種類", max_length=64)
    x = models.SmallIntegerField()
    y = models.SmallIntegerField()
    recipe = models.CharField("作り方", max_length=64, default="default")
    owner = models.CharField("持ち主", max_length=16, default="state")
    accounting = models.CharField("会計の形", max_length=32, default="treasury")
    name = models.CharField("名前", max_length=32, blank=True, default="")
    built = models.BooleanField("完成", default=False)
    progress = models.IntegerField("工事の進み（1000 分の 1 ターン）", default=0)
    workers = models.IntegerField("働いている人数", default=0)
    operating_rate = models.IntegerField("稼働率（1000 分の 1）", default=0)
    priority = models.SmallIntegerField("優先順位", null=True, blank=True)
    status = models.CharField("状態", max_length=64, blank=True, default="")
    ext = models.JSONField("拡張欄", default=dict, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "施設"
        constraints = [
            models.UniqueConstraint(fields=["island", "number"], name="unique_facility_number"),
            models.UniqueConstraint(fields=["island", "x", "y"], name="unique_facility_xy"),
        ]

    def __str__(self) -> str:
        return f"{self.island} ({self.x}, {self.y}) {self.kind}"


class Stock(models.Model):
    """在庫。facility が空なら島の共同倉庫の在庫。量は 1000 分の 1 単位の整数。"""

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="stocks")
    facility = models.ForeignKey(
        Facility, on_delete=models.CASCADE, null=True, blank=True, related_name="stocks"
    )
    good = models.CharField("財", max_length=64)
    made_turn = models.IntegerField("作られたターン", default=0)
    quantity = models.BigIntegerField("量（1000 分の 1）")

    class Meta:
        verbose_name = verbose_name_plural = "在庫"
        indexes = [models.Index(fields=["island", "facility"])]

    def __str__(self) -> str:
        return f"{self.island} {self.good} {self.quantity}"


class StatRecord(models.Model):
    """統計の1つの値。指標（name）を足しても表の形は変えない。"""

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="stats")
    turn = models.IntegerField("ターン")
    name = models.CharField("指標", max_length=64)
    value = models.FloatField("値")

    class Meta:
        verbose_name = verbose_name_plural = "統計"
        indexes = [models.Index(fields=["island", "turn"])]

    def __str__(self) -> str:
        return f"{self.island} ターン {self.turn} {self.name}"
