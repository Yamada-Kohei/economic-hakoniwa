"""画面の JavaScript が使う JSON API。"""

from __future__ import annotations

import json
import string
from functools import cache
from typing import Any

import yaml
from django.conf import settings
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods

from hakoniwa_core import facilities as fac
from hakoniwa_core.founding import land_terrains
from hakoniwa_core.naming import default_place_style, place_of, place_styles
from hakoniwa_core.orders import check_order
from hakoniwa_core.units import from_milli
from server.game import get_game
from server.orders.models import Order
from server.web import display
from server.world import models as m
from server.world.repository import island_from_row, load_island
from server.world.services import grid_size

JSON = {"json_dumps_params": {"ensure_ascii": False}}


def _json(data: Any, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status=status, **JSON)


@require_GET
def defs(request):
    """画面が使う定義データと表示名、テーマ。"""
    game = get_game()
    d = game.defs
    axes = d.regime_axes()
    return _json(
        {
            "terrain": {tid: _terrain_def(tid, t) for tid, t in d.terrain.items()},
            "facilities": {fid: _facility_def(fid, f) for fid, f in d.facilities.items()},
            "goods": {
                gid: {"name": d.name("goods", gid), "tier": g.get("tier"), "unit": d.unit_name(gid)}
                for gid, g in d.goods.items()
            },
            "orders": {
                oid: {"name": d.name("orders", oid), "admin_cost": o["admin_cost"]}
                for oid, o in d.orders.items()
            },
            "settlements": {
                sid: {
                    "name": d.name("settlements", sid),
                    **{k: v for k, v in s.items() if k != "materials"},
                    "materials": {g: from_milli(q) for g, q in s.get("materials", {}).items()},
                }
                for sid, s in d.settlements.items()
            },
            "axes": {
                axis: {
                    "name": display.message(f"axis_{axis}"),
                    "choices": {rid: d.name("regimes", rid) for rid in choices},
                }
                for axis, choices in axes.items()
            },
            "regimes": {
                rid: {
                    "axis": r["axis"],
                    "capital_building": r.get("capital_building"),
                    "capital_building_name": display.message(
                        f"capital_building_{r['capital_building']}"
                    )
                    if r.get("capital_building")
                    else "",
                }
                for rid, r in d.regimes.items()
            },
            "mining": {gid: d.name("goods", gid) for gid in d.mining_resources()},
            "housing": fac.housing_kind(game.config),
            "commute_radius": game.config.get("labor.commute_radius"),
            "founding": {
                "size": game.config.get("island.size"),
                "land_min": game.config.get("island.land_min"),
                "land_max": game.config.get("island.land_max"),
                "max_resources": game.config.get("founding.max_resources"),
                "terrain_edits": game.config.get("founding.terrain_edits"),
                "land_terrains": land_terrains(d, game.config),
                "sea_terrain": game.config.get("founding.sea_terrain"),
                "name_max": game.config.get("founding.name_max"),
                "name_labels": {
                    key: display.message(f"name_{key}")
                    for key in game.config.get("founding.name_max")
                },
                "place_styles": place_styles(d, game.config),
                "place_style": default_place_style(d, game.config),
            },
            "theme": _theme(game.config.raw.get("theme", "default")),
        }
    )


def _terrain_def(tid: str, t: Any) -> dict[str, Any]:
    name = get_game().defs.name("terrain", tid)
    return {"name": name, "height": t.get("height", 0), "land": t["land"]}


def _facility_def(fid: str, f: Any) -> dict[str, Any]:
    d = get_game().defs
    construction = f["construction"]
    return {
        "name": d.name("facilities", fid),
        "category": f["category"],
        "terrain": list(f["terrain"]),
        "buildable": f.get("buildable", True),
        "coastal": f.get("coastal", False),
        "near_capital": f.get("near_capital"),
        "max_per_island": f.get("max_per_island"),
        "workers": f["workers"],
        "priority": f.get("priority"),
        "sprite": f.get("sprite", fid),
        "construction": {
            "turns": construction["turns"],
            "labor": construction.get("labor", 0),
            "materials": {g: from_milli(q) for g, q in construction.get("materials", {}).items()},
        },
        "recipes": {
            rid: {
                "name": d.recipe_name(fid, rid),
                "inputs": {g: from_milli(q) for g, q in r["inputs"].items()},
                "outputs": {g: from_milli(q) for g, q in r["outputs"].items()},
                "requires_resource": r.get("requires_resource"),
            }
            for rid, r in f["recipes"].items()
        },
    }


@cache
def _theme(name: str) -> dict:
    path = settings.BASE_DIR / "server" / "static" / "themes" / name / "theme.yaml"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --- 島 ---------------------------------------------------------------------


