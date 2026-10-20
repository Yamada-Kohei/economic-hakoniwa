"""条約の条項の種類。

条約は条項の束で、両国が署名すると効きます（treaties.py）。条項の種類ごとに、中身の確かめ・効き始め・
毎ターンの見張り・終わりの処理を持ちます。経済の取り決め（貿易圏・契約・固定相場・融通・援助・移民）の
条項は、同じ形でここに足します（回 4-1 の②）。

4-1 の条項:

- ceasefire: 停戦。戦争の相手とだけ。両国の署名で効き、停戦の間は戦闘と攻めの動きを止める（部隊は
  その場に残る。補給と引き揚げの船は通す）。予告つきで破棄でき、予告のターンがたつと戦争に戻る
- peace: 講和。戦争の相手とだけ。戦争を終え、引き揚げの期限（withdraw_turns）を決め、捕虜を
  返すか（prisoners）と、賠償（reparations: 払う国・通貨・額・効いてから何ターンのうちに）を
  書ける。ゲームは賠償を払わせず、払う国が援助の命令で送り、期日までに届かなければ違反として
  記録する（MI-05）。破棄はできない
- non_aggression: 不可侵。破棄の予告から notice_turns ターンがたつ前に宣戦すると違反（MI-06）
- alliance: 同盟。互いの見える範囲を分け合い、相手の軍港と港で補給できる（4-2 以降の段が
  読む）。同盟国が攻められても自動では参戦しない。同盟国に宣戦すると違反
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.diplomacy import war
from hakoniwa_core.state import WorldState

CEASEFIRE = "ceasefire"
PEACE = "peace"
NON_AGGRESSION = "non_aggression"
ALLIANCE = "alliance"

TYPES = (CEASEFIRE, PEACE, NON_AGGRESSION, ALLIANCE)
# 戦争のあいだに結べる条項（それ以外は、戦争の相手と結ぶのは講和の条約の一部としてだけ）
WARTIME = (CEASEFIRE, PEACE)
# 破棄できる条項（予告つき）。講和は戦争を終えた事実で、賠償が残る間は破棄できない
TERMINABLE = (CEASEFIRE, NON_AGGRESSION, ALLIANCE)
# 同じ組で、効いている・返事待ちのものが 1 つだけの条項
SINGLE = (CEASEFIRE, PEACE, NON_AGGRESSION, ALLIANCE)
# 効いている間、同じ組の戦争を止める（宣戦しても戦争にならない）わけではない。違反になる条項
BREAKABLE_BY_WAR = (NON_AGGRESSION, ALLIANCE)


def notice_turns(clause: dict[str, Any], config: GameConfig) -> int:
    return int(clause.get("notice_turns") or config.get("diplomacy.notice_turns"))


def clean(
    clause: dict[str, Any], a: int, b: int, config: GameConfig
) -> tuple[dict[str, Any] | None, str | None]:
    """命令で書かれた条項を、保存する形にそろえる。(条項, None) か (None, 断るわけ)。

    a は提案する島、b は相手の島。戦争の有無などの世界との突き合わせは treaties.propose が行う。
    """
    kind = clause.get("type")
    if kind not in TYPES:
        return None, "reason_clause_unknown"
    out: dict[str, Any] = {"type": kind}
    cfg = config.get("diplomacy")
    if kind in TERMINABLE:
        n = int(clause.get("notice_turns") or cfg["notice_turns"])
        if not cfg["min_notice_turns"] <= n <= cfg["max_notice_turns"]:
            return None, "reason_clause_notice"
        out["notice_turns"] = n
    if kind == PEACE:
        withdraw = int(clause.get("withdraw_turns", cfg["withdraw_turns"]))
        if not 0 <= withdraw <= cfg["max_withdraw_turns"]:
            return None, "reason_clause_withdraw"
        out["withdraw_turns"] = withdraw
        out["prisoners"] = bool(clause.get("prisoners", True))
        rep = clause.get("reparations")
        if rep is not None:
            payer, amount = rep.get("payer"), int(rep.get("amount", 0))
            due = int(rep.get("due_turns", 0))
            if payer not in (a, b) or amount <= 0:
                return None, "reason_clause_reparations"
            if not 1 <= due <= cfg["max_reparation_turns"]:
                return None, "reason_clause_reparations"
            payee = b if payer == a else a
            currency = rep.get("currency", payer)
            if currency not in (a, b):
                return None, "reason_contract_currency"
            out["reparations"] = {
                "payer": payer,
                "payee": payee,
                "currency": currency,
                "amount": amount,
                "due_turns": due,
                "due": None,
                "paid": 0,
                "state": "scheduled",
            }
    return out, None


def check_set(
    world: WorldState, clauses: list[dict[str, Any]], a: int, b: int, turn: int
) -> str | None:
    """条項の組み合わせと、今の戦争の状態との突き合わせ。断るわけがあれば messages の名前。"""
    kinds = [c["type"] for c in clauses]
    if len(set(kinds)) != len(kinds):
        return "reason_clause_duplicate"
    if CEASEFIRE in kinds and PEACE in kinds:
        return "reason_clause_ceasefire_peace"
    w = war.open_war(world, a, b)
    if CEASEFIRE in kinds and (w is None or w["state"] != war.WAR):
        return "reason_clause_no_war"
    if PEACE in kinds and w is None:
        return "reason_clause_no_war"
    if w is not None:
        # 戦争のあいだは、停戦と講和（と、講和に添える取り決め）しか結べない
        if CEASEFIRE in kinds:
            if any(k not in WARTIME for k in kinds):
                return "reason_clause_wartime"
        elif PEACE not in kinds:
            return "reason_clause_wartime"
    return None
