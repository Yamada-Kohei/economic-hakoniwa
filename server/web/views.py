"""画面（テンプレート）。島の地図と命令の入力は JavaScript が JSON API（api.py）を使って動かす。"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from hakoniwa_core import facilities as fac
from server.game import get_game
from server.turns.models import TurnLog
from server.web import display
from server.world.models import Island, World
from server.world.services import grid_size

GUIDE_TURNS = 12  # 建国からこのターン数の間、島の画面に「はじめの手順」を出す


def top(request):
    world = World.objects.order_by("id").first()
    islands = world.islands.select_related("owner").order_by("id") if world else []
    own = None
    if request.user.is_authenticated:
        own = Island.objects.filter(owner=request.user).first()
    return render(request, "web/top.html", {"world": world, "islands": islands, "own": own})


def island_page(request, island_id: int):
    island = get_object_or_404(Island.objects.select_related("owner", "world"), pk=island_id)
    defs = get_game().defs
    turn, stats = display.latest_stats(island)
    currency = (island.names or {}).get("currency_symbol", "")
    founded = (island.ext or {}).get("founded_turn", 0)
    logs = TurnLog.objects.filter(island=island).order_by("-turn", "id")
    last_turn = logs.values_list("turn", flat=True).first()
    regime = [
        {
            "axis": display.message(f"axis_{defs.regimes[r]['axis']}"),
            "name": defs.name("regimes", r),
        }
        for r in (island.regime or {}).values()
        if r in defs.regimes
    ]
    context = {
        "island": island,
        "mine": request.user.is_authenticated and island.owner_id == request.user.id,
        "stats_turn": turn,
        "summary": display.summary(stats, currency),
        "goods": display.goods_table(stats, display.latest_markets(island, turn), currency),
        "regime": regime,
        "resources": [defs.name("goods", g) for g in island.resources or []],
        # 建設の欄で、この島で使えない施設や生産方式を見分けるため
        "resource_ids": island.resources or [],
        "producible": sorted(fac.producible_goods(defs, island.resources or [])),
        "capital_name": (island.names or {}).get("capital", ""),
        "logs": display.log_entries(logs.filter(turn=last_turn)) if last_turn is not None else [],
        "log_turn": last_turn,
        "show_guide": island.world.turn - founded < GUIDE_TURNS,
    }
    return render(request, "web/island.html", context)


def turn_log(request, island_id: int):
    """ターンログ。ターンごとに区切り、新しいターンから並べる。"""
    island = get_object_or_404(Island, pk=island_id)
    turns = (
        TurnLog.objects.filter(island=island)
        .order_by("-turn")
        .values_list("turn", flat=True)
        .distinct()
    )
    page = Paginator(list(turns), 10).get_page(request.GET.get("page"))
    entries = display.log_entries(
        TurnLog.objects.filter(island=island, turn__in=list(page)).order_by("-turn", "id")
    )
    groups: dict[int, list] = {}
    for entry in entries:
        groups.setdefault(entry["turn"], []).append(entry)
    return render(
        request,
        "web/log.html",
        {"island": island, "page": page, "groups": sorted(groups.items(), reverse=True)},
    )


def world_map(request):
    """世界マップ: 区画を並べ、島のある区画にはその島へのリンクを置く。"""
    world = World.objects.order_by("id").first()
    rows: list[list[Island | None]] = []
    if world is not None:
        islands = list(world.islands.select_related("owner"))
        grid = max([grid_size(world)] + [max(i.wx, i.wy) + 1 for i in islands])
        at = {(i.wx, i.wy): i for i in islands}
        rows = [[at.get((x, y)) for x in range(grid)] for y in range(grid)]
    own_id = None
    if request.user.is_authenticated:
        own_id = Island.objects.filter(owner=request.user).values_list("id", flat=True).first()
    return render(request, "web/world.html", {"world": world, "rows": rows, "own_id": own_id})


def help_page(request):
    game = get_game()
    defs = game.defs
    facilities = [
        {
            "name": defs.name("facilities", fid),
            "terrain": "・".join(defs.name("terrain", t) for t in f["terrain"]),
            "notes": _placement_notes(f),
            "workers": f["workers"],
            "recipes": [
                {
                    "name": defs.recipe_name(fid, rid),
                    "inputs": display.goods_list(r["inputs"]),
                    "outputs": display.goods_list(r["outputs"]),
                    "resource": defs.name("goods", r["requires_resource"])
                    if r.get("requires_resource")
                    else "",
                }
                for rid, r in f["recipes"].items()
            ],
        }
        for fid, f in defs.facilities.items()
        if f.get("buildable", True)
    ]
    orders = [
        {"name": defs.name("orders", oid), "cost": o["admin_cost"]}
        for oid, o in defs.orders.items()
    ]
    return render(
        request,
        "web/help.html",
        {
            "facilities": facilities,
            "orders": orders,
            "commute": game.config.get("labor.commute_radius"),
            "base_admin": game.config.get("orders.base_admin_power"),
            "max_admin": game.config.get("orders.max_admin_power"),
            "admin_per_office": _admin_people(defs),
            "settlements": [
                {
                    "name": defs.name("settlements", sid),
                    "capacity": s["capacity"],
                    "materials": display.goods_list(s.get("materials", {})),
                }
                for sid, s in ((sid, defs.settlements[sid]) for sid in defs.settlement_levels())
            ],
            "units": [
                {"name": defs.name("goods", g), "unit": defs.unit_name(g)}
                for g in defs.goods
                if defs.unit_name(g)
            ],
        },
    )


def _admin_people(defs) -> int | None:
    """行政力 1 に要る公務員の数（admin_power を持つ施設の係数から）。"""
    rates = [f["admin_power"] for f in defs.facilities.values() if f.get("admin_power")]
    return round(1 / max(rates)) if rates else None


def _placement_notes(f) -> str:
    notes = []
    if f.get("coastal"):
        notes.append("海に面したマスのみ")
    if f.get("near_capital") is not None:
        notes.append(f"首都から {f['near_capital']} マス以内")
    if f.get("max_per_island") is not None:
        notes.append(f"1国 {f['max_per_island']} か所まで")
    return "・".join(notes)
