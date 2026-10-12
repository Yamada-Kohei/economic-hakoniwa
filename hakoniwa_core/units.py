"""量の単位。

財の量は 1000 分の 1 単位の整数で持ちます（定義データには 2.5 のように普通に書けます）。
お金は通貨の最小単位の整数で持ちます（フェーズ2から）。
"""

from __future__ import annotations

QUANTITY_SCALE = 1000


def to_milli(value: float | int) -> int:
    """定義データに書いた量を内部の整数に直す。丸めはここだけで行う。"""
    return round(value * QUANTITY_SCALE)


def from_milli(value: int) -> float:
    return value / QUANTITY_SCALE
