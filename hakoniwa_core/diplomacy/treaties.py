"""条約（フェーズ4-1。docs/design/10-politics.md PL-37、docs/design/15-military.md MI-04〜MI-06）。

条約は、両国が署名すると効く条項の束です。片方の国が命令で提案し（提案した国はその時に署名したことに
なる）、相手が命令で署名すると、そのターンの外交の段から効きます。民主主義の国は、署名と破棄と宣戦に
議会の採決が要ります（orders.yaml の parliament）。

条約は ``world.ext["diplomacy"]["treaties"]`` に、番号の順に辞書で持ちます。

- id・title・text（本文）・parties（[提案した島, 相手の島]）・proposer・clauses（clauses.py）
- state: proposed（返事待ち）・in_force（効いている）・ended（終わった）・refused（断られた）・
  withdrawn（提案を取り下げた）・lapsed（返事がないまま流れた）
- created・signed（島 → 署名したターン）・start（効き始めたターン）・end（終わったターン）・
  end_reason（notice 予告どおりの破棄・fulfilled 講和が済んだ・ceasefire_broken ほか）
- notice: 破棄の予告 {by, turn, until}（until のターンに終わる）
- violations: 違反の記録 [{turn, by, clause, reason}]

効いている間の条約の違反（破った国）は、違反の記録に残り、信用の傷になります（credit.py）。予告どおりの
破棄は罰なしです。ゲームが条約の中身を止めたり強いたりはしません（外交は遊ぶ人に任せる。PL-37）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.diplomacy import clauses as cl
from hakoniwa_core.diplomacy import credit, economic, is_inactive, next_id, war, world_state
from hakoniwa_core.state import IslandState, LogEntry, WorldState

PROPOSED = "proposed"
IN_FORCE = "in_force"
ENDED = "ended"
REFUSED = "refused"
WITHDRAWN = "withdrawn"
LAPSED = "lapsed"

LIVE = (PROPOSED, IN_FORCE)  # 終わっていない状態
KEEP_CLOSED = 36  # 終わった条約を、画面に出す記録に残すターン数（年表は別に残る）


def treaties(world: WorldState) -> list[dict[str, Any]]:
    return world_state(world)["treaties"]


def find(world: WorldState, treaty_id: int) -> dict[str, Any] | None:
    for t in treaties(world):
        if t["id"] == treaty_id:
            return t
    return None


def of_island(world: WorldState, island_id: int) -> list[dict[str, Any]]:
    return [t for t in treaties(world) if island_id in t["parties"]]


def partner_of(treaty: dict[str, Any], island_id: int) -> int:
    a, b = treaty["parties"]
    return b if a == island_id else a


def kinds(treaty: dict[str, Any]) -> list[str]:
    return [c["type"] for c in treaty["clauses"]]


def between(
    world: WorldState, a: int, b: int, kind: str | None = None, states: tuple[str, ...] = LIVE
) -> list[dict[str, Any]]:
    """a と b の間の条約（番号の順）。kind を決めるとその条項のあるものだけ。"""
    key = war.pair(a, b)
    return [
        t
        for t in treaties(world)
        if war.pair(*t["parties"]) == key
        and t["state"] in states
        and (kind is None or kind in kinds(t))
    ]


def in_force(world: WorldState, a: int, b: int, kind: str) -> dict[str, Any] | None:
    """a と b の間で効いている kind の条項のある条約（予告の間も効いている）。なければ None。"""
    found = between(world, a, b, kind, (IN_FORCE,))
    return found[0] if found else None


def _name(world: WorldState, island_id: int) -> str:
    island = world.islands.get(island_id)
    return island.name if island is not None else str(island_id)


def _log(
    world: WorldState,
    treaty: dict[str, Any],
    kind: str,
    only: int | None = None,
    hall: bool = False,
    **extra: Any,
) -> None:
    """条約の当事国のログに書く。hall は国際会議場に自動で載せる印（1 つの出来事に 1 件だけ
    付ける）。文面の country は、書く島から見た相手の国。"""
    for i in treaty["parties"]:
        if only is not None and i != only:
            continue
        island = world.islands.get(i)
        if island is None:
            continue
        args = {
            "treaty": treaty["id"],
            "title": treaty["title"],
            "country": _name(world, partner_of(treaty, i)),
            "clauses": kinds(treaty),
            **extra,
        }
        if hall and i == treaty["parties"][0]:
            args["hall"] = True
            args["a"] = _name(world, treaty["parties"][0])
            args["b"] = _name(world, treaty["parties"][1])
        island.log.append(LogEntry(kind, args))


# --- 提案・署名・断り・取り下げ ---------------------------------------------------------------


def propose(
    world: WorldState,
    config: GameConfig,
    island: IslandState,
    payload: dict[str, Any],
    turn: int,
) -> str | None:
    """条約を提案する。断るわけがあれば messages の名前。"""
    partner = payload["partner"]
    other = world.islands.get(partner)
    if partner == island.id or other is None:
        return "reason_treaty_partner"
    cfg = config.get("diplomacy")
    waiting = [t for t in treaties(world) if t["proposer"] == island.id and t["state"] == PROPOSED]
    # 休止中の島への提案は返事待ちの席に残らない（受けるか断るかをその場で決める）ので数えない
    if not is_inactive(other) and len(waiting) >= cfg["max_proposals"]:
        return "reason_treaty_too_many"
    clean: list[dict[str, Any]] = []
    for raw in payload["clauses"]:
        clause, reason = cl.clean(raw, island.id, partner, config)
        if reason is not None:
            return reason
        assert clause is not None
        clean.append(clause)
    reason = cl.check_set(world, clean, island.id, partner, turn)
    if reason is not None:
        return reason
    caretaker = is_inactive(other)
    if caretaker and not cl.caretaker_accepts(clean, partner):
        return "reason_treaty_inactive"
    for c in clean:
        # 効いている講和は、戦争を終えた後は賠償が残っているだけなので、次の戦争の講和を塞がない
        states = (PROPOSED,) if c["type"] == cl.PEACE else LIVE
        if c["type"] in cl.SINGLE and between(world, island.id, partner, c["type"], states):
            return "reason_treaty_exists"
    title = (payload.get("title") or "").strip() or _default_title(clean)
    treaty = {
        "id": next_id(world_state(world), "treaty"),
        "title": title[:40],
        "text": (payload.get("text") or "").strip()[:400],
        "parties": [island.id, partner],
        "proposer": island.id,
        "clauses": clean,
        "state": PROPOSED,
        "created": turn,
        "signed": {str(island.id): turn},
        "start": None,
        "end": None,
        "end_reason": None,
        "notice": None,
        "violations": [],
    }
    treaties(world).append(treaty)
    if caretaker:
        # 休止中の国は応じられないので、停戦と、賠償も約束もない講和だけは、留守を預かる政府が受ける
        # （W-96）。署名したことにして、そのまま効かせる
        treaty["signed"][str(partner)] = turn
        treaty["caretaker"] = partner
        _force(world, config, treaty, turn)
        _log(world, treaty, "treaty_caretaker", only=partner)
        return None
    _log(world, treaty, "treaty_proposed", only=island.id)
    _log(world, treaty, "treaty_offer", only=partner)
    return None


def _default_title(clauses: list[dict[str, Any]]) -> str:
    names = {
        cl.CEASEFIRE: "停戦協定",
        cl.PEACE: "講和条約",
        cl.NON_AGGRESSION: "不可侵条約",
        cl.ALLIANCE: "同盟条約",
        cl.BLOC: "貿易圏の協定",
        cl.CONTRACT: "貿易の契約",
        cl.PEG: "固定相場の協定",
        cl.SWAP: "通貨融通の協定",
        cl.AID: "援助の約束",
        cl.FREE_MOVEMENT: "移民の協定",
        cl.REFUGEES: "難民の受け入れ",
        cl.NO_NUCLEAR: "核を持たない約束",
    }
    return "・".join(names.get(c["type"], c["type"]) for c in clauses)


def _party_check(treaty: dict[str, Any] | None, island: IslandState) -> str | None:
    if treaty is None:
        return "reason_treaty_unknown"
    if island.id not in treaty["parties"]:
        return "reason_treaty_not_party"
    return None


def sign(
    world: WorldState,
    config: GameConfig,
    island: IslandState,
    treaty: dict[str, Any] | None,
    turn: int,
) -> str | None:
    """相手が出した条約に署名する。両国がそろうと、そのターンから効く。"""
    reason = _party_check(treaty, island)
    if reason is not None:
        return reason
    assert treaty is not None
    if treaty["state"] != PROPOSED:
        return "reason_treaty_closed"
    if treaty["proposer"] == island.id:
        return "reason_treaty_own_offer"
    proposer = world.islands.get(treaty["proposer"])
    if proposer is None or is_inactive(proposer):
        return "reason_treaty_inactive"
    # 提案のあとで戦争の状態が変わっていれば、結び直す（戦争が終わっていた・停戦が済んでいた等）
    a, b = treaty["parties"]
    reason = cl.check_set(world, treaty["clauses"], a, b, turn)
    if reason is not None:
        return reason
    treaty["signed"][str(island.id)] = turn
    _force(world, config, treaty, turn)
    return None


def refuse(
    world: WorldState, island: IslandState, treaty: dict[str, Any] | None, turn: int
) -> str | None:
    reason = _party_check(treaty, island)
    if reason is not None:
        return reason
    assert treaty is not None
    if treaty["state"] != PROPOSED or treaty["proposer"] == island.id:
        return "reason_treaty_closed"
    treaty["state"] = REFUSED
    treaty["end"] = turn
    _log(world, treaty, "treaty_refused")
    return None


def withdraw(
    world: WorldState, island: IslandState, treaty: dict[str, Any] | None, turn: int
) -> str | None:
    reason = _party_check(treaty, island)
    if reason is not None:
        return reason
    assert treaty is not None
    if treaty["state"] != PROPOSED or treaty["proposer"] != island.id:
        return "reason_treaty_closed"
    treaty["state"] = WITHDRAWN
    treaty["end"] = turn
    _log(world, treaty, "treaty_withdrawn")
    return None


# --- 効き始め ---------------------------------------------------------------------------------


def _force(world: WorldState, config: GameConfig, treaty: dict[str, Any], turn: int) -> None:
    treaty["state"] = IN_FORCE
    treaty["start"] = turn
    a, b = treaty["parties"]
    w = war.open_war(world, a, b)
    have = kinds(treaty)
    for clause in treaty["clauses"]:
        kind = clause["type"]
        if kind == cl.CEASEFIRE and w is not None:
            war.begin_ceasefire(w, treaty["id"])
        elif kind == cl.PEACE and w is not None:
            war.end(w, treaty["id"], turn, clause["withdraw_turns"], clause["prisoners"])
            # 戦争を終えたので、その戦争の停戦の条約はここで終わる（次の戦争の停戦を塞がない）
            for old in between(world, a, b, cl.CEASEFIRE, (IN_FORCE,)):
                if old is not treaty:
                    _end(world, old, turn, "superseded")
        rep = clause.get("reparations")
        if rep is not None:
            rep["due"] = turn + rep["due_turns"]
            rep["state"] = "open"
            payer = world.islands.get(rep["payer"])
            if payer is not None:
                payer.log.append(
                    LogEntry(
                        "reparations_owed",
                        {
                            "country": _name(world, rep["payee"]),
                            "amount": rep["amount"],
                            "currency": _name(world, rep["currency"]),
                            "turn": rep["due"],
                            "treaty": treaty["id"],
                        },
                    )
                )
        if kind in cl.ECONOMIC:
            economic.on_force(world, config, treaty, clause, turn)
    # 国際会議場に載せる出来事は 1 つだけ（講和・停戦・ふつうの条約の順で、いちばん大きなもの）
    if cl.PEACE in have:
        _log(
            world,
            treaty,
            "war_ended",
            hall=True,
            withdraw=_clause(treaty, cl.PEACE)["withdraw_turns"],
        )
    elif cl.CEASEFIRE in have:
        _log(world, treaty, "ceasefire_started", hall=True)
    else:
        _log(world, treaty, "treaty_in_force", hall=True)


def _clause(treaty: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(c for c in treaty["clauses"] if c["type"] == kind)


# --- 破棄 -------------------------------------------------------------------------------------


def denounce(
    world: WorldState,
    config: GameConfig,
    island: IslandState,
    treaty: dict[str, Any] | None,
    turn: int,
) -> str | None:
    """効いている条約を、予告つきで破棄する。予告のターンがたつと終わる（罰なし）。"""
    reason = _party_check(treaty, island)
    if reason is not None:
        return reason
    assert treaty is not None
    if treaty["state"] != IN_FORCE:
        return "reason_treaty_closed"
    if treaty["notice"] is not None:
        return "reason_treaty_denounced"
    unsettled = {c["type"] for c in treaty["clauses"] if not _settled(c)} - set(cl.TERMINABLE)
    if cl.PEACE in unsettled:
        return "reason_treaty_reparations_open"
    if unsettled:  # 契約の期間か援助の回数が終わっていない
        return "reason_treaty_term_open"
    notice = max(
        (cl.notice_turns(c, config) for c in treaty["clauses"] if c["type"] in cl.TERMINABLE),
        default=0,
    )
    if notice <= 0:
        return "reason_treaty_not_terminable"
    treaty["notice"] = {"by": island.id, "turn": turn, "until": turn + notice}
    _log(world, treaty, "treaty_denounced", hall=True, by=island.name, until=turn + notice)
    return None


def _settled(clause: dict[str, Any]) -> bool:
    """済む条項が済んだか（講和は賠償を払い終えた・期日を過ぎて違反として記録した、契約は期間が
    終わった、援助は回数を終えた）。済まない条項（破棄できるもの）は True。"""
    if clause["type"] in (cl.CONTRACT, cl.AID):
        return economic.settled(clause)
    rep = clause.get("reparations")
    return rep is None or rep["state"] in ("paid", "defaulted")


# --- 毎ターンの見張り -------------------------------------------------------------------------


def upkeep(world: WorldState, config: GameConfig, turn: int) -> None:
    """流れた提案・予告のターンがたった破棄・講和の賠償の期日・済んだ条約の始末（番号の順）。"""
    proposal_turns = config.get("diplomacy.proposal_turns")
    for t in list(treaties(world)):
        if t["state"] == PROPOSED and turn - t["created"] >= proposal_turns:
            t["state"] = LAPSED
            t["end"] = turn
            _log(world, t, "treaty_lapsed")
        elif t["state"] == IN_FORCE:
            _reparations(world, config, t, turn)
            economic.watch(world, config, t, turn)
            notice = t["notice"]
            if notice is not None and turn >= notice["until"]:
                _end(world, t, turn, "notice")
            elif all(c["type"] in cl.FINITE for c in t["clauses"]) and all(
                _settled(c) for c in t["clauses"]
            ):
                _end(world, t, turn, "fulfilled")
    # 画面に出す記録は、終わって KEEP_CLOSED ターンたった条約を外す
    treaties(world)[:] = [
        t for t in treaties(world) if t["state"] in LIVE or turn - t["end"] <= KEEP_CLOSED
    ]


def _end(world: WorldState, treaty: dict[str, Any], turn: int, reason: str) -> None:
    treaty["state"] = ENDED
    treaty["end"] = turn
    treaty["end_reason"] = reason
    a, b = treaty["parties"]
    w = war.open_war(world, a, b)
    if cl.CEASEFIRE in kinds(treaty) and w is not None and w["ceasefire"] == treaty["id"]:
        war.resume(w)
        _log(world, treaty, "war_resumed", hall=True)
    elif reason == "notice":
        _log(world, treaty, "treaty_ended", hall=True)
    else:
        _log(world, treaty, "treaty_fulfilled")


def _reparations(world: WorldState, config: GameConfig, treaty: dict[str, Any], turn: int) -> None:
    for clause in treaty["clauses"]:
        rep = clause.get("reparations")
        if rep is None or rep["state"] != "open":
            continue
        if rep["paid"] >= rep["amount"]:
            rep["state"] = "paid"
            _log(world, treaty, "reparations_paid", hall=False)
        elif turn > rep["due"]:
            rep["state"] = "defaulted"
            violate(
                world,
                config,
                treaty,
                rep["payer"],
                clause["type"],
                "reparations",
                turn,
                weight=1,
            )


def note_payment(
    world: WorldState, payer: int, payee: int, currency: int, amount: int, turn: int
) -> None:
    """payer から payee へ送ったお金の援助を、講和の賠償の払いと、援助の約束の払いに数える（trade の
    援助から呼ぶ）。1 回の援助は、先に賠償の残りに数え、余りを援助の約束に数える。"""
    left = amount
    for t in treaties(world):
        if t["state"] != IN_FORCE:
            continue
        for clause in t["clauses"]:
            rep = clause.get("reparations")
            if (
                rep is not None
                and rep["state"] == "open"
                and rep["payer"] == payer
                and rep["payee"] == payee
                and rep["currency"] == currency
            ):
                take = min(left, max(0, rep["amount"] - rep["paid"]))
                rep["paid"] += take
                left -= take
    if left > 0:
        economic.count_aid(treaties(world), payer, payee, currency, left)


def partners_with(world: WorldState, island_id: int, kind: str) -> set[int]:
    """島が、条項 kind のある効いている条約（予告の間も）を結んでいる相手の島。"""
    return {
        partner_of(t, island_id)
        for t in of_island(world, island_id)
        if t["state"] == IN_FORCE and kind in kinds(t)
    }


# --- 違反 -------------------------------------------------------------------------------------


def violate(
    world: WorldState,
    config: GameConfig,
    treaty: dict[str, Any],
    by: int,
    clause: str,
    reason: str,
    turn: int,
    weight: int = 1,
) -> None:
    """条約の違反を記録し、破った国の信用に傷を付ける（credit.py）。当事国のログと国際会議場に出す。"""
    treaty["violations"].append({"turn": turn, "by": by, "clause": clause, "reason": reason})
    island = world.islands.get(by)
    if island is not None:
        credit.record(island, turn, treaty["id"], clause, weight, reason, config)
    _log(
        world,
        treaty,
        "treaty_violated",
        hall=True,
        by=_name(world, by),
        clause=f"clause_{clause}",
        why=f"why_{reason}",
    )


def on_war_declared(
    world: WorldState, config: GameConfig, attacker: int, defender: int, turn: int
) -> None:
    """宣戦したとき、不可侵と同盟の条約を破っていれば違反として記録する（宣戦そのものは止めない）。"""
    for t in between(world, attacker, defender, None, (IN_FORCE,)):
        notice = t.get("notice")
        if notice and turn >= notice["until"]:
            continue  # 予告のターンがたった条約は、このターンに終わる（予告どおりの破棄は罰なし）
        for kind in cl.BREAKABLE_BY_WAR:
            if kind in kinds(t):
                violate(world, config, t, attacker, kind, "war_declared", turn)
