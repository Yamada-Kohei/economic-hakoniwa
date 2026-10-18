"""島の中のマスの位置関係（距離・近くのマス・つながり）。"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator

from hakoniwa_core.state import IslandState

Pos = tuple[int, int]

NEIGHBORS4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
NEIGHBORS8 = (*NEIGHBORS4, (1, 1), (1, -1), (-1, 1), (-1, -1))


def distance(a: Pos, b: Pos) -> int:
    """2つのマスの距離（斜めも1歩と数える）。通勤圏や汚染の広がりはこの距離で測る。"""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def within(island: IslandState, center: Pos, radius: int) -> list[Pos]:
    """center から radius マス以内のマスを、近い順（同じ距離なら y, x の順）に並べる。"""
    key = (island.size, center[0], center[1], radius)  # center は保存から戻すと list のことがある
    found = _WITHIN.get(key)
    if found is None:
        cx, cy = center
        found = [
            (x, y)
            for y in range(max(0, cy - radius), min(island.size, cy + radius + 1))
            for x in range(max(0, cx - radius), min(island.size, cx + radius + 1))
        ]
        found.sort(key=lambda p: (distance(p, center), p[1], p[0]))
        if len(_WITHIN) >= _WITHIN_MAX:
            _WITHIN.clear()
        _WITHIN[key] = found
    return list(found)


# within の並べた結果（島の大きさ・中心・半径だけで決まるので、島やターンをまたいで使い回す）
_WITHIN: dict[tuple[int, int, int, int], list[Pos]] = {}
_WITHIN_MAX = 1 << 12  # 半径が島の大きさの並べ方は1件が大きいので、多くは持たない


def neighbors4(island: IslandState, pos: Pos) -> Iterator[Pos]:
    x, y = pos
    for dx, dy in NEIGHBORS4:
        if island.in_bounds(x + dx, y + dy):
            yield (x + dx, y + dy)


def is_coastal(island: IslandState, pos: Pos, is_land: Callable[[str], bool]) -> bool:
    """海か浅瀬（陸地でないマス）に上下左右で接しているか。"""
    return any(not is_land(island.tile(x, y).terrain) for x, y in neighbors4(island, pos))


def components(island: IslandState, nodes: Iterable[Pos]) -> list[list[Pos]]:
    """nodes のうち上下左右でつながったものをまとめる。

    まとまりは、y, x の順で一番前のマスの順に並べる。
    """
    remaining = set(nodes)
    groups = []
    for start in sorted(remaining, key=lambda p: (p[1], p[0])):
        if start not in remaining:
            continue
        remaining.discard(start)
        group = [start]
        queue = deque([start])
        while queue:
            for n in neighbors4(island, queue.popleft()):
                if n in remaining:
                    remaining.discard(n)
                    group.append(n)
                    queue.append(n)
        groups.append(sorted(group, key=lambda p: (p[1], p[0])))
    return groups


def shortest_path(
    island: IslandState, starts: Iterable[Pos], goal: Pos, passable: Callable[[Pos], bool]
) -> list[Pos] | None:
    """starts のどれかから goal までの、上下左右に進む一番短い道（starts の側は含めない）。"""
    start_set = set(starts)
    if goal in start_set:
        return []
    prev: dict[Pos, Pos | None] = {s: None for s in start_set}
    queue = deque(sorted(start_set, key=lambda p: (p[1], p[0])))
    while queue:
        cur = queue.popleft()
        for n in neighbors4(island, cur):
            if n in prev or not (n == goal or passable(n)):
                continue
            prev[n] = cur
            if n == goal:
                path = [n]
                while prev[path[-1]] is not None and prev[path[-1]] not in start_set:
                    path.append(prev[path[-1]])  # type: ignore[arg-type]
                return path[::-1]
            queue.append(n)
    return None
