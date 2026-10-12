"""消費と人口の段と、都市の発展（住宅の自動建設）の段。

- 住民は配分の段で受け取った食料を食べ、足りていて住宅に空きがあれば少しずつ増える。
  足りなければ減る。村の住民のうち仕事のない人は、自給の農業で自分の食べる分をまかなう。
- 幸福度は食料・働き口・電力・食事の質・汚染から計算する（式 happiness）。
- 人は仕事のないマスから、人手の足りない施設の通勤圏にある住宅へ少しずつ移る。
- 人手の足りない施設の通勤圏で住宅が足りなければ、住宅を上の段階へ育てるか、空いた平地に
  新しい住宅を建てる。開発を止めたマスには建てない。
"""

from __future__ import annotations

import random
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.geo import within
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import ZONE_NONE, Facility, IslandState, LogEntry, Tile
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import to_milli

Pos = tuple[int, int]


def _rand_round(value: float, rng: random.Random) -> int:
    """小数の部分を確率で切り上げる（小さなマスでも少しずつ増減するように）。"""
    whole = int(value)
    return whole + (1 if rng.random() < value - whole else 0)


def subsistence_persons(defs: Defs, tile: Tile) -> int:
    """自給の農業で食べている人の数（村の、仕事のない働き手とその家族）。"""
    level = fac.settlement_of(defs, tile.population)
    share = fac.labor_share(defs)
    if not defs.settlements[level].get("subsistence") or share <= 0:
        return 0
    unemployed = fac.labor_force(defs, tile) - tile.ext.get("employed", 0)
    return min(tile.population, int(max(0, unemployed) / share))


def food_need(island: IslandState, ctx: StageContext) -> int:
    """倉庫から配る食料の量（1000 分の 1 単位）。自給の農業で食べている人の分は除く。"""
    per_person = to_milli(ctx.config.get("population.food_per_person"))
    persons = sum(
        t.population - subsistence_persons(ctx.defs, t) for t in island.tiles if t.population
    )
    return persons * per_person


def happiness(island: IslandState, tile: Tile, facts: dict[str, Any], ctx: StageContext) -> int:
    """マスの幸福度（0〜100）。facts: food・jobs・power（1000 分の 1）と quality（0〜1）。"""
    w = ctx.config.get("population.happiness")
    value = (
        w["base"]
        + w["food"] * facts["food"] / fac.FULL
        + w["jobs"] * facts["jobs"] / fac.FULL
        + w["power"] * facts["power"] / fac.FULL
        + w["food_quality"] * facts["quality"]
        - w["pollution_per_point"] * tile.pollution
    )
    return max(0, min(100, round(value)))


def _idle(defs: Defs, tile: Tile) -> int:
    return max(0, fac.labor_force(defs, tile) - tile.ext.get("employed", 0))


def _homes(island: IslandState, config: GameConfig) -> dict[Pos, Facility]:
    kind = fac.housing_kind(config)
    return {(f.x, f.y): f for f in island.facilities if f.kind == kind}


def _vacancies(defs: Defs, island: IslandState, turn: int) -> list[tuple[Facility, int]]:
    """人手の足りない施設（工事中も含む）と、足りない人数。"""
    found = []
    for f in island.facilities:
        if defs.facilities[f.kind]["workers"] > 0:
            missing = fac.labor_demand(defs, f, turn) - f.workers
            if missing > 0:
                found.append((f, missing))
    return found