@require_GET
def island_map(request, island_id: int):
    """島のマスと施設。マスの値は y * size + x の順に並べる。"""
    island = get_object_or_404(m.Island, pk=island_id)
    columns = ["terrain", "population", "zone", "infra", "pollution", "ext"]
    data: dict[str, list] = {
        "terrain": [],
        "population": [],
        "zone": [],
        "power_line": [],
        "pollution": [],
        "happiness": [],
        "power": [],
    }
    for terrain, population, zone, infra, pollution, ext in island.tiles.order_by(
        "y", "x"
    ).values_list(*columns):
        data["terrain"].append(terrain)
        data["population"].append(population)
        data["zone"].append(zone)
        data["power_line"].append(1 if (infra or {}).get("power") else 0)
        data["pollution"].append(pollution)
        data["happiness"].append((ext or {}).get("happiness"))
        data["power"].append((ext or {}).get("power"))
    facilities = [
        {
            "number": f.number,
            "kind": f.kind,
            "x": f.x,
            "y": f.y,
            "built": f.built,
            "level": (f.ext or {}).get("level"),
            "status": f.status,
            "rate": f.operating_rate,
        }
        for f in island.facilities.order_by("number")
    ]
    return _json(
        {
            "id": island.id,
            "name": island.name,
            "size": island.size,
            "turn": island.world.turn,
            "capital": [island.capital_x, island.capital_y]
            if island.capital_x is not None
            else None,
            "regime": island.regime,
            "mine": request.user.is_authenticated and island.owner_id == request.user.id,
            "facilities": facilities,
            **data,
        }
    )


@require_GET
def tile(request, island_id: int, x: int, y: int):
    try:
        row = m.Tile.objects.select_related("island").get(island_id=island_id, x=x, y=y)
    except m.Tile.DoesNotExist as e:
        raise Http404 from e
    game = get_game()
    d = game.defs
    island = island_from_row(row.island)
    ext = row.ext or {}
    data: dict[str, Any] = {
        "x": row.x,
        "y": row.y,
        "terrain": row.terrain,
        "terrain_name": d.name("terrain", row.terrain),
        "population": row.population,
        "place": place_of(island, row.x, row.y, game.config),
        "zone": row.zone,
        "power_line": bool((row.infra or {}).get("power")),
        "pollution": row.pollution,
        "employed": ext.get("employed", 0),
        "happiness": ext.get("happiness"),
        "power": ext.get("power"),
        "capital": island.capital == (row.x, row.y),
    }
    if data["capital"]:
        data["capital_name"] = island.names.get("capital", "")
        # 体制で決まる首都の建物（議事堂・王宮など）
        regimes = [d.regimes[r] for r in island.regime.values() if r in d.regimes]
        data["capital_buildings"] = [
            display.message(f"capital_building_{r['capital_building']}")
            for r in regimes
            if r.get("capital_building")
        ]
    f = m.Facility.objects.filter(island_id=island_id, x=x, y=y).first()
    if f is not None:
        data["facility"] = display.facility_info(f, island)
    return _json(data)


# --- 命令 -------------------------------------------------------------------


@require_http_methods(["GET", "PUT"])
def orders(request, island_id: int):
    """GET: 待っている命令の列と行政力。PUT: 列を丸ごと置き換える（島の持ち主だけ）。"""
    island = get_object_or_404(m.Island, pk=island_id)
    if request.method == "GET":
        return _json(_orders_view(island))
    if not request.user.is_authenticated or island.owner_id != request.user.id:
        return _json({"error": "この島の命令は持ち主だけが変えられます"}, status=403)
    try:
        body = json.loads(request.body or b"{}")
        items = body["orders"]
        if not isinstance(items, list):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest("orders の一覧を JSON で送ってください")
    limit = get_game().config.get("orders.max_queue")
    if len(items) > limit:
        return _json({"error": f"命令は {limit} 個まで並べられます"}, status=400)
    state = load_island(island)
    errors, warnings = [], []
    for i, item in enumerate(items):
        kind, payload = item.get("kind"), item.get("payload")
        result = check_order(get_game().defs, get_game().registry, state, kind, payload)
        if result.ok:
            continue
        text = display.message(result.reason or "reason_unknown_order", **result.args)
        if result.reason in ("reason_unknown_order", "reason_invalid_payload"):
            errors.append({"index": i, "text": text, "details": list(result.details)})
        else:
            # 今の島では実行できなくても、前の命令が済めば実行できることがあるので受け付ける
            warnings.append({"index": i, "text": text})
    if errors:
        return _json({"errors": errors}, status=400)
    with transaction.atomic():
        Order.objects.filter(island=island, status="pending").delete()
        Order.objects.bulk_create(
            [
                Order(island=island, seq=i + 1, kind=item["kind"], payload=item["payload"])
                for i, item in enumerate(items)
            ]
        )
    return _json({**_orders_view(island), "warnings": warnings})


