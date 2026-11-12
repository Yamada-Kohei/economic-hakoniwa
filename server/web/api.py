"""画面の JavaScript が使う JSON API。"""

from __future__ import annotations

import hashlib
import json
import string
from collections import Counter
from collections.abc import Mapping
from functools import cache
from typing import Any

import yaml
from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.templatetags.static import static
from django.views.decorators.http import require_GET, require_http_methods

from hakoniwa_core import facilities as fac
from hakoniwa_core import nature
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import freight
from hakoniwa_core.founding import land_terrains
from hakoniwa_core.military import army
from hakoniwa_core.modules import production
from hakoniwa_core.naming import default_place_style, place_of, place_styles
from hakoniwa_core.orders import OrderCheck, check_order
from hakoniwa_core.units import from_milli, to_basis
from server import jsonbody
from server.accounts import keys
from server.game import get_game
from server.letters import services as letter_services
from server.orders.models import Order
from server.turns import services as turn_services
from server.web import budget_guide, display, order_confirm, overview, rankings, reports
from server.web import chain as chain_views
from server.web import country as country_views
from server.web import diplomacy as diplomacy_views
from server.web import facility_page as facility_views
from server.web import military as military_views
from server.web import news as news_views
from server.web import politics as politics_views
from server.web import top as top_page
from server.web import trade as trade_views
from server.web import views as pages
from server.world import models as m
from server.world.repository import load_island
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
                for gid, g in ((gid, d.goods[gid]) for gid in display.goods_in_order(d))
            },
            "orders": {
                oid: {
                    "name": d.name("orders", oid),
                    "admin_cost": o["admin_cost"],
                    **_caution(oid),
                }
                for oid, o in d.orders.items()
            },
            "settlements": {
                sid: {
                    "name": d.name("settlements", sid),
                    **{k: v for k, v in s.items() if k != "materials"},
                    "materials": _shown_amounts(s.get("materials", {})),
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
            "infrastructure": {kid: _infra_def(kid, k) for kid, k in d.infrastructure.items()},
            "mining": {gid: d.name("goods", gid) for gid in d.mining_resources()},
            "army": {aid: _army_def(aid, a) for aid, a in d.army.items()},
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


@require_GET
def order_defs(request):
    """命令の定義: 表示名・要る行政力・中身の形（JSON Schema）・議会の採決・実行まで伏せるか。

    中身の形は packs/base/data/orders.yaml の payload そのまま。命令の列（PUT .../orders）と確かめ
    （POST .../orders/check）に送る {"kind": 種類, "payload": 中身} の中身がこの形になる。ただし財の
    量と 1 単位あたりの値段は画面と同じ見せる単位で送る（W-30。値段は小数も受け付ける）。
    """
    d = get_game().defs
    return _json(
        {
            "orders": {
                oid: {
                    "name": d.name("orders", oid),
                    "admin_cost": o["admin_cost"],
                    "payload": _order_schema(oid, _plain(o["payload"])),
                    **({"units": ORDER_UNITS_NOTE} if display.order_has_units(oid) else {}),
                    **({"parliament": o["parliament"]} if o.get("parliament") else {}),
                    **({"secret": _plain(o["secret"])} if o.get("secret") else {}),
                    **_caution(oid),
                }
                for oid, o in d.orders.items()
            }
        }
    )


def _caution(kind: str) -> dict[str, str]:
    """軽く動かすと戻らない命令の注意（W-255）。ない命令は空。"""
    text = display.ORDER_CAUTIONS.get(kind)
    return {"caution": text} if text else {}


ORDER_UNITS_NOTE = (
    "財の量と 1 単位あたりの値段は、画面・国の要約と同じ見せる単位"
    "（重さの財は実物のトン）で書きます"
)


def _order_schema(kind: str, schema: Any) -> Any:
    """命令の中身の形。値段の項目は見せる単位で小数も書けるので number にする（W-30・W-182）。"""
    if not isinstance(schema, dict):
        return schema
    spec = display.ORDER_UNITS.get(kind)
    if spec is not None:
        schema = _number_prices(schema, spec[2])
    nested = display.ORDER_NESTED_UNITS.get(kind)
    if nested is not None:
        key, item_spec = nested
        properties = dict(schema.get("properties", {}))
        array = properties.get(key)
        if isinstance(array, dict) and isinstance(array.get("items"), dict):
            items = _number_prices(array["items"], item_spec[2])
            properties[key] = {**array, "items": items}
            schema = {**schema, "properties": properties}
    return schema


def _number_prices(schema: dict[str, Any], price_keys: tuple[str, ...]) -> dict[str, Any]:
    properties = dict(schema.get("properties", {}))
    for key in price_keys:
        if isinstance(properties.get(key), dict):
            properties[key] = {**properties[key], "type": "number"}
    return {**schema, "properties": properties}


def _shown_amounts(amounts: dict[str, int]) -> dict[str, float]:
    """財 → 1000 分の 1 単位の量を、財 → 見せる量に（display_scale を掛ける。W-30）。"""
    d = get_game().defs
    return {g: round(d.shown_amount(g, from_milli(q)), 3) for g, q in amounts.items()}


def _plain(value: Any) -> Any:
    """書き換えられない定義データ（Defs）を、JSON にできる dict と list に直す。"""
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def _terrain_def(tid: str, t: Any) -> dict[str, Any]:
    name = get_game().defs.name("terrain", tid)
    return {"name": name, "height": t.get("height", 0), "land": t["land"]}


def _infra_def(kid: str, k: Any) -> dict[str, Any]:
    """インフラの種類（フェーズ2-3）。資材と人手は 1マスを 0% から 100% にする分。"""
    d = get_game().defs
    return {
        "name": d.name("infrastructure", kid),
        "role": k["role"],
        "grid": k.get("grid"),
        "turns": k.get("turns", 1),
        "labor": k.get("labor", 0),
        "materials": _shown_amounts(k.get("materials", {})),
    }


def _army_def(aid: str, a: Any) -> dict[str, Any]:
    """部隊・艦隊の種類（army.yaml）。装備は財 → 見せる量、重さ・燃料・運べる重さは見せるトン。
    海の艦隊は 1 隻あたりの値。"""
    out = {
        "name": get_game().defs.name("army", aid),
        "arm": a["arm"],
        "soldiers": a["soldiers"],
        "equipment": _shown_amounts(a["equipment"]),
        "speed": a["speed"],
        "strength": a.get("strength"),
    }
    weight_scale = get_game().defs.weight_scale()
    for key in ("weight", "vehicles", "vision", "fuel", "capacity", "stealth", "detect", "hunt"):
        if key in a:
            # 重さ（トン）は積荷の量と同じ見せる単位に（W-182）
            out[key] = a[key] * weight_scale if key in ("weight", "fuel", "capacity") else a[key]
    return out


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
        "staff": dict(f.get("staff", {})),
        "priority": f.get("priority"),
        "sprite": f.get("sprite", fid),
        "tileless": f.get("tileless", False),
        "site": f.get("site"),
        "zone": f.get("zone"),
        "monument": "monument" in f,
        # 隠して建てられる施設（MI-62）だけ。工期と資材の倍率
        **({"hide": dict(f["hide"])} if f.get("hide") else {}),
        "construction": {
            "turns": construction["turns"],
            "labor": construction.get("labor", 0),
            "materials": _shown_amounts(construction.get("materials", {})),
        },
        "recipes": {
            rid: {
                "name": d.recipe_name(fid, rid),
                "inputs": _shown_amounts(r["inputs"]),
                "outputs": _shown_amounts(r["outputs"]),
                "requires_resource": r.get("requires_resource"),
                # 電子機器を使う作り方（MI-43）は少ない人手で同じだけ作り、学校は育ちが上がる
                "labor": r.get("labor", 1.0),
                "workers": fac.workers_needed(d, fid, rid),
                **(_graded_build(fid, rid) if fac.build_grade(d, fid, rid) else {}),
                **({"education_scale": r["education_scale"]} if "education_scale" in r else {}),
            }
            for rid, r in f["recipes"].items()
        },
    }


def _graded_build(fid: str, rid: str) -> dict[str, Any]:
    """段階の作り方（機械化・近代化）で新しく建てる農場の工期と資材（W-257）。手作業の農場の工事に、
    その段階までの工事を足した分。"""
    d = get_game().defs
    turns, materials = fac.build_spec(d, fid, rid)
    grade = fac.build_grade(d, fid, rid)
    name = display.message(fac.grade_name(d, fid, grade))
    return {
        "construction": {"turns": turns, "materials": _shown_amounts(materials)},
        "build_hint": f"初めから{name}の段階で建ちます。手作業の農場の工事に、{name}までの"
        "工事の工期と資材（機械など）を足した分がかかります。",
    }


@cache
def _theme(name: str) -> dict:
    path = settings.BASE_DIR / "server" / "static" / "themes" / name / "theme.yaml"
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    data["base"] = static(f"themes/{name}/")  # 絵（sprites）のファイル名はここからの相対
    return data


# --- 島 ---------------------------------------------------------------------


def _capital_buildings(regime: dict[str, str], ext: dict[str, Any], d: Defs) -> list[str]:
    """体制で決まる首都の建物（議事堂・王宮など）を軸の順に。地図の絵とマスの欄が使う。

    政治の仕組みを変えて建て替えている間は、前の体制の建物（フェーズ3-4）。
    """
    rebuilding = (ext or {}).get("politics", {}).get("building")
    buildings = []
    for axis in d.regime_axes():
        rid = regime.get(axis)
        if rid not in d.regimes:
            continue
        building = (rebuilding if axis == "politics" else None) or d.regimes[rid].get(
            "capital_building"
        )
        if building:
            buildings.append(building)
    return buildings


@require_GET
def island_map(request, island_id: int):
    """島のマスと施設。マスの値は y * size + x の順に並べる。

    infra は種類ごとの整備度（%）の並び、infra_queue はインフラの工事の列（[種類, x, y]）。
    ?freight=1 のときだけ、陸のマスごとの拠点までの運賃の率（freight、1万分の1。海は null）も返す
    （計算が重いので、重ね表示で選んだときだけ読む）。
    """
    island = get_object_or_404(m.Island, pk=island_id)
    columns = ["terrain", "population", "zone", "infra", "pollution", "ext"]
    mine = _mine(request, island)
    viewer = letter_services.own_island(request.user)
    facility_rows = list(island.facilities.filter(x__gte=0).order_by("number"))
    unseen = [
        f
        for f in facility_rows
        if not display.sees_facility(f, mine, viewer.id if viewer else None)
    ]
    visible = {str(f.number) for f in facility_rows if f not in unseen}
    hidden_places = {(f.x, f.y) for f in unseen}
    data: dict[str, Any] = {
        "terrain": [],
        "population": [],
        "zone": [],
        "power_line": [],
        "trench": [],
        "occupied": [],
        "fallout": [],
        "pollution": [],
        "happiness": [],
        "power": [],
        "infra": {kind: [] for kind in get_game().defs.infrastructure},
    }
    for index, (terrain, population, zone, infra, pollution, ext) in enumerate(
        island.tiles.order_by("y", "x").values_list(*columns)
    ):
        concealed = (index % island.size, index // island.size) in hidden_places
        data["terrain"].append(terrain)
        data["population"].append(population)
        data["zone"].append(zone)
        data["power_line"].append(1 if display.carries_power(infra) else 0)
        data["trench"].append(1 if (ext or {}).get(army.TRENCH) else 0)
        data["occupied"].append(((ext or {}).get("occupied") or {}).get("by", 0))
        data["fallout"].append(
            round((ext or {}).get("fallout", 0) / 1000, 1)
        )  # 核の汚染の濃さ（点）
        data["pollution"].append(display.visible_pollution(pollution, ext or {}, visible, mine))
        data["happiness"].append(None if concealed else (ext or {}).get("happiness"))
        data["power"].append(None if concealed else (ext or {}).get("power"))
        for kind, level in display.infra_levels(infra).items():
            data["infra"][kind].append(level)
    works = (island.economy or {}).get("infra", {})
    data["infra_queue"] = works.get("queue", [])
    data["infra_why"] = display.works_why(island.economy)
    if request.GET.get("freight"):
        data.update(_freight(island))
    # 部隊の印は、見ている国に見えるものだけ（自分の部隊と、見える範囲に入った相手の部隊。
    # 陣地と占領は誰にでも見える）
    data["units"] = military_views.island_units(island, letter_services.own_island(request.user))
    facilities = [
        {
            "number": f.number,
            "kind": f.kind,
            "x": f.x,
            "y": f.y,
            "built": f.built,
            "level": (f.ext or {}).get("level"),
            # 地図が絵を選ぶ: 作り方（綿の畑・銅の鉱山など）、農場の段階（FM-50）、
            # 建て増しの規模（FM-47）
            "recipe": f.recipe,
            "grade": (f.ext or {}).get("grade"),
            "size": (f.ext or {}).get("size", 1),
            "status": f.status,
            "rate": f.operating_rate,
            **({"hidden": True} if (f.ext or {}).get("hidden") else {}),
        }
        for f in facility_rows  # マスを持つ施設だけ
        # 隠した施設（MI-62）は、持ち主と、見つけた国（と同盟国）にだけ見える
        if display.sees_facility(f, mine, viewer.id if viewer else None)
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
            "capital_buildings": _capital_buildings(island.regime, island.ext, get_game().defs),
            "mine": mine,
            "facilities": facilities,
            **data,
        }
    )


def _freight(island: m.Island) -> dict[str, Any]:
    """陸のマスごとの、拠点（首都と大きな町）まで運ぶときの片側の運賃の率（1万分の1）と上限。"""
    game = get_game()
    state = load_island(island)
    rates = freight.hub_rates(state, game.defs, game.config)
    size = island.size
    return {
        "freight": [rates.get((i % size, i // size)) for i in range(size * size)],
        "freight_max": to_basis(game.config.get("freight.max_rate")),
    }


@require_GET
def tile(request, island_id: int, x: int, y: int):
    try:
        row = m.Tile.objects.select_related("island").get(island_id=island_id, x=x, y=y)
    except m.Tile.DoesNotExist as e:
        raise Http404 from e
    game = get_game()
    d = game.defs
    island = display.island_for_naming(row.island)
    ext = row.ext or {}
    data: dict[str, Any] = {
        "x": row.x,
        "y": row.y,
        "terrain": row.terrain,
        "terrain_name": d.name("terrain", row.terrain),
        "population": row.population,
        "place": place_of(island, row.x, row.y, game.config),
        "zone": row.zone,
        "pollution": row.pollution,
        "employed": ext.get("employed", 0),
        "happiness": ext.get("happiness"),
        "power": ext.get("power"),
        "capital": island.capital == (row.x, row.y),
    }
    if "fertility" in ext:  # 平地の肥沃度（フェーズ3-2。1000 分の 1）
        data["fertility"] = ext["fertility"] / 1000
    # インフラの整備度と並んでいる工事（フェーズ2-3）。送電線の表示は送電網の整備度で決まる
    economy = row.island.economy or {}
    data.update(display.infra_view(row.infra, economy.get("infra", {}).get("queue", []), x, y))
    if data["works"]:
        data["works_total"] = len(economy["infra"]["queue"])
        data["works_why"] = display.works_why(economy)
    if data["capital"]:
        data["capital_name"] = island.names.get("capital", "")
        data["capital_buildings"] = [
            display.message(f"capital_building_{b}")
            for b in _capital_buildings(island.regime, island.ext, d)
        ]
    f = m.Facility.objects.filter(island_id=island_id, x=x, y=y).first()
    viewer = letter_services.own_island(request.user)
    mine = _mine(request, row.island)
    visible = {
        str(facility.number)
        for facility in row.island.facilities.all()
        if display.sees_facility(facility, mine, viewer.id if viewer else None)
    }
    data["pollution"] = display.visible_pollution(row.pollution, ext, visible, mine)
    if f is not None and not display.sees_facility(f, mine, viewer.id if viewer else None):
        data.update(employed=0, happiness=None, power=None)
        f = None
    if f is not None:
        data["facility"] = display.facility_info(f, island, _mine(request, row.island))
    water = _water(row, f)
    if water is not None:  # 水力発電所の見込み（W-148）
        data["water" if f is None else "facility_water"] = water
    return _json(data)


def _water(row: m.Tile, f: m.Facility | None) -> dict[str, Any] | None:
    """山のマスに水力発電所を建てたときの見込みか、建っている水力発電所の今の出力（全力を 100）。
    集水域は周りのマスの地形で決まる。近くの水力発電所と水を分け合うので、島の施設も読む。"""
    game = get_game()
    defs = game.defs
    if f is None and row.terrain != "mountain":
        return None
    if f is not None:
        recipes = defs.facilities.get(f.kind, {}).get("recipes", {})
        spec = nature.water_spec(recipes.get(f.recipe, {}))
        kind = f.kind
    else:
        kind, spec = next(
            (
                (k, w)
                for k, v in defs.facilities.items()
                if v.get("buildable") is not False
                and (w := nature.water_spec(v["recipes"].get("default", {}))) is not None
                and row.terrain in v["terrain"]
            ),
            (None, None),
        )
    if spec is None:
        return None
    island = load_island(row.island)
    here = nature.water_factor(island, defs, spec, row.x, row.y, weather=False)
    out = {"kind": kind, "kind_name": defs.name("facilities", kind), "percent": round(here * 100)}
    if f is None:
        out["min"] = round(spec["min"] * 100)
        out["buildable"] = here >= spec["min"]
    else:  # 建っている発電所は、気候と今の年の雨のぶれも入れた出力
        out["percent"] = round(nature.water_factor(island, defs, spec, row.x, row.y) * 100)
        out["year"] = round(nature.rain_year(island) * 100)
        out["site"] = round(here * 100)
    return out


# --- 統計（経済・市場・施設・金融の画面） ---------------------------------------------


def _mine(request, island: m.Island) -> bool:
    return request.user.is_authenticated and island.owner_id == request.user.id


def _currency(island: m.Island) -> str:
    return (island.names or {}).get("currency_symbol", "")


@require_GET
def economy(request, island_id: int):
    """統計の系列。?names=gdp,cpi,price.*（末尾の * はその頭の統計すべて）&from=&to=（ターン）。

    名前を決めなければ経済の画面のグラフの統計を、範囲を決めなければ直近の 120 ターンを返す。
    他国の島には銀行ごとの統計を返さない。
    """
    island = get_object_or_404(m.Island, pk=island_id)
    names = [n.strip() for n in request.GET.get("names", "").split(",") if n.strip()]
    start, end = reports.turn_range(island, request.GET.get("from"), request.GET.get("to"))
    mine = _mine(request, island)
    data = reports.series(island, names or reports.default_names(), start, end, mine)
    return _json({**data, "mine": mine, "currency": _currency(island)})


@require_GET
def economy_overview(request, island_id: int):
    """経済の画面の中身: 主な指標と国勢（直近のターンの値）・得意産業と称号・系列を読める統計の
    名前の一覧（choices。画面の「統計を選ぶ」欄と同じ）。"""
    island = get_object_or_404(m.Island.objects.select_related("world"), pk=island_id)
    mine = _mine(request, island)
    turn, stats = display.latest_stats(island)
    summary_names = [key for key, _, _ in display.SUMMARY_STATS]
    return _json(
        {
            "island": island.id,
            "mine": mine,
            "stats_turn": turn,
            "currency": _currency(island),
            "tiles": reports.stats_part(reports.with_fiscal(stats), reports.ECONOMY_TILES),
            "summary": reports.stats_part(stats, summary_names),
            "strengths": rankings.island_strengths(island, stats),
            "choices": reports.stat_choices(island, mine),
        }
    )


@require_GET
def market(request, island_id: int, good: str):
    """財の値段と量の推移と直近のターンの市場。約定の相手と買えなかった施設は持ち主だけ。"""
    island = get_object_or_404(m.Island, pk=island_id)
    if good not in get_game().defs.goods:
        raise Http404
    start, end = reports.turn_range(island, request.GET.get("from"), request.GET.get("to"))
    return _json(
        reports.market_series(island, good, start, end, _mine(request, island), _currency(island))
    )


@require_GET
def facility_book(request, island_id: int, number: int):
    """施設の帳簿の推移（島の持ち主だけ）。?from=&to=（ターン）。"""
    island = get_object_or_404(m.Island, pk=island_id)
    if not _mine(request, island):
        return _json({"error": "施設の帳簿は島の持ち主だけが見られます"}, status=403)
    start, end = reports.turn_range(island, request.GET.get("from"), request.GET.get("to"))
    data = reports.facility_book(island, number, start, end, _currency(island))
    if data is None:
        raise Http404
    return _json(data)


@require_GET
def facility(request, island_id: int, number: int):
    """施設のページ（UI-72）と同じ中身。持ち主には帳簿・借入・この施設に出ている命令・前のターンの
    結果・出せる命令（actions）も。ほかの人には地図のマスの窓と同じ公開の中身。見えない施設は
    404。"""
    island = get_object_or_404(m.Island.objects.select_related("world"), pk=island_id)
    viewer = letter_services.own_island(request.user)
    data = facility_views.facility_data(
        island, number, _mine(request, island), viewer.id if viewer else None
    )
    if data is None:
        raise Http404
    return _json({"island": island.id, **data})


@require_GET
def finance(request, island_id: int):
    """金融の統計と中央銀行の方針。持ち主には銀行ごとの帳簿と金融の政策も。"""
    island = get_object_or_404(m.Island, pk=island_id)
    return _json(reports.finance_data(island, _mine(request, island), _currency(island)))


# --- 貿易と為替（フェーズ3-1） --------------------------------------------------------


@require_GET
def trade(request, island_id: int):
    """島の貿易。だれにでも輸出・輸入・貿易収支・船の数と貿易圏の加盟を、持ち主には貿易圏の値段・
    契約・商社・船団と港・運ぶ途中の荷・海外の商人も（server/web/trade.py）。"""
    island = get_object_or_404(m.Island.objects.select_related("world"), pk=island_id)
    return _json(trade_views.trade_data(island, _mine(request, island), _currency(island)))


@require_GET
def fx(request, island_id: int):
    """為替と国際収支。だれにでもレート・国全体の数・推移（?from=&to=）を、持ち主には外貨の
    持ち高と非居住者の預金の内訳も。"""
    island = get_object_or_404(m.Island.objects.select_related("world"), pk=island_id)
    data = trade_views.fx_data(
        island,
        _mine(request, island),
        _currency(island),
        request.GET.get("from"),
        request.GET.get("to"),
    )
    return _json(data)


# --- 政治と計画（フェーズ3-4） ----------------------------------------------------------


@require_GET
def island_politics(request, island_id: int):
    """政治。だれにでも体制・政情とその段階・支持率・議席・選挙・混乱期間・報道と集会の扱いを、
    持ち主には政情の内訳・出来事・採決の履歴・たまった不満・警察・動員も（server/web/politics.py）。"""
    island = get_object_or_404(m.Island, pk=island_id)
    return _json(politics_views.politics_data(island, _mine(request, island)))


@require_GET
def island_military(request, island_id: int):
    """軍（フェーズ4-2）。持ち主には兵の数・士気・練度・国防費・部隊・艦隊・基地・陣地を、ほかの国には
    国全体の兵の数と国防費と見えた艦隊（MI-26・MI-55。server/web/military.py）。"""
    island = get_object_or_404(m.Island.objects.select_related("world"), pk=island_id)
    viewer = letter_services.own_island(request.user)
    return _json(
        {
            "military": military_views.brief(
                island, _mine(request, island), viewer.id if viewer else None
            )
        }
    )


@require_GET
def world_fleets(request):
    """世界マップの艦隊: 自分の艦隊と、見える範囲に入ったほかの国の艦隊（前に見た所とターンも）。"""
    w = m.World.objects.order_by("id").first()
    if w is None:
        return _json({"turn": 0, "viewer": None, "mine": [], "seen": []})
    viewer = letter_services.own_island(request.user)
    return _json(military_views.world_fleets(w, viewer.id if viewer else None))


@require_GET
def island_diplomacy(request, island_id: int):
    """外交。だれにでも戦争の状態・効いている条約・終わった条約・条約の違反の記録を、持ち主には返事待ちの
    条約の提案（届いたもの・出したもの）と命令の選択肢も（server/web/diplomacy.py）。"""
    island = get_object_or_404(m.Island, pk=island_id)
    return _json(diplomacy_views.diplomacy_data(island, _mine(request, island)))


@require_GET
def news(request):
    """世界の近況（?page=）。全部の国の主な出来事を新しい順に。誰でも読める（server/web/news.py）。"""
    world = m.World.objects.order_by("id").first()
    if world is None:
        return _json({"turn": 0, "page": 1, "pages": 1, "count": 0, "entries": []})
    return _json(news_views.news_data(world, request.GET.get("page")))


@require_GET
def island_country(request, island_id: int):
    """国の公開ページ。だれにでも国旗・名前・元首・体制・通貨・人口・紹介文・年表（国ごとの紀年の
    日付）・来訪者の一言を、持ち主には紀年の選択肢と紹介文の元の文も。書き込みは
    server/country/api.py。"""
    island = get_object_or_404(m.Island.objects.select_related("world", "owner"), pk=island_id)
    viewer = letter_services.own_island(request.user)
    return _json(country_views.country_data(island, _mine(request, island), viewer))


@require_GET
def island_plan(request, island_id: int):
    """計画経済の計画と値段の規制。だれにでも国全体の行列と強制貯蓄を、持ち主には公定価格・生産
    目標と達成率・重点産業・財ごとの行列と、値段の上限と下限も。"""
    island = get_object_or_404(m.Island, pk=island_id)
    mine = _mine(request, island)
    _, stats = display.latest_stats(island)
    data = politics_views.plan_data(island, mine, stats)
    return _json({**data, "mine": mine, **reports.stats_part(stats, pages.PLAN_STATS)})


# --- 画面だけにあった中身（施設・財政・市場の表・産業の鎖・ターンログ・順位） ---------------
# 画面と同じ値を、同じ見せる範囲で JSON にしたもの。


@require_GET
def facilities(request, island_id: int):
    """施設の画面: 国全体の施設の統計と、持ち主には施設ごとの状態・人手・損益・預金・借入。"""
    island = get_object_or_404(m.Island, pk=island_id)
    mine = _mine(request, island)
    turn, stats = display.latest_stats(island)
    data: dict[str, Any] = {
        "island": island.id,
        "mine": mine,
        "stats_turn": turn,
        "stats": reports.stat_rows(stats, pages.FACILITY_STATS, _currency(island)),
        "towns": reports.town_rows(island),
        "industry_workers": reports.industry_workers(island),
        "fuel_reserves": display.fuel_reserves(island) if mine else [],
    }
    if mine:
        data["table"] = reports.facility_rows(island, _currency(island))
    else:  # 他国には企業の名前・所有・施設の数と種類・働く人だけ
        state = display.island_for_naming(island)
        data["firms"] = reports.firm_rows(island, state, _currency(island), mine=False)
    return _json(data)


@require_GET
def fiscal(request, island_id: int):
    """財政の画面: 国庫の収入と支出、予算と使った額、税率、国債の一覧（持ち主には持ち手も）。"""
    island = get_object_or_404(m.Island, pk=island_id)
    mine = _mine(request, island)
    turn, stats = display.latest_stats(island)
    return _json(
        {
            "island": island.id,
            "mine": mine,
            "stats_turn": turn,
            "fiscal": reports.fiscal_view(island, stats, _currency(island), mine),
            "bonds": reports.bond_rows(island, _currency(island), mine),
        }
    )


@require_GET
def market_table(request, island_id: int):
    """市場の画面の表: 財ごとの値段・変わり方・売買量・買いたかった量と売りたかった量・生産量・
    在庫。持ち主には、原料を買えずに止まった施設も。"""
    island = get_object_or_404(m.Island, pk=island_id)
    mine = _mine(request, island)
    turn, stats = display.latest_stats(island)
    data: dict[str, Any] = {
        "island": island.id,
        "mine": mine,
        "turn": reports.market_turn(island),
        "rows": reports.market_table(island, stats, _currency(island), mine),
    }
    if mine:
        data["short"] = reports.short_facilities(island, display.island_for_naming(island))
    return _json(data)


@require_GET
def chain(request, island_id: int):
    """産業の鎖（誰にでも。見えない隠した施設は数えない）。"""
    island = get_object_or_404(m.Island, pk=island_id)
    mine = _mine(request, island)
    viewer = letter_services.own_island(request.user)
    _, stats = display.latest_stats(island)
    return _json(
        chain_views.chain_view(
            island, stats, _currency(island), mine, viewer.id if viewer else None
        )
    )


@require_GET
def turn_log(request, island_id: int):
    """ターンログ（誰にでも。画面と同じ）。?from=&to=（ターン）。決めなければ、記録のある最後の
    ターンだけ。1回に返すのは LOG_TURNS_MAX ターン分まで（新しいほうを残す）。
    kind と facility で、公開してよい行から出来事・施設の種類を絞る。"""
    island = get_object_or_404(m.Island, pk=island_id)
    last = island.logs.aggregate(t=Max("turn"))["t"]
    if last is None:
        return _json({"island": island.id, "from": None, "to": None, "entries": []})
    end = _turn_arg(request.GET.get("to"), last)
    start = _turn_arg(request.GET.get("from"), end)
    start = max(start, end - overview.LOG_TURNS_MAX + 1)
    return _json(
        {
            "island": island.id,
            "from": start,
            "to": end,
            "entries": overview.log_range(
                island,
                start,
                end,
                _mine(request, island),
                request.GET.get("kind"),
                request.GET.get("facility"),
            ),
        }
    )


def _turn_arg(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@require_GET
def rankings_data(request):
    """統計の順位の画面（?base=島の番号 で、お金の額を直す通貨。決めなければ見る人の国の通貨）。"""
    w = m.World.objects.order_by("id").first()
    if w is None:
        return _json({"turn": 0, "categories": [], "industries": []})
    own = None
    if request.user.is_authenticated:
        own = m.Island.objects.filter(owner=request.user, world=w).first()
    base = _turn_arg(request.GET.get("base"), -1)
    if not w.islands.filter(pk=base).exists():
        base = rankings.default_base(w, own)
    return _json(rankings.ranking_data(w, base))


# --- AI が遊ぶための入り口（国の要約・差分・自分） -------------------------------------
# 中身は server/web/overview.py。鍵（server/accounts/keys.py）でも、ログインした画面からでも読める。


@require_GET
def me(request):
    """自分: アカウント・使っている AI 用の鍵・自分の島・世界のターン・届いた公電の未読の数。"""
    w = m.World.objects.order_by("id").first()
    turn = w.turn if w else 0
    data: dict[str, Any] = {
        "user": request.user.username if request.user.is_authenticated else None,
        "key": None,
        "island": None,
        "turn": turn,
        "date": display.turn_text(turn),
        "next_turn_at": w.next_turn_at.isoformat() if w and w.next_turn_at else None,
        "turn_schedule": display.turn_schedule(w) if w else None,
    }
    key = getattr(request, "api_key", None)
    if key is not None:
        data["key"] = {
            "name": key.name,
            "prefix": key.prefix,
            "calls_per_minute": settings.HAKONIWA_AI_CALLS_PER_MINUTE,
        }
    island = letter_services.own_island(request.user)
    if island is not None:
        data["island"] = {"id": island.id, "name": island.name, "ai": keys.is_ai_island(island)}
        data["unread_letters"] = letter_services.unread_count(island)
    return _json(data)


@require_GET
def summary(request, island_id: int):
    """国の要約: 主な指標と前のターンからの変化、政情、止まった施設と工事、財の値段と品切れ、
    満期の近い国債、直近のターンの出来事、注意、貿易の 1 行。持ち主には今月すべきこと（todo）・
    赤字の施設・命令の列と結果・公電も。"""
    island = get_object_or_404(m.Island.objects.select_related("owner", "world"), pk=island_id)
    viewer = letter_services.own_island(request.user)
    return _json(overview.summary(island, _mine(request, island), viewer.id if viewer else None))


@require_GET
def changes(request, island_id: int):
    """ターン since より後に起きたこと（?since=ターン。決めなければ前のターンから）。"""
    island = get_object_or_404(m.Island.objects.select_related("world"), pk=island_id)
    since = _turn_arg(request.GET.get("since"), island.world.turn - 1)
    mine = _mine(request, island)
    return _json(overview.changes(island, since, mine, _secret_orders(), _hidden_orders()))


# --- 命令 -------------------------------------------------------------------


def _check_order(state, kind: Any, payload: Any):
    """命令を確かめる。種類が文字でないもの（JSON の型違い）は、知らない命令として断る。"""
    if not isinstance(kind, str):
        return OrderCheck(False, "reason_unknown_order")
    return check_order(get_game().defs, get_game().registry, state, kind, payload)


@require_http_methods(["GET", "PUT"])
def orders(request, island_id: int):
    """GET: 待っている命令の列と行政力。PUT: 列を丸ごと置き換える（島の持ち主だけ）。

    PUT に version（GET の答えの版）を付けると、その後にほかの画面や道具で列が変わっていれば
    置き換えずに 409 を返す（古い列で上書きして、足した命令を消さないため。UI-74）。画面は
    1 件ずつの API（order_items・order_item・order_sequence）を使う。
    新しく足す命令に打ち間違いに見えるものか戻せないものがあれば、送りに confirm: true が
    付いていない限り並べずに、確かめの文を 428 で返す（UI-75。order_confirm）。"""
    island = get_object_or_404(m.Island, pk=island_id)
    if request.method == "GET":
        mine = _mine(request, island)
        data = _orders_view(island, mine)
        if mine and request.GET.get("check") == "1":  # 命令のページ: 今は実行できない命令の警告も
            data["warnings"] = _queue_warnings(island, load_island(island))
        return _json(data)
    if not request.user.is_authenticated or island.owner_id != request.user.id:
        return _json({"error": "この島の命令は持ち主だけが変えられます"}, status=403)
    try:
        body = jsonbody.loads(request.body or b"{}")
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
        if not isinstance(item, dict):
            text = display.message("reason_invalid_payload")
            errors.append(
                {"index": i, "text": text, "details": ["{kind, payload} の形で送ってください"]}
            )
            continue
        # 量と値段は見せる単位で受け取り、計算の単位に直して確かめ、並べる（W-30）
        kind = item.get("kind")
        payload = item["payload"] = display.order_payload(kind, item.get("payload"), to_game=True)
        result = _check_order(state, kind, payload)
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
    if body.get("confirm") is not True:
        asks = _confirmations(island, state, items)
        if asks:
            return _json(
                {"error": order_confirm.CONFIRM_TEXT, "confirm": asks},
                status=order_confirm.CONFIRM_STATUS,
            )
    with transaction.atomic():
        if turn_services.update_running():
            return _json({"error": BUSY_TEXT}, status=409)
        stale = _stale(island, body.get("version"))
        if stale is not None:
            return stale
        Order.objects.filter(island=island, status="pending").delete()
        Order.objects.bulk_create(
            [
                Order(island=island, seq=i + 1, kind=item["kind"], payload=item["payload"])
                for i, item in enumerate(items)
            ]
        )
    return _json({**_orders_view(island), "warnings": warnings})


BUSY_TEXT = "いま更新中か、別の書き換えと重なりました。数秒おいてもう一度送ってください"
STALE_TEXT = (
    "命令の列が、読んだ後にほかの画面か道具で変わりました。読み直してから送ってください"
    "（version は今の版）"
)
GONE_TEXT = "その命令はもうありません（実行されたか、ほかの画面で消されました）"


def queue_version(island: m.Island) -> str:
    """待っている命令の列の版（命令の番号と順番から作る）。足す・消す・並べ替える・実行するたびに
    変わるので、古い列を元にした丸ごとの置き換えを見分けられる（UI-74）。"""
    rows = island.orders.filter(status="pending").order_by("seq", "id").values_list("id", "seq")
    text = ",".join(f"{i}:{s}" for i, s in rows)
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _stale(island: m.Island, version: Any) -> JsonResponse | None:
    """送られた版（省けば見ない）が今の列の版と違えば 409 の答え。"""
    if version is None:
        return None
    current = queue_version(island)
    if version == current:
        return None
    return _json({"error": STALE_TEXT, "version": current}, status=409)


def _owner_only(request, island: m.Island) -> JsonResponse | None:
    if _mine(request, island):
        return None
    return _json({"error": "この島の命令は持ち主だけが変えられます"}, status=403)


def _queue_warnings(island: m.Island, state) -> list[dict[str, Any]]:
    """待っている命令のうち、今の島では実行できないもの（列の位置・命令の番号・わけ）。"""
    out = []
    pending = island.orders.filter(status="pending").order_by("seq", "id")
    for i, o in enumerate(pending):
        result = _check_order(state, o.kind, o.payload)
        if not result.ok:
            text = display.message(result.reason or "reason_unknown_order", **result.args)
            out.append({"index": i, "id": o.id, "text": text})
    return out


def _changed(island: m.Island, state=None, **extra: Any) -> JsonResponse:
    """1 件ずつの書き換えの答え: 今の列（版つき）と、今は実行できない命令の警告。"""
    state = state if state is not None else load_island(island)
    view = _orders_view(island)
    return _json({**view, "warnings": _queue_warnings(island, state), **extra})


@require_http_methods(["POST"])
def order_items(request, island_id: int):
    """命令を 1 つ、列の最後に足す（島の持ち主だけ。UI-74）。本文は {kind, payload}。

    今の列を読み直さずにサーバーが最後に足すので、2 つの画面や道具で同時に足しても消えない。
    形の誤りは 400、確かめの要る命令（UI-75）は confirm: true が付いていなければ 428 と確かめの文、
    今の島では実行できない命令は警告（warning）を付けて受け付ける。答えは今の列と、足した命令の
    番号（added）。"""
    island = get_object_or_404(m.Island, pk=island_id)
    refused = _owner_only(request, island)
    if refused is not None:
        return refused
    try:
        body = jsonbody.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseBadRequest("JSON で送ってください")
    if not isinstance(body, dict) or not isinstance(body.get("payload", {}), dict):
        return HttpResponseBadRequest("命令は1つずつ {kind, payload} の形で送ってください")
    limit = get_game().config.get("orders.max_queue")
    if island.orders.filter(status="pending").count() >= limit:
        return _json({"error": f"命令は {limit} 個まで並べられます"}, status=400)
    state = load_island(island)
    kind = body.get("kind")
    payload = display.order_payload(kind, body.get("payload", {}), to_game=True)
    result = _check_order(state, kind, payload)
    warning = ""
    if not result.ok:
        text = display.message(result.reason or "reason_unknown_order", **result.args)
        if result.reason in ("reason_unknown_order", "reason_invalid_payload"):
            return _json({"error": text, "details": list(result.details)}, status=400)
        warning = text  # 前の命令が済めば実行できることがあるので受け付ける
    if body.get("confirm") is not True:
        notes = order_confirm.confirmations(order_confirm.Context(island, state), kind, payload)
        if notes:
            text = display.describe_order(kind, payload)
            ask = {"index": 0, "kind": kind, "text": text, "notes": notes}
            return _json(
                {"error": order_confirm.CONFIRM_TEXT, "confirm": [ask]},
                status=order_confirm.CONFIRM_STATUS,
            )
    with transaction.atomic():
        if turn_services.update_running():
            return _json({"error": BUSY_TEXT}, status=409)
        top = island.orders.filter(status="pending").aggregate(top=Max("seq"))["top"] or 0
        order = Order.objects.create(island=island, seq=top + 1, kind=kind, payload=payload)
    return _changed(island, state, added=order.id, warning=warning)


@require_http_methods(["DELETE"])
def order_item(request, island_id: int, order_id: int):
    """待っている命令を 1 つ消す（島の持ち主だけ。UI-74）。もうなければ 404 と今の列。"""
    island = get_object_or_404(m.Island, pk=island_id)
    refused = _owner_only(request, island)
    if refused is not None:
        return refused
    with transaction.atomic():
        if turn_services.update_running():
            return _json({"error": BUSY_TEXT}, status=409)
        deleted, _ = island.orders.filter(pk=order_id, status="pending").delete()
    if not deleted:
        return _json({**_orders_view(island), "error": GONE_TEXT}, status=404)
    return _changed(island)


@require_http_methods(["POST"])
def order_sequence(request, island_id: int):
    """待っている命令を並べ替える（島の持ち主だけ。UI-74）。本文は {"ids": [命令の番号を上から]}。

    もうない番号（実行された・ほかの画面で消された）は飛ばし、ids にない命令（ほかの画面で足した
    命令）は、今の順のまま後ろに置く。version（列の版）を付けると、変わっていれば 409。"""
    island = get_object_or_404(m.Island, pk=island_id)
    refused = _owner_only(request, island)
    if refused is not None:
        return refused
    try:
        body = jsonbody.loads(request.body or b"{}")
        ids = body["ids"]
        if not isinstance(ids, list) or not all(
            isinstance(i, int) and not isinstance(i, bool) for i in ids
        ):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest("ids に命令の番号の一覧を JSON で送ってください")
    with transaction.atomic():
        if turn_services.update_running():
            return _json({"error": BUSY_TEXT}, status=409)
        stale = _stale(island, body.get("version"))
        if stale is not None:
            return stale
        pending = list(island.orders.filter(status="pending").order_by("seq", "id"))
        by_id = {o.id: o for o in pending}
        chosen = list(dict.fromkeys(i for i in ids if i in by_id))
        ordered = [by_id[i] for i in chosen] + [o for o in pending if o.id not in set(chosen)]
        for seq, o in enumerate(ordered, 1):
            o.seq = seq
        Order.objects.bulk_update(ordered, ["seq"])
    return _changed(island)


def _order_key(kind: Any, payload: Any) -> str:
    """同じ命令かを比べる鍵（中身は計算の単位。小数の丸めの差は見ない）。"""

    def plain(value: Any) -> Any:
        if isinstance(value, float):
            return round(value, 6)
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()}
        if isinstance(value, list):
            return [plain(v) for v in value]
        return value

    return json.dumps([kind, plain(payload)], sort_keys=True, ensure_ascii=False, default=str)


def _confirmations(island: m.Island, state, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """新しく足す命令（今の列になかった命令）の確かめの文（UI-75）。並べ替えと消すだけなら空。

    列を丸ごと送る作りなので、今待っている命令と同じ種類と中身の命令は数の分だけ足したものに
    数えない。"""
    waiting = Counter(_order_key(o.kind, o.payload) for o in island.orders.filter(status="pending"))
    ctx = order_confirm.Context(island, state)
    asks = []
    for i, item in enumerate(items):
        key = _order_key(item.get("kind"), item.get("payload"))
        if waiting[key] > 0:
            waiting[key] -= 1
            continue
        texts = order_confirm.confirmations(ctx, item.get("kind"), item.get("payload"))
        if texts:
            text = display.describe_order(item["kind"], item.get("payload") or {})
            asks.append({"index": i, "kind": item["kind"], "text": text, "notes": texts})
    return asks


@require_http_methods(["POST"])
def check(request, island_id: int):
    """命令を1つ、今の島の状態で確かめる（並べる前の確認用。島の持ち主だけ。ほかの人に返すと、
    倉庫の中身など持ち主だけの値を確かめの結果から読めてしまう）。"""
    island = get_object_or_404(m.Island, pk=island_id)
    if not _mine(request, island):
        return _json({"error": "この島の命令は持ち主だけが確かめられます"}, status=403)
    try:
        body = jsonbody.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseBadRequest("JSON で送ってください")
    if not isinstance(body, dict) or not isinstance(body.get("payload", {}), dict):
        return HttpResponseBadRequest("命令は1つずつ {kind, payload} の形で送ってください")
    state = load_island(island)
    payload = display.order_payload(body.get("kind"), body.get("payload"), to_game=True)
    result = _check_order(state, body.get("kind"), payload)
    text = "" if result.ok else display.message(result.reason or "", **result.args)
    data = {"ok": result.ok, "text": text, "details": list(result.details)}
    # 打ち間違いに見えるか、戻せない命令なら、並べるときに聞く確かめの文も先に返す（UI-75）
    asks = order_confirm.confirmations(
        order_confirm.Context(island, state), body.get("kind"), payload
    )
    if asks:
        data["confirm"] = asks
    if result.ok:  # どう読んだか（率の % や量の単位）を見られるように、命令の列と同じ説明を添える
        data["summary"] = display.describe_order(
            body["kind"], payload or {}, display.facility_labels(island)
        )  # 計算の単位で渡す
    if result.ok and body.get("kind") == "build":
        # 建てても人手が集まるかを見られるように、通勤圏の労働者の数を添える
        payload = body["payload"]
        kind, x, y = payload["facility"], payload["x"], payload["y"]
        labor, employed = fac.commute_labor(get_game().defs, get_game().config, state, kind, x, y)
        radius = fac.commute_radius(get_game().defs, get_game().config, kind)
        data["commute"] = {"radius": radius, "labor": labor, "employed": employed}
        data["notes"] = _build_notes(state, kind, x, y, labor - employed)
    return _json(data)


def _build_notes(state, kind: str, x: int, y: int, free: int) -> list[str]:
    """建てられるが、建てた後に止まりそうなわけ（試し遊びで、確かめの時点で分かっていたのに
    言わなかったもの: 送電網につながらない・通勤圏に空いている働き手が足りない）。"""
    game = get_game()
    notes = []
    if production.reaches_power(state, game.defs, game.config, kind, x, y) is False:
        notes.append(display.message("check_note_no_grid"))
    need = game.defs.facilities[kind].get("workers", 0)
    if need > free:
        notes.append(display.message("check_note_few_workers", free=max(0, free), need=need))
    return notes


def _secret_orders() -> Q:
    """実行するまで伏せる命令の条件。

    定義の secret が true か always ならその種類の命令すべて、{項目: 値} なら中身のその項目が
    その値の命令。
    """
    secret = Q(pk__in=[])
    for kind, spec in get_game().defs.orders.items():
        when = spec.get("secret")
        if when is True or when == "always":
            secret |= Q(kind=kind)
        elif when:
            secret |= Q(kind=kind, **{f"payload__{key}": value for key, value in when.items()})
    return secret


def _hidden_orders() -> Q:
    """実行した後も持ち主だけに見せる命令の条件（secret が always の種類。部隊と艦隊への命令）。"""
    hidden = Q(pk__in=[])
    for kind, spec in get_game().defs.orders.items():
        if spec.get("secret") == "always":
            hidden |= Q(kind=kind)
    return hidden


def _orders_view(island: m.Island, mine: bool = True) -> dict[str, Any]:
    """命令の列・行政力・今の政策と、最近実行した命令。

    ほかの国の人（mine でない）には、実行するまで伏せる命令（定義の secret。為替介入・利下げ・
    不履行・国の倉庫の買い入れなど、現実の国が前もって公表しないもの）を待っている列から除き、
    最近の結果からは、伏せる命令のうち失敗したもの（実行されなかったもの）と、実行した後も伏せる命令
    （secret: always。部隊と艦隊への命令）を除く。前もって公表する
    命令（予算・税・関税・備蓄の放出など）と、実行した命令は誰にでも見える。
    """
    d = get_game().defs
    pending = island.orders.filter(status="pending")
    recent = island.orders.exclude(status="pending")
    if not mine:
        secret = _secret_orders()
        pending = pending.exclude(secret)
        recent = recent.exclude(Q(status="failed") & secret).exclude(_hidden_orders())
    pending = pending.order_by("seq", "id")
    turn, stats = display.latest_stats(island)
    admin_power = display.usable_admin(stats)
    recent = recent.order_by("-executed_turn", "-seq")[:20]
    labels = display.facility_labels(island)  # 施設への命令の文に種類と作る物を（W-262）
    return {
        "admin_power": admin_power,
        "admin_bank": int(stats.get("admin_bank", 0)),  # ためた行政力（W-265）
        # ためる前の 1 ターンの行政力（重い命令はこれだけを使う）と、使える倍率
        "admin_granted": int(stats.get("admin_power", admin_power)),
        "admin_burst": get_game().config.get("orders.burst"),
        "policy": (
            budget_guide.attach(display.policy_view(island), island)
            if mine
            else display.policy_view(island, mine=False)
        ),
        "power_good": get_game().config.get("power.good"),
        "orders": [
            {
                **({"id": o.id} if mine else {}),
                "kind": o.kind,
                "payload": display.order_payload(o.kind, o.payload, to_game=False),
                "cost": d.orders[o.kind]["admin_cost"] if o.kind in d.orders else 0,
                "text": display.describe_order(o.kind, o.payload, labels),
            }
            for o in pending
        ],
        # 列の版（持ち主だけ。丸ごとの置き換えと並べ替えで、古い列を元にしていないかを見る。UI-74）
        **({"version": queue_version(island)} if mine else {}),
        "recent": [
            {
                "turn": o.executed_turn,
                "status": o.status,
                "text": display.describe_order(o.kind, o.payload, labels),
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
    viewer = letter_services.own_island(request.user)
    for island_id, x, y, kind, built, ext in facilities.iterator(chunk_size=4000):
        island = islands[island_id]
        if x < 0 or y < 0:  # 運送業のようなマスを持たない施設
            continue
        # 隠した施設（MI-62）は、持ち主と見つけた国にだけ。ほかには元の地形のまま
        if (ext or {}).get("hidden") and not (
            viewer is not None and (viewer.id == island_id or viewer.id in ext.get("found", ()))
        ):
            continue
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
def world_trade(request):
    """世界マップの貿易（フェーズ3-1）: 貿易圏、島ごとの加盟と港、運ぶ途中の荷のある航路。

    航路は誰にでも見せ、荷の中身（財と量）は見ている人の島が積み出したか受け取る荷だけ返す。
    """
    w = m.World.objects.order_by("id").first()
    if w is None:
        return _json({"turn": 0, "blocs": [], "islands": [], "routes": []})
    own: set[int] = set()
    if request.user.is_authenticated:
        own = set(m.Island.objects.filter(owner=request.user).values_list("id", flat=True))
    return _json(trade_views.world_trade(w, own))


@require_GET
def world(request):
    w = m.World.objects.order_by("id").first()
    if w is None:
        return _json({"grid": 0, "turn": 0, "islands": [], "recent": [], **_site_notes()})
    islands = w.islands.select_related("owner").order_by("id")
    ai_owners = keys.ai_owner_ids(w.turn)
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
                    "ai": i.owner_id is not None and i.owner_id in ai_owners,  # AI の国の印
                    "climate": (i.ext or {}).get("climate", ""),  # フェーズ3-2
                }
                for i in islands
            ],
            # トップの画面の「最近の出来事」と、管理人の書く欄（カウンタとキリ番は AI に要らない）
            "recent": top_page.recent_events(w),
            **_site_notes(),
        }
    )


def _site_notes() -> dict[str, Any]:
    """更新履歴（管理画面）・管理人より・リンク集（server/web/top.yaml）。"""
    notes = top_page.site_notes()
    return {"updates": notes["updates"], "message": notes["message"], "links": notes["links"]}
