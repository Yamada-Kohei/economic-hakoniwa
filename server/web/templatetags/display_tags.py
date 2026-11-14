"""金額など、画面で共通に使う表示の規則。"""

from __future__ import annotations

from django import template

from server.web import display

register = template.Library()


@register.filter
def currency_prefix(symbol: str) -> str:
    """金額の前に置く通貨記号を整える。記号単体の表示には使わない。"""
    return display.currency_prefix(symbol)


@register.inclusion_tag("web/island_nav.html", takes_context=True)
def island_nav(context) -> dict:
    """島の画面の入口と、今の入口の中のタブ（UI-71。server/web/island_nav.py）。"""
    from server.web import island_nav as nav

    island = context["island"]
    return nav.nav(
        island.id,
        context.get("page", ""),
        context.get("plan_label", ""),
        bool(context.get("mine")),
    )
