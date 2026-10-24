"""軍の要約（フェーズ4-2 の②）。国の要約と JSON API に出す、兵と国防費の状態。

ルールの計算はせず、軍の維持の段が島に残した状態（island.ext["military"]。説明は
hakoniwa_core/military/）と国の統計を読みます。見える範囲の決まり（MI-26・MI-55）に合わせて、
持ち主には全部、ほかの国には国全体の兵の数と国防費と、その国の艦隊のうち見えたもの（見えている
艦隊と、前に見た場所とターン）だけを見せます。
"""

from __future__ import annotations

from typing import Any

from django.db.models import Sum

from hakoniwa_core.military import army, civil_war, forces, missiles, occupation, vision
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
    civil = civil_war_brief(island, mine)
    if not mine:
        return {
            "soldiers": volunteers + conscripts,
            "budget": spend,
            "civil_war": civil,
            "stats_turn": stats_turn,
            "fleets": seen_fleets(island.world, viewer, island.id),
            "seen_units": seen_land(island.world, viewer, owner=island.id),
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
        "enemy_units": seen_land(island.world, island.id, place=island.id, others=True),
        "wounded": sum(n for _, n in raw.get("wounded", [])),
        "abroad": raw.get("abroad", 0),
        "reserve": {g: q / QUANTITY_SCALE for g, q in (raw.get("reserve") or {}).items()},
        "prisoners": prisoners(raw),
        "captive": raw.get("captive", 0),
        "civil_war": civil,
        "occupied": occupied_tiles(island),
        "occupying": occupying(island),
        "stats": {
            "killed": stats.get("military.killed", 0),
            "wounded": stats.get("military.wounded", 0),
            "kills": stats.get("military.kills", 0),
            "civilian_dead": stats.get("military.civilian_dead", 0),
            "refugees": stats.get("military.refugees", 0),
        },
    }


def _raw_units(island: m.Island) -> list[dict[str, Any]]:
    return ((island.world.ext or {}).get(forces.KEY) or {}).get("units", [])


def units(island: m.Island) -> list[dict[str, Any]]:
    """島の持ち主の陸の部隊（フェーズ4-2 の③。外国の島に揚陸した部隊も含む。4-3 の①）。ほかの国の
    部隊は seen_land（見える範囲）で出す。"""
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
                "island": army.where(u),  # いる島（外国の島に揚陸していれば、その島の番号）
                "abroad": army.abroad(u),
                "stores": {g: q / QUANTITY_SCALE for g, q in (u.get("stores") or {}).items()},
                "hungry": bool(u.get("hungry")),
                "role": u.get("role"),  # 戦っている間: attack 攻め・defend 守り
                "fought": u.get("fought", 0),
                "requisition": u.get("requisition", True),
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
                "duty": u.get("duty"),
                "order": {k: v for k, v in order.items() if k != "route"} if order else None,
                "steps_left": len(order["route"]) if order and "route" in order else 0,
                "cargo": u["cargo"],
                "capacity": spec.get("capacity", 0) * u["ships"],
                "goods": {g: q / QUANTITY_SCALE for g, q in (u.get("goods") or {}).items()},
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
        if row.get("arm") == "land" or (owner is not None and row["owner"] != owner):
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


def seen_land(
    world: m.World,
    viewer: int | None,
    owner: int | None = None,
    place: int | None = None,
    others: bool = False,
) -> list[dict[str, Any]]:
    """見ている国（viewer）が見た、ほかの国の陸の部隊（今見えているものは current、前に見たものは
    最後の場所とターン）。owner はその国の部隊だけ、place はその島にいる部隊だけ、others は持ち主
    以外の部隊だけ。兵の数は出さない（自分の部隊だけが知っている）。"""
    if viewer is None:
        return []
    defs = get_game().defs
    memory = ((world.ext or {}).get(forces.KEY) or {}).get(vision.SEEN, {}).get(str(viewer), {})
    out = []
    for key, row in memory.items():
        if row.get("arm") != "land":
            continue
        if owner is not None and row["owner"] != owner:
            continue
        if place is not None and row["at"] != place:
            continue
        if others and row["owner"] == viewer:
            continue
        out.append(
            {
                "id": int(key[1:]),
                "owner": row["owner"],
                "army": row["kind"],
                "name": defs.name("army", row["kind"]) if row["kind"] in defs.army else row["kind"],
                "island": row["at"],
                "x": row["x"],
                "y": row["y"],
                "turn": row["turn"],
                "current": row["turn"] == world.turn,
            }
        )
    return sorted(out, key=lambda u: u["id"])


def prisoners(raw: dict[str, Any]) -> dict[str, Any]:
    """捕らえている捕虜（出身の島ごと）。"""
    by_origin: dict[int, int] = {}
    for _turn, origin, count in raw.get("prisoners", []):
        by_origin[origin] = by_origin.get(origin, 0) + count
    return {
        "total": sum(by_origin.values()),
        "by_island": {str(k): v for k, v in sorted(by_origin.items())},
    }


