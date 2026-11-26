"""戦闘の跡（グラフィック強化 案26）とお金の出来事（案15）の材料。

どちらもターンログ（server.turns.models.TurnLog）に残る出来事のうち、
マスの位置を持つもの・首都に結び付けられるものを寄せるだけ。計算は変えず、
既にある記録を読む。核の出来事（nuclear_hit）は入れない（#5803: 第4波の
戦闘は通常の戦闘だけ。核の扱いは W-130(2) が開いたまま）。
"""

from __future__ import annotations

from typing import Any

from server.turns.models import TurnLog
from server.world import models as m

BATTLE_TURNS = 3  # 戦闘の跡を出すターンの幅（今のターンを含む）
MONEY_TURNS = 6  # お金の出来事を出すターンの幅（今のターンを含む）

# 戦闘の跡にする出来事（着弾・戦闘・施設の破壊・占領・暴動・反乱）。
# tile_occupying は占領した側のログに相手の島の座標で残るので入れない
# （tile_occupied が占領された島側の記録）。battle は両当事者のログに残るので、
# その島での戦い（args の island が自分の島の名前）のものだけを採る。
_BATTLE_HIT = {
    "battle_on_land",
    "missile_hit",
    "war_facility_ruined",
    "tile_occupied",
    "riot_destroyed",
    "rebellion_started",
    "rebels_routed",
}
_BATTLE_INTERCEPT = {"missile_intercepted"}
_BATTLE_KINDS = _BATTLE_HIT | _BATTLE_INTERCEPT | {"battle"}

# 銀行が危なかった出来事（救済・公的資金・債務の肩代わり）。どれも銀行の
# マスの座標で銀行のある島のログに残る。
_BANK_EVENTS = {"bank_bailed_out", "bank_bailed_in", "bank_debts_covered"}
_MONEY_KINDS = _BANK_EVENTS | {"bonds_backstop", "fx_peg_broken"}


def _xy(args: Any) -> tuple[int, int] | None:
    if not isinstance(args, dict):
        return None
    x, y = args.get("x"), args.get("y")
    if isinstance(x, int) and isinstance(y, int) and x >= 0 and y >= 0:
        return x, y
    return None


def battle_marks(island: m.Island) -> list[dict[str, Any]]:
    """直近 BATTLE_TURNS ターンの戦闘の跡 [{x, y, kind, age}]（y, x の順）。

    kind は "hit"（着弾・戦闘・破壊）か "intercept"（迎撃）。同じマスに
    複数あれば、いちばん新しいものを残す（同じ古さなら hit を先にする）。
    """
    turn = island.world.turn
    rows = TurnLog.objects.filter(
        island=island, turn__gt=turn - BATTLE_TURNS, kind__in=_BATTLE_KINDS
    ).order_by("turn", "id")
    seen: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        xy = _xy(row.args)
        if xy is None:
            continue
        if row.kind in _BATTLE_INTERCEPT:
            kind = "intercept"
        elif row.kind in _BATTLE_HIT:
            kind = "hit"
        elif row.kind == "battle" and (row.args or {}).get("island") == island.name:
            kind = "hit"
        else:
            continue
        age = turn - row.turn
        old = seen.get(xy)
        if old is None or age < old["age"] or (age == old["age"] and kind == "hit"):
            seen[xy] = {"x": xy[0], "y": xy[1], "kind": kind, "age": age}
    return sorted(seen.values(), key=lambda r: (r["y"], r["x"]))


def money_marks(island: m.Island) -> list[dict[str, Any]]:
    """直近 MONEY_TURNS ターンのお金の出来事 [{x, y, kind, age}]（y, x の順）。

    kind は "bank"（銀行の救済などがあった銀行のマス）、"cloud"（国債の
    下支えがあったターンの首都のマス）、"crowd"（通貨の peg が崩れた
    ターンの銀行のマス。銀行が無ければ首都）。
    """
    turn = island.world.turn
    rows = TurnLog.objects.filter(
        island=island, turn__gt=turn - MONEY_TURNS, kind__in=_MONEY_KINDS
    ).order_by("turn", "id")
    marks: dict[tuple[int, int, str], dict[str, Any]] = {}
    fx_age: int | None = None
    for row in rows:
        age = turn - row.turn
        if row.kind in _BANK_EVENTS:
            xy = _xy(row.args)
            if xy is None:
                continue
            key = (xy[0], xy[1], "bank")
            if key not in marks or age < marks[key]["age"]:
                marks[key] = {"x": xy[0], "y": xy[1], "kind": "bank", "age": age}
        elif row.kind == "bonds_backstop":
            if island.capital_x is None:
                continue
            key = (island.capital_x, island.capital_y, "cloud")
            if key not in marks or age < marks[key]["age"]:
                marks[key] = {
                    "x": island.capital_x,
                    "y": island.capital_y,
                    "kind": "cloud",
                    "age": age,
                }
        elif row.kind == "fx_peg_broken":
            if fx_age is None or age < fx_age:
                fx_age = age
    if fx_age is not None:
        targets = [(f.x, f.y) for f in island.facilities.filter(kind="bank", built=True, x__gte=0)]
        if not targets and island.capital_x is not None:
            targets = [(island.capital_x, island.capital_y)]
        for x, y in targets:
            key = (x, y, "crowd")
            if key not in marks:
                marks[key] = {"x": x, "y": y, "kind": "crowd", "age": fx_age}
    return sorted(marks.values(), key=lambda r: (r["y"], r["x"], r["kind"]))
