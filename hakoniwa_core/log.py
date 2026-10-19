"""ターンログの文面づくり。ログには文の種類と引数（名前）だけを保存し、文面は表示のときに作る。"""

from __future__ import annotations

import re
import string
from typing import Any

from hakoniwa_core.dates import Calendar
from hakoniwa_core.defs import Defs

# 引数の名前ごとに、どの表示名に置き換えるか
_ARG_KINDS = {
    "order": "orders",
    "reason": "messages",
    "facility": "facilities",
    "good": "goods",
    "terrain": "terrain",
    "level": "settlements",
    "axis": "messages",
    "field": "messages",
    "accounting": "messages",
    "zone": "messages",
    "item": "messages",
    "term": "finance",
    "entry": "messages",
    "unsold": "messages",
    "unsold_before": "messages",
    "method": "messages",
    "subsidy": "messages",
    "infra": "infrastructure",
    "climate": "climate",
    "recipe": "recipes",
    "industry": "industries",
    "mode": "messages",
    "holder": "messages",
    "politics": "regimes",
    "head_of_state": "regimes",
    "economy": "regimes",
    "before": "regimes",
    "building": "messages",
}
# マスを持たない施設（x, y が負）の場所の書き方: 文面のこの部分を、表示名 OFF_MAP_TO に置き換える
OFF_MAP_FROM = "({x}, {y}) の"
OFF_MAP_TO = "off_map_place"
# 文面の {turn:date} のように書いた引数（ターンの数）は、その時点の暦の日付で書く（format_turn）
DATE_SPEC = "date"


def _off_map(args: dict[str, Any]) -> bool:
    x, y = args.get("x"), args.get("y")
    return isinstance(x, int) and isinstance(y, int) and (x < 0 or y < 0)


def format_turn(defs: Defs, turn: int, calendar: Calendar | None = None, lang: str = "ja") -> str:
    """ある時点のターンの書き方。暦があれば「箱庭暦32年11月（ターン 383）」、なければ
    「ターン 383」。

    文面は定義データの messages（calendar_date・calendar_before・calendar_turn・turn_point）。
    """
    if calendar is None:
        return defs.name("messages", "turn_point", lang).format(turn=turn)
    date = calendar.date(turn)
    if date is None:  # 暦の始まる前（ターン 0）
        shown = defs.name("messages", "calendar_before", lang).format(calendar=calendar.name)
    else:
        shown = defs.name("messages", "calendar_date", lang).format(
            calendar=calendar.name, year=date[0], month=date[1]
        )
    return defs.name("messages", "calendar_turn", lang).format(date=shown, turn=turn)


# 文面の中で量と値段を見分ける形（W-30）: 「{amount:g} {unit}」の amount は量、
# 「1{unit}あたり {price:,}」の price は 1 単位あたりの値段。見せるときに財の display_scale で
# 量を掛け、値段を割る
_AMOUNT_BEFORE_UNIT = re.compile(r"\{(\w+)(?::[^}]*)?\} ?\{unit\}")
_PRICE_PER_UNIT = re.compile(r"1\{unit\}あたり ?\{(\w+)")


def _scale_goods_args(
    defs: Defs, good: str, template: str, args: dict[str, Any], shown: dict[str, Any]
) -> None:
    """財の文の量と値段を、見せる単位に直す（記録は計算の単位のまま、文にするときだけ）。"""
    for key in _AMOUNT_BEFORE_UNIT.findall(template):
        if isinstance(args.get(key), (int, float)):
            shown[key] = round(defs.shown_amount(good, args[key]), 3)
    for key in _PRICE_PER_UNIT.findall(template):
        if isinstance(args.get(key), (int, float)):
            shown[key] = defs.shown_price(good, args[key])


def format_log(
    defs: Defs,
    kind: str,
    args: dict[str, Any],
    lang: str = "ja",
    calendar: Calendar | None = None,
) -> str:
    """文の種類と引数から文面を作る。reason の文に引数があれば reason_args に入れておく。

    calendar は、文面の {turn:date} のような時点を書く暦（サーバーは世界の暦を渡す）。
    """
    template = defs.name("messages", kind, lang)
    if _off_map(args):  # 運送業・海運業のようなマスを持たない施設は、島全体の施設として書く
        template = template.replace(OFF_MAP_FROM, defs.name("messages", OFF_MAP_TO, lang))
    shown = {
        k: defs.name(_ARG_KINDS[k], v, lang) if k in _ARG_KINDS and isinstance(v, str) else v
        for k, v in args.items()
        if k != "reason_args"
    }
    if isinstance(args.get("good"), str):
        shown.setdefault("unit", defs.unit_name(args["good"], lang))
        _scale_goods_args(defs, args["good"], template, args, shown)
    if isinstance(args.get("goods"), list):
        names = [defs.name("goods", g, lang) for g in args["goods"]]
        shown["goods"] = "・".join(names) or defs.name("messages", "none", lang)
    if isinstance(args.get("industries"), list):  # 重点産業（フェーズ3-4）
        names = [defs.name("industries", g, lang) for g in args["industries"]]
        shown["industries"] = "・".join(names) or defs.name("messages", "none", lang)
    for key in ("terms", "terms_before"):  # 国債の期間と割合 [[期間, %], ...]
        if isinstance(args.get(key), list):
            names = [f"{defs.name('finance', t, lang)} {p}%" for t, p in args[key]]
            shown[key] = "・".join(names)
    if isinstance(args.get("reason"), str):
        shown["reason"] = format_log(
            defs, args["reason"], args.get("reason_args") or {}, lang, calendar
        )
    try:
        for _, key, spec, _ in list(string.Formatter().parse(template)):
            if key and spec == DATE_SPEC and isinstance(shown.get(key), int):
                shown[key] = format_turn(defs, shown[key], calendar, lang)
                template = template.replace(f"{{{key}:{DATE_SPEC}}}", f"{{{key}}}")
        return template.format(**shown)
    except (KeyError, IndexError, ValueError):
        return f"{template} {shown}"
