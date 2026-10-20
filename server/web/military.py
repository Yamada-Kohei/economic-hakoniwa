"""軍の要約（フェーズ4-2 の②）。国の要約と JSON API に出す、兵と国防費の状態。

ルールの計算はせず、軍の維持の段が島に残した状態（island.ext["military"]。説明は
hakoniwa_core/military/）と国の統計を読みます。見える範囲の決まり（MI-26）に合わせて、
兵の数や士気は島の持ち主だけに見せます（ほかの国には None）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.military import forces
from hakoniwa_core.units import BASIS
from server.web import display
from server.world import models as m

KEY = forces.KEY


def brief(island: m.Island, mine: bool) -> dict[str, Any] | None:
    """国の要約に出す軍の要約。島の持ち主だけが見られる（ほかの国は None）。"""
    if not mine:
        return None
    raw = (island.ext or {}).get(KEY) or {}
    stats_turn, stats = display.latest_stats(island)
    policy = island.policy or {}
    budget = policy.get("budget") or {}
    volunteers = raw.get("volunteers", 0)
    conscripts = raw.get("conscripts", 0)
    return {
        "soldiers": volunteers + conscripts,
        "volunteers": volunteers,
        "conscripts": conscripts,
        "morale": raw.get("morale", 0) / BASIS,
        "training": raw.get("training", 0) / BASIS,
        "wages_late_turns": raw.get("late", 0),
        "hungry": bool(raw.get("hungry", False)),
        "volunteer_wage": raw.get("volunteer_wage", BASIS) / BASIS,
        "conscript_wage": raw.get("conscript_wage", BASIS) / BASIS,
        "budget": {
            "personnel": budget.get(forces.BUDGET_PERSONNEL, 0),
            "procurement": budget.get(forces.BUDGET_PROCUREMENT, 0),
        },
        "stats_turn": stats_turn,
        "wages_paid": stats.get("military.wages", 0),
        "wages_unpaid": stats.get("military.wages_unpaid", 0),
        "deserted": stats.get("military.deserted", 0),
        "starved": stats.get("military.starved", 0),
    }
