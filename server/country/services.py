"""国の公開ページの書き込み（紹介文・紀年・国旗・来訪者の一言）と、国ごとの日付の書き方、
持ち主だけの国の日誌。

画面（server/web/views.py）・JSON API（api.py）・MCP が同じ関数を使う。出せない・書けないときは
CountryError（text は画面と API にそのまま出し、status は API の HTTP の状態にする）。
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from hakoniwa_core.dates import Calendar
from server.country import png
from server.country.models import ERA_KINDS, Diary, Flag, MonumentImage, Note, Profile
from server.game import get_game
from server.letters.services import own_island
from server.world.models import Facility, Island

INTRO_MAX = Profile._meta.get_field("intro").max_length
NOTE_MAX = Note._meta.get_field("text").max_length
DIARY_MAX = Diary._meta.get_field("text").max_length
ERA_NAME_MAX = 8  # 元号の長さ
MAX_ERAS = 50
NOTES_PER_TARGET = 3  # 同じ国のページに 1 つの国が 24 時間に残せる一言の数
FOUNDING_NAME = "建国紀元"
KIND_NAMES = dict(ERA_KINDS)

__all__ = ["CountryError", "own_island"]


class CountryError(Exception):
    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.text = text
        self.status = status


# 文字の向きを変える・見えなくする制御（表示の偽装に使われる）
HIDDEN_CHARS = set("\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060")
HIDDEN_CHARS |= set("\u2066\u2067\u2068\u2069\ufeff")


def _plain(text: str, label: str, *, lines: bool = False) -> str:
    """書かれた文から、使えない文字（制御文字・NUL・孤立サロゲート・文字の向きの制御）を断る。
    lines が真なら改行は使える（CRLF は LF にそろえる）。"""
    if lines:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    for c in text:
        kind = unicodedata.category(c)
        if (kind in ("Cc", "Cs") and not (lines and c == "\n")) or c in HIDDEN_CHARS:
            raise CountryError(f"{label}に使えない文字が含まれています")
    return text


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
    name = _plain(name.strip(), "元号") if isinstance(name, str) else ""
    if not name:
        raise CountryError("元号の名前を書いてください")
    if any(c.isspace() for c in name):
        raise CountryError("元号に空白は使えません")
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
    """改元する（今のターンから新しい元号。元年は今の年）。元号がまだなければ、最初の元号として
    建国のターンから数える（set_era と同じ）。"""
    name = _clean_era_name(name)
    profile = profile_of(island)
    turn = max(1, island.world.turn)
    # 巻き戻しで残った、今のターンより後（同じターンも）の元号は捨てる
    eras = [e for e in profile.eras if e["start_turn"] < turn]
    if not eras:
        profile.eras = [{"name": name, "start_turn": min(turn, founded_turn(island))}]
    else:
        if len(eras) >= MAX_ERAS:
            raise CountryError(f"元号は {MAX_ERAS} までです。紀年を世界の暦か建国紀元に戻せます")
        if eras[-1]["name"] == name:
            raise CountryError("いまと同じ元号です")
        profile.eras = [*eras, {"name": name, "start_turn": turn}]
    profile.era_kind = "regnal"
    profile.save()
    return profile


# --- 紹介文 ---------------------------------------------------------------------------------


def set_intro(island: Island, text: Any) -> Profile:
    if not isinstance(text, str):
        raise CountryError("紹介文は文字で送ってください")
    text = _plain(text.strip(), "紹介文", lines=True)
    if len(text) > INTRO_MAX:
        raise CountryError(f"紹介文は {INTRO_MAX} 字までです（いま {len(text)} 字）")
    profile = profile_of(island)
    # 管理者が隠した文と同じ文を保存し直しても、隠したまま（書き直したら、管理者がもう一度見る）
    if text != profile.intro:
        profile.intro_hidden = False
    profile.intro = text
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
    digest = hashlib.sha256(cleaned).hexdigest()
    flag = Flag.objects.filter(island=island).first()
    # 管理者が隠した画像と同じ画像を上げ直しても、隠したまま
    hidden = flag is not None and flag.hidden and flag.digest == digest
    flag, _ = Flag.objects.update_or_create(
        island=island,
        defaults={
            "png": cleaned,
            "digest": digest,
            "width": width,
            "height": height,
            "hidden": hidden,
        },
    )
    return flag


def clear_flag(island: Island) -> None:
    Flag.objects.filter(island=island).delete()


# --- 自作の記念碑の絵 -----------------------------------------------------------------------

CUSTOM_MONUMENT = "monument_custom"


def _custom_monument(island: Island, number: Any) -> Facility:
    try:
        row = Facility.objects.filter(island=island, number=int(number)).first()
    except (TypeError, ValueError):
        row = None
    if row is None:
        raise CountryError("その施設はありません", 404)
    if row.kind != CUSTOM_MONUMENT:
        raise CountryError("絵を上げられるのは自作の記念碑だけです")
    return row


def set_monument_image(island: Island, number: Any, data: bytes) -> MonumentImage:
    """自作の記念碑の絵（PNG。国旗と同じ検査）。隠された絵と同じ画像の上げ直しは隠したまま。"""
    _custom_monument(island, number)
    try:
        cleaned, width, height = png.clean(
            data, settings.HAKONIWA_FLAG_MAX_KB * 1024, settings.HAKONIWA_FLAG_MAX_SIDE
        )
    except png.PngError as e:
        raise CountryError(e.text) from e
    digest = hashlib.sha256(cleaned).hexdigest()
    old = MonumentImage.objects.filter(island=island, number=int(number)).first()
    hidden = old is not None and old.hidden and old.digest == digest
    image, _ = MonumentImage.objects.update_or_create(
        island=island,
        number=int(number),
        defaults={
            "png": cleaned,
            "digest": digest,
            "width": width,
            "height": height,
            "hidden": hidden,
        },
    )
    return image


def clear_monument_image(island: Island, number: Any) -> None:
    _custom_monument(island, number)
    MonumentImage.objects.filter(island=island, number=int(number)).delete()


# --- 来訪者の一言 ---------------------------------------------------------------------------


def post_note(user, target: Island, text: Any) -> Note:
    """自分の国から、ほかの国のページに一言を残す。"""
    visitor = own_island(user)
    if visitor is None:
        raise CountryError("一言を残せるのは自分の国を持つ人だけです", 403)
    if visitor.pk == target.pk:
        raise CountryError("自分の国のページには残せません")
    if not isinstance(text, str):
        raise CountryError("一言は文字で送ってください")
    text = " ".join(_plain(text, "一言").split())
    if not text:
        raise CountryError("一言を書いてください")
    if len(text) > NOTE_MAX:
        raise CountryError(f"一言は {NOTE_MAX} 字までです（いま {len(text)} 字）")
    since = timezone.now() - timedelta(days=1)
    recent = Note.objects.filter(visitor=visitor, posted_at__gte=since)
    limit = settings.HAKONIWA_COUNTRY_NOTES_PER_DAY
    if recent.count() >= limit:
        raise CountryError(
            f"1つの国が残せる一言は 24 時間に {limit} 件までです。しばらく待ってください", 429
        )
    if recent.filter(island=target).count() >= NOTES_PER_TARGET:
        raise CountryError(
            f"同じ国のページに残せる一言は 24 時間に {NOTES_PER_TARGET} 件までです", 429
        )
    return Note.objects.create(
        world=target.world,
        island=target,
        visitor=visitor,
        author=user,
        text=text,
        turn=target.world.turn,
    )


def hide_note(island: Island, note_id: Any) -> None:
    """自分の国のページの一言を隠す（隠した一言は管理者が見られる）。"""
    try:
        updated = Note.objects.filter(island=island, pk=int(note_id)).update(hidden=True)
    except (TypeError, ValueError):
        updated = 0
    if not updated:
        raise CountryError("その一言はありません", 404)


def edit(island: Island, data: dict[str, Any]) -> None:
    """紹介文と紀年をまとめて書き換える。途中で断られたら、何も書き換えない。"""
    with transaction.atomic():
        if "intro" in data:
            set_intro(island, data["intro"])
        if "era" in data:
            era = data["era"]
            if not isinstance(era, dict):
                raise CountryError('era は {"kind": …, "name": …} で送ってください')
            set_era(island, str(era.get("kind", "")), era.get("name"))
        if "new_era" in data:
            new_era(island, data["new_era"])


def diary_data(island: Island) -> dict[str, Any]:
    """国の日誌の中身（まだ書いていなければ空）。持ち主だけに見せる（呼ぶ側で確かめる）。"""
    diary = Diary.objects.filter(island=island).first()
    if diary is None:
        return {"text": "", "turn": None, "updated_at": None}
    return {
        "text": diary.text,
        "turn": diary.turn,
        "updated_at": timezone.localtime(diary.updated_at).isoformat(),
    }


def set_diary(island: Island, text: Any) -> Diary:
    """国の日誌を書き換える（全文の置き換え。空にもできる）。"""
    if not isinstance(text, str):
        raise CountryError("日誌の文を送ってください")
    text = _plain(text, "日誌", lines=True).strip()
    if len(text) > DIARY_MAX:
        raise CountryError(f"日誌は {DIARY_MAX} 字までです（いまは {len(text)} 字）")
    diary, _ = Diary.objects.update_or_create(
        island=island, defaults={"text": text, "turn": island.world.turn}
    )
    return diary
