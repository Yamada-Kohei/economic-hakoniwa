"""国の公開ページの書き込み（紹介文・紀年・国旗・来訪者の一言）と、国ごとの日付の書き方。

画面（server/web/views.py）・JSON API（api.py）・MCP が同じ関数を使う。出せない・書けないときは
CountryError（text は画面と API にそのまま出し、status は API の HTTP の状態にする）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from hakoniwa_core.dates import Calendar
from server.country import png
from server.country.models import ERA_KINDS, Flag, Note, Profile
from server.game import get_game
from server.letters.services import own_island
from server.world.models import Island

INTRO_MAX = Profile._meta.get_field("intro").max_length
NOTE_MAX = Note._meta.get_field("text").max_length
ERA_NAME_MAX = 8  # 元号の長さ
MAX_ERAS = 50
FOUNDING_NAME = "建国紀元"
KIND_NAMES = dict(ERA_KINDS)

__all__ = ["CountryError", "own_island"]


class CountryError(Exception):
    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.text = text
        self.status = status


def founded_turn(island: Island) -> int:
    return max(1, int((island.ext or {}).get("founded_turn") or 1))


def profile_of(island: Island) -> Profile:
    """国のページの設定（なければ作る）。"""
    return Profile.objects.get_or_create(island=island)[0]


# --- 国ごとの紀年 ---------------------------------------------------------------------------


def _fmt(name: str, year: int, month: int, gannen: bool) -> str:
    shown = "元" if gannen and year == 1 else str(year)
    return f"{name}{shown}年{month}月"


def date_text(island: Island, profile: Profile | None, turn: int) -> str:
    """ターン turn を、その国の選んだ紀年で書く（世界の暦のままなら箱庭暦）。始まる前のターンや、
    元号の最初の改元より前のターンは、世界の暦で書く。"""
    kind = profile.era_kind if profile else "world"
    world = get_game().calendar
    if kind == "founding":
        date = Calendar(FOUNDING_NAME, 1, founded_turn(island)).date(turn)
        if date:
            return _fmt(FOUNDING_NAME, *date, gannen=False)
    elif kind == "regnal" and profile and profile.eras:
        eras = [e for e in profile.eras if e["start_turn"] <= turn]
        if eras:
            era = eras[-1]
            date = Calendar(era["name"], 1, era["start_turn"]).date(turn)
            if date:
                return _fmt(era["name"], *date, gannen=True)
    date = world.date(turn) if world else None
    if world is None or date is None:
        return f"ターン {turn}"
    return _fmt(world.name, *date, gannen=False)


def _clean_era_name(name: Any) -> str:
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        raise CountryError("元号の名前を書いてください")
    if len(name) > ERA_NAME_MAX:
        raise CountryError(f"元号は {ERA_NAME_MAX} 字までです（いま {len(name)} 字）")
    return name


def set_era(island: Island, kind: str, name: Any = None) -> Profile:
    """紀年を選ぶ。元号に切り替えるときは最初の元号の名前も（建国のターンから数える）。"""
    if kind not in KIND_NAMES:
        raise CountryError(f"紀年は {'・'.join(KIND_NAMES)} のどれかです")
    profile = profile_of(island)
    if kind == "regnal" and not profile.eras:
        first = _clean_era_name(name)
        profile.eras = [{"name": first, "start_turn": founded_turn(island)}]
    profile.era_kind = kind
    profile.save()
    return profile


def new_era(island: Island, name: Any) -> Profile:
    """改元する（今のターンから新しい元号。元年は今の年）。"""
    name = _clean_era_name(name)
    profile = profile_of(island)
    turn = max(1, island.world.turn)
    eras = [e for e in profile.eras if e["start_turn"] < turn]
    if len(eras) >= MAX_ERAS:
        raise CountryError(f"元号は {MAX_ERAS} までです")
    if eras and eras[-1]["name"] == name:
        raise CountryError("いまと同じ元号です")
    profile.eras = [*eras, {"name": name, "start_turn": turn}]
    profile.era_kind = "regnal"
    profile.save()
    return profile


# --- 紹介文 ---------------------------------------------------------------------------------


def set_intro(island: Island, text: Any) -> Profile:
    if not isinstance(text, str):
        raise CountryError("紹介文は文字で送ってください")
    text = text.strip()
    if len(text) > INTRO_MAX:
        raise CountryError(f"紹介文は {INTRO_MAX} 字までです（いま {len(text)} 字）")
    profile = profile_of(island)
    profile.intro = text
    profile.intro_hidden = False  # 書き直したら、管理者がもう一度見る
    profile.save()
    return profile


# --- 国旗 -----------------------------------------------------------------------------------


def set_flag(island: Island, data: bytes) -> Flag:
    """PNG を検査して国旗にする（検査に通らなければ CountryError）。"""
    try:
        cleaned, width, height = png.clean(
            data,
            settings.HAKONIWA_FLAG_MAX_KB * 1024,
            settings.HAKONIWA_FLAG_MAX_SIDE,
        )
    except png.PngError as e:
        raise CountryError(e.text) from e
    flag, _ = Flag.objects.update_or_create(
        island=island,
        defaults={"png": cleaned, "width": width, "height": height, "hidden": False},
    )
    return flag


def clear_flag(island: Island) -> None:
    Flag.objects.filter(island=island).delete()


# --- 来訪者の一言 ---------------------------------------------------------------------------


def post_note(user, target: Island, text: Any) -> Note:
    """自分の国から、ほかの国のページに一言を残す。"""
    visitor = own_island(user)
    if visitor is None:
        raise CountryError("一言を残せるのは自分の国を持つ人だけです", 403)
    if visitor.pk == target.pk:
        raise CountryError("自分の国のページには残せません")
    if not isinstance(text, str) or not text.strip():
        raise CountryError("一言を書いてください")
    text = " ".join(text.split())
    if len(text) > NOTE_MAX:
        raise CountryError(f"一言は {NOTE_MAX} 字までです（いま {len(text)} 字）")
    limit = settings.HAKONIWA_COUNTRY_NOTES_PER_DAY
    since = timezone.now() - timedelta(days=1)
    if Note.objects.filter(visitor=visitor, posted_at__gte=since).count() >= limit:
        raise CountryError(
            f"1つの国が残せる一言は 24 時間に {limit} 件までです。しばらく待ってください", 429
        )
    return Note.objects.create(
        world=target.world,
        island=target,
        visitor=visitor,
        author=user,
        text=text,
        turn=target.world.turn,
    )
