"""消費と人口の段と、都市の発展（町の自動建設と成長）の段。

- 家計の層ごとに、市場の段で買えた食料・消費財と、届いた電力から幸福度を出す（式 happiness）。
  一般の層はマスごと（働き口・電力・汚染はマスの値）、ほかの層は島全体で出し、島の幸福度は
  人数で平均する。資産を持つ層の働き口はいつも満点。
- 住民は一般の層が食べられた割合で、足りていて町に空きがあれば少しずつ増え、足りなければ減る。
  村の住民のうち仕事のない人は、自給の農業で自分の食べる分をまかなう（市場で買わない）。
- 人は仕事のないマスから、人手の足りない施設の通勤圏にある町へ少しずつ移る。
- 最後に、住民の数から家計の層の人数を決め直す（層を移った人の預金も一緒に移す）。
- 人手の足りない施設の通勤圏で住む所が足りなければ、町を上の段階へ育てるか、空いた平地に
  新しい町（村）を作る。どちらも資材（settlements.yaml の materials）が要り、一般の層が
  市場で買う（買った資材は economy.housing["stock"] に入る）。足りなければ、要る資材を
  economy.housing["want"] に書いて、次のターンの市場で買う。開発を止めたマスには作らない。
"""

from __future__ import annotations

import random
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import GENERAL
from hakoniwa_core.economy.households import move_people, split_persons
from hakoniwa_core.economy.stats import inflation, real_wage_change
from hakoniwa_core.geo import within
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import ZONE_NONE, Facility, IslandState, LogEntry, Tile
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import QUANTITY_SCALE, to_milli

Pos = tuple[int, int]


def _rand_round(value: float, rng: random.Random) -> int:
    """小数の部分を確率で切り上げる（小さなマスでも少しずつ増減するように）。"""
    whole = int(value)
    return whole + (1 if rng.random() < value - whole else 0)


def subsistence_persons(defs: Defs, island: IslandState, tile: Tile) -> int:
    """自給の農業で食べている人の数（村の、仕事のない働き手とその家族）。"""
    level = fac.settlement_of(defs, tile.population)
    share = fac.labor_share(defs, island)
    if not defs.settlements[level].get("subsistence") or share <= 0:
        return 0
    unemployed = fac.labor_force(defs, island, tile) - tile.ext.get("employed", 0)
    return min(tile.population, int(max(0, unemployed) / share))


def food_need(island: IslandState, ctx: StageContext) -> int:
    """家計が市場で買う食料の量（栄養、1000 分の 1 単位）。自給の農業で食べている人の分は除く。"""
    persons = sum(
        t.population - subsistence_persons(ctx.defs, island, t)
        for t in island.tiles
        if t.population
    )
    # 1人あたりの量は 1000 分の 1 単位より細かいことがあるので、人数を掛けてから丸める
    return to_milli(persons * ctx.config.get("population.food_per_person"))


def layer_food_needs(island: IslandState, ctx: StageContext) -> dict[str, int]:
    """層ごとの食料の要る量。自給の農業で食べている人は一般の層なので、一般の層の分から除く。"""
    per_person = ctx.config.get("population.food_per_person")
    total = ctx.formula("food_need")(island, ctx)
    needs = {}
    others = 0
    for layer, h in sorted(island.economy.households.items()):
        if layer != GENERAL:
            needs[layer] = to_milli(h["persons"] * per_person)
            others += needs[layer]
    needs[GENERAL] = max(0, total - others)
    return needs


def happiness_weights(island: IslandState, ctx: StageContext) -> dict[str, float]:
    """幸福度の重み。移し替えたフェーズ1の島は、population.blend_turns ターンかけて
    population.happiness_phase1 の重みから移す。"""
    weights = dict(ctx.config.get("population.happiness"))
    left = island.economy.happiness_blend
    turns = ctx.config.get("population.blend_turns")
    if left > 0 and turns > 0:
        old = ctx.config.get("population.happiness_phase1")
        t = left / turns
        for key, value in old.items():
            weights[key] = weights[key] * (1 - t) + value * t
    return weights


