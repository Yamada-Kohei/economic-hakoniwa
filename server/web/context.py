"""全部の画面で使う値（見出しの「自分の島」のリンク、画面ごとの「この画面の手引き」）。"""

from __future__ import annotations

from django.urls import reverse

from server.world.models import Island

# 画面（URL の名前）と、その画面を説明する手引きの節。help_start は入門、help は詳しい仕組み
SCREEN_HELP = {
    "top": ("help_start", ""),
    "found": ("help_start", "regimes"),
    "island": ("help_start", "first-year"),
    "world": ("help", "trade"),
    "rankings": ("help", "statistics"),
    "news": ("help", "country-pages"),
    "turn_log": ("help", "flow"),
    "island_economy": ("help", "statistics"),
    "island_market": ("help", "island"),
    "island_chain": ("help", "goods"),
    "island_facilities": ("help", "facilities"),
    "island_fiscal": ("help", "money"),
    "island_finance": ("help", "money"),
    "island_trade": ("help", "trade"),
    "island_fx": ("help", "trade"),
    "island_politics": ("help", "politics"),
    "island_plan": ("help", "politics"),
    "island_diplomacy": ("help", "diplomacy"),
    "island_military": ("help", "military"),
    "island_country": ("help", "country-pages"),
    "island_flag": ("help", "country-pages"),
    "diary": ("help", "country-pages"),
    "hall": ("help", "diplomacy"),
    "letters": ("help", "letters"),
    "letters_sent": ("help", "letters"),
    "letter_new": ("help", "letters"),
    "letter": ("help", "letters"),
    "board": ("help", "board"),
    "board_forum": ("help", "board"),
    "board_log": ("help", "board"),
    "board_thread": ("help", "board"),
    "api_keys": ("help", "ai"),
}


def screen_help(request) -> dict:
    """開いている画面の手引きの節へのリンク（見出しの下の「この画面の手引き」）。"""
    match = getattr(request, "resolver_match", None)
    target = SCREEN_HELP.get(match.url_name if match else "")
    if not target:
        return {}
    page, anchor = target
    return {"screen_help_url": reverse(page) + (f"#{anchor}" if anchor else "")}


def own_island(request) -> dict:
    if not request.user.is_authenticated:
        return {}
    island = Island.objects.filter(owner=request.user).values("id", "name").first()
    return {"own_island": island}
