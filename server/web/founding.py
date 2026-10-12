"""建国の画面と、その JSON API。

島の形・地形・資源・首都・体制・名前を画面で選び、最後にまとめて送ります。
地形は「描いた形」と「種」だけで決まるので、画面で見せた地形がそのまま建国後の地形になります。
"""

from __future__ import annotations

import json
import random
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from hakoniwa_core.founding import (
    FoundingError,
    FoundingPlan,
    auto_terrain,
    edit_problems,
    shape_problems,
    start_of,
    suggest_capital,
)
from hakoniwa_core.state.generate import generate_land
from server.game import get_game
from server.web import display
from server.world import models as m
from server.world.services import found_for_user, free_cells, get_or_create_world

JSON = {"json_dumps_params": {"ensure_ascii": False}}


@login_required
def page(request):
    own = m.Island.objects.filter(owner=request.user).first()
    if own is not None:
        return redirect("island", island_id=own.id)
    seed = random.SystemRandom().randrange(2**31)
    return render(request, "web/found.html", {"seed": seed})


def _body(request) -> dict[str, Any]:
    data = json.loads(request.body or b"{}")
    if not isinstance(data, dict):
        raise ValueError
    return data


def _land(data: dict[str, Any]) -> set[tuple[int, int]]:
    return {(int(x), int(y)) for x, y in data.get("land", [])}


def _texts(problems) -> list[str]:
    return [display.message(reason, **args) for reason, args in problems]


@login_required
@require_POST
def random_shape(request):
    """ランダムな形の陸地（描き始めの手がかり）。"""
    try:
        seed = int(_body(request).get("seed", 0))
    except (ValueError, TypeError):
        return HttpResponseBadRequest("JSON で送ってください")
    config = get_game().config
    rng = random.Random(f"shape:{seed}")
    land = generate_land(
        rng, config.get("island.size"), config.get("island.land_min"), config.get("island.land_max")
    )
    return JsonResponse({"land": sorted([x, y] for x, y in land)})


@login_required
@require_POST
def terrain(request):
    """描いた形から自動で配った地形と、形の問題、首都の候補。"""
    try:
        data = _body(request)
        land = _land(data)
        seed = int(data.get("seed", 0))
        edits = {(int(x), int(y)): str(t) for x, y, t in data.get("edits", [])}
    except (ValueError, TypeError):
        return HttpResponseBadRequest("JSON で送ってください")
    game = get_game()
    size = game.config.get("island.size")
    land = {(x, y) for x, y in land if 0 <= x < size and 0 <= y < size}
    tiles = auto_terrain(land, game.config, seed)
    problems = shape_problems(land, game.config) + edit_problems(
        land, edits, game.defs, game.config
    )
    for pos, t in edits.items():
        if pos in land and t in game.defs.terrain:
            tiles[pos] = t
    regime = data.get("regime") or {}
    kind = start_of(game.defs, regime)["capital"]["facility"]
    capital = suggest_capital(tiles, land, game.defs, game.config, kind) if land else None
    return JsonResponse(
        {
            "terrain": [tiles[(x, y)] for y in range(size) for x in range(size)],
            "land_count": len(land),
            "problems": _texts(problems),
            "suggested_capital": list(capital) if capital else None,
            "capital_terrain": list(game.defs.facilities[kind]["terrain"]),
        },
        **JSON,
    )


@login_required
@require_POST
def found(request):
    """建国する。問題があれば 400 と問題の文の一覧。"""
    try:
        data = _body(request)
        plan = FoundingPlan(
            land=_land(data),
            capital=(int(data["capital"][0]), int(data["capital"][1])),
            regime={str(k): str(v) for k, v in (data.get("regime") or {}).items()},
            names={str(k): str(v) for k, v in (data.get("names") or {}).items()},
            resources=[str(r) for r in data.get("resources", [])],
            terrain_edits={(int(x), int(y)): str(t) for x, y, t in data.get("edits", [])},
            seed=int(data.get("seed", 0)),
        )
        wx, wy = int(data["cell"][0]), int(data["cell"][1])
    except (ValueError, TypeError, KeyError, IndexError):
        return JsonResponse(
            {"problems": ["送られた内容の形が正しくありません"]}, status=400, **JSON
        )
    world = get_or_create_world()
    try:
        island = found_for_user(world, request.user, plan, wx, wy)
    except FoundingError as e:
        return JsonResponse({"problems": _texts(e.problems)}, status=400, **JSON)
    return JsonResponse({"url": reverse("island", args=[island.id])})


def world_cells(request):
    """建国で選べる区画（空いている区画）。"""
    world = get_or_create_world()
    return JsonResponse({"free": [list(c) for c in free_cells(world)]})
