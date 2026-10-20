"""国どうしの取り決め（フェーズ4-1）の共通の部品。

国際会議場（公開の声明）はサーバーの表（server/hall）で持ち、ここにあるのは計算に効く状態だけです。

- war.py: 戦争の状態（国と国の間は平時か戦争。宣戦・停戦・講和）
- treaties.py: 条約（両国が署名すると効く条項の束。予告つきの破棄・違反の記録）
- clauses.py: 条項の種類（停戦・講和・不可侵・同盟）。経済の取り決めの条項はここに足す
- credit.py: 条約の違反が残す信用の傷（国債の利回りの上乗せ）

世界の状態（条約と戦争と番号の振り出し）は ``world.ext["diplomacy"]``、島ごとの状態（違反の記録）は
``island.ext["diplomacy"]`` に置きます。どちらも JSON にそのまま保存できる形（辞書のキーは文字列）に
します。島の番号は、辞書のキーでは文字列、一覧の中の値では整数です。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.state import IslandState, WorldState

KEY = "diplomacy"


def world_state(world: WorldState) -> dict[str, Any]:
    """世界の外交の状態（条約・戦争と、番号の振り出し）。"""
    state = world.ext.setdefault(KEY, {})
    state.setdefault("seq", {"treaty": 0, "war": 0})
    state.setdefault("treaties", [])
    state.setdefault("wars", [])
    return state


def next_id(state: dict[str, Any], kind: str) -> int:
    seq = state["seq"]
    seq[kind] = seq.get(kind, 0) + 1
    return seq[kind]


def island_state(island: IslandState) -> dict[str, Any]:
    """島の外交の状態（条約の違反の記録）。"""
    state = island.ext.setdefault(KEY, {})
    state.setdefault("violations", [])
    return state


def is_inactive(island: IslandState) -> bool:
    """休止中の島か（サーバーが読み込むときに印を付ける）。"""
    return bool(island.ext.get("inactive"))
