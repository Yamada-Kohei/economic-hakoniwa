"""外交の画面（フェーズ4-1）と、その JSON API の中身の組み立て。

ルールの計算はせず、外交の段が世界と島に残した状態（world.ext["diplomacy"]・island.ext["diplomacy"]。
説明は hakoniwa_core/diplomacy/）を読みます。

見せる範囲（設計「外交」MI-04〜MI-06）:
- 戦争と、効いている条約と、終わった条約と、条約の違反の記録は、誰にでも見せる（外交は公のこと）
- 返事待ちの条約の提案（届いたもの・出したもの）と、命令の選択肢は島の持ち主だけ
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.diplomacy import clauses as cl
from server.game import get_game
from server.web import display
from server.world import models as m

KEY = "diplomacy"
CLOSED_SHOWN = 12  # 終わった条約を出す数（新しいものから）
STATES = ("declared", "war", "ceasefire")
STATE_NAMES = {
    "declared": "布告（戦争は次のターンから）",
    "war": "戦争中",
    "ceasefire": "停戦中",
    "ended": "終結",
}
TREATY_STATE_NAMES = {
    "proposed": "返事待ち",
    "in_force": "効いている",
    "ended": "終わった",
    "refused": "断られた",
    "withdrawn": "取り下げた",
    "lapsed": "返事がなく流れた",
}
RULES = (
    "proposal_turns",
    "max_proposals",
    "notice_turns",
    "min_notice_turns",
    "max_notice_turns",
    "withdraw_turns",
    "max_withdraw_turns",
    "max_reparation_turns",
    "violation_premium",
    "violation_premium_max",
    "violation_decay_turns",
    "violation_repeat_turns",
    "max_contract_turns",
    "max_aid_period_turns",
    "max_aid_count",
)


def _raw(world: m.World) -> dict[str, Any]:
    return (world.ext or {}).get(KEY) or {}


def _names(world: m.World) -> dict[int, str]:
    return dict(m.Island.objects.filter(world=world).values_list("id", "name"))


def _ref(names: dict[int, str], island_id: int) -> dict[str, Any]:
    return {"id": island_id, "name": names.get(island_id, str(island_id))}


def _war_state(w: dict[str, Any], turn: int) -> str:
    if w["state"] == "war" and w["start"] > turn:
        return "declared"
    return w["state"]


def _war_row(w: dict[str, Any], names: dict[int, str], turn: int) -> dict[str, Any]:
    state = _war_state(w, turn)
    return {
        "id": w["id"],
        "attacker": _ref(names, w["attacker"]),
        "defender": _ref(names, w["defender"]),
        "declared": w["declared"],
        "start": w["start"],
        "state": state,
        "state_name": STATE_NAMES[state],
        "ceasefire": w.get("ceasefire"),
        "ended": w.get("ended"),
        "ended_by": w.get("ended_by"),
    }


def _clause_row(c: dict[str, Any], names: dict[int, str]) -> dict[str, Any]:
    row: dict[str, Any] = {"type": c["type"], "name": display.message(f"clause_{c['type']}")}
    for key in ("notice_turns", "withdraw_turns", "prisoners"):
        if key in c:
            row[key] = c[key]
    if c["type"] in cl.ECONOMIC:
        row["detail"] = display.clause_summary(c)
        row["terms"] = _terms(c, names)
    rep = c.get("reparations")
    if rep:
        row["reparations"] = {
            "payer": _ref(names, rep["payer"]),
            "payee": _ref(names, rep["payee"]),
            "currency": _ref(names, rep["currency"]),
            "amount": rep["amount"],
            "paid": rep["paid"],
            "due": rep["due"],
            "due_turns": rep["due_turns"],
            "state": rep["state"],
        }
    return row


TERM_KEYS = (
    "name",
    "open",
    "bloc_id",
    "good",
    "quantity",
    "price",
    "turns",
    "delivery",
    "contract",
    "done",
    "rate",
    "amount",
    "period",
    "count",
    "paid",
    "done_count",
    "start",
)
TERM_ISLANDS = ("seller", "buyer", "pegger", "payer", "payee", "host", "currency")


def _terms(c: dict[str, Any], names: dict[int, str]) -> dict[str, Any]:
    """経済の条項の中身（保存された条項から、島の番号を名前つきにして取り出す）。"""
    terms: dict[str, Any] = {k: c[k] for k in TERM_KEYS if k in c}
    for key in TERM_ISLANDS:
        if c.get(key) is not None:
            terms[key] = _ref(names, c[key])
    return terms


def _treaty_row(t: dict[str, Any], names: dict[int, str]) -> dict[str, Any]:
    a, b = t["parties"]
    return {
        "id": t["id"],
        "title": t["title"],
        "text": t.get("text", ""),
        "parties": [_ref(names, a), _ref(names, b)],
        "proposer": _ref(names, t["proposer"]),
        "clauses": [_clause_row(c, names) for c in t["clauses"]],
        "state": t["state"],
        "state_name": TREATY_STATE_NAMES[t["state"]],
        "created": t["created"],
        "start": t.get("start"),
        "end": t.get("end"),
        "end_reason": t.get("end_reason"),
        "notice": (
            {**t["notice"], "by": _ref(names, t["notice"]["by"])} if t.get("notice") else None
        ),
        "violations": [{**v, "by": _ref(names, v["by"])} for v in t.get("violations", [])],
    }


def world_view(world: m.World) -> dict[str, Any]:
    """世界の戦争（終わっていないもの）と、効いている条約。国際会議場に出す。"""
    names = _names(world)
    raw = _raw(world)
    turn = world.turn
    return {
        "wars": [_war_row(w, names, turn) for w in raw.get("wars", []) if w["state"] in STATES],
        "treaties": [
            _treaty_row(t, names) for t in raw.get("treaties", []) if t["state"] == "in_force"
        ],
    }


def diplomacy_data(island: m.Island, mine: bool) -> dict[str, Any]:
    """外交の画面の中身。持ち主には返事待ちの提案と命令の選択肢も。"""
    world = island.world
    names = _names(world)
    raw = _raw(world)
    turn = world.turn
    wars = [_war_row(w, names, turn) for w in raw.get("wars", []) if island.id in _sides(w)]
    mine_treaties = [t for t in raw.get("treaties", []) if island.id in t["parties"]]
    closed = [t for t in mine_treaties if t["state"] in ("ended",) or (mine and _dead(t))]
    violations = (island.ext or {}).get(KEY, {}).get("violations", [])
    data: dict[str, Any] = {
        "turn": turn,
        "wars": [w for w in wars if w["state"] in STATES],
        "past_wars": [w for w in wars if w["state"] == "ended"][-CLOSED_SHOWN:],
        "treaties": [_treaty_row(t, names) for t in mine_treaties if t["state"] == "in_force"],
        "closed": [_treaty_row(t, names) for t in closed][-CLOSED_SHOWN:][::-1],
        "violations": [
            {
                **v,
                "treaty_title": next(
                    (t["title"] for t in raw.get("treaties", []) if t["id"] == v["treaty"]), ""
                ),
                "clause_name": display.message(f"clause_{v['clause']}"),
                "reason_name": display.message(f"why_{v['reason']}"),
            }
            for v in violations
        ],
        "rules": rules(),
    }
    if mine:
        data["incoming"] = [
            _treaty_row(t, names)
            for t in mine_treaties
            if t["state"] == "proposed" and t["proposer"] != island.id
        ]
        data["outgoing"] = [
            _treaty_row(t, names)
            for t in mine_treaties
            if t["state"] == "proposed" and t["proposer"] == island.id
        ]
        data["choices"] = proposal_choices(world, island)
    return data


def rules() -> dict[str, Any]:
    """外交の決まりの数（game.yaml の diplomacy。入力欄の説明と上限に使う）。"""
    config = get_game().config
    return {k: config.get(f"diplomacy.{k}") for k in RULES}


def proposal_choices(world: m.World, island: m.Island) -> dict[str, Any]:
    """条約の提案の選択肢（相手にできる国と条項）。外交の画面と国際会議場の入力欄が使う。"""
    return {
        "partners": _partners(world, island),
        "truce_partners": _truce_partners(world, island),
        "clauses": [{"id": c, "name": display.message(f"clause_{c}")} for c in cl.TYPES],
    }


def _sides(w: dict[str, Any]) -> tuple[int, int]:
    return w["attacker"], w["defender"]


def _dead(t: dict[str, Any]) -> bool:
    return t["state"] in ("refused", "withdrawn", "lapsed")


def _partners(world: m.World, island: m.Island) -> list[dict[str, Any]]:
    """条約の相手にできる国（休止中の国は入れない）。"""
    rows = m.Island.objects.filter(world=world, inactive=False).exclude(pk=island.pk)
    return [{"id": i, "name": n} for i, n in rows.order_by("id").values_list("id", "name")]


def _truce_partners(world: m.World, island: m.Island) -> list[dict[str, Any]]:
    """自国と戦争（停戦の間も）をしている休止中の国。休止中の国とは、停戦と、休止中の国が払う
    賠償のない講和だけ結べる（W-96）ので、条約の「相手」の欄にだけ足す。"""
    at_war = {
        other
        for w in (_raw(world).get("wars") or [])
        if w["state"] in ("war", "ceasefire") and island.id in _sides(w)
        for other in _sides(w)
        if other != island.id
    }
    rows = m.Island.objects.filter(world=world, inactive=True, id__in=at_war)
    return [
        {"id": i, "name": f"{n}（休止中。停戦と講和だけ）"}
        for i, n in rows.order_by("id").values_list("id", "name")
    ]


def brief(island: m.Island, mine: bool) -> dict[str, Any]:
    """国の要約に出す外交の要約（戦争・効いている条約・持ち主には返事待ちの提案）。"""
    data = diplomacy_data(island, mine)
    out: dict[str, Any] = {
        "wars": [
            {
                "id": w["id"],
                "with": w["defender"] if w["attacker"]["id"] == island.id else w["attacker"],
                "state": w["state"],
                "state_name": w["state_name"],
            }
            for w in data["wars"]
        ],
        "treaties": [
            {
                "id": t["id"],
                "title": t["title"],
                "with": next(p for p in t["parties"] if p["id"] != island.id),
                "clauses": [c.get("detail") or c["name"] for c in t["clauses"]],
                "notice": t["notice"],
            }
            for t in data["treaties"]
        ],
        "violations": len(data["violations"]),
    }
    if mine:
        out["to_answer"] = [
            {"id": t["id"], "title": t["title"], "from": t["proposer"]} for t in data["incoming"]
        ]
    return out
