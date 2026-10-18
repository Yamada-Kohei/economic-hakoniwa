"""トップの画面（島の一覧）の、順位の表のほかの中身: 最近の出来事と、管理人の書く欄（更新履歴・
管理人より・リンク集。server/web/top.yaml）。順位の表と歴代の記録は rankings.py。"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml
from django.db.models import Q

from server.turns.models import TurnLog
from server.web import display
from server.world import models as m

RECENT = 15  # 最近の出来事に出す行の数

# 最近の出来事に出す文の種類（新聞の見出しになるもの）。国債の発行・品切れ・施設の止まりなど、
# 毎ターンのように出る細かい文は島のターンログで読む。
# 名前が _ で終わるものは、その始まりの種類全部。
HEADLINES = (
    "island_founded",
    "island_renamed",
    "economy_started",
    "construction_done",
    "title_gained",
    "title_lost",
    "disaster_",
    "politics_demo",
    "politics_riot",
    "politics_rebellion",
    "riot_destroyed",
    "election_",
    "constitution_amended",
    "economy_changed",
    "nationalized_all",
    "transition_ended",
    "mobilization_started",
    "leader_renamed",
    "capital_rebuilt",
    "bank_failed_",
    "bank_liquidated",
    "bond_default",
    "facility_bankrupt",
    "famine_flight",
    "population_starved",
    "population_hunger_deaths",
    "trade_started",
    "bloc_created",
    "bloc_joined",
    "bloc_left",
    "embargo_set",
    "aid_sent",
    "fx_peg_broken",
    "settlement_grew",
)


def _headline_filter() -> Q:
    q = Q()
    for kind in HEADLINES:
        q |= Q(kind__startswith=kind) if kind.endswith("_") else Q(kind=kind)
    return q


def recent_events(world: m.World, limit: int = RECENT) -> list[dict[str, Any]]:
    """全部の島の最近の出来事（新しい順）。文は島のターンログと同じ。"""
    logs = list(
        TurnLog.objects.filter(_headline_filter(), island__world=world)
        .select_related("island")
        .order_by("-turn", "-id")[:limit]
    )
    names = {log.island_id: log.island.name for log in logs}
    out = []
    for log, entry in zip(logs, display.log_entries(logs), strict=True):
        out.append({**entry, "island": log.island_id, "name": names[log.island_id]})
    return out


@cache
def site_notes() -> dict[str, Any]:
    """管理人の書く欄（更新履歴・管理人より・リンク集）。server/web/top.yaml を直して書き換える。"""
    path = Path(__file__).with_name("top.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        "updates": list(data.get("updates") or []),
        "message": str(data.get("message") or ""),
        "links": list(data.get("links") or []),
    }
