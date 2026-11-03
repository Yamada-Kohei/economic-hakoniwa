"""手引き「産業のつながり」（/help/chain/）の中身。

施設の作り方（定義データの facilities）から、品目ごとに「何から作り、何の材料になるか」を
組み立てます。量は施設 1 つが全力で 1 ターンに使う・作る量で、画面と同じ単位（display_scale を
掛けた量）です。図は js/help-chain.js が描きます。ここで決めるのは図の列（並べ方）だけで、線は
作り方から引きます。新しい品目を足したら COLUMNS にも足します（tests/server/test_help_chain.py）。
"""

from __future__ import annotations

from typing import Any

from server.game import get_game
from server.web import display

# 図の列。左から原料・一次加工・加工・組み立て・完成品。列の中は上からこの順
COLUMNS = [
    [
        "grain",
        "cotton",
        "luxury_crop",
        "fish",
        "wood",
        "stone",
        "iron_ore",
        "coal",
        "crude_oil",
        "bauxite",
        "copper_ore",
        "uranium_ore",
    ],
    [
        "food",
        "meat",
        "clothing",
        "building_materials",
        "steel",
        "electricity",
        "fuel",
        "chemicals",
        "aluminium",
        "copper",
    ],
    ["consumer_goods", "machinery", "ammunition"],
    ["retail", "services", "auto_parts", "transport_ship", "electronics", "weapons"],
    [
        "automobile",
        "military_vehicle",
        "warship",
        "frigate",
        "submarine",
        "missile",
        "interceptor",
        "nuclear_warhead",
    ],
]
SKIP_CATEGORIES = ("monument", "military")  # 記念碑と軍の施設は物を作らない


def _amounts(table) -> list[list[Any]]:
    return [[g, display.amount(q, g)] for g, q in table.items()]


def chain_data() -> dict[str, Any]:
    game = get_game()
    defs, config = game.defs, game.config
    goods = {
        gid: {
            "n": defs.name("goods", gid),
            "tier": g.get("tier", 3),
            "u": defs.name("units", g.get("unit", "")) if g.get("unit") else "",
            "consumer": bool(g.get("consumer")),
            "arms": bool(g.get("arms")),
        }
        for gid, g in defs.goods.items()
    }
    facilities = []
    for fid, f in defs.facilities.items():
        if f.get("category") in SKIP_CATEGORIES:
            continue
        recipes = [
            {
                "id": rid,
                "rn": defs.recipe_name(fid, rid) if len(f["recipes"]) > 1 else "",
                "in": _amounts(r.get("inputs", {})),
                "out": _amounts(r.get("outputs", {})),
                "labor": r.get("labor", 1),
                "grade": r.get("grade", 0),
                "res": bool(r.get("requires_resource")),
                "grow": r.get("grow_turns"),
            }
            for rid, r in f["recipes"].items()
            if r.get("inputs") or r.get("outputs")
        ]
        if not recipes:
            continue
        construction = f.get("construction", {})
        facilities.append(
            {
                "id": fid,
                "n": defs.name("facilities", fid),
                "cat": f.get("category"),
                "ind": defs.name("industries", f["industry"]) if f.get("industry") else None,
                "terrain": [defs.name("terrain", t) for t in f.get("terrain", [])],
                "coastal": bool(f.get("coastal")),
                "water": bool(f.get("water")),
                "zone": f.get("zone"),
                "staff": dict(f.get("staff") or {}),
                "turns": construction.get("turns"),
                "mat": _amounts(construction.get("materials", {})),
                "r": recipes,
            }
        )
    build = {g for f in facilities for g, _ in f["mat"]}
    for kind in defs.infrastructure.values():
        build |= set(kind.get("materials", {})) | set(kind.get("fallback", {}))
    town = {g for s in defs.settlements.values() for g in s.get("materials", {})}
    trucks = config.get("freight.trucks")
    ships = {config.get("trade.ship_good"), *config.get("trade.ship_fuel")}
    move = {trucks["good"], *trucks["fuel"], *ships}
    arms = sorted(g for g, v in goods.items() if v["arms"] and g != "uranium_ore")
    school = {g for f in facilities if f["cat"] == "education" for r in f["r"] for g, _ in r["in"]}
    sinks = [
        ("house", "家計", "住民が買う物", sorted(g for g, v in goods.items() if v["consumer"])),
        ("town", "町が育つ", "町の段階が上がるときの資材", sorted(town)),
        (
            "build",
            "工事・インフラ",
            "施設を建てる資材と、道路・鉄道・上下水道・送電網",
            sorted(build),
        ),
        ("move", "運送・海運", "トラック（自動車）と輸送船、その燃料", sorted(move)),
        ("school", "学校・大学", "教材", sorted(school)),
        ("army", "国（軍）", "兵器は国だけが買う", arms),
    ]
    return {
        "goods": goods,
        "facs": facilities,
        "cols": [[g for g in col if g in goods] for col in COLUMNS],
        "sinks": [{"id": i, "n": n, "note": note, "g": g} for i, n, note, g in sinks],
    }
