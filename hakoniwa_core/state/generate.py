"""試験用・サンプル用の島の地形を作る。

本番の建国ではプレイヤーが形を描きます（フェーズ1）。ここは自動テスト、一括シミュレーション、
開発用のサンプル世界のための、つながった陸地をランダムに作る簡単な方法です。
"""

from __future__ import annotations

import math
import random

from hakoniwa_core.config import GameConfig
from hakoniwa_core.state.model import IslandState, Tile, WorldState


def generate_land(
    rng: random.Random, size: int, land_min: int, land_max: int
) -> set[tuple[int, int]]:
    """中央から広げた、つながった陸地のマスの集まり。縁の1マスは必ず海にする。"""
    target = rng.randint(land_min, land_max)
    center = (size // 2, size // 2)
    land = {center}
    frontier = [center]
    while len(land) < target and frontier:
        x, y = frontier[rng.randrange(len(frontier))]
        dx, dy = rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
        nx, ny = x + dx, y + dy
        if 1 <= nx < size - 1 and 1 <= ny < size - 1 and (nx, ny) not in land:
            land.add((nx, ny))
            frontier.append((nx, ny))
    return land


def generate_island(
    rng: random.Random, island_id: int, name: str, wx: int, wy: int, config: GameConfig
) -> IslandState:
    size = config.get("island.size")
    land = generate_land(rng, size, config.get("island.land_min"), config.get("island.land_max"))
    tiles = []
    for y in range(size):
        for x in range(size):
            if (x, y) in land:
                terrain = rng.choices(["plain", "forest", "mountain"], weights=[6, 3, 1])[0]
            elif any((x + dx, y + dy) in land for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                terrain = "shallows"
            else:
                terrain = "sea"
            tiles.append(Tile(x=x, y=y, terrain=terrain))
    return IslandState(id=island_id, name=name, wx=wx, wy=wy, size=size, tiles=tiles)


def world_grid_size(n_islands: int, config: GameConfig) -> int:
    """世界マップの1辺の区画数。区画数は島数のおよそ cells_per_island 倍（50島なら 10×10）。"""
    return max(1, math.ceil(math.sqrt(n_islands * config.get("world.cells_per_island"))))


def generate_world(seed: int, n_islands: int, config: GameConfig) -> WorldState:
    rng = random.Random(seed)
    grid = world_grid_size(n_islands, config)
    cells = rng.sample([(x, y) for y in range(grid) for x in range(grid)], n_islands)
    islands = {}
    for i, (wx, wy) in enumerate(cells, start=1):
        islands[i] = generate_island(rng, i, f"島{i}", wx, wy, config)
    return WorldState(turn=0, seed=seed, islands=islands)
