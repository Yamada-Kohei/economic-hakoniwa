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
}


def format_log(defs: Defs, kind: str, args: dict[str, Any], lang: str = "ja") -> str:
    template = defs.name("messages", kind, lang)
    shown = {
        k: defs.name(_ARG_KINDS[k], v, lang) if k in _ARG_KINDS and isinstance(v, str) else v
        for k, v in args.items()
    }
    try:
        return template.format(**shown)
    except (KeyError, IndexError):
        return f"{template} {shown}"
