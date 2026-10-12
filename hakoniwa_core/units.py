"""量とお金の単位。

財の量は 1000 分の 1 単位の整数で持ちます（定義データには 2.5 のように普通に書けます）。
お金は通貨の最小単位の整数、値段は財 1 単位（1000 分の 1 単位で 1000）あたりのお金で持ちます。
率と金利は 1万分の1 の整数（3% は 300）で持ちます。丸め（切り捨て）はこのファイルだけで行います。
"""

from __future__ import annotations

import math

QUANTITY_SCALE = 1000
BASIS = 10000  # 率の 100%（1万分の1 の単位）


def to_milli(value: float | int) -> int:
    """定義データに書いた量を内部の整数に直す。丸めはここだけで行う。"""
    return round(value * QUANTITY_SCALE)


def from_milli(value: int) -> float:
    return value / QUANTITY_SCALE


def to_basis(rate: float) -> int:
    """設定に書いた率（0.05 など）を 1万分の1 の整数に直す。"""
    return round(rate * BASIS)


def money_of(price: int, qty: int) -> int:
    """値段 price の財 qty（1000 分の 1 単位）の代金（切り捨て）。"""
    return price * qty // QUANTITY_SCALE


def qty_for(money: int, price: int) -> int:
    """お金 money で値段 price の財が買える量（1000 分の 1 単位、切り捨て）。"""
    if price <= 0:
        return 0
    return money * QUANTITY_SCALE // price


def unit_price(money: int, qty: int) -> int:
    """量 qty（1000 分の 1 単位）に払ったお金 money の、1 単位あたりの値段（切り捨て）。"""
    if qty <= 0:
        return 0
    return money * QUANTITY_SCALE // qty


def part(amount: int, rate: int) -> int:
    """amount の rate / 1万（切り捨て）。税・運賃・配当の額に使う。"""
    return amount * rate // BASIS


def scale_price(price: int, factor: float) -> int:
    """値段に倍率を掛ける（切り捨て、最低 1）。値段の見直しに使う。"""
    return max(1, math.floor(price * factor))


def round_money(value: float) -> int:
    """小数のお金を整数に（切り捨て）。"""
    return math.floor(value)
