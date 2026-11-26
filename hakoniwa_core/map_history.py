"""地図の変遷（グラフィック強化 案29）: 節目のターンの地図の保存。

節目（案27: 建国記念日・人口・GDP の節目）を迎えたターンに、その時点の島の地図の
姿（地形と施設の見た目）を ``island.ext["map_snapshots"]`` に残します。節目表示
（案27）から開いて、昔と今を同じ位置・倍率で見比べたり、差分を地図に重ねる
「変化レンズ」（hal #5716）の材料にします。比べと描き直しは server/web/history.py。

記録がまだ無い島は、最初にこの記録が走ったターンの姿を「記録開始」の 1 枚として
残します（過去の節目をまとめては保存しない。案27 の節目の記録と同じ考え方で、
これから記録を始める国も今の島を最初の 1 枚にします。codex #5707）。

保存は記録の段（records）で、節目を残した直後に呼びます。計算・バランスは変えず、
記録だけです。写しは最大 MAX_SNAPSHOTS 枚まで持ち、最初の 1 枚は落とさず、
古い中間の物から落とします。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.state import IslandState

SNAPSHOTS = "map_snapshots"  # island.ext: [{turn, kinds, size, terrain, capital, facilities}]
MAX_SNAPSHOTS = 16
START = "start"  # 記録開始の 1 枚（節目ではない）
# 節目のログの種類（hakoniwa_core/milestones.py が残す）
MILESTONE_KINDS = ("anniversary_founding", "milestone_population", "milestone_gdp")


def _look(facility: Any) -> dict[str, Any]:
    """施設の見た目を比べるための材料（server/web/changes.py の対応表と同じ項目）。"""
    ext = facility.ext or {}
    return {
        "number": facility.id,
        "x": facility.x,
        "y": facility.y,
        "kind": facility.kind,
        "recipe": facility.recipe,
        "built": bool(facility.built),
        "level": ext.get("level"),
        "grade": ext.get("grade"),
        "size": ext.get("size", 1),
    }


def snapshot_of(island: IslandState, turn: int, kinds: list[str]) -> dict[str, Any]:
    """今の島の地図の姿を、比べと描き直しに足りる分だけ写す（地形は y * size + x の順）。"""
    terrain: list[Any] = [None] * (island.size * island.size)
    for tile in island.tiles:
        terrain[tile.y * island.size + tile.x] = tile.terrain
    return {
        "turn": turn,
        "kinds": list(kinds),
        "size": island.size,
        "terrain": terrain,
        "capital": [island.capital_x, island.capital_y] if island.capital else None,
        "facilities": [_look(f) for f in island.facilities if f.x >= 0],
    }


def record(island: IslandState, turn: int) -> None:
    """節目のターンの地図を残す。記録の段で、節目（milestones.celebrate）の直後に呼ぶ。

    このターンのログに節目があれば、その節目の種類を写しに付けます。節目が無い
    ターンでは、記録がまだ無い島の最初の 1 枚だけを残し、それ以外は何もしません。
    """
    hit = [kind for kind in dict.fromkeys(e.kind for e in island.log) if kind in MILESTONE_KINDS]
    snaps = island.ext.setdefault(SNAPSHOTS, [])
    if not snaps:
        snaps.append(snapshot_of(island, turn, [START, *hit]))
    elif hit:
        if snaps[-1]["turn"] == turn:
            for kind in hit:
                if kind not in snaps[-1]["kinds"]:
                    snaps[-1]["kinds"].append(kind)
        else:
            snaps.append(snapshot_of(island, turn, hit))
    while len(snaps) > MAX_SNAPSHOTS:
        del snaps[1]  # 最初の 1 枚（記録開始の姿）は落とさない