def happiness(island: IslandState, facts: dict[str, Any], ctx: StageContext) -> int:
    """幸福度（0〜100）。

    facts: food・jobs・power・consumer_goods（1000 分の 1）、quality（0〜1）、pollution（汚染）。
    実質賃金が1年前より下がった分と、物価上昇率が目安を超えた分を減点する。
    """
    w = happiness_weights(island, ctx)
    value = (
        w["base"]
        + w["food"] * facts["food"] / fac.FULL
        + w["jobs"] * facts["jobs"] / fac.FULL
        + w["power"] * facts["power"] / fac.FULL
        + w["food_quality"] * facts["quality"]
        + w["consumer_goods"] * facts["consumer_goods"] / fac.FULL
        - w["pollution_per_point"] * facts["pollution"]
    )
    fall = -real_wage_change(island.economy) * 100
    if fall > 0:
        value -= min(w["real_wage_max"], w["real_wage_per_point"] * fall)
    over = (inflation(island.economy) - w["inflation_threshold"]) * 100
    if over > 0:
        value -= min(w["inflation_max"], w["inflation_per_point"] * over)
    return max(0, min(100, round(value)))


def _idle(defs: Defs, island: IslandState, tile: Tile) -> int:
    return max(0, fac.labor_force(defs, island, tile) - tile.ext.get("employed", 0))


def _homes(island: IslandState, config: GameConfig) -> dict[Pos, Facility]:
    kind = fac.housing_kind(config)
    return {(f.x, f.y): f for f in island.facilities if f.kind == kind}


def _vacancies(defs: Defs, island: IslandState, turn: int) -> list[tuple[Facility, int]]:
    """人手の足りない施設（工事中も含む。マスを持たない施設は除く）と、足りない人数。

    雇いたい人数は、雇用の段で払える賃金の分に抑えた人数（economy["wanted"]）。
    """
    found = []
    for f in island.facilities:
        if not fac.on_map(f) or (f.built and fac.workers_needed(defs, f.kind) <= 0):
            continue
        want = f.economy.get("wanted", fac.labor_demand(defs, f, turn))
        missing = want - f.workers
        if missing > 0:
            found.append((f, missing))
    return found


def _layer_facts(island: IslandState, ctx: StageContext) -> dict[str, dict[str, Any]]:
    """層ごとの、食べられた割合・食事の質・消費財を買えた割合（1000 分の 1）。"""
    defs, config = ctx.defs, ctx.config
    bought = island.scratch.get("household_goods", {})
    needs = layer_food_needs(island, ctx)
    target = config.get("household.consumer_goods_target")
    power = config.get("power.good")
    facts = {}
    for layer, h in sorted(island.economy.households.items()):
        goods = bought.get(layer, {})
        eaten = 0
        quality = 0.0
        consumer = 0
        for good, qty in sorted(goods.items()):
            spec = defs.goods[good]
            if "food" in spec:
                amount = qty * to_milli(spec["food"]["nutrition"]) // QUANTITY_SCALE
                eaten += amount
                quality += amount * spec["food"].get("quality", 0)
            elif spec.get("consumer") and good != power:
                consumer += qty
        need = needs.get(layer, 0)
        facts[layer] = {
            "food": fac.ratio(eaten, need),
            "quality": quality / eaten if eaten else 0.0,
            "consumer_goods": fac.ratio(consumer, to_milli(h["persons"] * target)),
            "eaten": eaten,
            "need": need,
        }
    return facts


