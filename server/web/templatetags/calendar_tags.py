"""暦の表示（hakoniwa_core/dates.py）。テンプレートでは {% load calendar_tags %} で使う。

- {{ turn|turn_date }}: ある時点のターンを「箱庭暦32年11月（ターン 383）」と書く。
- {% calendar_script %}: 画面の JavaScript（js/calendar.js）が日付を書くのに使う暦の設定。
"""

from __future__ import annotations

from typing import Any

from django import template
from django.utils.html import json_script

from server.web import display

register = template.Library()


@register.filter
def turn_date(turn: Any) -> str:
    if isinstance(turn, bool) or not isinstance(turn, int):
        return "" if turn is None else str(turn)
    return display.turn_text(turn)


@register.simple_tag
def calendar_script() -> str:
    return json_script(display.calendar_settings(), "hakoniwa-calendar")
