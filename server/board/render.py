"""書き込みの本文を HTML にする。本文は必ず文字としてエスケープし、`>>番号` だけをリンクにする。"""

from __future__ import annotations

import html
import re
from collections.abc import Callable

from django.utils.safestring import SafeString, mark_safe

ANCHOR = re.compile(r"&gt;&gt;(\d{1,4})(?:-(\d{1,4}))?")
RANGE_MAX = 10  # `>>3-20` のように並べて返せる数


def anchor_numbers(text: str) -> list[int]:
    """本文が返信している番号（`>>3`・`>>3-5`）。重なりは 1 つにし、1 つの書き込みでの数を絞る。"""
    found: list[int] = []
    for match in ANCHOR.finditer(html.escape(text)):
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) else first
        last = min(max(first, last), first + RANGE_MAX - 1)
        found.extend(range(first, last + 1))
    return sorted(set(n for n in found if n >= 1))[: RANGE_MAX * 2]


def render_body(text: str, thread_id: int, href_for: Callable[[int], str]) -> SafeString:
    """エスケープして改行を <br> にし、`>>番号` を同じスレッドの書き込みへのリンクにする。

    href_for(番号) はその書き込みが載るページの住所（ページが分かれていても飛べるように）。
    """
    escaped = html.escape(text).replace("\r\n", "\n").replace("\r", "\n")

    def link(match: re.Match[str]) -> str:
        first, last = int(match.group(1)), match.group(2)
        to = f' data-to="{last}"' if last else ""
        return (
            f'<a class="anchor" href="{href_for(first)}" data-n="{first}"'
            f'{to} data-thread="{thread_id}">{match.group(0)}</a>'
        )

    return mark_safe(ANCHOR.sub(link, escaped).replace("\n", "<br>"))  # noqa: S308 エスケープ済み
