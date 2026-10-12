"""試験用・サンプル用の島を作る。

本番の建国ではプレイヤーが形を描きます（hakoniwa_core/founding.py）。ここは自動テスト、
一括シミュレーション、開発用のサンプル世界のために、つながった陸地をランダムに作り、
定義データを渡されたときは、資源・首都・体制もランダムに選んで建国します。
"""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

from hakoniwa_core.config import GameConfig
from hakoniwa_core.state.model import IslandState, Tile, WorldState

if TYPE_CHECKING:
    from hakoniwa_core.defs import Defs


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
    rng: random.Random,
    island_id: int,
    name: str,
    wx: int,
    wy: int,
    config: GameConfig,
    defs: Defs | None = None,
    regime: dict[str, str] | None = None,
) -> IslandState:
    """ランダムな形の島。defs を渡すと、建国の処理で首都や初めの施設も置く。

    regime を渡すと、その軸だけはランダムに選ばずにその体制にする。
    """
    size = config.get("island.size")
    land = generate_land(rng, size, config.get("island.land_min"), config.get("island.land_max"))
    if defs is not None:
        return _found_random(rng, island_id, name, wx, wy, config, defs, land, regime)
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


def random_plan(
    rng: random.Random,
    name: str,
    config: GameConfig,
    defs: Defs,
    land: set[tuple[int, int]],
    fixed: dict[str, str] | None = None,
):
    """ランダムに選んだ建国の中身。fixed に書いた体制の軸は、その選択にする。"""
    from hakoniwa_core import founding

    seed = rng.randrange(2**31)
    regime = {axis: rng.choice(choices) for axis, choices in defs.regime_axes().items()}
    regime.update(fixed or {})
    terrain = founding.auto_terrain(land, config, seed)
    kind = founding.start_of(defs, regime)["capital"]["facility"]
    capital = founding.suggest_capital(terrain, land, defs, config, kind)
    if capital is None:  # 首都を置ける地形がなければ、陸地の真ん中を描き直して置く
        capital = min(land, key=lambda p: (abs(p[0] - config.get("island.size") // 2), p))
        edits = {capital: defs.facilities[kind]["terrain"][0]}
    else:
        edits = {}
    resources = defs.mining_resources()
    count = min(len(resources), config.get("founding.max_resources"))
    return founding.FoundingPlan(
        land=land,
        capital=capital,
        regime=regime,
        names={
            "country": name,
            "capital": f"{name}市",
            "currency": f"{name}通貨",
            "currency_symbol": "¤",
            "leader": f"{name}の元首",
        },
        resources=sorted(rng.sample(resources, count)),
        terrain_edits=edits,
        seed=seed,
    )


def _found_random(rng, island_id, name, wx, wy, config, defs, land, regime) -> IslandState:
    from hakoniwa_core.founding import found_island

    plan = random_plan(rng, name, config, defs, land, regime)
    return found_island(defs, config, plan, island_id, wx, wy)


def world_grid_size(n_islands: int, config: GameConfig) -> int:
    """世界マップの1辺の区画数。区画数は島数のおよそ cells_per_island 倍（50島なら 10×10）。"""
    return max(1, math.ceil(math.sqrt(n_islands * config.get("world.cells_per_island"))))


def generate_world(
    seed: int, n_islands: int, config: GameConfig, defs: Defs | None = None
) -> WorldState:
    """n_islands 個の島の世界。defs を渡すと、どの島も建国の処理を通して作る。

    経済体制は、定義した順に島へ順番に割り当てる（市場・混合・計画経済の島が同じくらいの数になる。
    ほかの軸と鉱業資源はランダム）。
    """
    rng = random.Random(seed)
    grid = world_grid_size(n_islands, config)
    cells = rng.sample([(x, y) for y in range(grid) for x in range(grid)], n_islands)
    economies = defs.regime_axes().get("economy", []) if defs is not None else []
    islands = {}
    for i, (wx, wy) in enumerate(cells, start=1):
        regime = {"economy": economies[(i - 1) % len(economies)]} if economies else None
        islands[i] = generate_island(rng, i, f"島{i}", wx, wy, config, defs, regime)
    return WorldState(turn=0, seed=seed, islands=islands)