class Population(IslandStage):
    name = "population"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs, config = ctx.defs, ctx.config
        rng = ctx.rng(island.id)
        food = island.scratch.get("food") or {"ratio": fac.FULL, "quality": 0.0}
        homes = _homes(island, config)
        before = {(t.x, t.y): fac.settlement_of(defs, t.population) for t in island.tiles}
        p = config.get("population")
        for tile in island.tiles:
            if tile.population <= 0:
                tile.ext.pop("happiness", None)
                continue
            labor = fac.labor_force(defs, tile)
            working = tile.ext.get("employed", 0)
            if subsistence_persons(defs, tile):
                working = labor  # 村で自給の農業をしている人は、仕事がないとは数えない
            facts = {
                "food": food["ratio"],
                "jobs": fac.ratio(working, labor) if labor else fac.FULL,
                "power": tile.ext.get("power", 0),
                "quality": food["quality"],
            }
            mood = ctx.formula("happiness")(island, tile, facts, ctx)
            tile.ext["happiness"] = mood
            home = homes.get((tile.x, tile.y))
            capacity = fac.housing_capacity(defs, home) if home else 0
            pop = tile.population
            if food["ratio"] >= fac.FULL:
                # 仕事のある人の割合が growth_min_jobs を下回るマスでは増えない
                low = p["growth_min_jobs"]
                jobs = max(0.0, min(1.0, (facts["jobs"] / fac.FULL - low) / (1 - low)))
                births = _rand_round(pop * p["growth_rate"] * mood / 100 * jobs, rng)
                pop += min(births, max(0, capacity - pop))
            else:
                hunger = (fac.FULL - food["ratio"]) / fac.FULL
                pop -= _rand_round(pop * p["starvation_rate"] * hunger, rng)
            if mood < p["emigration_happiness"]:
                pop -= _rand_round(pop * p["emigration_rate"], rng)
            tile.population = max(0, pop)
        self._migrate(island, ctx, rng, homes)
        for tile in island.tiles:
            level = fac.settlement_of(defs, tile.population)
            if (tile.x, tile.y) in homes and level != before[(tile.x, tile.y)]:
                levels = defs.settlement_levels()
                if levels.index(level) > levels.index(before[(tile.x, tile.y)]):
                    island.log.append(
                        LogEntry("settlement_grew", {"x": tile.x, "y": tile.y, "level": level})
                    )

    def _migrate(
        self,
        island: IslandState,
        ctx: StageContext,
        rng: random.Random,
        homes: dict[Pos, Facility],
    ) -> None:
        """住む所のない人と仕事のない人を、人手の足りない施設の近くの空いた住宅へ移す。"""
        defs, config = ctx.defs, ctx.config
        share = fac.labor_share(defs)
        if share <= 0:
            return
        free = {
            pos: fac.housing_capacity(defs, f) - island.tile(*pos).population
            for pos, f in homes.items()
        }
        pull: dict[Pos, int] = {}
        needed = 0
        for f, missing in _vacancies(defs, island, ctx.turn):
            persons = int(missing / share)
            needed += persons
            for pos in within(island, (f.x, f.y), fac.commute_radius(defs, config, f.kind)):
                if pos in homes:
                    pull[pos] = pull.get(pos, 0) + persons
        rate = config.get("population.migration_rate")
        movers: list[tuple[Pos, int, bool]] = []  # (元のマス, 人数, 住む所がないか)
        for tile in island.tiles:
            pos = (tile.x, tile.y)
            if tile.population <= 0:
                continue
            room = free.get(pos, -tile.population)
            if room < 0:
                movers.append((pos, -room, True))
                tile.population += room
                free[pos] = 0
            elif pull.get(pos, 0) == 0:
                idle = fac.labor_force(defs, tile) - tile.ext.get("employed", 0)
                count = min(tile.population, _rand_round(idle / share * rate, rng))
                if count > 0:
                    movers.append((pos, count, False))
                    tile.population -= count
        targets = sorted(
            (pos for pos in homes if free[pos] > 0),
            key=lambda pos: (
                -pull.get(pos, 0),
                -island.tile(*pos).ext.get("happiness", 0),
                pos[1],
                pos[0],
            ),
        )
        left_island = 0
        for source, count, homeless in movers:
            for pos in targets:
                if count <= 0:
                    break
                if pos == source or free[pos] <= 0:
                    continue
                space = free[pos]
                if not homeless:
                    space = min(space, pull.get(pos, 0), needed)
                move = min(count, space)
                if move <= 0:
                    continue
                island.tile(*pos).population += move
                free[pos] -= move
                count -= move
                if not homeless:
                    pull[pos] -= move
                    needed -= move
            if count > 0:
                if homeless:
                    left_island += count
                else:
                    island.tile(*source).population += count  # 移る先がなければ元のマスに残る
        if left_island:
            island.log.append(LogEntry("residents_left", {"count": left_island}))


