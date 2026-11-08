"""金額など、画面で共通に使う表示の規則。"""

from __future__ import annotations

from django import template

from server.web import display

register = template.Library()


@register.filter
def currency_prefix(symbol: str) -> str:
    """金額の前に置く通貨記号を整える。記号単体の表示には使わない。"""
    return display.currency_prefix(symbol)
