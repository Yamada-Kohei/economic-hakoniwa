"""画面と JSON API。"""

from __future__ import annotations

from functools import cache

import yaml
from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from server.game import get_game
from server.world.models import Island, Tile, World


def top(request):
    world = World.objects.order_by("id").first()
    islands = world.islands.select_related("owner").order_by("id") if world else []
    return render(request, "web/top.html", {"world": world, "islands": islands})


def island_page(request, island_id: int):
    island = get_object_or_404(Island, pk=island_id)
    return render(request, "web/island.html", {"island": island})


@require_GET
def api_island_map(request, island_id: int):
    """島のマスの一覧。terrain と population は y * size + x の順に並べる。"""
    island = get_object_or_404(Island, pk=island_id)
    rows = island.tiles.order_by("y", "x").values_list("terrain", "population")
    terrain, population = [], []
    for t, p in rows:
        terrain.append(t)
        population.append(p)
    return JsonResponse(
        {
            "id": island.id,
            "name": island.name,
            "size": island.size,
            "turn": island.world.turn,
            "terrain": terrain,
            "population": population,
        }
    )


@require_GET
def api_tile(request, island_id: int, x: int, y: int):
    try:
        tile = Tile.objects.get(island_id=island_id, x=x, y=y)
    except Tile.DoesNotExist as e:
        raise Http404 from e
    defs = get_game().defs
    return JsonResponse(
        {
            "x": tile.x,
            "y": tile.y,
            "terrain": tile.terrain,
            "terrain_name": defs.name("terrain", tile.terrain),
            "population": tile.population,
        }
    )


@require_GET
def api_defs(request):
    """画面が使う定義データと表示名、テーマ。"""
    game = get_game()
    defs = game.defs
    return JsonResponse(
        {
            "terrain": {
                tid: {"name": defs.name("terrain", tid), "height": t.get("height", 0)}
                for tid, t in defs.terrain.items()
            },
            "facilities": {fid: {"name": defs.name("facilities", fid)} for fid in defs.facilities},
            "goods": {gid: {"name": defs.name("goods", gid)} for gid in defs.goods},
            "theme": _theme(game.config.raw.get("theme", "default")),
        },
        json_dumps_params={"ensure_ascii": False},
    )


@cache
def _theme(name: str) -> dict:
    path = settings.BASE_DIR / "server" / "static" / "themes" / name / "theme.yaml"
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)
