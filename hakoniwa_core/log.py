"""ターンログの文面づくり。ログには文の種類と引数（名前）だけを保存し、文面は表示のときに作る。"""

from __future__ import annotations

from typing import Any

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
}
# マスを持たない施設（x, y が負）の場所の書き方: 文面のこの部分を、表示名 OFF_MAP_TO に置き換える
OFF_MAP_FROM = "({x}, {y}) の"
OFF_MAP_TO = "off_map_place"


def _off_map(args: dict[str, Any]) -> bool:
    x, y = args.get("x"), args.get("y")
    return isinstance(x, int) and isinstance(y, int) and (x < 0 or y < 0)


def format_log(defs: Defs, kind: str, args: dict[str, Any], lang: str = "ja") -> str:
    """文の種類と引数から文面を作る。reason の文に引数があれば reason_args に入れておく。"""
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
    if isinstance(args.get("goods"), list):
        shown["goods"] = "・".join(defs.name("goods", g, lang) for g in args["goods"])
    for key in ("terms", "terms_before"):  # 国債の期間と割合 [[期間, %], ...]
        if isinstance(args.get(key), list):
            names = [f"{defs.name('finance', t, lang)} {p}%" for t, p in args[key]]
            shown[key] = "・".join(names)
    if isinstance(args.get("reason"), str):
        shown["reason"] = format_log(defs, args["reason"], args.get("reason_args") or {}, lang)
    try:
        return template.format(**shown)
    except (KeyError, IndexError, ValueError):
        return f"{template} {shown}"
