"""国の公開ページ（フェーズ4-1 ③）と、その JSON API の中身の組み立て。

国旗・名前・元首・体制・通貨・人口と、国が書く紹介文、自動の年表（ターンログの主な出来事と国際会議場の
出来事）、来訪者の一言を、誰にでも見せる。持ち主には、紹介文・紀年・国旗の入力欄に要る値も付ける。
年表の日付は、その国が選んだ紀年（世界の暦のまま・建国紀元・元号）で書く。
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q
from django.urls import reverse

from server.country import services
from server.country.models import Flag, Note, Profile
from server.turns.models import TurnLog
from server.web import awards, display, overview, rankings, top
from server.world import models as m

TIMELINE = 300  # 年表に出す出来事の数（新しいほうから）
NOTES = 30  # 来訪者の一言を出す数（新しいほうから）


def flag_info(island: m.Island, mine: bool = False) -> dict[str, Any]:
    """国旗の画像の場所と、仮の旗かどうか。管理者が隠した旗は仮の旗に戻る（持ち主にはそう伝える）。"""
    flag = (
        Flag.objects.filter(island=island).only("width", "height", "hidden", "updated_at").first()
    )
    shown = flag is not None and not flag.hidden
    info: dict[str, Any] = {
        "url": reverse("island_flag", args=[island.id]),
        "provisional": not shown,
    }
    if shown:
        info["width"], info["height"] = flag.width, flag.height
        info["url"] += f"?v={int(flag.updated_at.timestamp())}"
    if mine and flag is not None:
        info["hidden_by_admin"] = flag.hidden
    return info


def era_info(island: m.Island, profile: Profile | None, mine: bool) -> dict[str, Any]:
    turn = island.world.turn
    kind = profile.era_kind if profile else "world"
    info: dict[str, Any] = {
        "kind": kind,
        "kind_name": services.KIND_NAMES[kind],
        "now": services.date_text(island, profile, turn),
        "founded": services.date_text(island, profile, services.founded_turn(island)),
        "eras": [
            {**e, "start": services.date_text(island, profile, e["start_turn"])}
            for e in (profile.eras if profile else [])
        ],
    }
    if mine:
        info["choices"] = [{"id": k, "name": v} for k, v in services.KIND_NAMES.items()]
        info["name_max"] = services.ERA_NAME_MAX
    return info


# 年表に載せる外交の出来事（この国のログの文。宣戦した側・された側・条約の当事国のどちらにも載る）
DIPLOMACY_KINDS = (
    "war_declared",
    "war_declared_on",
    "war_resumed",
    "war_ended",
    "ceasefire_started",
    "treaty_in_force",
    "treaty_denounced",
    "treaty_ended",
    "treaty_fulfilled",
    "treaty_violated",
)


def timeline(island: m.Island, profile: Profile | None) -> list[dict[str, Any]]:
    """この国の年表（古い順）: ターンログの主な出来事（建国・選挙・憲法の改正など）と、外交の出来事
    （宣戦・停戦・講和・条約）。"""
    kinds = TurnLog.objects.filter(top.headline_filter() | Q(kind__in=DIPLOMACY_KINDS))
    logs = list(kinds.filter(island=island).order_by("-turn", "-id")[:TIMELINE])
    logs.reverse()
    return [
        {
            "turn": log.turn,
            "date": services.date_text(island, profile, log.turn),
            "text": entry["text"],
        }
        for log, entry in zip(logs, display.log_entries(logs), strict=True)
    ]


def timeline_text(rows: list[dict[str, Any]]) -> str:
    """年表を、外の wiki にそのまま貼れる文にする（1 行に 1 つの出来事）。"""
    return "\n".join(f"* {r['date']}: {r['text']}" for r in rows)


def notes(island: m.Island, profile: Profile | None) -> list[dict[str, Any]]:
    rows = (
        Note.objects.filter(island=island, hidden=False)
        .select_related("visitor")
        .order_by("-id")[:NOTES]
    )
    return [
        {
            "id": n.id,
            "from": {"id": n.visitor_id, "name": n.visitor.name},
            "text": n.text,
            "turn": n.turn,
            "date": services.date_text(island, profile, n.turn),
        }
        for n in rows
    ]


def country_data(island: m.Island, mine: bool, viewer: m.Island | None = None) -> dict[str, Any]:
    """国の公開ページ。viewer は見ている人の国（一言を残せるかの判断に使う）。"""
    profile = Profile.objects.filter(island=island).first()
    head = overview.island_head(island)
    _, stats = display.latest_stats(island)
    rows = timeline(island, profile)
    intro_hidden = bool(profile and profile.intro_hidden)
    data: dict[str, Any] = {
        "flag": flag_info(island, mine),
        "head": {
            "name": head["name"],
            "owner": head["owner"],
            "leader": head["leader"],
            "capital": head["capital"],
            "currency": head["currency"],
            "currency_symbol": head["currency_symbol"],
            "regime": head["regime"],
            "climate": head["climate"],
            "founded_turn": head["founded_turn"],
            "population": stats.get("population"),
            "inactive": island.inactive,
        },
        "era": era_info(island, profile, mine),
        "awards": awards.holders(island.world, only=island.id).get(island.id, []),
        "titles": rankings.island_strengths(island, stats)["titles"],
        "intro": "" if (intro_hidden or not profile) else profile.intro,
        "timeline": rows,
        "timeline_text": timeline_text(rows),
        "notes": notes(island, profile),
        "limits": {"intro": services.INTRO_MAX, "note": services.NOTE_MAX},
        "can_note": viewer is not None and viewer.pk != island.pk,
    }
    if mine:
        data["intro_hidden_by_admin"] = intro_hidden
        data["intro_edit"] = profile.intro if profile else ""
    return data


def brief(island: m.Island, mine: bool) -> dict[str, Any]:
    """国の要約に添える、公開ページの短い要約（国旗・今の日付・紹介文・来訪者の一言の数）。"""
    profile = Profile.objects.filter(island=island).first()
    shown = bool(profile and profile.intro and not profile.intro_hidden)
    out: dict[str, Any] = {
        "flag": flag_info(island, mine),
        "date": services.date_text(island, profile, island.world.turn),
        "era": services.KIND_NAMES[profile.era_kind if profile else "world"],
        "intro": profile.intro if shown else "",
        "notes": Note.objects.filter(island=island, hidden=False).count(),
    }
    return out
