"""国のカード（案30）: 旗・国名・首都・元首・人口・GDP・節目を 1 枚に収める画面の材料。

絵そのもの（地図とカードの合成）はブラウザ側（static/js/map/export.js と card.js）が
描く。ここは公開してよい値と、キャンバスに出す文言（翻訳済みの見出し）を渡すだけ。
"""

from __future__ import annotations

from typing import Any

from django.urls import reverse
from django.utils.translation import gettext

from server.country import services
from server.country.models import Profile
from server.web import country, display, overview
from server.world import models as m

MILESTONES = 6  # カードに出す節目の数（新しいほうから）


def card_data(island: m.Island) -> dict[str, Any]:
    """国のカードの材料。国の公開ページ（country.py）と同じ公開範囲の値だけを使う。"""
    profile = Profile.objects.filter(island=island).first()
    head = overview.island_head(island)
    _, stats = display.latest_stats(island)
    currency = (island.names or {}).get("currency_symbol", "")
    rows = [r for r in country.timeline(island, profile) if r["milestone"]]
    lines = [
        {"label": gettext("首都"), "value": head["capital"] or "—"},
        {"label": gettext("元首"), "value": head["leader"] or "—"},
    ]
    if stats.get("population") is not None:
        lines.append(
            {"label": gettext("人口"), "value": f"{int(stats['population']):,} {gettext('人')}"}
        )
    if stats.get("gdp") is not None:
        value = display.stat_text("gdp", stats["gdp"], "money", currency)
        lines.append({"label": gettext("GDP"), "value": value})
    return {
        "title": island.name,
        "flag_url": country.flag_info(island)["url"],
        "lines": lines,
        "milestones": [{"date": r["date"], "text": r["text"]} for r in rows[-MILESTONES:]],
        "milestones_label": gettext("節目"),
        "date_text": services.date_text(island, profile, island.world.turn),
        "foot": gettext("経済版箱庭諸国"),
        "accent": "",  # テーマカラー（案32）で国の色が入る。それまでは基盤の既定色
        "map_url": reverse("api_island_map", args=[island.id]),
        "defs_url": reverse("api_defs"),
        "labels": {"download": gettext("カードを PNG で保存")},
    }
