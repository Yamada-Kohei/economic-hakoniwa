"""命令の保存。中身の検証はコア（hakoniwa_core.orders.check_order）に任せる。"""

from __future__ import annotations

from django.db import models

from server.world.models import Island


class Order(models.Model):
    STATUS_CHOICES = [("pending", "待ち"), ("done", "実行済み"), ("failed", "失敗")]

    island = models.ForeignKey(Island, on_delete=models.CASCADE, related_name="orders")
    seq = models.IntegerField("順番")
    kind = models.CharField("種類", max_length=64)
    payload = models.JSONField("中身", default=dict, blank=True)
    status = models.CharField("状態", max_length=16, choices=STATUS_CHOICES, default="pending")
    reason = models.CharField("失敗の理由", max_length=64, blank=True, default="")
    executed_turn = models.IntegerField("実行したターン", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = "命令"
        ordering = ["island", "seq", "id"]

    def __str__(self) -> str:
        return f"{self.island} #{self.seq} {self.kind}"
