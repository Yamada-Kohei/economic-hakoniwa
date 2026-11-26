"""地名（案34）: 湾・岬・山に、地形から自動で仮の名前を付け、島主が付け替える。

仮の名前は、その地形のある区画の地名（ext.places）に山・湾・岬を足した形で、
地形からその場で決める（保存しない）。付け替えた名前だけを GeoName に持ち、
付け替えは GeoNameChange に残して国の年表に出す。川は地形に無いので対象外。
表示は地図を拡大したときだけ（static/js/map/geonames.js）。
"""

from __future__ import annotations

import unicodedata
from collections import deque
from typing import Any

from django.utils.translation import gettext

from server.game import get_game
from server.world import models as m

NAME_MAX = 16  # 付け替える名前の長さ
BAY_LAND_NEIGHBOURS = 5  # 8 近傍のうち陸がこれ以上ある水マスを湾の内側とする
CAPE_WATER_NEIGHBOURS = 5  # 8 近傍のうち水がこれ以上ある陸マスを岬の先とする
MOUNTAIN_MIN = 2  # 山のまとまりは 2 マス以上（1 マスだけの岩は名前を付けない）


def _kind_word(kind: str) -> str:
    """種類の言葉（山・湾・岬）。仮の名前の後ろにも、そのまま付ける。"""
    return {"mountain": gettext("山"), "bay": gettext("湾"), "cape": gettext("岬")}[kind]


def _grid(island: m.Island) -> dict[tuple[int, int], str]:
    return {(t.x, t.y): t.terrain for t in island.tiles.all()}


def _components(cells: set[tuple[int, int]], diagonal: bool) -> list[list[tuple[int, int]]]:
    """マスの集合を、つながったまとまりに分ける。"""
    steps = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    if diagonal:
        steps += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
    out = []
    seen: set[tuple[int, int]] = set()
    for start in sorted(cells):
        if start in seen:
            continue
        group = []
        queue = deque([start])
        seen.add(start)
        while queue:
            x, y = queue.popleft()
            group.append((x, y))
            for dx, dy in steps:
                nxt = (x + dx, y + dy)
                if nxt in cells and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        out.append(group)
    return out


def _anchor(group: list[tuple[int, int]]) -> tuple[int, int]:
    """まとまりの重心にいちばん近いマス（同点なら小さい座標）。"""
    cx = sum(x for x, _ in group) / len(group)
    cy = sum(y for _, y in group) / len(group)
    return min(group, key=lambda p: ((p[0] - cx) ** 2 + (p[1] - cy) ** 2, p[1], p[0]))


def _place_of(island: m.Island, x: int, y: int, block: int, places: list[str]) -> str:
    if not places:
        return (island.names or {}).get("capital", island.name)
    per_side = -(-island.size // block)
    index = (y // block) * per_side + x // block
    return places[index % len(places)]


def features(island: m.Island) -> list[dict[str, Any]]:
    """その島の地名の一覧（自動の仮の名前に、付け替えを重ねたもの）。"""
    game = get_game()
    defs = game.defs
    grid = _grid(island)
    if not grid:
        return []
    land = {p for p, t in grid.items() if defs.terrain.get(t, {}).get("land")}
    water = set(grid) - land
    block = game.config.get("naming.place_block")
    places = (island.ext or {}).get("places") or []

    def neighbours(p: tuple[int, int]) -> list[tuple[int, int]]:
        x, y = p
        return [
            (x + dx, y + dy)
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            if (dx or dy) and (x + dx, y + dy) in grid
        ]

    found: list[
        tuple[str, tuple[int, int], tuple[int, int]]
    ] = []  # (種類, 基準マス, 名前の元のマス)
    mountains = {p for p, t in grid.items() if t == "mountain"}
    for group in _components(mountains, diagonal=False):
        if len(group) >= MOUNTAIN_MIN:
            anchor = _anchor(group)
            found.append(("mountain", anchor, anchor))
    bays = {p for p in water if sum(q in land for q in neighbours(p)) >= BAY_LAND_NEIGHBOURS}
    for group in _components(bays, diagonal=True):
        anchor = _anchor(group)
        near_land = min(
            (q for q in neighbours(anchor) if q in land),
            key=lambda q: (abs(q[0] - anchor[0]) + abs(q[1] - anchor[1]), q[1], q[0]),
            default=anchor,
        )
        found.append(("bay", anchor, near_land))
    capes = {p for p in land if sum(q in water for q in neighbours(p)) >= CAPE_WATER_NEIGHBOURS}
    for group in _components(capes, diagonal=False):
        anchor = _anchor(group)
        found.append(("cape", anchor, anchor))

    overrides = {g.key: g.name for g in island.geo_names.all()}
    out = []
    for kind, (ax, ay), (px, py) in sorted(found, key=lambda f: (f[0], f[1][1], f[1][0])):
        base = _place_of(island, px, py, block, places)
        auto = gettext("{place}{suffix}").format(place=base, suffix=_kind_word(kind))
        key = f"{kind}:{ax}:{ay}"
        out.append(
            {
                "key": key,
                "kind": kind,
                "kind_name": _kind_word(kind),
                "x": ax,
                "y": ay,
                "name": overrides.get(key, auto),
                "auto": auto,
                "renamed": key in overrides,
            }
        )
    return out


def _clean_name(name: Any) -> str:
    if not isinstance(name, str):
        raise ValueError(gettext("地名は文字で送ってください"))
    name = name.strip()
    for c in name:
        if unicodedata.category(c).startswith("C"):
            raise ValueError(gettext("地名に使えない文字があります"))
    if not name:
        raise ValueError(gettext("地名を入れてください"))
    if len(name) > NAME_MAX:
        raise ValueError(gettext("地名は {max} 字までです").format(max=NAME_MAX))
    return name


def rename(island: m.Island, key: Any, name: Any) -> dict[str, Any]:
    """地名を付け替える。自動の仮の名前に戻すときは、付け替えの持ち分を消す。
    どちらも記録（GeoNameChange）に残す。"""
    name = _clean_name(name)
    current = {f["key"]: f for f in features(island)}
    feature = current.get(key) if isinstance(key, str) else None
    if feature is None:
        raise ValueError(gettext("その地形は見つかりませんでした"))
    old = feature["name"]
    if old == name:
        raise ValueError(gettext("いまと同じ名前です"))
    if name == feature["auto"]:
        island.geo_names.filter(key=key).delete()
    else:
        m.GeoName.objects.update_or_create(island=island, key=key, defaults={"name": name})
    m.GeoNameChange.objects.create(
        island=island, key=key, old=old, new=name, turn=island.world.turn
    )
    return {**feature, "name": name, "renamed": name != feature["auto"]}
