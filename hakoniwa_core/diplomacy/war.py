"""戦争の状態（フェーズ4-1。docs/design/15-military.md MI-02〜MI-05）。

国と国の間は「平時」か「戦争」です。戦いと占領と通商破壊とミサイルは、戦争の相手にだけ向けられます
（中立の国の船と部隊は撃ちません。4-2・4-3 の段が ``fighting`` で確かめます）。

戦争は ``world.ext["diplomacy"]["wars"]`` に、番号の順に辞書で持ちます。

- id・attacker（宣戦した島）・defender（された島）・declared（布告のターン）・start（戦争が始まる
  ターン。布告の次のターン）
- state: war（戦っている）・ceasefire（停戦の間。戦闘と攻めの動きを止めるが戦争は終わっていない）・
  ended（終わった）
- ceasefire: 停戦の条約の番号（停戦の間だけ）
- ended: 終わったターン。ended_by: 終わらせた講和の条約の番号。withdraw_until: 相手の島の部隊が引き
  揚げる期限のターン。prisoners: 講和で捕虜を返すか

同じ組の間の戦争は同時に 1 つだけで、終わった戦争は記録に残します（年表・伝統の賞・記念碑の
条件が読む）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.diplomacy import next_id, world_state
from hakoniwa_core.state import WorldState

WAR = "war"
CEASEFIRE = "ceasefire"
ENDED = "ended"

PEACE = "peace"  # 戦争のない組の状態（status の答え）
DECLARED = "declared"  # 布告したが、まだ戦争が始まっていない（布告のターンの間）


def wars(world: WorldState) -> list[dict[str, Any]]:
    return world_state(world)["wars"]


def pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def open_war(world: WorldState, a: int, b: int) -> dict[str, Any] | None:
    """a と b の間の、終わっていない戦争（戦争中・停戦中）。なければ None。"""
    key = pair(a, b)
    for w in wars(world):
        if w["state"] != ENDED and pair(w["attacker"], w["defender"]) == key:
            return w
    return None


def status(world: WorldState, a: int, b: int, turn: int) -> str:
    """a と b の今の状態: peace・declared・war・ceasefire。"""
    w = open_war(world, a, b)
    if w is None:
        return PEACE
    if w["state"] == CEASEFIRE:
        return CEASEFIRE
    return WAR if w["start"] <= turn else DECLARED


def fighting(world: WorldState, a: int, b: int, turn: int) -> bool:
    """a と b が戦っていて、戦闘・占領・攻撃をしてよいか（停戦の間と、布告のターンは False）。"""
    return status(world, a, b, turn) == WAR


def open_wars_of(world: WorldState, island_id: int) -> list[dict[str, Any]]:
    """島が加わっている、終わっていない戦争（番号の順）。"""
    return [
        w
        for w in wars(world)
        if w["state"] != ENDED and island_id in (w["attacker"], w["defender"])
    ]


def enemies(world: WorldState, island_id: int, turn: int) -> list[int]:
    """島が今戦っている相手（島の番号の順。停戦の間の相手は含まない）。"""
    out = []
    for w in open_wars_of(world, island_id):
        other = w["defender"] if w["attacker"] == island_id else w["attacker"]
        if w["state"] == WAR and w["start"] <= turn:
            out.append(other)
    return sorted(out)


def other_side(w: dict[str, Any], island_id: int) -> int:
    return w["defender"] if w["attacker"] == island_id else w["attacker"]


def declare(world: WorldState, attacker: int, defender: int, turn: int) -> dict[str, Any]:
    """宣戦する。戦争は次のターンから始まる（同じターンに動いた部隊は奇襲になる。4-3）。"""
    w = {
        "id": next_id(world_state(world), "war"),
        "attacker": attacker,
        "defender": defender,
        "declared": turn,
        "start": turn + 1,
        "state": WAR,
        "ceasefire": None,
        "ended": None,
        "ended_by": None,
        "withdraw_until": None,
        "prisoners": False,
    }
    wars(world).append(w)
    return w


def begin_ceasefire(w: dict[str, Any], treaty_id: int) -> None:
    w["state"] = CEASEFIRE
    w["ceasefire"] = treaty_id


def resume(w: dict[str, Any]) -> None:
    """停戦が終わって戦争に戻る。"""
    w["state"] = WAR
    w["ceasefire"] = None


def end(
    w: dict[str, Any],
    treaty_id: int,
    turn: int,
    withdraw_turns: int,
    prisoners: bool,
) -> None:
    """講和で戦争を終える。相手の島の部隊は withdraw_turns ターンのうちに引き揚げる。"""
    w["state"] = ENDED
    w["ceasefire"] = None
    w["ended"] = turn
    w["ended_by"] = treaty_id
    w["withdraw_until"] = turn + withdraw_turns
    w["prisoners"] = prisoners


def must_withdraw(world: WorldState, a: int, b: int, turn: int) -> bool:
    """講和の後、a の部隊が b の島から引き揚げる期限の中か（4-2・4-3 の動きが読む）。"""
    key = pair(a, b)
    for w in wars(world):
        if w["state"] == ENDED and pair(w["attacker"], w["defender"]) == key:
            if w["withdraw_until"] is not None and w["ended"] <= turn <= w["withdraw_until"]:
                return True
    return False
