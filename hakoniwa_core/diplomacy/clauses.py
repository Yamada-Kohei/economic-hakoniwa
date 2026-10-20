"""条約の条項の種類。

条約は条項の束で、両国が署名すると効きます（treaties.py）。条項の種類ごとに、中身の確かめ・効き始め・
毎ターンの見張り・終わりの処理を持ちます。経済の取り決め（貿易圏・契約・固定相場・融通・援助・移民）の
条項は、同じ形で economic.py に置きます（回 4-1 の②）。

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

4-1 の②の条項（経済の取り決め。economic.py）:

- bloc: 貿易圏。圏を作って相手を入れる（代表は提案した国）か、提案した国が代表の圏に相手を入れる。
  どちらかが予告なしに圏を抜けると違反
- contract: 二国間契約。効くと契約（trade の契約）が始まる。決めた期間の前に解約する・物かお金が
  足りず届けられないと違反。期間が終わると済む
- peg: 固定相場。pegger の中央銀行が相手の通貨に固定する。やめる・外れると違反
- swap: 中央銀行の融通。相手の中央銀行が、手元の外貨の余りを貸せる相手に数える（違反はない）
- aid: お金の援助の約束。payer が一定の間隔で amount を送る（お金の援助の命令で送る）。届かない
  と違反。回数を終えると済む
- free_movement: 人の行き来の自由。両国が移民の受け入れを open に保つ。変えると違反
- refugees: 難民の受け入れ。host が難民を受け入れる政策（open か refugees）を保つ。変えると違反
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

BLOC = "bloc"
CONTRACT = "contract"
PEG = "peg"
SWAP = "swap"
AID = "aid"
FREE_MOVEMENT = "free_movement"
REFUGEES = "refugees"

MILITARY = (CEASEFIRE, PEACE, NON_AGGRESSION, ALLIANCE)
ECONOMIC = (BLOC, CONTRACT, PEG, SWAP, AID, FREE_MOVEMENT, REFUGEES)
TYPES = MILITARY + ECONOMIC
# 戦争のあいだに結べる条項（それ以外は、戦争の相手と結ぶのは講和の条約の一部としてだけ）
WARTIME = (CEASEFIRE, PEACE)
# 破棄できる条項（予告つき）。講和は戦争を終えた事実で、賠償が残る間は破棄できない
TERMINABLE = (CEASEFIRE, NON_AGGRESSION, ALLIANCE, BLOC, PEG, SWAP, FREE_MOVEMENT, REFUGEES)
# 済む条項（賠償を払い終える・契約の期間が終わる・援助の回数を終える）。済むまで予告つきの破棄は
# できず、条約の条項がすべて済んだものなら、条約が終わる
FINITE = (PEACE, CONTRACT, AID)
# 同じ組で、効いている・返事待ちのものが 1 つだけの条項（契約と援助は何本でも結べる）
SINGLE = (CEASEFIRE, PEACE, NON_AGGRESSION, ALLIANCE, BLOC, PEG, SWAP, FREE_MOVEMENT, REFUGEES)
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
    if kind in ECONOMIC:
        from hakoniwa_core.diplomacy import economic

        return economic.clean(out, clause, a, b, config)
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
    single = [k for k in kinds if k in SINGLE]
    if len(set(single)) != len(single):
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
    from hakoniwa_core.diplomacy import economic

    for c in clauses:
        if c["type"] in ECONOMIC:
            reason = economic.check(world, c, a, b)
            if reason is not None:
                return reason
    return None
