"""条約の違反が残す信用の傷（docs/design/15-military.md MI-06、タブ 10「国どうしのやりとり」）。

破った国は違反として記録され、国債の不履行と同じく信用の傷になります。ゲームは止めも強いもしません。
傷は国債の利回りへの上乗せ（modules/bonds の default_premium に足す）で、違反の重みごとに
diplomacy.violation_premium を、diplomacy.violation_decay_turns ターンかけて 0 に減らし、
足し合わせた上乗せは diplomacy.violation_premium_max まで。債務不履行のように銀行が国債を
買わなくなることはなく、予告どおりの破棄なら違反にならず罰もない。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.diplomacy import island_state
from hakoniwa_core.state import IslandState


def record(
    island: IslandState,
    turn: int,
    treaty: int,
    clause: str,
    weight: int,
    reason: str,
    config: GameConfig,
) -> dict[str, Any]:
    """島の違反の記録に1件足す。古くて傷が消えた分は捨てる。"""
    decay = config.get("diplomacy.violation_decay_turns")
    rows = island_state(island)["violations"]
    rows[:] = [v for v in rows if turn - v["turn"] < decay]
    row = {"turn": turn, "treaty": treaty, "clause": clause, "weight": weight, "reason": reason}
    rows.append(row)
    return row


def premium(island: IslandState, turn: int, config: GameConfig) -> float:
    """違反が国債の利回りに足す上乗せ（利率。0 から violation_premium_max）。"""
    rows = island.ext.get("diplomacy", {}).get("violations")
    if not rows:
        return 0.0
    each = config.get("diplomacy.violation_premium")
    decay = config.get("diplomacy.violation_decay_turns")
    total = sum(v["weight"] * each * max(0.0, 1 - (turn - v["turn"]) / decay) for v in rows)
    return min(config.get("diplomacy.violation_premium_max"), total)
