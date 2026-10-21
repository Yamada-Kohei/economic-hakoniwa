"""隠した施設（フェーズ4-3 の③c。設計書 15-military の MI-62）。

核施設とミサイル基地は、建てるとき「隠す」を選べる（命令 build の hidden）。地下に造るので
工期が延び（facilities.yaml の `hide: {turns, materials_factor}`）、資材が増える。
ほかの国には元の地形のマスに見え、
俯瞰図・島の API・ターンログのどれにも名前と場所が出ない（server/web/api.py の island_map と
display.visible_logs）。国全体の統計の数には含めたまま。

見つかるのは、次のとき（見つけた国と同盟国には、その後ずっと見える。`ext["found"]` に島の番号）:

- 相手の陸の部隊が、そのマスか隣のマスに来た
- そのマスが占領された
- そこからミサイルを撃った（撃ったターンに、撃たれた国が見つける）
"""

from __future__ import annotations

from hakoniwa_core.diplomacy import clauses as cl
from hakoniwa_core.diplomacy import treaties
from hakoniwa_core.military import army, occupation
from hakoniwa_core.state import Facility, IslandState, LogEntry, WorldState

HIDDEN = "hidden"
FOUND = "found"


def is_hidden(f: Facility) -> bool:
    return bool(f.ext.get(HIDDEN))


def visible_to(f: Facility, owner: int, viewer: int | None) -> bool:
    """施設が viewer の国に見えるか（持ち主と、見つけた国は見える）。"""
    return not is_hidden(f) or viewer == owner or viewer in f.ext.get(FOUND, ())


def reveal(world: WorldState, f: Facility, finder: int) -> bool:
    """finder（と同盟国）が施設を見つけたことにする。新しく見つかったら True。"""
    seen = set(f.ext.get(FOUND, ()))
    new = {finder, *treaties.partners_with(world, finder, cl.ALLIANCE)} - seen
    if not new:
        return False
    f.ext[FOUND] = sorted(seen | new)
    return True


def announce(owner: IslandState, finder: IslandState, f: Facility) -> None:
    """見つかったことを、持ち主と見つけた国のログに書く。"""
    where = {"x": f.x, "y": f.y, "facility": f.kind}
    owner.log.append(LogEntry("hidden_found_by", {**where, "country": finder.name}))
    finder.log.append(LogEntry("hidden_found", {**where, "country": owner.name}))


def _near(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1])) <= 1


def discover(world: WorldState) -> list[tuple[int, int, int]]:
    """部隊が隣に来た・占領されたことで見つかった隠した施設を記録する。
    (持ち主の島, 見つけた島, 施設) の組を返す。"""
    found: list[tuple[int, int, int]] = []
    for island_id in world.island_ids():
        island = world.islands[island_id]
        hidden = [f for f in island.facilities if f.built and is_hidden(f)]
        if not hidden:
            continue
        foreign = [u for u in army.on_land(world, island_id) if u["owner"] != island_id]
        for f in hidden:
            tile = island.tile(f.x, f.y)
            by: set[int] = set()
            if tile is not None and tile.ext.get(occupation.OCCUPIED):
                by.add(tile.ext[occupation.OCCUPIED]["by"])
            by |= {u["owner"] for u in foreign if _near((u["x"], u["y"]), (f.x, f.y))}
            for finder in sorted(by):
                if reveal(world, f, finder):
                    found.append((island_id, finder, f.id))
                    announce(island, world.islands[finder], f)
    return found
