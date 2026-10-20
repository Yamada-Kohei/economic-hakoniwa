"""記念碑の碑文と一覧（フェーズ4-1。設計書 PO-47・PO-48）。

碑文は「自動の文＋遊ぶ人が足した文」。自動の文は、建てたときの記録（施設の ``ext["monument"]``。
hakoniwa_core/monuments.py）から作り、後で変わらない。検証の碑だけは、建国から決まったターン後の統計
（残してある）を読む。足した文は国が書くもので、制御文字や文字の向きの制御は断ってある（命令を受けるとき）。
自作の記念碑（monument_custom）の絵は MonumentImage（国旗と同じ守り。管理者が隠せる）。
"""

from __future__ import annotations

from typing import Any

from django.urls import reverse

from hakoniwa_core import monuments as core
from hakoniwa_core.log import format_log
from server.country.models import MonumentImage
from server.game import get_game
from server.web import display
from server.world import models as m

PREFIX = "monument_"


def _text_of(kind: str) -> str:
    return kind.removeprefix(PREFIX)


def _verification(island: m.Island, symbol: str, turns: int) -> dict[str, Any]:
    """建国から turns ターン目（1 か月 1 ターン）の人口・就業者・GDP。まだ残っていなければ空。"""
    turn = max(1, int((island.ext or {}).get("founded_turn") or 1)) + turns
    values = dict(
        m.StatRecord.objects.filter(
            island=island, turn=turn, name__in=("population", "employed", "gdp")
        ).values_list("name", "value")
    )
    if len(values) < 3:
        return {}
    return {
        "months": turns,
        "population": f"{int(values['population']):,} 人",
        "employed": f"{int(values['employed']):,} 人",
        "gdp": display.money_text(int(values["gdp"]), symbol),
    }


def auto_text(island: m.Island, kind: str, facts: dict[str, Any]) -> str:
    """建てたときの記録から、自動の碑文を作る。"""
    game = get_game()
    defs = game.defs
    symbol = (island.names or {}).get("currency_symbol", "")
    key = _text_of(kind)
    args = dict(facts)
    if key == "founding":
        args["leader"] = facts.get("leader") or "この国の元首"
    elif key == "peace":
        args["years"] = int(facts.get("turns", 0)) // 12
    elif key == "battle":
        args.setdefault("country", "")
    elif key == "cenotaph":
        cause = str(facts.get("cause", ""))
        args["cause"] = defs.name("messages", f"monument_cause_{cause}") if cause else ""
    elif key == "leader":
        args["leader"] = facts.get("leader") or "この国の元首"
    elif key == "worker":
        industry = str(facts.get("industry", ""))
        args["industry"] = display.title_name(industry) if industry else ""
    elif key == "ledger":
        if not facts:
            return defs.name("messages", "monument_text_ledger_none")
        args = {
            "entry": defs.name("finance", str(facts.get("kind", ""))),
            "amount": display.money_text(int(facts.get("amount", 0)), symbol),
        }
    elif key == "verification":
        turns = int(game.config.get("monuments.verification_turns"))
        args = _verification(island, symbol, turns)
        if not args:
            return defs.name("messages", "monument_text_verification_none")
    return format_log(defs, f"monument_text_{key}", args, calendar=game.calendar)


def detail(island: m.Island, row: m.Facility) -> dict[str, Any] | None:
    """施設 row が記念碑なら、碑文など（なければ None）。"""
    if not core.is_monument(get_game().defs, row.kind):
        return None
    record = (row.ext or {}).get(core.MONUMENT) or {}
    note = [line for line in str(record.get("note", "")).split("\n") if line]
    out: dict[str, Any] = {
        "text": auto_text(island, row.kind, record.get("facts") or {}),
        "note": note,
        "built_turn": record.get("turn"),
        "attraction": get_game().defs.facilities[row.kind].get("attraction", 0),
    }
    if row.kind == "monument_custom":
        out["image"] = reverse("monument_image", args=[island.id, row.number])
    return out


def listing(island: m.Island, mine: bool = False) -> list[dict[str, Any]]:
    """国の記念碑の一覧（番号の順。工事中も built で見分ける）。"""
    defs = get_game().defs
    rows = [f for f in island.facilities.order_by("number") if core.is_monument(defs, f.kind)]
    hidden = set(
        MonumentImage.objects.filter(island=island, hidden=True).values_list("number", flat=True)
    )
    out = []
    for f in rows:
        info = detail(island, f) or {}
        out.append(
            {
                "number": f.number,
                "kind": f.kind,
                "kind_name": defs.name("facilities", f.kind),
                "name": f.name or defs.name("facilities", f.kind),
                "x": f.x,
                "y": f.y,
                "built": f.built,
                **info,
            }
        )
        if mine and f.number in hidden:
            out[-1]["image_hidden_by_admin"] = True
    return out


def count(island: m.Island) -> int:
    """完成している記念碑の数（国の要約に添える）。"""
    kinds = [k for k, f in get_game().defs.facilities.items() if "monument" in f]
    return island.facilities.filter(kind__in=kinds, built=True).count()
