"""トップの画面（島の一覧）の、順位の表のほかの中身: 最近の出来事と、管理人の書く欄（更新履歴・
管理人より・リンク集。server/web/top.yaml）。順位の表と歴代の記録は rankings.py。"""

from __future__ import annotations

import subprocess
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from django.db.models import Q
from django.utils import timezone

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


# 更新履歴は、コミットの文に「更新履歴: 〜」の行を書くと、そのコミットが反映されたときに載る
# （git の記録から読む。手で書く分は top.yaml の updates）。新しい順に UPDATES 件まで。
UPDATE_MARKS = ("更新履歴:", "更新履歴：")  # 半角と全角のコロン
UPDATES = 30


@cache
def site_notes() -> dict[str, Any]:
    """管理人の書く欄（更新履歴・管理人より・リンク集）。更新履歴は git の記録と top.yaml を
    合わせる。サーバーは反映のたびに起動し直すので、読むのは起動して最初の1回だけ。"""
    path = Path(__file__).with_name("top.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    written = [{"date": str(u["date"]), "text": str(u["text"])} for u in data.get("updates") or []]
    return {
        "updates": merge_updates(git_updates(), written),
        "message": str(data.get("message") or ""),
        "links": list(data.get("links") or []),
    }


def git_updates() -> list[dict[str, str]]:
    """git の記録から、「更新履歴:」の行を持つコミットを読む（git がない・記録がないときは空）。"""
    root = Path(__file__).resolve().parents[2]
    try:
        out = subprocess.run(
            ["git", "log", "--fixed-strings", "--grep=更新履歴", "--format=%x1e%cI%x1f%B"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_git_updates(out)


def parse_git_updates(log: str) -> list[dict[str, str]]:
    """git log（%x1e%cI%x1f%B）の出力から、日付（JST の日）と「更新履歴:」の行の文を取り出す。"""
    out = []
    for record in log.split("\x1e"):
        if "\x1f" not in record:
            continue
        when, body = record.split("\x1f", 1)
        try:
            day = datetime.fromisoformat(when.strip()).astimezone(timezone.get_current_timezone())
        except ValueError:
            continue
        for line in body.splitlines():
            line = line.strip()
            mark = next((m for m in UPDATE_MARKS if line.startswith(m)), None)
            text = line[len(mark) :].strip() if mark else ""
            if text:
                out.append({"date": day.date().isoformat(), "text": text})
    return out


def merge_updates(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    """更新履歴を新しい日の順に（同じ日の中は渡した順）。同じ文は1つにする。"""
    seen: set[str] = set()
    rows = []
    for group in groups:
        for row in group:
            if row["text"] not in seen:
                seen.add(row["text"])
                rows.append(row)
    rows.sort(key=lambda r: r["date"], reverse=True)  # 安定な並べ替え
    return rows[:UPDATES]