def civil_war_brief(island: m.Island, mine: bool) -> dict[str, Any]:
    """反乱と内戦（フェーズ4-4。MI-39〜MI-41）。起きているか、反乱軍の兵の総数と占領しているマスの数は
    だれにも見える（国じゅうが知ること）。持ち主には反乱軍のグループ（いる場所・兵・士気・武器・飢え）と、
    次の反乱までの見張り（前触れが続いたターン数・鎮まるまでのターン数・次の反乱が起きない期間）も。"""
    raw = ((island.world.ext or {}).get(forces.KEY) or {}).get(civil_war.KEY) or []
    groups = [g for g in raw if g["island"] == island.id]
    held = island.tiles.filter(**{f"ext__{occupation.OCCUPIED}__by": civil_war.REBELS}).count()
    out: dict[str, Any] = {
        "active": bool(groups),
        "rebels": sum(g["soldiers"] for g in groups),
        "held_tiles": held,
    }
    if mine:
        state = (island.ext or {}).get(civil_war.STATE) or {}
        out["groups"] = [
            {
                "id": g["id"],
                "x": g["x"],
                "y": g["y"],
                "soldiers": g["soldiers"],
                "weapons": g["weapons"],
                "morale": g["morale"] / BASIS,
                "hungry": bool(g.get("hungry")),
                "since": g["since"],
            }
            for g in sorted(groups, key=lambda g: g["id"])
        ]
        out["alarm_turns"] = state.get("alarm", 0)
        out["calm_turns"] = state.get("calm", 0)
        out["cooldown_until"] = state.get("cooldown", 0)
    return out


def occupied_tiles(island: m.Island) -> list[dict[str, int]]:
    """この島で占領されているマス（占領した国の島の番号と、占領されたターン）。"""
    rows = island.tiles.filter(**{f"ext__{occupation.OCCUPIED}__isnull": False}).order_by("y", "x")
    return [
        {
            "x": t.x,
            "y": t.y,
            "by": t.ext[occupation.OCCUPIED]["by"],
            "turn": t.ext[occupation.OCCUPIED]["turn"],
        }
        for t in rows
    ]


def occupying(island: m.Island) -> list[dict[str, int]]:
    """この国が占領しているほかの島のマス。"""
    rows = m.Tile.objects.filter(
        island__world=island.world, **{f"ext__{occupation.OCCUPIED}__by": island.id}
    ).order_by("island_id", "y", "x")
    return [
        {"island": t.island_id, "x": t.x, "y": t.y, "turn": t.ext[occupation.OCCUPIED]["turn"]}
        for t in rows
    ]


def island_units(island: m.Island, viewer: m.Island | None) -> list[dict[str, Any]]:
    """島の俯瞰図に出す陸の部隊: 見ている国（viewer）に見えるもの。島の持ち主には、
    入ってきた敵も見える
    （MI-26）。船に載っている部隊は出さない。"""
    if viewer is None:
        return []
    mine = [
        {
            "id": u["id"],
            "owner": viewer.id,
            "army": u["army"],
            "x": u["x"],
            "y": u["y"],
            "current": True,
        }
        for u in units(viewer)
        if u["aboard"] is None and u["island"] == island.id
    ]
    seen = [
        {k: row[k] for k in ("id", "owner", "army", "x", "y", "current")}
        for row in seen_land(island.world, viewer.id, place=island.id, others=True)
    ]
    return sorted(mine + seen, key=lambda r: (r["owner"], r["id"]))


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
    kept = {
        (facility, good): qty
        for facility, good, qty in island.stocks.filter(
            facility__isnull=False, good__in=[g for _, g in missiles.SOURCES]
        )
        .values("facility", "good")
        .annotate(qty=Sum("quantity"))
        .values_list("facility", "good", "qty")
    }  # ミサイルと迎撃ミサイルの在庫（要約は持ち主の分だけ）
    out = []
    for f in island.facilities.filter(built=True).order_by("number"):
        info = defs.facilities.get(f.kind, {}).get("military")
        if info:
            row = {"facility": f.number, "kind": f.kind, "x": f.x, "y": f.y, **info}
            for key, good in missiles.SOURCES:
                if key in info:  # 核弾頭は stock と別の欄（warheads）に出す
                    name = "warheads" if good == missiles.WARHEAD else "stock"
                    row[name] = kept.get((f.pk, good), 0) // QUANTITY_SCALE
            out.append(row)
    return out


def trenches(island: m.Island) -> list[dict[str, int]]:
    """陣地（塹壕）のあるマス。"""
    rows = island.tiles.filter(**{f"ext__{army.TRENCH}": 1}).order_by("y", "x")
    return [{"x": t.x, "y": t.y} for t in rows]
