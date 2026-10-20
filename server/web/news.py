"""世界の近況（フェーズ4-1）: 全部の国の主な出来事を、新しい順に 1 本の並びで見せる。

載せるのは、島の一覧の「最近の出来事」と同じ主な出来事（server/web/top.py の HEADLINES。建国・選挙・
憲法の改正・災害・倒産・称号など）と、国際会議場に載る条約・戦争の出来事（1 つの出来事に 1 件）。
日付は世界の暦で書き、国が別の紀年（建国紀元・元号）を選んでいれば、その日付も添える。
"""

from __future__ import annotations

from typing import Any

from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse

from server.country import services
from server.country.models import Profile
from server.hall import services as hall_services
from server.turns.models import TurnLog
from server.web import display, top
from server.world import models as m

PAGE_SIZE = 30
# 世界の近況に出す種類。国の数が増えると毎ターンのように出る施設の完成と町の成長は、島の一覧の
# 「最近の出来事」と国の年表にだけ出す
KINDS = tuple(k for k in top.HEADLINES if k not in ("construction_done", "settlement_grew"))


def _page_number(value: Any) -> int:
    """page の値（数でなければ 1。0 以下も 1。多すぎるときは Paginator が最後のページにする）。"""
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def news_data(world: m.World, page_number: Any = 1) -> dict[str, Any]:
    """世界の近況の 1 ページ（page は 1 から）。"""
    rows = (
        TurnLog.objects.filter(top.headline_filter(KINDS) | Q(args__hall=True), island__world=world)
        .select_related("island", "island__world")
        .defer("island__economy", "island__policy")
        .order_by("-turn", "-id")
    )
    page = Paginator(rows, PAGE_SIZE).get_page(_page_number(page_number))
    logs = list(page)
    profiles = {
        p.island_id: p for p in Profile.objects.filter(island_id__in={x.island_id for x in logs})
    }
    entries = []
    for log, entry in zip(logs, display.log_entries(logs), strict=True):
        island = log.island
        hall = (log.args or {}).get("hall")
        local = services.date_text(island, profiles.get(island.id), log.turn)
        world_date = display.turn_text(log.turn)
        out: dict[str, Any] = {
            "turn": log.turn,
            "date": world_date,
            "kind": log.kind,
            "text": hall_services.event_text(log) if hall else entry["text"],
            "island": {
                "id": island.id,
                "name": island.name,
                "flag": reverse("island_flag", args=[island.id]),
            },
        }
        profile = profiles.get(island.id)
        if profile and profile.era_kind != "world":
            out["country_date"] = local
        entries.append(out)
    return {
        "turn": world.turn,
        "date": display.turn_text(world.turn),
        "page": page.number,
        "pages": page.paginator.num_pages,
        "count": page.paginator.count,
        "entries": entries,
    }
