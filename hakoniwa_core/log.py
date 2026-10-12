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
}


def format_log(defs: Defs, kind: str, args: dict[str, Any], lang: str = "ja") -> str:
    """文の種類と引数から文面を作る。reason の文に引数があれば reason_args に入れておく。"""
    template = defs.name("messages", kind, lang)
    shown = {
        k: defs.name(_ARG_KINDS[k], v, lang) if k in _ARG_KINDS and isinstance(v, str) else v
        for k, v in args.items()
        if k != "reason_args"
    }
    if isinstance(args.get("good"), str):
        shown.setdefault("unit", defs.unit_name(args["good"], lang))
    if isinstance(args.get("goods"), list):
        shown["goods"] = "・".join(defs.name("goods", g, lang) for g in args["goods"])
    if isinstance(args.get("reason"), str):
        shown["reason"] = format_log(defs, args["reason"], args.get("reason_args") or {}, lang)
    try:
        return template.format(**shown)
    except (KeyError, IndexError, ValueError):
        return f"{template} {shown}"
