"""軍の要約（フェーズ4-2 の②）。国の要約と JSON API に出す、兵と国防費の状態。

ルールの計算はせず、軍の維持の段が島に残した状態（island.ext["military"]。説明は
hakoniwa_core/military/）と国の統計を読みます。見える範囲の決まり（MI-26・MI-55）に合わせて、
持ち主には全部、ほかの国には国全体の兵の数と国防費と、その国の艦隊のうち見えたもの（見えている
艦隊と、前に見た場所とターン）だけを見せます。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.military import army, forces, vision
from hakoniwa_core.units import BASIS, QUANTITY_SCALE
from server.game import get_game
from server.web import display
from server.world import models as m

KEY = forces.KEY


def brief(island: m.Island, mine: bool, viewer: int | None = None) -> dict[str, Any] | None:
    """国の要約に出す軍の要約。持ち主には全部、ほかの国には国全体の兵の数と国防費と、見えた艦隊だけ
    （viewer は見ている人の島の番号。艦隊が見えるかはその国の見える範囲による）。"""
    raw = (island.ext or {}).get(KEY) or {}
    stats_turn, stats = display.latest_stats(island)
    policy = island.policy or {}
    budget = policy.get("budget") or {}
    volunteers = raw.get("volunteers", 0)
    conscripts = raw.get("conscripts", 0)
    spend = {
        "personnel": budget.get(forces.BUDGET_PERSONNEL, 0),
        "procurement": budget.get(forces.BUDGET_PROCUREMENT, 0),
    }
    if not mine:
        return {
            "soldiers": volunteers + conscripts,
            "budget": spend,
            "stats_turn": stats_turn,
            "fleets": seen_fleets(island.world, viewer, island.id),
        }
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
        "budget": spend,
        "stats_turn": stats_turn,
        "wages_paid": stats.get("military.wages", 0),
        "wages_unpaid": stats.get("military.wages_unpaid", 0),
        "deserted": stats.get("military.deserted", 0),
        "starved": stats.get("military.starved", 0),
        "assigned": raw.get("assigned", 0),
        "free_soldiers": volunteers + conscripts - raw.get("assigned", 0),
        "units": units(island),
        "fleets": fleets(island),
        "bases": bases(island),
        "trenches": trenches(island),
    }


def _raw_units(island: m.Island) -> list[dict[str, Any]]:
    return ((island.world.ext or {}).get(forces.KEY) or {}).get("units", [])


def units(island: m.Island) -> list[dict[str, Any]]:
    """島の持ち主の陸の部隊（フェーズ4-2 の③）。他の国の部隊が見える範囲に入ってくるのは、揚陸の入る
    4-3 から。今は持ち主だけが見る。"""
    defs = get_game().defs
    out = []
    for u in _raw_units(island):
        if u["owner"] != island.id or army.is_sea(u):
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
                "aboard": u.get("aboard"),
            }
        )
    return sorted(out, key=lambda u: u["id"])


def fleets(island: m.Island) -> list[dict[str, Any]]:
    """島の持ち主の艦隊（フェーズ4-2 の③'）。置き場所は世界マップの区画 (x, y)。"""
    defs = get_game().defs
    out = []
    for u in _raw_units(island):
        if u["owner"] != island.id or not army.is_sea(u):
            continue
        spec = defs.army.get(u["kind"], {})
        order = u["order"]
        out.append(
            {
                "id": u["id"],
                "army": u["kind"],
                "name": defs.name("army", u["kind"]) if u["kind"] in defs.army else u["kind"],
                "ships": u["ships"],
                "x": u["x"],
                "y": u["y"],
                "docked": (u["x"], u["y"]) == (island.wx, island.wy),
                "soldiers": u["soldiers"],
                "soldiers_full": spec.get("soldiers", 0) * u["ships"],
                "equipment": u["equipment"],
                "status": u["status"],
                "order": {k: v for k, v in order.items() if k != "route"} if order else None,
                "steps_left": len(order["route"]) if order and "route" in order else 0,
                "cargo": u["cargo"],
                "capacity": spec.get("capacity", 0) * u["ships"],
            }
        )
    return sorted(out, key=lambda f: f["id"])


def seen_fleets(
    world: m.World, viewer: int | None, owner: int | None = None
) -> list[dict[str, Any]]:
    """見ている国（viewer）が見た、ほかの国の艦隊（見えている艦隊は current、前に見たものは最後の
    場所とターン）。owner を決めるとその国の艦隊だけ。operations の段が残した記録を読む。"""
    if viewer is None:
        return []
    defs = get_game().defs
    memory = ((world.ext or {}).get(forces.KEY) or {}).get(vision.SEEN, {}).get(str(viewer), {})
    out = []
    for fid, row in memory.items():
        if owner is not None and row["owner"] != owner:
            continue
        out.append(
            {
                "id": int(fid),
                "owner": row["owner"],
                "army": row["kind"],
                "name": defs.name("army", row["kind"]) if row["kind"] in defs.army else row["kind"],
                "x": row["x"],
                "y": row["y"],
                "turn": row["turn"],
                "current": row["turn"] == world.turn,
            }
        )
    return sorted(out, key=lambda f: f["id"])


def world_fleets(world: m.World, viewer: int | None) -> dict[str, Any]:
    """世界マップに出す艦隊: 見ている国の艦隊と、見えたほかの国の艦隊（MI-26）。"""
    mine: list[dict[str, Any]] = []
    if viewer is not None:
        island = world.islands.filter(pk=viewer).first()
        if island is not None:
            mine = fleets(island)
    return {"turn": world.turn, "viewer": viewer, "mine": mine, "seen": seen_fleets(world, viewer)}


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
