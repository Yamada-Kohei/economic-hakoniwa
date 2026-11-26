"""前のターンから変わったマス（グラフィック強化 案3 の静止版＋案10 の衰退の印）。

「変わった」は見た目の差で決める。いちばん新しいターンのバックアップ（前の
ターンの更新が始まる直前の世界の写し）と、今の地図の材料（地形と施設）を
マスごとに比べ、建設・撤去・段階・衰退の4種に分ける。指標の変化（W-170／UI-62 の
notable_changes）はマスの位置を持たないので、ここでは使わない。施設の名前や
状態・工事の進み具合のような、絵に出ない違いは数えない。

変化種別の対応表:
- built（建設）: 前は施設が無く今ある／工事中だった施設が完成した／
  別の種類の施設に替わった
- removed（撤去）: 前は施設があり今は無い
- decline（衰退）: 住宅の段階が下がった（案10 の第一段。下向きの印で出す。
  長く止まっている施設の老朽・空き家風の差分絵は、停止期間の記録が
  確認できてから足す）
- stage（段階・姿の変化）: 住宅の段階が上がった、施設の作り方・段階・規模、
  地形が変わった（施設の下の地形は描かれないので、施設の姿が同じなら数えない）

バックアップが無い（まだ更新が走っていない）島と、バックアップの後に
建国した島は、前の姿が無いので空を返す。読みは持ち主の地図だけに使う
（島の地図の API が ?changes=1 のとき、持ち主にだけ足す）。
"""

from __future__ import annotations

import gzip
import json
from typing import Any

from hakoniwa_core import facilities as fac
from server.game import get_game
from server.turns.models import Backup
from server.world import models as m

BUILT = "built"
REMOVED = "removed"
STAGE = "stage"
DECLINE = "decline"


def _look(kind: Any, recipe: Any, built: Any, ext: Any) -> tuple:
    """施設の見た目を比べるための組。絵に出ない項目（名前・状態など）は入れない。"""
    ext = ext or {}
    return (
        kind,
        recipe,
        bool(built),
        ext.get("level"),
        ext.get("grade"),
        ext.get("size", 1),
    )


def _level_index(levels: list[str], level: Any) -> int:
    """住宅の段階の順位（小さい順の並びの中の位置）。

    記録が無い・知らない段階は、いちばん小さい段階として扱う（画面側が
    絵を選ぶときの倒し方と同じ）。
    """
    try:
        return levels.index(level)
    except ValueError:
        return 0


def changed_cells(island: m.Island) -> list[dict[str, Any]]:
    """前のターンの更新で見た目が変わったマスを [{x, y, kind}] で返す（y, x の順）。"""
    backup = Backup.objects.filter(world=island.world).order_by("-turn", "-id").first()
    if backup is None:
        return []
    try:
        world = json.loads(gzip.decompress(bytes(backup.data)))
    except (OSError, ValueError):
        return []  # 写しが読めなければ印は出さない（地図自体は出す）
    before = next((row for row in world.get("islands", []) if row.get("id") == island.id), None)
    if before is None:
        return []  # バックアップの後に建国した島は、前の姿がない
    before_terrain = {(tile["x"], tile["y"]): tile["terrain"] for tile in before.get("tiles", [])}
    before_facilities = {
        (f["x"], f["y"]): _look(f["kind"], f.get("recipe"), f.get("built"), f.get("ext"))
        for f in before.get("facilities", [])
        if f.get("x", -1) >= 0
    }
    now_terrain = {
        (x, y): terrain for x, y, terrain in island.tiles.values_list("x", "y", "terrain")
    }
    now_facilities = {
        (f.x, f.y): _look(f.kind, f.recipe, f.built, f.ext)
        for f in island.facilities.filter(x__gte=0)
    }
    game = get_game()
    housing = fac.housing_kind(game.config)
    levels = game.defs.settlement_levels()
    marks = []
    cells = set(before_terrain) | set(now_terrain) | set(before_facilities) | set(now_facilities)
    for x, y in sorted(cells, key=lambda cell: (cell[1], cell[0])):
        cell = (x, y)
        old = before_facilities.get(cell)
        new = now_facilities.get(cell)
        if new is not None and old is None:
            kind = BUILT
        elif old is not None and new is None:
            kind = REMOVED
        elif old is not None and new is not None:
            if old == new:
                continue
            # 種類が替わった・工事が終わって建った、は「建った」。住宅の段階が
            # 下がったものは「衰退」。残りは姿の変化
            if old[0] != new[0] or (not old[2] and new[2]):
                kind = BUILT
            elif old[0] == housing and _level_index(levels, new[3]) < _level_index(levels, old[3]):
                kind = DECLINE
            else:
                kind = STAGE
        elif before_terrain.get(cell) != now_terrain.get(cell):
            kind = STAGE
        else:
            continue
        marks.append({"x": x, "y": y, "kind": kind})
    return marks
