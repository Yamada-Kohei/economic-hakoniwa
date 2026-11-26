"""地図の変遷と変化レンズ（グラフィック強化 案29）。

節目のターンに残した地図の写し（hakoniwa_core/map_history が island.ext に保存）を、
持ち主の地図で見比べるための材料にします。

- snapshot_list: 写しの一覧（ターンと、節目の名前を付けた表示名）
- snapshot_map: 写しを地図の API と同じ形にしたもの（昔の地図をそのまま描く）
- lens_marks: 写しと今の姿を見た目で比べた差（変化レンズ）。新しくできた物は
  new、無くなった・衰えた物は down、残りの姿の変化は changed の 3 種に分けます
  （hal #5716: 新規は上向き、衰退は下向き、変化は輪郭だけ）

比べ方は server/web/changes.py と同じ「見た目の差」で、施設の名前や状態のような
絵に出ない違いや、指標の変化は数えません。読みは持ち主の地図だけに使います
（島の地図の API が ?history=1 / ?snapshot= / ?lens= のとき、持ち主にだけ足す）。

記憶の刻印（案35）は engraving_list: 残した出来事を、災害（disaster）と
戦い・騒ぎの傷跡（scar）に分けて地図の印の材料にします。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import map_history
from server.game import get_game
from server.web import display
from server.web.changes import _look
from server.world import models as m

NEW = "new"
DOWN = "down"
CHANGED = "changed"

DISASTER = "disaster"
SCAR = "scar"

_KIND_KEYS = {
    map_history.START: "map_snapshot_start",
    "anniversary_founding": "map_snapshot_anniversary",
    "milestone_population": "map_snapshot_population",
    "milestone_gdp": "map_snapshot_gdp",
}


def _snapshots(island: m.Island) -> list[dict[str, Any]]:
    rows = (island.ext or {}).get(map_history.SNAPSHOTS) or []
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get("turn"), int)]


def snapshot_list(island: m.Island) -> list[dict[str, Any]]:
    """写しの一覧。表示名（節目の和名）は messages から作る（直書きを増やさない）。"""
    out = []
    for snap in _snapshots(island):
        kinds = display.message("map_snapshot_kind_join").join(
            display.message(_KIND_KEYS[kind])
            for kind in snap.get("kinds", [])
            if kind in _KIND_KEYS
        )
        out.append(
            {
                "turn": snap["turn"],
                "kinds": [kind for kind in snap.get("kinds", []) if kind in _KIND_KEYS],
                "label": display.message("map_snapshot_option", turn=snap["turn"], kinds=kinds),
            }
        )
    return out


def engraving_list(island: m.Island) -> list[dict[str, Any]]:
    """記憶の刻印（案35）の一覧。種類を災害と傷跡に分け、地図の印の材料にする。"""
    rows = (island.ext or {}).get(map_history.ENGRAVINGS) or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        x, y, kind = row.get("x"), row.get("y"), row.get("kind")
        if not isinstance(x, int) or not isinstance(y, int) or not isinstance(kind, str):
            continue
        out.append(
            {
                "turn": row.get("turn"),
                "x": x,
                "y": y,
                "kind": kind,
                "mark": DISASTER if kind.startswith(map_history.DISASTER_PREFIX) else SCAR,
            }
        )
    return out


def find_snapshot(island: m.Island, turn: Any) -> dict[str, Any] | None:
    """ターンの写しを探す。数でない値や、無いターンは None（印を出さない側に倒す）。"""
    try:
        wanted = int(turn)
    except (TypeError, ValueError):
        return None
    for snap in _snapshots(island):
        if snap["turn"] == wanted:
            return snap
    return None


def snapshot_map(island: m.Island, snap: dict[str, Any]) -> dict[str, Any]:
    """写しを地図の API（island_map）と同じ形にする。描き直しに要る項目だけを返す。"""
    facilities = [
        {
            "number": f["number"],
            "kind": f["kind"],
            "x": f["x"],
            "y": f["y"],
            "built": f["built"],
            "level": f["level"],
            "recipe": f["recipe"],
            "grade": f["grade"],
            "size": f["size"],
            "status": "",
            "rate": 0,
            "stopped": False,
        }
        for f in snap.get("facilities", [])
    ]
    return {
        "id": island.id,
        "name": island.name,
        "size": snap["size"],
        "turn": snap["turn"],
        "capital": snap.get("capital"),
        "facilities": facilities,
        "terrain": snap.get("terrain", []),
    }


def _declined(old: tuple, new: tuple) -> bool:
    """施設の姿が衰えたか。old/new は changes._look の組（種類・作り方・建ったか・段階・格・規模）。

    衰えたとみるのは: 建っていた物が工事中に戻った、住宅の段階が下がった、
    建て増しの規模が縮んだとき。作り方が替わった等は「姿が変わった」側に置きます。
    """
    if old[2] and not new[2]:
        return True
    if isinstance(old[5], int) and isinstance(new[5], int) and new[5] < old[5]:
        return True
    levels = get_game().defs.settlement_levels()
    if old[3] in levels and new[3] in levels:
        return levels.index(new[3]) < levels.index(old[3])
    return False


def lens_marks(island: m.Island, snap: dict[str, Any]) -> list[dict[str, Any]]:
    """写し（節目）と今の姿を見た目で比べた差を [{x, y, kind}] で返す（y, x の順）。"""
    size = snap["size"]
    before_terrain = {
        (i % size, i // size): terrain for i, terrain in enumerate(snap.get("terrain", []))
    }
    before_facilities = {
        (f["x"], f["y"]): (f["kind"], f["recipe"], f["built"], f["level"], f["grade"], f["size"])
        for f in snap.get("facilities", [])
    }
    now_terrain = {
        (x, y): terrain for x, y, terrain in island.tiles.values_list("x", "y", "terrain")
    }
    now_facilities = {
        (f.x, f.y): _look(f.kind, f.recipe, f.built, f.ext)
        for f in island.facilities.filter(x__gte=0)
    }
    marks = []
    cells = set(before_terrain) | set(now_terrain) | set(before_facilities) | set(now_facilities)
    for x, y in sorted(cells, key=lambda cell: (cell[1], cell[0])):
        cell = (x, y)
        old = before_facilities.get(cell)
        new = now_facilities.get(cell)
        if new is not None and old is None:
            kind = NEW
        elif old is not None and new is None:
            kind = DOWN
        elif old is not None and new is not None:
            if old == new:
                continue
            kind = DOWN if _declined(old, new) else CHANGED
        elif before_terrain.get(cell) != now_terrain.get(cell):
            kind = CHANGED
        else:
            continue
        marks.append({"x": x, "y": y, "kind": kind})
    return marks
