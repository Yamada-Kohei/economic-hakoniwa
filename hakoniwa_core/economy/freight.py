"""島の中の運賃の率。

物は売り手から拠点（首都のマスと、都市以上の町のマス）へ、拠点から買い手へ運ばれ、売り手と
買い手はそれぞれ「約定価格 × 自分の運賃の率」を島の運送業に払います。率は拠点からの道のり
（陸のマスを上下左右にたどる歩数）1 あたり game.yaml の freight.per_step、上限 freight.max_rate
です。陸でつながらないマスは上限の率です。道路と鉄道で道のりが短くなるのはフェーズ2-3 から。

運送業の人手が足りないときは、足りない割合だけ率を割り増します（economy.freight["surcharge"]、
1万分の1、最大 freight.max_surcharge 倍）。
"""

from __future__ import annotations

from collections import deque

from hakoniwa_core import facilities as fac
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.geo import neighbors4
from hakoniwa_core.state import IslandState
from hakoniwa_core.units import BASIS, to_basis

Pos = tuple[int, int]


def hubs(island: IslandState, defs: Defs, config: GameConfig) -> list[Pos]:
    """拠点のマス: 首都と、game.yaml の freight.hub_level 以上の段階の町。"""
    found = set()
    if island.capital is not None:
        found.add(island.capital)
    levels = defs.settlement_levels()
    hub_level = config.get("freight.hub_level")
    least = levels.index(hub_level) if hub_level in levels else len(levels)
    housing = fac.housing_kind(config)
    for f in island.facilities:
        if f.kind == housing and fac.on_map(f):
            level = f.ext.get("level") or levels[0]
            if levels.index(level) >= least:
                found.add((f.x, f.y))
    return sorted(found, key=lambda p: (p[1], p[0]))


def distances(island: IslandState, defs: Defs, config: GameConfig) -> dict[Pos, int]:
    """拠点から陸地の各マスまでの道のり（陸でつながらないマスは入らない）。"""
    land = {(t.x, t.y) for t in island.tiles if fac.is_land(defs, t.terrain)}
    dist: dict[Pos, int] = {}
    queue: deque[Pos] = deque()
    for pos in hubs(island, defs, config):
        dist[pos] = 0
        queue.append(pos)
    while queue:
        pos = queue.popleft()
        for n in neighbors4(island, pos):
            if n in land and n not in dist:
                dist[n] = dist[pos] + 1
                queue.append(n)
    return dist


def rates(island: IslandState, defs: Defs, config: GameConfig) -> dict[Pos, int]:
    """マスごとの運賃の率（1万分の1、割り増しを含む）。陸でつながらないマスは入らない。"""
    per_step = to_basis(config.get("freight.per_step"))
    top = to_basis(config.get("freight.max_rate"))
    surcharge = island.economy.freight.get("surcharge", BASIS)
    return {
        pos: min(top, d * per_step) * surcharge // BASIS
        for pos, d in distances(island, defs, config).items()
    }


def rate_at(table: dict[Pos, int], pos: Pos, config: GameConfig, island: IslandState) -> int:
    """マス pos の運賃の率。陸でつながらないマスは上限の率（割り増しを含む）。"""
    if pos in table:
        return table[pos]
    surcharge = island.economy.freight.get("surcharge", BASIS)
    return to_basis(config.get("freight.max_rate")) * surcharge // BASIS


def household_rate(island: IslandState, table: dict[Pos, int], config: GameConfig) -> int:
    """家計の運賃の率: 住んでいるマスの率を人口で平均した値。"""
    people = total = 0
    for tile in island.tiles:
        if tile.population > 0:
            people += tile.population
            total += tile.population * rate_at(table, (tile.x, tile.y), config, island)
    return total // people if people else 0


def facility_rates(island: IslandState, defs: Defs, config: GameConfig) -> dict[int, int]:
    """施設ごとの運賃の率。ターンの中では同じなので、初めに使うときに計算して island.scratch に置く
    （割り増しが決まる雇用の段より後で使う）。家計の率も一緒に置く（freight_household）。"""
    cached = island.scratch.get("freight_rates")
    if cached is None:
        table = rates(island, defs, config)
        cached = {
            f.id: rate_at(table, (f.x, f.y), config, island)
            for f in island.facilities
            if fac.on_map(f)
        }
        island.scratch["freight_rates"] = cached
        island.scratch["freight_household"] = household_rate(island, table, config)
    return cached
