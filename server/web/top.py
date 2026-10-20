"""トップの画面（島の一覧）の、順位の表のほかの中身: 最近の出来事と、管理人の書く欄（更新履歴・
管理人より・リンク集。server/web/top.yaml）。順位の表と歴代の記録は rankings.py。"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from django.db.models import Q
from django.templatetags.static import static
from django.utils import timezone

from server.turns.models import TurnLog
from server.web import display
from server.web.models import SiteUpdate
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
    "firm_bankrupt",
    "famine_flight",
    "famine_flight_jobless",
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


def headline_filter(kinds: tuple[str, ...] = HEADLINES) -> Q:
    q = Q()
    for kind in kinds:
        q |= Q(kind__startswith=kind) if kind.endswith("_") else Q(kind=kind)
    return q


def recent_events(world: m.World, limit: int = RECENT) -> list[dict[str, Any]]:
    """全部の島の最近の出来事（新しい順）。文は島のターンログと同じ。"""
    logs = list(
        TurnLog.objects.filter(headline_filter(), island__world=world)
        .select_related("island")
        .order_by("-turn", "-id")[:limit]
    )
    names = {log.island_id: log.island.name for log in logs}
    out = []
    for log, entry in zip(logs, display.log_entries(logs), strict=True):
        out.append({**entry, "island": log.island_id, "name": names[log.island_id]})
    return out


# 更新履歴は SiteUpdate（管理画面で足す・直す・隠す）。コミットの文に「更新履歴: 〜」の行を
# 書くと、反映（migrate）のたびに sync_updates がその行を写す。top.yaml の updates も同じく写す
# （git の記録より前の分）。画面には新しい順に UPDATES 件まで。
UPDATE_MARKS = ("更新履歴:", "更新履歴：")  # 半角と全角のコロン
UPDATES = 30
UPDATE_NEW_DAYS = 7  # この日数のうちの更新履歴に NEW! の印を付ける


@cache
def _yaml() -> dict[str, Any]:
    path = Path(__file__).with_name("top.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


BANNER = "img/banner-88x31.svg"  # 当サイトのバナー（88×31。server/static/img/）


def banner_html(request) -> str:
    """ほかのサイトに貼ってもらう、当サイトのバナーの HTML（このサイトの住所で書く）。"""
    home = request.build_absolute_uri("/")
    image = request.build_absolute_uri(static(BANNER))
    return f'<a href="{home}"><img src="{image}" width="88" height="31" alt="経済版箱庭諸国"></a>'


def site_notes() -> dict[str, Any]:
    """管理人の書く欄: 更新履歴（管理画面）・管理人より・リンク集（top.yaml）。"""
    data = _yaml()
    return {
        "updates": updates(),
        "message": str(data.get("message") or ""),
        "links": list(data.get("links") or []),
    }


def updates() -> list[dict[str, Any]]:
    today = timezone.localdate()
    rows = SiteUpdate.objects.filter(hidden=False).order_by("-date", "-id")[:UPDATES]
    return [
        {
            "date": row.date.isoformat(),
            "text": row.text,
            "new": (today - row.date).days < UPDATE_NEW_DAYS,
        }
        for row in rows
    ]


def sync_updates() -> int:
    """git の記録の「更新履歴:」の行と top.yaml の updates を、まだ写していなければ写す。
    写した数を返す。管理画面で直した・隠した行はそのまま（key で見分ける）。"""
    found = git_updates()
    for row in _yaml().get("updates") or []:
        text = str(row["text"])
        digest = hashlib.sha1(f"{row['date']}|{text}".encode()).hexdigest()
        found.append({"key": f"yaml:{digest}", "date": str(row["date"]), "text": text})
    known = set(SiteUpdate.objects.filter(key__isnull=False).values_list("key", flat=True))
    texts = set(SiteUpdate.objects.values_list("text", flat=True))
    new = []
    for r in found:
        if r["key"] in known or r["text"] in texts:
            continue
        texts.add(r["text"])
        new.append(
            SiteUpdate(key=r["key"], date=date.fromisoformat(r["date"]), text=r["text"][:200])
        )
    SiteUpdate.objects.bulk_create(new, ignore_conflicts=True)
    return len(new)


def git_updates() -> list[dict[str, str]]:
    """git の記録から、「更新履歴:」の行を持つコミットを読む（git がない・記録がないときは空）。"""
    root = Path(__file__).resolve().parents[2]
    try:
        out = subprocess.run(
            ["git", "log", "--fixed-strings", "--grep=更新履歴", "--format=%x1e%H%x1f%cI%x1f%B"],
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
    """git log（%x1e%H%x1f%cI%x1f%B）の出力から、コミットと行の番号（key）・日付（JST の日）・
    「更新履歴:」の行の文を取り出す。"""
    out = []
    for record in log.split("\x1e"):
        parts = record.split("\x1f", 2)
        if len(parts) != 3:
            continue
        sha, when, body = parts
        try:
            day = datetime.fromisoformat(when.strip()).astimezone(timezone.get_current_timezone())
        except ValueError:
            continue
        lines = [line.strip() for line in body.splitlines()]
        for i, line in enumerate(lines):
            mark = next((m for m in UPDATE_MARKS if line.startswith(m)), None)
            text = line[len(mark) :].strip() if mark else ""
            if text:
                key = f"git:{sha.strip()}:{i}"
                out.append({"key": key, "date": day.date().isoformat(), "text": text})
    return out
