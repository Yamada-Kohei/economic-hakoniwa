"""見える範囲（フェーズ4-2 の③'。設計書 15-military の MI-26・MI-55）。

見える範囲はサーバーが国ごとに計算します。見る国の目になるのは次のものです。

- 自分の島の区画（入ってきた艦隊も見える）
- 自分の艦隊の周り（艦の種類ごとの広さ。army.yaml の vision。0 は同じ区画だけ）
- レーダーの周り（施設の military: radar の区画の数）
- 同盟の条約を結んだ国の、上の 3 つ（見える範囲を分け合う。MI-06）

潜水艦は、同じ区画にいる軍艦（army.yaml の detect が 0 より大きい艦）の見込みでだけ見つかり、
ほかの目には見えません。見つかるかどうかは世界の種・ターン・見る国・艦隊から決めた乱数で決めます
（島の順番に結果が左右されません）。

見えた艦隊の最後の場所とターンは `world.ext["military"]["seen"][見る国][艦隊の番号]` に残します
（ターンが今のターンなら今見えている、前のターンなら「前に見た場所」）。持ち主以外の画面・API・MCP・
国の要約は、艦隊をここからだけ出します。書くのは operations の段だけです（MI-54）。
"""

from __future__ import annotations

import random
from typing import Any

from hakoniwa_core.defs import Defs
from hakoniwa_core.diplomacy import clauses as cl
from hakoniwa_core.diplomacy import treaties
from hakoniwa_core.military import army
from hakoniwa_core.state import WorldState

SEEN = "seen"
# 目: (x, y, 広さ, 軍艦の目か)。軍艦の目だけが、同じ区画の潜水艦を見つけられる
Eye = tuple[int, int, int, bool]


def allies(world: WorldState, island_id: int) -> set[int]:
    """見える範囲を分け合う国（同盟の条約が効いている相手）。"""
    return treaties.partners_with(world, island_id, cl.ALLIANCE)


def eyes_of(world: WorldState, defs: Defs, island_id: int) -> list[Eye]:
    """島の持ち主一人ぶんの目（同盟国は含まない）。"""
    island = world.islands.get(island_id)
    if island is None:
        return []
    out: list[Eye] = [(island.wx, island.wy, 0, False)]
    for _facility, radius in army.bases(island, defs, "radar"):
        out.append((island.wx, island.wy, int(radius), False))
    for unit in army.fleets_of(world, island_id):
        spec = defs.army.get(unit["kind"])
        if spec is None or unit["status"] != army.READY:
            continue
        out.append((unit["x"], unit["y"], int(spec.get("vision", 0)), spec.get("detect", 0) > 0))
    return out


def eyes(world: WorldState, defs: Defs, viewer: int) -> list[Eye]:
    """見る国の目（同盟国の目を足す）。"""
    out = eyes_of(world, defs, viewer)
    for ally in sorted(allies(world, viewer)):
        out += eyes_of(world, defs, ally)
    return out


def _reach(eye: Eye, x: int, y: int) -> bool:
    return max(abs(eye[0] - x), abs(eye[1] - y)) <= eye[2]


def _detected(world: WorldState, defs: Defs, viewer: int, unit: dict[str, Any]) -> bool:
    """同じ区画の軍艦に潜水艦が見つかるか。いちばん見込みの高い軍艦の detect で決める。"""
    chance = 0.0
    for who in {viewer, *allies(world, viewer)}:
        for fleet in army.fleets_of(world, who):
            spec = defs.army.get(fleet["kind"], {})
            if (fleet["x"], fleet["y"]) == (unit["x"], unit["y"]) and fleet["status"] == army.READY:
                chance = max(chance, spec.get("detect", 0.0))
    if chance <= 0:
        return False
    rng = random.Random(f"{world.seed}:{world.turn}:detect:{viewer}:{unit['id']}")
    return rng.random() < chance


def can_see(
    world: WorldState,
    defs: Defs,
    viewer: int,
    unit: dict[str, Any],
    sight: list[Eye] | None = None,
) -> bool:
    """見る国が艦隊 unit を今見られるか（自分の艦隊はいつも見える）。"""
    if unit["owner"] == viewer:
        return True
    spec = defs.army.get(unit["kind"], {})
    sight = eyes(world, defs, viewer) if sight is None else sight
    if spec.get("stealth"):
        return _detected(world, defs, viewer, unit)
    return any(_reach(eye, unit["x"], unit["y"]) for eye in sight)


def update(world: WorldState, defs: Defs, turn: int) -> None:
    """全島の見える範囲を計算し直し、見えた艦隊の最後の場所とターンを残す（operations の最後）。"""
    state = army.world_state(world)
    seen = state.setdefault(SEEN, {})
    alive = {u["id"]: u for u in state["units"] if army.is_sea(u)}
    for viewer in world.island_ids():
        if not world.islands[viewer].has_economy:
            continue
        mine = seen.setdefault(str(viewer), {})
        for fid in [k for k in mine if int(k) not in alive]:
            del mine[fid]  # なくなった艦隊の記録は捨てる
        sight = eyes(world, defs, viewer)
        for fid, unit in alive.items():
            if unit["owner"] == viewer:
                continue
            if can_see(world, defs, viewer, unit, sight):
                mine[str(fid)] = {
                    "owner": unit["owner"],
                    "kind": unit["kind"],
                    "x": unit["x"],
                    "y": unit["y"],
                    "turn": turn,
                }
    for key in [k for k in seen if int(k) not in world.islands]:
        del seen[key]
