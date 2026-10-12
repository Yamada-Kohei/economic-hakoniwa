"""ターンログとバックアップ。"""

from __future__ import annotations

from django.db import models

from server.world.models import Island, World


class TurnLog(models.Model):
    """ターンログの1行。文面は表示のときに定義データの messages から作る。"""

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="logs")
    turn = models.IntegerField("ターン")
    kind = models.CharField("文の種類", max_length=64)
    args = models.JSONField("引数", default=dict, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "ターンログ"
        ordering = ["-turn", "id"]
        indexes = [models.Index(fields=["island", "turn"])]

    def __str__(self) -> str:
        return f"{self.island} ターン {self.turn} {self.kind}"


class Backup(models.Model):
    """更新前の世界全体の写し（圧縮した JSON）。巻き戻しはここから戻す。"""

    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="backups")
    turn = models.IntegerField("ターン")
    data = models.BinaryField("中身")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = "バックアップ"
        ordering = ["-turn"]

    def __str__(self) -> str:
        return f"ターン {self.turn} のバックアップ"