@require_http_methods(["POST"])
def check(request, island_id: int):
    """命令を1つ、今の島の状態で確かめる（並べる前の確認用）。"""
    island = get_object_or_404(m.Island, pk=island_id)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseBadRequest("JSON で送ってください")
    state = load_island(island)
    result = check_order(
        get_game().defs, get_game().registry, state, body.get("kind"), body.get("payload")
    )
    text = "" if result.ok else display.message(result.reason or "", **result.args)
    return _json({"ok": result.ok, "text": text, "details": list(result.details)})


def _orders_view(island: m.Island) -> dict[str, Any]:
    d = get_game().defs
    pending = list(island.orders.filter(status="pending").order_by("seq", "id"))
    turn, stats = display.latest_stats(island)
    admin_power = stats.get("admin_power")
    if admin_power is None:
        admin_power = get_game().config.get("orders.base_admin_power")
    recent = island.orders.exclude(status="pending").order_by("-executed_turn", "-seq")[:20]
    return {
        "admin_power": int(admin_power),
        "orders": [
            {
                "kind": o.kind,
                "payload": o.payload,
                "cost": d.orders[o.kind]["admin_cost"] if o.kind in d.orders else 0,
                "text": display.describe_order(o.kind, o.payload),
            }
            for o in pending
        ],
        "recent": [
            {
                "turn": o.executed_turn,
                "status": o.status,
                "text": display.describe_order(o.kind, o.payload),
                "reason": display.message(o.reason, **(o.reason_args or {})) if o.reason else "",
            }
            for o in recent
        ],
    }


# --- 世界 -------------------------------------------------------------------


SYMBOLS = string.ascii_uppercase + string.ascii_lowercase + string.digits


@require_GET
def world_shapes(request):
    """世界マップに1マス1ピクセルで描く、島の形と町・施設の並び。

    島ごとに、マスごとの1文字の記号を y * size + x の順に並べた文字列を返す。記号の意味は codes
    （記号 → "terrain:地形"・"settlement:段階"・"facility:種類"・"capital"・"construction"）。
    """
    w = m.World.objects.order_by("id").first()
    if w is None:
        return _json({"codes": {}, "islands": []})
    d = get_game().defs
    housing = fac.housing_kind(get_game().config)
    codes: dict[str, str] = {}
    symbol: dict[str, str] = {}

    def code_of(key: str) -> str:
        if key not in symbol:
            symbol[key] = SYMBOLS[len(symbol)] if len(symbol) < len(SYMBOLS) else "?"
            codes[symbol[key]] = key
        return symbol[key]

    islands = {}
    for i in w.islands.order_by("id").values("id", "size", "capital_x", "capital_y"):
        islands[i["id"]] = {**i, "cells": ["?"] * (i["size"] * i["size"])}
    tiles = m.Tile.objects.filter(island__world=w).values_list("island_id", "x", "y", "terrain")
    for island_id, x, y, terrain in tiles.iterator(chunk_size=4000):
        island = islands[island_id]
        island["cells"][y * island["size"] + x] = code_of(f"terrain:{terrain}")
    facilities = m.Facility.objects.filter(island__world=w).values_list(
        "island_id", "x", "y", "kind", "built", "ext"
    )
    for island_id, x, y, kind, built, ext in facilities.iterator(chunk_size=4000):
        island = islands[island_id]
        if kind == housing:
            level = (ext or {}).get("level") or d.settlement_levels()[0]
            key = f"settlement:{level}"
        elif (x, y) == (island["capital_x"], island["capital_y"]):
            key = "capital"
        else:
            key = f"facility:{kind}" if built else "construction"
        island["cells"][y * island["size"] + x] = code_of(key)
    return _json(
        {
            "codes": codes,
            "islands": [
                {"id": i["id"], "size": i["size"], "cells": "".join(i["cells"])}
                for i in islands.values()
            ],
        }
    )


@require_GET
def world(request):
    w = m.World.objects.order_by("id").first()
    if w is None:
        return _json({"grid": 0, "turn": 0, "islands": []})
    islands = w.islands.select_related("owner").order_by("id")
    return _json(
        {
            "grid": grid_size(w),
            "turn": w.turn,
            "islands": [
                {
                    "id": i.id,
                    "name": i.name,
                    "wx": i.wx,
                    "wy": i.wy,
                    "owner": i.owner.username if i.owner else "",
                    "mine": request.user.is_authenticated and i.owner_id == request.user.id,
                }
                for i in islands
            ],
        }
    )
