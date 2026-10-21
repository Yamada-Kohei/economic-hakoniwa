"""軍の要約（フェーズ4-2 の②）。国の要約と JSON API に出す、兵と国防費の状態。

ルールの計算はせず、軍の維持の段が島に残した状態（island.ext["military"]。説明は
hakoniwa_core/military/）と国の統計を読みます。見える範囲の決まり（MI-26）に合わせて、
兵の数や士気は島の持ち主だけに見せます（ほかの国には None）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.military import army, forces
from hakoniwa_core.units import BASIS, QUANTITY_SCALE
from server.game import get_game
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
        "assigned": raw.get("assigned", 0),
        "free_soldiers": volunteers + conscripts - raw.get("assigned", 0),
        "units": units(island),
        "bases": bases(island),
        "trenches": trenches(island),
    }


def units(island: m.Island) -> list[dict[str, Any]]:
    """島の持ち主の部隊（フェーズ4-2 の③）。見える範囲の決まり（MI-26）は、他の国の部隊が
    入ってくる 4-3 で足す。今は持ち主だけが見る。"""
    defs = get_game().defs
    raw = ((island.world.ext or {}).get(forces.KEY) or {}).get("units", [])
    out = []
    for u in raw:
        if u["owner"] != island.id:
            continue
        spec = defs.army.get(u["kind"], {})
        out.append(
            {
                "id": u["id"],
                "army": u["kind"],
                "name": defs.name("army", u["kind"]) if u["kind"] in defs.army else u["kind"],
                "x": u["x"],
                "y": u["y"],
                "soldiers": u["soldiers"],
                "soldiers_full": spec.get("soldiers"),
                "equipment": u["equipment"],
                "equipment_full": {
                    g: q // QUANTITY_SCALE for g, q in spec.get("equipment", {}).items()
                },
                "status": u["status"],
                "order": u["order"],
            }
        )
    return sorted(out, key=lambda u: u["id"])


def bases(island: m.Island) -> list[dict[str, Any]]:
    """建ち終わった基地の施設（兵舎・軍港・レーダー・要塞）。誰にでも見える（施設は俯瞰図で見える）が、
    要約には持ち主の分だけ入れる。"""
    defs = get_game().defs
    out = []
    for f in island.facilities.filter(built=True).order_by("number"):
        info = defs.facilities.get(f.kind, {}).get("military")
        if info:
            out.append({"facility": f.number, "kind": f.kind, "x": f.x, "y": f.y, **info})
    return out


def trenches(island: m.Island) -> list[dict[str, int]]:
    """陣地（塹壕）のあるマス。"""
    rows = island.tiles.filter(**{f"ext__{army.TRENCH}": 1}).order_by("y", "x")
    return [{"x": t.x, "y": t.y} for t in rows]
