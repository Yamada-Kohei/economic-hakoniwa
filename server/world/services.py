"""世界と島を作る。"""

from __future__ import annotations

import random

from django.db import transaction

from hakoniwa_core.state.generate import generate_island, world_grid_size
from server.game import get_game
from server.world import models as m
from server.world.repository import create_island


def get_or_create_world(seed: int | None = None) -> m.World:
    world = m.World.objects.order_by("id").first()
    if world is None:
        world = m.World.objects.create(
            seed=seed if seed is not None else random.SystemRandom().randrange(2**62),
            packs=get_game().defs.pack_versions(),
        )
    return world


def free_cells(world: m.World, n_islands: int) -> list[tuple[int, int]]:
    grid = world_grid_size(n_islands, get_game().config)
    used = set(world.islands.values_list("wx", "wy"))
    return [(x, y) for y in range(grid) for x in range(grid) if (x, y) not in used]


@transaction.atomic
def create_sample_island(world: m.World, name: str, rng: random.Random, owner=None) -> m.Island:
    """空いている区画に、ランダムな形の島を作る（開発・試験用）。"""
    config = get_game().config
    cells = free_cells(world, max(config.get("world.default_islands"), world.islands.count() + 1))
    if not cells:
        raise ValueError("世界マップに空いている区画がありません")
    wx, wy = rng.choice(cells)
    island = generate_island(rng, 0, name, wx, wy, config)
    return create_island(world, island, owner=owner)