class Population(IslandStage):
    name = "population"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs, config = ctx.defs, ctx.config
        rng = ctx.rng(island.id)
        layers = _layer_facts(island, ctx)
        general = layers.get(GENERAL) or {"food": fac.FULL, "quality": 0.0, "consumer_goods": 0}
        island.scratch["food"] = {
            "need": sum(f["need"] for f in layers.values()),
            "supplied": sum(f["eaten"] for f in layers.values()),
            "ratio": general["food"],
        }
        homes = _homes(island, config)
        before = {(t.x, t.y): fac.settlement_of(defs, t.population) for t in island.tiles}
        p = config.get("population")
        moods: dict[str, list[float]] = {}
        power_sum = pollution_sum = people = 0
        for tile in island.tiles:
            if tile.population <= 0:
                tile.ext.pop("happiness", None)
                continue
            labor = fac.labor_force(defs, island, tile)
            working = tile.ext.get("employed", 0)
            if subsistence_persons(defs, island, tile):
                working = labor  # 村で自給の農業をしている人は、仕事がないとは数えない
            facts = {
                "food": general["food"],
                "jobs": fac.ratio(working, labor) if labor else fac.FULL,
                "power": tile.ext.get("power", 0),
                "quality": general["quality"],
                "consumer_goods": general["consumer_goods"],
                "pollution": tile.pollution,
            }
            mood = ctx.formula("happiness")(island, facts, ctx)
            tile.ext["happiness"] = mood
            power_sum += facts["power"] * tile.population
            pollution_sum += tile.pollution * tile.population
            people += tile.population
            moods.setdefault(GENERAL, [0, 0])
            moods[GENERAL][0] += mood * tile.population
            moods[GENERAL][1] += tile.population
            home = homes.get((tile.x, tile.y))
            capacity = fac.housing_capacity(defs, home) if home else 0
            pop = tile.population
            if general["food"] >= fac.FULL:
                # 仕事のある人の割合が growth_min_jobs を下回るマスでは増えない
                low = p["growth_min_jobs"]
                jobs = max(0.0, min(1.0, (facts["jobs"] / fac.FULL - low) / (1 - low)))
                births = _rand_round(pop * p["growth_rate"] * mood / 100 * jobs, rng)
                pop += min(births, max(0, capacity - pop))
            else:
                hunger = (fac.FULL - general["food"]) / fac.FULL
                pop -= _rand_round(pop * p["starvation_rate"] * hunger, rng)
            if mood < p["emigration_happiness"]:
                pop -= _rand_round(pop * p["emigration_rate"], rng)
            tile.population = max(0, pop)
        moods_by_layer = (
            {GENERAL: moods[GENERAL][0] / moods[GENERAL][1]} if GENERAL in moods else {}
        )
        for layer, f in sorted(layers.items()):
            if layer == GENERAL or not people:
                continue
            facts = {
                "food": f["food"],
                "jobs": fac.FULL,
                "power": power_sum // people,
                "quality": f["quality"],
                "consumer_goods": f["consumer_goods"],
                "pollution": pollution_sum / people,
            }
            moods_by_layer[layer] = ctx.formula("happiness")(island, facts, ctx)
        island.scratch["layer_happiness"] = moods_by_layer
        island.scratch["layer_facts"] = layers
        self._migrate(island, ctx, rng, homes)
        for tile in island.tiles:
            level = fac.settlement_of(defs, tile.population)
            if (tile.x, tile.y) in homes and level != before[(tile.x, tile.y)]:
                levels = defs.settlement_levels()
                if levels.index(level) > levels.index(before[(tile.x, tile.y)]):
                    island.log.append(
                        LogEntry("settlement_grew", {"x": tile.x, "y": tile.y, "level": level})
                    )
        if island.has_economy:
            changes = split_persons(island, sum(t.population for t in island.tiles))
            move_people(island, changes)
            if island.economy.happiness_blend > 0:
                island.economy.happiness_blend -= 1

    def _migrate(
        self,
        island: IslandState,
        ctx: StageContext,
        rng: random.Random,
        homes: dict[Pos, Facility],
    ) -> None:
        """住む所のない人と仕事のない人を、人手の足りない施設の近くの空いた町へ移す。"""
        defs, config = ctx.defs, ctx.config
        share = fac.labor_share(defs, island)
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
                idle = fac.labor_force(defs, island, tile) - tile.ext.get("employed", 0)
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
    """都市の発展: 人手の足りない施設の通勤圏で住む所が足りなければ、町を育てるか新しく作る。

    育つ・作るには、その段階の資材（settlements.yaml の materials）を、一般の層が市場で買った
    資材（economy.housing["stock"]）から使う。足りなければ要る資材を economy.housing["want"] に
    書き、次のターンの市場で一般の層が買う。2ターン続けて足りなければ知らせる。
    """

    name = "housing"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs, config = ctx.defs, ctx.config
        h = config.get("housing")
        share = fac.labor_share(defs, island)
        housing = island.economy.housing
        housing.setdefault("stock", {})
        housing["want"] = {}
        homes = _homes(island, config)
        kind = fac.housing_kind(config)
        levels = defs.settlement_levels()
        built = upgraded = 0
        short: set[str] = set()  # 足りなくて町が育たなかった資源
        vacancies = sorted(_vacancies(defs, island, ctx.turn), key=lambda v: (-v[1], v[0].id))
        for f, missing in vacancies:
            if built >= h["max_new_per_turn"] and upgraded >= h["max_upgrades_per_turn"]:
                break
            area = within(island, (f.x, f.y), fac.commute_radius(defs, config, f.kind))
            near = [pos for pos in area if pos in homes]
            # 空いている町に入れる人と、もう近くに住んでいて仕事のない人で足りれば育てない
            room = sum(
                fac.housing_capacity(defs, homes[pos]) - island.tile(*pos).population
                for pos in near
            )
            idle = sum(_idle(defs, island, island.tile(*pos)) for pos in near)
            if share <= 0 or room * share + idle >= missing:
                continue
            if upgraded < h["max_upgrades_per_turn"]:
                pos = self._upgradable(island, defs, homes, near, levels, h["fill_ratio"])
                if pos is not None:
                    home = homes[pos]
                    target = levels[levels.index(home.ext.get("level", levels[0])) + 1]
                    missing = self._pay(island, defs, target)
                    if not missing:
                        home.ext["level"] = target
                        upgraded += 1
                        continue
                    short.update(missing)
            if built < h["max_new_per_turn"]:
                pos = self._empty_site(island, defs, kind, area)
                if pos is not None:
                    missing = self._pay(island, defs, levels[0])
                    if not missing:
                        homes[pos] = fac.new_housing(island, kind, pos, levels[0])
                        built += 1
                    short.update(missing)
        # 2ターン続けて資材を買えなかったときだけ知らせる
        # （足りない間は毎ターン同じ知らせを出さない）
        if short:
            turns = island.ext.get("housing_short_turns", 0) + 1
            if turns == 2:
                island.log.append(LogEntry("housing_short", {"goods": sorted(short)}))
            island.ext["housing_short"] = sorted(short)
            island.ext["housing_short_turns"] = turns
        else:
            island.ext.pop("housing_short", None)
            island.ext.pop("housing_short_turns", None)

    @staticmethod
    def _pay(island: IslandState, defs: Defs, level: str) -> list[str]:
        """段階 level に育つ資材を、家計が買った資材から使う。

        足りなければ何も使わず、要る資材を次のターンに買う分（want）に足して、足りない資材を返す。
        """
        need = defs.settlements[level].get("materials", {})
        housing = island.economy.housing
        held = housing["stock"]
        missing = [g for g, q in sorted(need.items()) if stock.total(held, g) < q]
        if not missing:
            for good, qty in sorted(need.items()):
                stock.take(held, good, qty)
        else:
            want = housing["want"]
            for good, qty in sorted(need.items()):
                want[good] = want.get(good, 0) + qty
        return missing

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
        problems.append("workers.yaml に general（一般労働者）がありません")
    if not defs.settlements:
        problems.append("settlements.yaml に町の段階がありません")
    return problems


def register(registry: Registry) -> None:
    registry.add_stage(Population())
    registry.add_stage(Housing())
    registry.add_formula("food_need", food_need)
    registry.add_formula("happiness", happiness)
    registry.add_check(check_population_defs)
