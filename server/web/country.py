"""国の公開ページ（フェーズ4-1 ③）と、その JSON API の中身の組み立て。

国旗・名前・元首・体制・通貨・人口と、国が書く紹介文、自動の年表（ターンログの主な出来事と国際会議場の
出来事）、来訪者の一言を、誰にでも見せる。持ち主には、紹介文・紀年・国旗の入力欄に要る値も付ける。
年表の日付は、その国が選んだ紀年（世界の暦のまま・建国紀元・元号）で書く。
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q
from django.urls import reverse

from hakoniwa_core import milestones
from server.country import services
from server.country.models import Flag, Note, Profile
from server.turns.models import TurnLog
from server.web import awards, display, monuments, overview, rankings, top
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


def theme_info(profile: Profile | None) -> dict[str, Any] | None:
    """テーマカラー（案32）: 選んでいれば {key, name, hex}、いなければ None。"""
    key = profile.theme_color if profile else ""
    spec = services.THEME_COLORS.get(key)
    if not spec or not spec[1]:
        return None
    return {"key": key, "name": str(spec[0]), "hex": spec[1]}


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


# 年表で節目として目立たせる出来事（国の歴史の転機: 建国・憲法の改正・戦争の始まりと終わりと、
# 建国記念日・人口と GDP の節目）
MILESTONE_KINDS = (
    "island_founded",
    "constitution_amended",
    "war_declared",
    "war_declared_on",
    "war_ended",
    "anniversary_founding",
    "milestone_population",
    "milestone_gdp",
)


def timeline(island: m.Island, profile: Profile | None) -> list[dict[str, Any]]:
    """この国の年表（古い順）: ターンログの主な出来事（建国・選挙・憲法の改正など）と、外交の出来事
    （宣戦・停戦・講和・条約）。各行に、紀年ごとの区切り（period）と節目かどうか（milestone）を付ける。
    賞を取った瞬間も混ぜる（賞は記録を持たないので、到達ターンは awards.moments が統計と
    ターンログから導く。同じターンの賞は、そのターンの出来事の後に置く）。"""
    kinds = TurnLog.objects.filter(top.headline_filter() | Q(kind__in=DIPLOMACY_KINDS))
    logs = list(kinds.filter(island=island).order_by("-turn", "-id")[:TIMELINE])
    logs.reverse()
    rows = [
        {
            "turn": log.turn,
            "date": services.date_text(island, profile, log.turn),
            "period": services.period_text(island, profile, log.turn),
            "milestone": log.kind in MILESTONE_KINDS,
            "text": entry["text"],
        }
        for log, entry in zip(logs, display.log_entries(logs), strict=True)
    ]
    for moment in awards.moments(island.world, island.id):
        rows.append(
            {
                "turn": moment["turn"],
                "date": services.date_text(island, profile, moment["turn"]),
                "period": services.period_text(island, profile, moment["turn"]),
                "milestone": True,
                "text": display.message(
                    "award_gained", award=moment["name"], detail=moment["detail"]
                ),
            }
        )
    rows.sort(key=lambda r: r["turn"])  # 安定ソート: 同じターンでは出来事 → 賞の順を保つ
    return rows


def timeline_text(rows: list[dict[str, Any]]) -> str:
    """年表を、外の wiki にそのまま貼れる文にする（区切りの見出し＋1 行に 1 つの出来事）。"""
    lines: list[str] = []
    period: str | None = None
    for r in rows:
        if r["period"] != period:
            period = r["period"]
            lines.append(f"== {period} ==")
        lines.append(f"* {r['date']}: {r['text']}")
    return "\n".join(lines)


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
    # 建国記念日の年（満○年を迎えた後の1年間）なら、その年数（案27。それ以外は None）
    anniversary = milestones.anniversary_years(services.founded_turn(island), island.world.turn)
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
        "theme": theme_info(profile),
        "anniversary": {"years": anniversary} if anniversary is not None else None,
        "awards": awards.holders(island.world, only=island.id).get(island.id, []),
        "titles": rankings.island_strengths(island, stats)["titles"],
        "intro": "" if (intro_hidden or not profile) else profile.intro,
        "timeline": rows,
        "timeline_text": timeline_text(rows),
        "monuments": monuments.listing(island, mine),
        "notes": notes(island, profile),
        "limits": {"intro": services.INTRO_MAX, "note": services.NOTE_MAX},
        "can_note": viewer is not None and viewer.pk != island.pk,
    }
    if mine:
        data["intro_hidden_by_admin"] = intro_hidden
        data["intro_edit"] = profile.intro if profile else ""
        data["theme_choices"] = [
            {"key": k, "name": str(name), "hex": hex_}
            for k, (name, hex_) in services.THEME_COLORS.items()
        ]
        data["theme_key"] = profile.theme_color if profile else ""
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
        "monuments": monuments.count(island),
    }
    return out