class Housing(IslandStage):
    """都市の発展: 人手の足りない施設の通勤圏で住宅が足りなければ、住宅を育てるか新しく建てる。"""

    name = "housing"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs, config = ctx.defs, ctx.config
        h = config.get("housing")
        share = fac.labor_share(defs)
        homes = _homes(island, config)
        kind = fac.housing_kind(config)
        levels = defs.settlement_levels()
        built = upgraded = 0
        vacancies = sorted(_vacancies(defs, island, ctx.turn), key=lambda v: (-v[1], v[0].id))
        for f, missing in vacancies:
            if built >= h["max_new_per_turn"] and upgraded >= h["max_upgrades_per_turn"]:
                break
            area = within(island, (f.x, f.y), fac.commute_radius(defs, config, f.kind))
            near = [pos for pos in area if pos in homes]
            # 空いている住宅に入れる人と、もう近くに住んでいて仕事のない人で足りれば育てない
            room = sum(
                fac.housing_capacity(defs, homes[pos]) - island.tile(*pos).population
                for pos in near
            )
            idle = sum(_idle(defs, island.tile(*pos)) for pos in near)
            if share <= 0 or room * share + idle >= missing:
                continue
            if upgraded < h["max_upgrades_per_turn"]:
                pos = self._upgradable(island, defs, homes, near, levels, h["fill_ratio"])
                if pos is not None:
                    home = homes[pos]
                    home.ext["level"] = levels[levels.index(home.ext.get("level", levels[0])) + 1]
                    upgraded += 1
                    continue
            if built < h["max_new_per_turn"]:
                pos = self._empty_site(island, defs, kind, area)
                if pos is not None:
                    homes[pos] = fac.new_housing(island, kind, pos, levels[0])
                    built += 1

    @staticmethod
    def _upgradable(
        island: IslandState,
        defs: Defs,
        homes: dict[Pos, Facility],
        near: list[Pos],
        levels: list[str],
        fill_ratio: float,
    ) -> Pos | None:
        for pos in near:
            home = homes[pos]
            level = home.ext.get("level", levels[0])
            if levels.index(level) + 1 >= len(levels):
                continue
            if island.tile(*pos).population >= fac.housing_capacity(defs, home) * fill_ratio:
                return pos
        return None

    @staticmethod
    def _empty_site(island: IslandState, defs: Defs, kind: str, area: list[Pos]) -> Pos | None:
        terrains = defs.facilities[kind]["terrain"]
        occupied = island.facilities_by_tile()
        for pos in area:
            tile = island.tile(*pos)
            if tile.terrain in terrains and tile.zone == ZONE_NONE and pos not in occupied:
                return pos
        return None


def check_population_defs(defs: Defs, config: GameConfig) -> list[str]:
    problems = []
    kind = config.get("housing.facility")
    if kind not in defs.facilities:
        problems.append(f"game.yaml の housing.facility にある施設 {kind} が定義されていません")
    if "general" not in defs.workers:
        problems.append("workers.yaml に general（一般の労働者）がありません")
    if not defs.settlements:
        problems.append("settlements.yaml に住宅地の段階がありません")
    return problems


def register(registry: Registry) -> None:
    registry.add_stage(Population())
    registry.add_stage(Housing())
    registry.add_formula("food_need", food_need)
    registry.add_formula("happiness", happiness)
    registry.add_check(check_population_defs)
