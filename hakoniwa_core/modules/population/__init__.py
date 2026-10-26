"""消費と人口の段と、都市の発展（町の自動建設と成長）の段。

- 家計の層ごとに、市場の段で買えた食料・消費財と、届いた電力から幸福度を出す（式 happiness）。
  一般の層はマスごと（働き口・電力・汚染はマスの値）、ほかの層は島全体で出し、島の幸福度は
  人数で平均する。資産を持つ層の働き口はいつも満点。
- 人が生まれ、亡くなる（バランス調整の回4。現実の人口転換）。生まれる割合は学んだ人の割合と所得が
  上がるほど下がり、亡くなる割合は上下水道と食事の質で下がる（式 birth_rate・death_rate）。仕事が
  なくても増える。飢えている層からは生まれず、飢えで亡くなる人は別に数える（回3 の飢えの蓄え）。
  マスに住む所がなければ、生まれた家族は島の空いた町へ移り、どこにも空きがなければそのマスに詰めて
  住む（過密。回5）。過密のマスは亡くなる人が増え、生まれる人が減り、幸福度が下がる。
  村の住民のうち仕事のない人は、自給の農業で自分の食べる分をまかなう（市場で買わない）。食べ物が
  買えないときは、村の働き手が畑に戻る（modules/employment の back_to_fields）。町の人は、食べ物が
  買えなければ空きのある村へ移る（_flee_famine。どちらも回4）。一般の層の飢えの蓄えは、市場で
  食料を買う人と、村で自給の農業をしている人とで分けて持つ（_move_to_fields）。
- 人は仕事のないマスから、人手の足りない施設（と商業・サービス業が雇いきれなかった集落）の通勤圏に
  ある町へ少しずつ移る。町が育つのは、この移り住みから。
- 最後に、住民の数から家計の層の人数を決め直す（層を移った人の預金も一緒に移す）。
- 人手の足りない施設の通勤圏（と、商業・サービス業が雇いきれなかった集落の通勤圏。バランス調整）で
  住む所が足りなければ、町を上の段階へ育てるか、空いた平地に
  新しい町（村）を作る。どちらも資材（settlements.yaml の materials）が要り、一般の層が
  市場で買う（買った資材は economy.housing["stock"] に入る）。足りなければ、要る資材を
  economy.housing["want"] に書いて、次のターンの市場で買う。開発を止めたマスには作らない。
- 島の住む所の上限を超えて住む人が島の空きより多ければ、いちばん過密なマスの近くの空いた平地に、
  家計が新しい村を開く（分家と開拓。回5）。施設の通勤圏でなくてよい。
"""

from __future__ import annotations

import math
import random
from typing import Any, NamedTuple

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, ledger, politics, stock
from hakoniwa_core.allocation import proportional
from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import GENERAL, JOBLESS, household, needs, services
from hakoniwa_core.economy import public_housing as pub
from hakoniwa_core.economy.households import (
    blend_hunger,
    move_people,
    recount_engineers,
    split_persons,
)
from hakoniwa_core.economy.stats import inflation, real_wage_change
from hakoniwa_core.geo import distance, within
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import ZONE_NONE, Facility, IslandState, LogEntry, Tile
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import QUANTITY_SCALE, to_milli

Pos = tuple[int, int]
# 幸福度の重みの名前（前のフェーズの重みから移すときに、今の重みにない名前も 0 として移す）
HAPPINESS_FACTS = (
    "base",
    "food",
    "jobs",
    "power",
    "food_quality",
    "consumer_goods",
    "clothing",
    "luxury",
    "water",
    "education",
    "housing",
)
OLD_CONSUMER_GOOD = "consumer_goods"  # フェーズ3-1 までの幸福度の「消費財」で数える財
HUNGER = "hunger"  # 層の飢えの蓄え（economy.households[層]。栄養の不足が何か月分たまったか）
# 層の生き残った人が、この飢えのあいだに耐えたいちばん大きい蓄え（余力のそれより小さい人はもう
# 亡くなっている。蓄えが 0 に戻ると消す）
ENDURED = "hunger_endured"
FED = "fed"  # 層が前のターンに食べられた割合（1000 分の 1）
FLED = "fled"  # 村のマスの、飢饉で町から逃れてきた人数（tile.ext。町へ戻るとき。回7）
# 飢えの知らせ（Codex の試し遊び、ターン 250〜300。10-17 のカード「知らせて回7」）: 食べられなかった
# 層の買えなかったわけ（money: 食費が使えるお金を使い切った、goods: 売りに出ていた物が足りない、
# outbid: 売りはあったが買い負けた）、市場で食料を買う人の数（このターンに亡くなった人を除く）、
# いまの飢えの蓄えで亡くなる人の割合（starved_share。すでに亡くなった人も含めた累計。不足が深まって
# いるターンだけ）。
# economy.households[層] に置く
FOOD_SHORT = "food_short"
FOOD_BUYERS = "food_buyers"
HUNGER_RISK = "hunger_risk"
FOOD_ORDERS = "household_food"  # island.scratch: 層ごとの食料の注文の要点（市場の段が書く）
# 一般の層のうち、村で自給の農業をしている人の飢えの蓄え（HUNGER・ENDURED）と、前のターンに
# 市場で食料を買っていた人の数（market）。一般の層の HUNGER は、市場で食料を買う人の蓄え（回4）
FIELDS = "fields"


def _rand_round(value: float, rng: random.Random) -> int:
    """小数の部分を確率で切り上げる（小さなマスでも少しずつ増減するように）。"""
    whole = int(value)
    return whole + (1 if rng.random() < value - whole else 0)


def _farming(defs: Defs, tile: Tile) -> bool:
    """自給の農業ができる集落（村）か。"""
    level = fac.settlement_of(defs, tile.population)
    return bool(defs.settlements[level].get("subsistence"))


def _built_farming(defs: Defs, home: Facility) -> bool:
    """建った段階が自給の農業をする集落（村）の住宅地か。"""
    level = home.ext.get("level") or defs.settlement_levels()[0]
    return bool(defs.settlements[level].get("subsistence"))


def overcrowded(defs: Defs, island: IslandState, homes: dict[Pos, Facility]) -> int:
    """住む所の上限を超えて住む人の数（回5。住む所のないマスの人も数える）。"""
    total = 0
    for tile in island.tiles:
        if tile.population <= 0:
            continue
        home = homes.get((tile.x, tile.y))
        capacity = fac.housing_capacity(defs, home) if home else 0
        total += max(0, tile.population - capacity)
    return total


def village_size(defs: Defs) -> int:
    """村のままでいられる人数（自給の農業ができない段階のうち、いちばん小さい段階の人数の手前まで）。"""
    levels = defs.settlements.values()
    sizes = [d["min_population"] - 1 for d in levels if not d.get("subsistence")]
    return min(sizes, default=max(d["capacity"] for d in levels))


def subsistence_persons(
    defs: Defs, island: IslandState, tile: Tile, share: float | None = None
) -> int:
    """自給の農業で食べている人の数（村の、仕事のない働き手とその家族）。share は labor_share。"""
    if share is None:
        share = fac.labor_share(defs, island)
    if not _farming(defs, tile) or share <= 0:
        return 0
    unemployed = fac.labor_force(defs, island, tile, share) - tile.ext.get("employed", 0)
    return min(tile.population, int(max(0, unemployed) / share))


def tile_jobless(defs: Defs, island: IslandState, tile: Tile, share: float | None = None) -> int:
    """マスの、仕事のない一般の労働者の数（村で自給の農業をしている人を除く。失業率と同じ数え方）。
    share は labor_share。"""
    if share is None:
        share = fac.labor_share(defs, island)
    idle = max(0, fac.labor_force(defs, island, tile, share) - tile.ext.get("employed", 0))
    return max(0, idle - int(subsistence_persons(defs, island, tile, share) * share))


def jobless_family(defs: Defs, island: IslandState) -> float:
    """一般の労働者1人あたりの家族の人数（住民のうち一般の労働者になる割合の逆数。資産を持つ層と
    技術者の層の分は除く）。"""
    share = defs.workers["general"].get("share", 1.0)
    share += island.ext.get("politics", {}).get("mobilized", 0.0)
    return 1 / share if share > 0 else 1.0


def jobless_persons(defs: Defs, island: IslandState) -> int:
    """失業している家計の人数（仕事のない一般の労働者とその家族）。"""
    family = jobless_family(defs, island)
    share = fac.labor_share(defs, island)
    total = 0
    for tile in island.tiles:
        if tile.population > 0:
            total += min(tile.population, int(tile_jobless(defs, island, tile, share) * family))
    return total


def food_need(island: IslandState, ctx: StageContext) -> int:
    """家計が市場で買う食料の量（栄養、1000 分の 1 単位）。自給の農業で食べている人の分は除く。"""
    share = fac.labor_share(ctx.defs, island)
    persons = sum(
        t.population - subsistence_persons(ctx.defs, island, t, share)
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


def layer_income(island: IslandState, config: GameConfig, name: str) -> float:
    """層の1人あたりの実質の可処分所得の、household.base_income に対する倍率（直近の
    household.income_turns ターンの平均を物価指数で割る）。記録がなければ 1。"""
    h = island.economy.households.get(name)
    if not h or h["persons"] <= 0 or not h.get("income"):
        return 1.0
    history = h["income"][-config.get("household.income_turns") :]
    cpi_history = island.economy.cpi_history
    cpi = cpi_history[-1] / 100 if cpi_history and cpi_history[-1] > 0 else 1.0
    per_person = sum(history) / len(history) / h["persons"]
    return max(0.0, per_person / cpi / config.get("household.base_income"))


def desired_space(island: IslandState, config: GameConfig, layer: str) -> float:
    """層の望む広さ（1 人あたりの延べ床、㎡。W-201）。housing.space.base ×（層の実質の所得の倍率）
    ^ housing.space.elasticity で、最低の広さ housing.space.min より下げない。"""
    spec = config.get("housing.space")
    income = layer_income(island, config, layer)
    return max(float(spec["min"]), spec["base"] * income ** spec["elasticity"])


class Dwelling(NamedTuple):
    """町のマスの床と住む人の望み（㎡）。floor は傷みを引いた床、nominal は傷みを見ない床、want は
    住む人が望む広さで使う床の合計、layers は want の層ごとの内わけ。"""

    floor: float
    nominal: float
    want: float
    layers: dict[str, float]

    @property
    def ratio(self) -> float:
        """住む人の望みに対する床の割合（1 まで。住む人がいなければ 1）。"""
        return min(1.0, self.floor / self.want) if self.want > 0 else 1.0


def dwelling_status(
    defs: Defs,
    config: GameConfig,
    island: IslandState,
    homes: dict[Pos, Facility],
    placed: dict[str, dict[Pos, int]] | None = None,
) -> dict[Pos, Dwelling]:
    """人が住む、または町のあるマスごとの床と望み（W-201）。住む人は層ごとの人数（layer_tiles）に
    層の望む広さを掛けて合わせ、層のないマスの人は一般の層の望む広さで数える。"""
    min_space = config.get("housing.space")["min"]
    if placed is None:
        placed = layer_tiles(defs, island, island.economy.households)
    desires = {name: desired_space(island, config, name) for name in placed}
    general = desires.get(GENERAL) or desired_space(island, config, GENERAL)
    out: dict[Pos, Dwelling] = {}
    for tile in island.tiles:
        pos = (tile.x, tile.y)
        home = homes.get(pos)
        if tile.population <= 0 and home is None:
            continue
        layers = {n: here * desires[n] for n in placed if (here := placed[n].get(pos, 0)) > 0}
        gap = tile.population - sum(placed[n].get(pos, 0) for n in placed)
        if gap > 0:
            layers[GENERAL] = layers.get(GENERAL, 0.0) + gap * general
        floor = fac.housing_capacity(defs, home) * min_space if home else 0
        nominal = fac.nominal_housing_capacity(defs, home) * min_space if home else 0
        out[pos] = Dwelling(floor, nominal, sum(layers.values()), layers)
    return out


def birth_rate(island: IslandState, income: float, ctx: StageContext) -> float:
    """1年に生まれる人の割合（粗出生率。名前付きの式 birth_rate の既定）。

    low + (high − low) × (1 − E)^education × y^(−income)。E は島の教育水準（働き手のうち学んだ
    人の割合）、y は層の実質の所得（layer_income。下限 population.births.income_floor）。学んだ人が
    増え、暮らしが豊かになるほど、子を持つ数が減る（現実の人口転換。女性の教育と、子にかかる費用と
    老後の支え）。high を超えない。
    """
    spec = ctx.config.get("population.births")
    education = max(0.0, min(1.0, island.economy.education / fac.FULL))
    y = max(spec["income_floor"], income)
    rate = spec["low"] + (spec["high"] - spec["low"]) * (1 - education) ** spec[
        "education"
    ] * y ** (-spec["income"])
    return min(spec["high"], rate)


def death_rate(water: int, quality: float, people: int, ctx: StageContext) -> float:
    """1年に亡くなる人の割合（粗死亡率。飢えで亡くなる人は別。名前付きの式 death_rate の既定）。

    base + food ×（1 − 食事の質）+ water ×（1 − 上下水道の整備度）×（マスの人数 people ÷ crowd、
    1 まで）。肉や魚の多い食事が子どもの病気を、きれいな水と下水が感染症を減らす（19世紀からの
    死亡率の下がり方）。汚れた水からの病気は、人が密に住むほど広がる（村より町で多く亡くなる）。
    """
    spec = ctx.config.get("population.deaths")
    clean = max(0.0, min(1.0, water / fac.FULL))
    fed = max(0.0, min(1.0, quality))
    crowding = max(0.0, min(1.0, people / spec["crowd"]))
    return spec["base"] + spec["food"] * (1 - fed) + spec["water"] * (1 - clean) * crowding


def happiness_weights(island: IslandState, ctx: StageContext) -> dict[str, float]:
    """幸福度の重み。前のフェーズから移し替えた島は、population.blend_turns ターンかけて、
    その時の重み（economy.ext["happiness_from"] の名前の設定。既定は happiness_phase1）から移す。
    """
    weights = dict(ctx.config.get("population.happiness"))
    left = island.economy.happiness_blend
    turns = ctx.config.get("population.blend_turns")
    if left > 0 and turns > 0:
        source = island.economy.ext.get("happiness_from", "happiness_phase1")
        old = ctx.config.get(f"population.{source}")
        t = left / turns
        for key in set(old) | {k for k in weights if k in HAPPINESS_FACTS}:
            weights[key] = weights.get(key, 0) * (1 - t) + old.get(key, 0) * t
    return weights


def _blend_notice(island: IslandState, ctx: StageContext) -> None:
    """幸福度の重みを移し始めたターンに、重みが変わることを知らせる（知らせないと、何もしていないのに
    幸福度が下がり続けるように見える）。"""
    turns = ctx.config.get("population.blend_turns")
    if island.economy.happiness_blend != turns:
        return
    source = island.economy.ext.get("happiness_from", "happiness_phase1")
    old = ctx.config.get(f"population.{source}")
    now = ctx.config.get("population.happiness")
    if source == "happiness_before_housing":
        # 住まいの広さ（W-201）を足す: 基礎の点が住まいの広さの項目へ移る
        args = {
            "turns": turns,
            "base_from": old["base"],
            "base": now["base"],
            "housing": now["housing"],
        }
        island.log.append(LogEntry("happiness_weights_changed_housing", args))
        return
    if old.get("water", 0) > 0:
        # フェーズ3-1 から続く島: 上下水道と教育はもうあり、消費財が衣料とぜいたく品に替わる
        # （基礎の点は変わらないのに「15 点から 15 点に下がります」と出ていた）
        args = {
            "turns": turns,
            "consumer_goods": old.get("consumer_goods", 0),
            "clothing": now.get("clothing", 0),
            "luxury": now.get("luxury", 0),
        }
        island.log.append(LogEntry("happiness_weights_changed_goods", args))
        return
    args = {
        "turns": turns,
        "water": now["water"],
        "education": now["education"],
        "base_from": old["base"],
        "base": now["base"],
    }
    island.log.append(LogEntry("happiness_weights_changed", args))


def happiness(island: IslandState, facts: dict[str, Any], ctx: StageContext) -> int:
    """幸福度（0〜100）。

    facts: food・jobs・power・consumer_goods・clothing・luxury・water・education・housing
    （1000 分の 1。housing は住まいの広さ: 住む人の望む広さに対する床の割合。書かなければ満点）、
    quality（0〜1）、pollution（汚染）、hunger（飢えの蓄え。か月）、arrears（賃金が払われなかった
    働き手の割合。0〜1）、crowding（マスの過密の度合い。0 から）。water はそのマスの上下水道の
    整備度、education は島の教育水準。clothing と luxury はフェーズ3-2 の衣料とぜいたく品
    （economy/needs.py）、consumer_goods はフェーズ3-1 までの消費財（移し替えの間だけ重みが
    残る）。
    実質賃金が1年前より下がった分と、物価上昇率が目安を超えた分、飢えの蓄えと賃金の遅配（回3）を
    減点する。上限は体制の
    happiness_cap（全体主義 80。フェーズ3-4）。
    """
    w = happiness_weights(island, ctx)
    value = (
        w["base"]
        + w["food"] * facts["food"] / fac.FULL
        + w["jobs"] * facts["jobs"] / fac.FULL
        + w["power"] * facts["power"] / fac.FULL
        + w["food_quality"] * facts["quality"]
        + w.get("consumer_goods", 0) * facts.get("consumer_goods", 0) / fac.FULL
        + w.get("clothing", 0) * facts.get("clothing", 0) / fac.FULL
        + w.get("luxury", 0) * facts.get("luxury", 0) / fac.FULL
        + w.get("water", 0) * facts.get("water", 0) / fac.FULL
        + w.get("education", 0) * facts.get("education", 0) / fac.FULL
        + w.get("housing", 0) * facts.get("housing", fac.FULL) / fac.FULL
        - w["pollution_per_point"] * facts["pollution"]
    )
    fall = -real_wage_change(island.economy) * 100
    if fall > 0:
        value -= min(w["real_wage_max"], w["real_wage_per_point"] * fall)
    over = (inflation(island.economy) - w["inflation_threshold"]) * 100
    if over > 0:
        value -= min(w["inflation_max"], w["inflation_per_point"] * over)
    # 飢えの蓄え（回3）: 食べられるようになっても、体が戻るまで暮らしは苦しい
    value -= min(w.get("hunger_max", 0), w.get("hunger", 0) * facts.get(HUNGER, 0.0))
    # 賃金の遅配（回3）: 働いても払われない人の割合
    value -= w.get("arrears", 0) * facts.get("arrears", 0.0)
    # 過密（回5）: 住む所の上限を超えて詰めて住むマス
    value -= min(w.get("crowding_max", 0), w.get("crowding", 0) * facts.get("crowding", 0.0))
    # 体制の幸福度の上限（全体主義 80。フェーズ3-4）
    return max(0, min(politics.happiness_cap(island, ctx.defs), round(value)))


def _idle(defs: Defs, island: IslandState, tile: Tile) -> int:
    return max(0, fac.labor_force(defs, island, tile) - tile.ext.get("employed", 0))


def homes_of(island: IslandState, config: GameConfig) -> dict[Pos, Facility]:
    kind = fac.housing_kind(config)
    return {(f.x, f.y): f for f in island.facilities if f.kind == kind}


def vacancies_of(
    defs: Defs, island: IslandState, config: GameConfig, turn: int
) -> list[tuple[Pos, int, int, int]]:
    """人手の足りない所: (マス, 通勤圏, 足りない人数, 並べる番号) の並び。

    人手の足りない施設（工事中も含む。マスを持たない施設は除く）は、雇いたい人数（雇用の段で払える
    賃金の分に抑えた人数。economy["wanted"]）に足りない人数。商業・サービス業（バランス調整）は、
    店の場所（集落）ごとに雇いきれなかった人数（economy/services.py の vacancies）。
    """
    found = []
    for f in island.facilities:
        if not fac.located(f) or (f.built and fac.workers_of(defs, f) <= 0):
            continue
        want = f.economy.get("wanted", fac.labor_demand(defs, f, turn))
        missing = want - f.workers
        if missing > 0:
            radius = fac.commute_radius(defs, config, f.kind)
            found.append((fac.position(f), radius, missing, f.id))
    shop = services.facility_of(island, config)
    if shop is not None:
        radius = config.get("labor.commute_radius")
        for pos, missing in services.vacancies(island, config):
            found.append((pos, radius, missing, shop.id))
    return found


def _layer_facts(island: IslandState, ctx: StageContext) -> dict[str, dict[str, Any]]:
    """層ごとの、食べられた割合・食事の質・消費財と衣料とぜいたく品の満足（1000 分の 1）。"""
    defs, config = ctx.defs, ctx.config
    bought = island.scratch.get("household_goods", {})
    food_needs = layer_food_needs(island, ctx)
    old_good = OLD_CONSUMER_GOOD
    target = needs.target_of(config, old_good)
    facts = {}
    for layer, h in sorted(island.economy.households.items()):
        goods = bought.get(layer, {})
        eaten = 0
        quality = 0.0
        for good, qty in sorted(goods.items()):
            spec = defs.goods[good]
            if "food" in spec:
                amount = qty * to_milli(spec["food"]["nutrition"]) // QUANTITY_SCALE
                eaten += amount
                quality += amount * spec["food"].get("quality", 0)
        need = food_needs.get(layer, 0)
        facts[layer] = {
            "food": fac.ratio(eaten, need),
            "quality": quality / eaten if eaten else 0.0,
            "consumer_goods": fac.ratio(goods.get(old_good, 0), to_milli(h["persons"] * target)),
            **needs.satisfaction(island, config, defs, layer, h["persons"]),
            "eaten": eaten,
            "need": need,
        }
        if layer == GENERAL:  # 賃金が払われないまま働いている人の割合（雇用の段。回3）
            facts[layer]["arrears"] = island.scratch.get("arrears_share", 0.0)
    return facts


def hunger_step(stock: float, shortfall: float, settings: dict[str, float]) -> float:
    """飢えの蓄えの次の値: S + d − tolerance − recovery × S（0 未満は 0）。

    d はこのターンに足りなかった食料の割合（0〜1）。体は少しの不足を代謝を落として耐え
    （tolerance）、やせるほど使う量が減り、食べられれば少しずつ戻る（recovery）。半分しか食べ
    られない月が続くと、蓄えは (d − tolerance) ÷ recovery に近づく。
    """
    value = stock + shortfall - settings["tolerance"] - settings["recovery"] * stock
    return max(0.0, value)


def _move_to_fields(h: dict[str, Any], market: int, settings: dict[str, float]) -> None:
    """一般の層の、市場で食料を買う人と村で自給の農業をしている人の間を移った人の飢えの蓄えを、
    移った先の蓄えに人数で混ぜ、畑で食べている人の蓄えを1ターン進める（回4）。

    飢えの蓄えは人の体のことなので、畑に戻って食べている間は戻り、市場で買う暮らしに戻っても、
    市場で飢えていた人の蓄えをそのまま負わない（層で1つの蓄えにしていたころは、畑で食べていた人が
    仕事に戻ったとたんに、市場で飢えていた人と同じ蓄えで亡くなった）。market はこのターンに市場で
    食料を買う人の数。前のターンより増えた分は畑から、減った分は畑へ移った人として混ぜる。
    """
    persons = h["persons"]
    market = max(0, min(market, persons))
    fields = persons - market
    group = h.get(FIELDS, {"market": market})
    buyers = {"persons": market, HUNGER: h.get(HUNGER, 0.0), ENDURED: h.get(ENDURED, 0.0)}
    farmers = {"persons": fields, HUNGER: group.get(HUNGER, 0.0), ENDURED: group.get(ENDURED, 0.0)}
    moved = market - group["market"]
    if moved > 0:  # 畑から市場へ
        blend_hunger(farmers, buyers, min(moved, market))
    elif moved < 0:  # 市場から畑へ
        blend_hunger(buyers, farmers, min(-moved, fields))
    for key in (HUNGER, ENDURED):
        if buyers[key] > 0:
            h[key] = buyers[key]
        else:
            h.pop(key, None)
    # 畑で食べている人は、食べられた人として蓄えが戻る
    stock = hunger_step(farmers[HUNGER], 0.0, settings) if fields > 0 else 0.0
    h[FIELDS] = {"market": market}
    if stock > 0:
        h[FIELDS][HUNGER] = round(stock, 4)
        h[FIELDS][ENDURED] = round(max(farmers[ENDURED], stock), 4)


def food_short_reason(orders: dict[str, int] | None) -> str:
    """食べられなかった層の買えなかったわけ。市場の段が残した注文の要点（FOOD_ORDERS）で、食費が使える
    お金を使い切っていれば money（お金がない）。そうでなく、要る量の分の売りが出ていなければ goods
    （物がない）、出ていたのに買えなければ outbid（ほかの買い手に買い負けたか、売りの値段が指値より
    高かった）。要点がなければ goods。"""
    if not orders:
        return "goods"
    if orders["budget"] <= 0 or orders["cost"] >= orders["budget"] * 0.98:
        return "money"
    # 層の取り分（売りの量 × 人数の割合）と注文の量は切り捨てで数単位欠けるので、1% までは足りた
    # とみなす（食べた割合 99.9% で「物がない」と出ていた。試し遊び1回目の報告の 2）
    enough = orders["need"] * 0.99
    if orders["offered"] >= enough and orders["ordered"] >= enough:
        return "outbid"
    return "goods"


def starved_share(stock: float, settings: dict[str, float]) -> float:
    """飢えの蓄えが stock になるまでに亡くなる人の割合（同じだけ食べられなかった人たちのうち）。

    体の余力（何か月分の不足に耐えられるか）は、年齢・体格・病気で人によって違う。余力は平均
    reserve・ばらつき reserve_spread の正規分布に従い、いちばん弱い人でも reserve_min はある
    （その下を切った分布）。蓄えが自分の余力を超えた人が亡くなる。
    """
    low = settings["reserve_min"]
    if stock <= low:
        return 0.0

    def below(x: float) -> float:
        z = (x - settings["reserve"]) / (settings["reserve_spread"] * math.sqrt(2))
        return 0.5 * math.erfc(-z)

    floor = below(low)
    return (below(stock) - floor) / (1 - floor)


def hunger_deaths(stock: float, endured: float, ctx: StageContext) -> float:
    """飢えの蓄え stock の層で、このターンに亡くなる人の割合（名前付きの式 hunger_deaths）。

    層の生き残った人は、この飢えのあいだに蓄え endured まで耐えた人（余力のそれより小さい人は
    もう亡くなっている）。亡くなるのは、余力が endured から stock までの間にある人で、生き残って
    いる人のうち (F(stock) − F(endured)) ÷ (1 − F(endured))（F は starved_share）。弱った人から
    先に亡くなり、同じだけ食べられない層でも、何か月かに分かれて亡くなる（設計書の回3。オランダの
    飢餓の冬・ベンガル・レニングラードの最悪の冬・何も食べられないときの幅に入るかで確かめた値）。
    """
    if stock <= endured:
        return 0.0
    settings = ctx.config.get("population.hunger")
    before = starved_share(endured, settings)
    if before >= 1.0:
        return 1.0
    return min(1.0, (starved_share(stock, settings) - before) / (1 - before))


def layer_tiles(
    defs: Defs, island: IslandState, households: dict[str, dict[str, Any]]
) -> dict[str, dict[Pos, int]]:
    """層ごとの、マスに住む人数の見込み（層の人数をマスに割り振る）。

    失業している家計はマスの仕事のない人の家族、資産を持つ層と技術者の層は住民の数に比例、一般の
    層は残り。島全体の人数として持つ層をマスに置くのは、亡くなる人と生まれる人をマスに割り振る
    ためだけ。"""
    tiles = [t for t in island.tiles if t.population > 0]
    population = sum(t.population for t in tiles)
    if population <= 0 or not households:
        return {}
    spread = sum(h["persons"] for h in households.values() if "share" in h or "workers" in h)
    family = jobless_family(defs, island)
    raw: dict[str, dict[Pos, float]] = {name: {} for name in households}
    for t in tiles:
        pos = (t.x, t.y)
        idle = min(t.population, tile_jobless(defs, island, t) * family)
        rest = max(0.0, t.population * (1 - spread / population) - idle)
        for name, h in households.items():
            if h.get("jobless"):
                raw[name][pos] = idle
            elif "share" in h or "workers" in h:
                raw[name][pos] = float(t.population)
            else:
                raw[name][pos] = rest
    out: dict[str, dict[Pos, int]] = {}
    for name, h in households.items():
        weights = raw[name]
        total = sum(weights.values())
        if total <= 0:
            # 重みがなければ住民の数で（失業している家計の数え直しの前など）
            weights = {(t.x, t.y): float(t.population) for t in tiles}
            total = float(population)
        out[name] = {pos: int(h["persons"] * w / total) for pos, w in weights.items()}
    return out


def _exposed(
    defs: Defs, island: IslandState, placed: dict[str, dict[Pos, int]]
) -> dict[str, dict[Pos, int]]:
    """層ごとの、市場で食料を買って食べている人のマスごとの数（layer_tiles の数から、一般の層の
    村で自給の農業をしている人を除く）。"""
    out = dict(placed)
    general = placed.get(GENERAL)
    if general:
        share = fac.labor_share(defs, island)
        own = {
            (t.x, t.y): subsistence_persons(defs, island, t, share)
            for t in island.tiles
            if t.population
        }
        out[GENERAL] = {pos: max(0, n - own.get(pos, 0)) for pos, n in general.items()}
    return out


def spread_over(amount: int, places: dict[Pos, int]) -> dict[Pos, int]:
    """amount 人を、マスごとの人数 places に比例して割り振る（足りない分は多いマスから）。"""
    order = sorted(places.items(), key=lambda kv: (-kv[1], kv[0][1], kv[0][0]))
    return proportional(min(amount, sum(places.values())), order)


def crowding(population: int, capacity: int) -> float:
    """過密の度合い（回5）: 住める上限を超えた分の割合（上限の倍で 1、3倍で 2）。家のないマスは 1。

    頭打ちにしない（詰めるほど亡くなる人が増え、生まれる人が減って、どこかで釣り合う）。"""
    if population <= capacity:
        return 0.0
    if capacity <= 0:
        return 1.0
    return population / capacity - 1


class Population(IslandStage):
    name = "population"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs, config = ctx.defs, ctx.config
        rng = ctx.rng(island.id)
        layers = _layer_facts(island, ctx)
        households = island.economy.households
        fed = {
            "food": fac.FULL,
            "quality": 0.0,
            "consumer_goods": 0,
            "clothing": 0,
            "luxury": 0,
            HUNGER: 0.0,
        }
        placed = layer_tiles(defs, island, households)
        exposed = _exposed(defs, island, placed)
        stocks = self._hunger(island, ctx, layers, exposed, rng)
        general = layers.get(GENERAL) or fed
        jobless = layers.get(JOBLESS)
        need = sum(f["need"] for f in layers.values())
        supplied = sum(f["eaten"] for f in layers.values())
        island.scratch["food"] = {
            "need": need,
            "supplied": supplied,
            "ratio": fac.ratio(supplied, need) if layers else fac.FULL,
        }
        homes = homes_of(island, config)
        dwell = dwelling_status(defs, config, island, homes, placed)
        # 飢えている層は子を持つ人が減る（回4）: 飢えの蓄えが、いちばん弱い人が亡くなり始める量
        # （population.hunger.reserve_min）に近づくほど減り、そこで生まれなくなる。体が耐えられる
        # 少しの不足（蓄えが増えない不足）では減らない
        reserve = config.get("population.hunger")["reserve_min"]
        fertile = {n: max(0.0, 1 - f[HUNGER] / reserve) for n, f in layers.items()}
        moods: dict[str, list[float]] = {}
        power_sum = pollution_sum = water_sum = housing_sum = people = 0
        education = island.economy.education
        # 亡くなる人を、層ごとにその層の住むマスへ割り振る（村で自給の農業をしている人は除く）
        lost_by_tile: dict[Pos, int] = {}
        deaths: dict[str, int] = {}
        for name, count in sorted(stocks.items()):
            applied = 0
            for pos, n in spread_over(count, exposed.get(name, {})).items():
                n = min(n, island.tile(*pos).population - lost_by_tile.get(pos, 0))
                if n > 0:
                    lost_by_tile[pos] = lost_by_tile.get(pos, 0) + n
                    applied += n
            if applied > 0:
                deaths[name] = applied
        self._log_deaths(island, deaths, layers)
        # 層ごとの、1年に生まれる人の割合（回4。学んだ人の割合と層の実質の所得で）
        birth_of = ctx.formula("birth_rate")
        rates = {n: birth_of(island, layer_income(island, config, n), ctx) for n in households}
        default_rate = birth_of(island, 1.0, ctx)
        died_by_layer: dict[str, float] = {}  # 層ごとの、飢えのほかで亡くなった人（回4）
        natural = born = 0
        overcrowding = config.get("population.overcrowding")
        unhoused: dict[Pos, int] = {}  # 生まれたマスに入れなかった家族（_migrate で移す）
        for tile in island.tiles:
            if tile.population <= 0:
                tile.ext.pop("happiness", None)
                continue
            pos = (tile.x, tile.y)
            home = homes.get(pos)
            capacity = fac.housing_capacity(defs, home) if home else 0
            crowd = crowding(tile.population, capacity)
            labor = fac.labor_force(defs, island, tile)
            working = tile.ext.get("employed", 0)
            if subsistence_persons(defs, island, tile):
                working = labor  # 村で自給の農業をしている人は、仕事がないとは数えない
            local = {
                "jobs": fac.ratio(working, labor) if labor else fac.FULL,
                "power": tile.ext.get("power", 0),
                "pollution": tile.pollution,
                "water": infra.water(defs, tile),
                "education": education,
                "crowding": crowd,
                "housing": round(dwell[pos].ratio * fac.FULL) if pos in dwell else fac.FULL,
            }
            idle = placed.get(JOBLESS, {}).get(pos, 0) if jobless else 0
            workers = max(0, placed.get(GENERAL, {}).get(pos, tile.population))
            # マスの幸福度（地図・町の育ち・人の移り先）: 一般の労働者の、仕事のある家計と失業して
            # いる家計を、マスに住む人数で混ぜる
            mood = ctx.formula("happiness")(
                island, {**local, **_mix(general, jobless, workers, idle)}, ctx
            )
            tile.ext["happiness"] = mood
            if jobless is not None:
                # 層の幸福度: 仕事のある家計は仕事が満点、失業している家計は 0
                for name, facts, weight, jobs in (
                    (GENERAL, general, workers, fac.FULL),
                    (JOBLESS, jobless, idle, 0),
                ):
                    if weight <= 0:
                        continue
                    value = ctx.formula("happiness")(
                        island, {**local, **_facts(facts), "jobs": jobs}, ctx
                    )
                    entry = moods.setdefault(name, [0, 0])
                    entry[0] += value * weight
                    entry[1] += weight
            else:
                entry = moods.setdefault(GENERAL, [0, 0])
                entry[0] += mood * tile.population
                entry[1] += tile.population
            power_sum += local["power"] * tile.population
            pollution_sum += tile.pollution * tile.population
            water_sum += local["water"] * tile.population
            housing_sum += local["housing"] * tile.population
            people += tile.population
            pop = tile.population - min(tile.population, lost_by_tile.get(pos, 0))
            # 生まれる人と亡くなる人（回4）: 層ごとの割合を、マスに住む層の人数で混ぜる
            here = {n: placed.get(n, {}).get(pos, 0) for n in placed}
            weight = sum(here.values())
            quality = (
                sum(layers[n]["quality"] * c for n, c in here.items() if n in layers) / weight
                if weight
                else general["quality"]
            )
            dying = ctx.formula("death_rate")(local["water"], quality, tile.population, ctx)
            dying += overcrowding["deaths"] * crowd  # 詰め込み住まいの病気（回5）
            dead = min(pop, _rand_round(pop * dying / YEAR, rng))
            if dead > 0:
                pop -= dead
                natural += dead
                for name, count in here.items():
                    if weight:
                        died_by_layer[name] = died_by_layer.get(name, 0.0) + dead * count / weight
            # 生まれるのは、層の割合で、飢えている層は市場で食料を買う人のうち飢えで減った分を除いて
            # （自給の農業で食べている人は、市場の食料が足りなくても食べられている）
            if weight:
                rate = (
                    sum(
                        rates.get(n, default_rate)
                        * (c - exposed.get(n, {}).get(pos, 0) * (1 - fertile.get(n, 1.0)))
                        for n, c in here.items()
                    )
                    / weight
                )
            else:
                rate = default_rate
            rate *= max(0.0, 1 - overcrowding["births"] * crowd)  # 土地と家が足りない（回5）
            if rate > 0:
                # 住む所がなくても生まれる。入れない家族は、ほかの人が移った後に空いた町へ移り、
                # なければ詰めて住む（_migrate。回5）
                births = _rand_round(pop * rate / YEAR, rng)
                room = max(0, capacity - pop)
                pop += min(births, room)
                born += births
                if births > room:
                    unhoused[pos] = births - room
            tile.population = max(0, pop)
        moods_by_layer = {n: v / w for n, (v, w) in moods.items() if w > 0}
        for layer, f in sorted(layers.items()):
            if layer in (GENERAL, JOBLESS) or not people:
                continue
            facts = {
                **_facts(f),
                "jobs": fac.FULL,
                "power": power_sum // people,
                "pollution": pollution_sum / people,
                "water": water_sum // people,
                "education": education,
                "housing": housing_sum // people,
            }
            moods_by_layer[layer] = ctx.formula("happiness")(island, facts, ctx)
        island.scratch["layer_happiness"] = moods_by_layer
        island.scratch["layer_facts"] = layers
        self._migrate(island, ctx, rng, homes, unhoused)
        stats = island.stats
        stats["population.births"] = born
        stats["population.deaths"] = natural
        crowded = overcrowded(defs, island, homes)
        if crowded > 0:
            stats["population.overcrowded"] = crowded
        if island.has_economy:
            for name, count in _round_shares(natural, died_by_layer).items():
                deaths[name] = deaths.get(name, 0) + count
            remove_dead(island, defs, deaths)
            changes = split_persons(island, defs, sum(t.population for t in island.tiles))
            move_people(island, changes)
            if island.economy.happiness_blend > 0:
                _blend_notice(island, ctx)
                island.economy.happiness_blend -= 1

    @staticmethod
    def _hunger(
        island: IslandState,
        ctx: StageContext,
        layers: dict[str, dict[str, Any]],
        exposed: dict[str, dict[Pos, int]],
        rng: random.Random,
    ) -> dict[str, int]:
        """層ごとに飢えの蓄えを進め、亡くなる人数を返す（層の状態に蓄えと食べた割合を書く）。

        亡くなりうるのは、市場で食料を買って食べている人（exposed。村で自給の農業をしている人は、
        一般の層でも、市場の食料が足りないことでは飢えない）。
        """
        settings = ctx.config.get("population.hunger")
        households = island.economy.households
        deaths: dict[str, int] = {}
        for name, f in sorted(layers.items()):
            h = households[name]
            if name == GENERAL and name in exposed:
                _move_to_fields(h, sum(exposed[name].values()), settings)
            shortfall = max(0.0, 1 - f["food"] / fac.FULL)
            stock = hunger_step(h.get(HUNGER, 0.0), shortfall, settings)
            endured = h.get(ENDURED, 0.0)
            if stock > 0:
                h[HUNGER] = round(stock, 4)
                h[ENDURED] = round(max(endured, stock), 4)
            else:  # 体が戻った
                h.pop(HUNGER, None)
                h.pop(ENDURED, None)
            h[FED] = f["food"]
            f[HUNGER] = stock
            rate = ctx.formula("hunger_deaths")(stock, endured, ctx)
            persons = h["persons"]
            if name == GENERAL and name in exposed:
                persons = min(persons, sum(exposed[name].values()))
            # 飢えの知らせ: 買えなかったわけ・市場で買う人の数・いまの蓄えで亡くなる割合
            if f["food"] < fac.FULL and f["need"] > 0:
                h[FOOD_SHORT] = food_short_reason(island.scratch.get(FOOD_ORDERS, {}).get(name))
            else:
                h.pop(FOOD_SHORT, None)
            if stock > endured:  # 不足が深まっている（戻りかけなら新しく亡くなる人はいない）
                h[HUNGER_RISK] = round(starved_share(stock, settings), 4)
            else:
                h.pop(HUNGER_RISK, None)
            island.stats[f"layer.{name}.food"] = f["food"] / fac.FULL
            if stock > 0:
                island.stats[f"layer.{name}.hunger"] = round(stock, 4)
            count = min(persons, _rand_round(persons * rate, rng)) if rate > 0 else 0
            if count > 0:
                deaths[name] = count
            h[FOOD_BUYERS] = persons - count  # このターンを生き延びて、市場で食料を買う人
            if name == GENERAL and FIELDS in h:
                h[FIELDS]["market"] = max(0, h[FIELDS]["market"] - count)
        return deaths

    @staticmethod
    def _log_deaths(
        island: IslandState, deaths: dict[str, int], layers: dict[str, dict[str, Any]]
    ) -> None:
        """飢えで亡くなった人を統計とターンログに残す（毎ターン減っても気づけるように）。"""
        total = sum(deaths.values())
        if total <= 0:
            return
        stats = island.stats
        stats["population.starved"] = total
        for name, count in deaths.items():
            stats[f"layer.{name}.starved"] = count
        worst = max(layers[name][HUNGER] for name in deaths)
        args = {"count": total, "months": math.floor(worst * 10) / 10}
        island.log.append(LogEntry("population_hunger_deaths", args))

    def _migrate(
        self,
        island: IslandState,
        ctx: StageContext,
        rng: random.Random,
        homes: dict[Pos, Facility],
        unhoused: dict[Pos, int] | None = None,
    ) -> None:
        """住む所の上限を超えた人と仕事のない人を、人手の足りない施設の近くの空いた町へ移す。

        unhoused は、生まれたマスに住む所がなかった家族（回4）。ほかの人を移した後に、まだ空いている
        町へ入れる（人手の足りない所に近い町から）。上限を超えた人と入れなかった家族は、空いた町が
        なければ消えずに詰めて住む（過密。_crowd_in。回5）。
        """
        defs, config = ctx.defs, ctx.config
        share = fac.labor_share(defs, island)
        if share <= 0:
            for pos, count in (unhoused or {}).items():
                island.tile(*pos).population += count  # 働く場のない島: 生まれたマスに詰めて住む
            return
        free = {
            pos: fac.housing_capacity(defs, f) - island.tile(*pos).population
            for pos, f in homes.items()
        }
        pull: dict[Pos, int] = {}
        needed = 0
        for site, radius, missing, _ in vacancies_of(defs, island, config, ctx.turn):
            persons = int(missing / share)
            needed += persons
            for pos in within(island, site, radius):
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
                    left_island += self._crowd_in(defs, island, homes, free, source, count)
                else:
                    island.tile(*source).population += count  # 移る先がなければ元のマスに残る
        for source, count in sorted((unhoused or {}).items(), key=lambda kv: (kv[0][1], kv[0][0])):
            for pos in targets:
                if count <= 0:
                    break
                move = min(count, free[pos])
                if move <= 0:
                    continue
                island.tile(*pos).population += move
                free[pos] -= move
                count -= move
            if count > 0:
                left_island += self._crowd_in(defs, island, homes, free, source, count)
        if left_island:
            island.log.append(LogEntry("residents_left", {"count": left_island}))
        self._return_to_towns(island, ctx, rng, free, pull, needed)
        self._flee_famine(island, ctx, rng, free)

    @staticmethod
    def _crowd_in(
        defs: Defs,
        island: IslandState,
        homes: dict[Pos, Facility],
        free: dict[Pos, int],
        source: Pos,
        count: int,
    ) -> int:
        """空いた町のない、住む所の上限を超えた count 人を詰めて住ませ（過密。回5）、住ませられな
        かった人数（島に住宅地が1つもない）を返す。

        住宅地のマスの人は元のマスに残る。ただし自給の農業をする村が、村のままでいられる人数
        （village_size）を超える分は、いちばん近い自給でない住宅地（町）へ移る（土地の足りない農村から
        あふれた人が町へ出る。19世紀の都市への流入。村が人数で町と見られて自給をやめる崖を避ける）。
        住む所のないマス（住宅地が壊れたなど）の人は、いちばん近い住宅地へ移る。
        """
        if not homes:
            return count
        tile = island.tile(*source)
        if source in homes:
            stay = count
            if _built_farming(defs, homes[source]):
                stay = min(count, max(0, village_size(defs) - tile.population))
            tile.population += stay
            free[source] -= stay
            count -= stay
            if count <= 0:
                return 0
            # 行き先は人数でも町と見られる（自給をしていない）町。なければ建った段階が町の住宅地
            # （人数が少なく自給をしている町へ押し出して、そこの人が一度に自給をやめないように）
            towns = [p for p in homes if p != source and not _farming(defs, island.tile(*p))]
            towns = towns or [p for p, f in homes.items() if not _built_farming(defs, f)]
            if not towns:
                tile.population += count  # 町のない島: 村に残る
                free[source] -= count
                return 0
            places = towns
        else:
            places = list(homes)
        target = min(places, key=lambda p: (distance(source, p), p[1], p[0]))
        island.tile(*target).population += count
        free[target] -= count
        return 0

    @staticmethod
    def _return_to_towns(
        island: IslandState,
        ctx: StageContext,
        rng: random.Random,
        free: dict[Pos, int],
        pull: dict[Pos, int],
        needed: int,
    ) -> int:
        """飢饉で村へ逃れた人（tile.ext["fled"]）が、町で食べ物が買えるようになり、町に人手の足りない
        仕事があれば、1ターンに population.hunger.return_share の割合ずつ、人手の足りない所に近い
        空いた町へ戻る（回7。W-21。村の人は失業者に数えないので、仕事のない人の移り住みでは戻らな
        かった）。戻った人数を返す。現実でも、飢饉や内戦が終わると、逃れた人は何年かかけて町へ戻る
        （ペトログラードの人口は内戦の後の数年で戻り始めた）。"""
        settings = ctx.config.get("population.hunger")
        households = island.economy.households
        if any(
            1 - h.get(FED, fac.FULL) / fac.FULL > settings["tolerance"]
            for h in households.values()
            if h.get("persons", 0) > 0
        ):
            return 0
        share = fac.labor_share(ctx.defs, island)
        targets = sorted(
            (
                pos
                for pos, n in pull.items()
                if n > 0 and free.get(pos, 0) > 0 and not _farming(ctx.defs, island.tile(*pos))
            ),
            key=lambda pos: (-pull[pos], pos[1], pos[0]),
        )
        room = int(needed / share) if share > 0 else 0
        moved = 0
        for tile in sorted(island.tiles, key=lambda t: (t.y, t.x)):
            fled = min(tile.ext.get(FLED, 0), tile.population)
            if fled <= 0:
                tile.ext.pop(FLED, None)
                continue
            count = min(fled, max(1, _rand_round(fled * settings["return_share"], rng)))
            back = 0
            for pos in targets:
                if count <= 0 or room <= 0:
                    break
                move = min(count, free[pos], pull[pos], room)
                if move <= 0:
                    continue
                island.tile(*pos).population += move
                free[pos] -= move
                pull[pos] -= move
                room -= move
                count -= move
                back += move
            tile.population -= back
            moved += back
            left = fled - back
            if left > 0:
                tile.ext[FLED] = left
            else:
                tile.ext.pop(FLED, None)
        if moved:
            island.stats["population.returned_to_towns"] = moved
        return moved

    @staticmethod
    def _flee_famine(
        island: IslandState, ctx: StageContext, rng: random.Random, free: dict[Pos, int]
    ) -> int:
        """食べ物が買えない町の人が、空きのある村へ移る（回4）。移った人数を返す。

        失業している家計と一般の家計のそれぞれで、飢えの蓄えが population.hunger.flee を超えている間
        （家の蓄えを食べ尽くすまでは移らない）、このターンに食べられなかった割合（体が耐えられる少しの
        不足 population.hunger.tolerance までなら移らない）の町の人（失業している家計はマスの仕事の
        ない人の家族、一般の家計は残りの人）が、近い村（自給の農業ができる集落）から、住む所の空き
        （free）と、移った先が村のままでいられる人数（village_size）の分まで移る。移った人は村で自給の
        農業をする（現実の飢饉でも、町の人は食べ物のある農村へ逃れた。ロシアの内戦期の飢えでは、
        ペトログラードの人口が3年で3分の1ほどになった）。家計ごとに見るのは、先に飢えるのは仕事の
        ない家計で、一般の家計が食べられているうちに逃れるため（一般の家計だけで見ていたころは、国営の
        農場が賃金を払えずに失業した人が、町にとどまって飢えた）。
        """
        defs, config = ctx.defs, ctx.config
        settings = config.get("population.hunger")
        households = island.economy.households
        hungry = {}  # 家計 → 食べられなかった割合（逃れる家計だけ）
        for name, h in households.items():
            short = 1 - h.get(FED, fac.FULL) / fac.FULL
            if short > settings["tolerance"] and h.get(HUNGER, 0.0) >= settings["flee"]:
                hungry[name] = short
        if not hungry:
            return 0

        def farming(pos: Pos) -> bool:
            return _farming(defs, island.tile(*pos))

        family = jobless_family(defs, island)

        # 移った先が村のままでいられる人数まで（回4。人の減った町の住宅地も人数では村に数えるので、
        # そこへ逃れると人が増えてまた町に戻り、市場で食べ物を買うことになって、逃れた人が飢えた）
        size = village_size(defs)

        def room(pos: Pos) -> int:
            return min(free[pos], size - island.tile(*pos).population)

        villages = [pos for pos in sorted(free) if farming(pos) and room(pos) > 0]
        moved = 0
        moved_jobless = 0.0  # 移った人のうち、仕事のない人とその家族（ログに出す）
        for tile in sorted(island.tiles, key=lambda t: (t.y, t.x)):
            pos = (tile.x, tile.y)
            if tile.population <= 0 or farming(pos) or not villages:
                continue
            idle = min(tile.population, tile_jobless(defs, island, tile) * family)
            idle_leaving = idle * hungry.get(JOBLESS, 0.0)
            leaving = idle_leaving + (tile.population - idle) * hungry.get(GENERAL, 0.0)
            count = min(tile.population, _rand_round(leaving, rng))
            idle_part = idle_leaving / leaving if leaving > 0 else 0.0
            for target in sorted(villages, key=lambda v: (distance(pos, v), v[1], v[0])):
                if count <= 0:
                    break
                move = min(count, room(target))
                if move <= 0:
                    continue
                tile.population -= move
                village = island.tile(*target)
                village.population += move
                village.ext[FLED] = village.ext.get(FLED, 0) + move  # 町へ戻るとき（回7）
                free[target] -= move
                count -= move
                moved += move
                moved_jobless += move * idle_part
            villages = [v for v in villages if room(v) > 0]
        if moved:
            island.stats["population.fled_to_villages"] = moved
            jobless = min(moved, round(moved_jobless))
            if jobless > 0:  # 仕事のない人の家族がいれば、その人数も書く（失業率から外れるため）
                island.log.append(
                    LogEntry("famine_flight_jobless", {"count": moved, "jobless": jobless})
                )
            else:
                island.log.append(LogEntry("famine_flight", {"count": moved}))
        return moved


def _round_shares(total: int, shares: dict[str, float]) -> dict[str, int]:
    """total 人を、層ごとの小数の人数 shares の比で整数に分ける（端数は大きい順）。"""
    order = sorted(shares.items(), key=lambda kv: (-kv[1], kv[0]))
    weights = [(name, round(value * 1000)) for name, value in order if value > 0]
    if total <= 0 or not weights:
        return {}
    return {n: c for n, c in proportional(total, weights).items() if c > 0}


def _facts(f: dict[str, Any]) -> dict[str, Any]:
    """層の食べた割合などを、幸福度の式の facts の形にする。"""
    return {
        "food": f["food"],
        "quality": f["quality"],
        "consumer_goods": f["consumer_goods"],
        "clothing": f["clothing"],
        "luxury": f["luxury"],
        HUNGER: f.get(HUNGER, 0.0),
        "arrears": f.get("arrears", 0.0),
    }


def _mix(
    general: dict[str, Any], jobless: dict[str, Any] | None, workers: int, idle: int
) -> dict[str, Any]:
    """仕事のある家計と失業している家計の facts を、マスに住む人数で混ぜる。"""
    a = _facts(general)
    if jobless is None or idle <= 0:
        return a
    b = _facts(jobless)
    total = workers + idle
    if total <= 0:
        return a
    out: dict[str, Any] = {}
    for key, value in a.items():
        mixed = (value * workers + b[key] * idle) / total
        out[key] = round(mixed) if isinstance(value, int) else mixed
    return out


def remove_dead(island: IslandState, defs: Defs, deaths: dict[str, int]) -> None:
    """亡くなった人を層の人数から引く（技術者は働き手の数も。預金は残った家族が持つ）。
    層の人数を決め直す（split_persons）前に呼ぶ。"""
    households = island.economy.households
    for name, count in sorted(deaths.items()):
        h = households.get(name)
        if h is None or count <= 0:
            continue
        persons = h["persons"]
        kind = h.get("workers")
        if kind:
            pool = island.economy.workers.get(kind, 0)
            gone = min(pool, pool * count // persons) if persons > 0 else 0
            if gone > 0:
                island.economy.workers[kind] = pool - gone
                if kind == fac.ENGINEER:
                    recount_engineers(island, pool, 0, gone)
        h["persons"] = max(0, persons - count)


class Housing(IslandStage):
    """都市の発展: 人手の足りない施設の通勤圏で住む所が足りなければ、町に床を足すか新しく作る。
    望む広さに足りない層は、足りない床の一部を毎ターン買って住むマスに足す（住み替え。W-201）。

    床を足す・作るには、その床の資材（settlements.yaml の materials を段階の幅で割ったもの）を、
    家計が市場で買った資材（economy.housing["stock"]）から使う。足りなければ要る資材を
    economy.housing["want"]（人手の足りない所の分。一般の層が買う）と economy.housing["move"]
    （住み替えの分。層ごと）に書き、次のターンの市場で買う。2ターン続けて足りなければ知らせる。
    """

    name = "housing"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs, config = ctx.defs, ctx.config
        h = config.get("housing")
        share = fac.labor_share(defs, island)
        housing = island.economy.housing
        housing.setdefault("stock", {})
        housing["want"] = {}
        housing["move"] = {}
        public = pub.planned(island)  # 計画経済の国は、国が建てる（床は国の床。PO-56）
        pub.state(island)["want"] = {}
        homes = homes_of(island, config)
        kind = fac.housing_kind(config)
        levels = defs.settlement_levels()
        if island.has_economy:
            self._maintain(island, defs, h, homes)
        status = dwelling_status(defs, config, island, homes)
        if island.has_economy:
            self._collect_rent(island, config, homes, status)
        general = desired_space(island, config, GENERAL)
        min_space = h["space"]["min"]
        built = grown = 0
        short: set[str] = set()  # 足りなくて町が育たなかった資源
        dry: set[Pos] = set()  # 上下水道が足りなくて育たなかった町のマス
        vacancies = sorted(
            vacancies_of(defs, island, config, ctx.turn),
            key=lambda v: (-v[2], v[3], v[0][1], v[0][0]),
        )
        self._run_works(island, defs, config, h, homes, status, dry, short)
        for site, radius, missing, _ in vacancies:
            if built >= h["max_new_per_turn"] and grown >= h["max_upgrades_per_turn"]:
                break
            area = within(island, site, radius)
            near = [pos for pos in area if pos in homes]
            # 空いている町に入れる人（望む広さで使う床の空き）と、もう近くに住んでいて仕事のない人で
            # 足りれば育てない（PO-17）
            room = sum(_room(status[pos], island.tile(*pos).population, general) for pos in near)
            idle = sum(_idle(defs, island, island.tile(*pos)) for pos in near)
            if share <= 0 or room * share + idle >= missing:
                continue
            if grown < h["max_upgrades_per_turn"]:
                land_open = self._empty_site(island, defs, kind, area) is not None
                pos = self._growable(
                    island, defs, homes, near, status, h["fill_ratio"], dry, land_open
                )
                if pos is not None:
                    home = homes[pos]
                    floor = fac.floor_of(defs, home)
                    target = _next_floor(defs, floor, h["floor_step"])
                    missing = self._pay_goods(
                        island, fac.floor_materials(defs, floor, target), public
                    )
                    if not missing:
                        self._add_floor(island, defs, home, target, public)
                        grown += 1
                        continue
                    short.update(missing)
            if built < h["max_new_per_turn"]:
                pos = self._empty_site(island, defs, kind, area)
                if pos is not None:
                    missing = self._pay(island, defs, levels[0], public)
                    if not missing:
                        homes[pos] = self._new_village(island, defs, kind, pos, levels[0], public)
                        status[pos] = _with_home(defs, config, status.get(pos), homes[pos])
                        built += 1
                    short.update(missing)
        if built < h["max_new_per_turn"]:
            left = h["max_new_per_turn"] - built
            self._settle(island, defs, h, homes, kind, levels[0], left, short, public)
        for pos, home in homes.items():  # このターンに建った村
            if pos not in status or status[pos].nominal != fac.floor_of(defs, home) * min_space:
                status[pos] = _with_home(defs, config, status.get(pos), home)
        if island.has_economy and not public:  # 計画経済の家計は住宅の資材を買わない
            self._relocate(island, ctx, h, homes, status, dry)
        self._watch_water(island, defs, homes, dry)
        self._record(island, ctx, homes)
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
    def _record(island: IslandState, ctx: StageContext, homes: dict[Pos, Facility]) -> None:
        """住まいの広さの統計（W-201）: 町の延べ床の合計（最低の広さで住める人数）と、1 人あたりの
        広さ・住む人が望む広さ（㎡）。"""
        people = sum(t.population for t in island.tiles)
        if people <= 0:
            return
        status = dwelling_status(ctx.defs, ctx.config, island, homes)
        stats = island.stats
        stats["housing.floor"] = sum(
            fac.nominal_housing_capacity(ctx.defs, h) for h in homes.values()
        )
        stats["housing.space"] = sum(d.floor for d in status.values()) / people
        stats["housing.want"] = sum(d.want for d in status.values()) / people

    def _settle(
        self,
        island: IslandState,
        defs: Defs,
        h: dict[str, Any],
        homes: dict[Pos, Facility],
        kind: str,
        level: str,
        limit: int,
        short: set[str],
        public: bool = False,
    ) -> None:
        """分家と開拓（回5、10-19 のカード「分家と開拓」）: 島の上限を超えて住む人が島の空きより
        多ければ、いちばん過密なマスから housing.settle_radius マスの内の空いた平地に、新しい村を
        開く（資材は村の段階の materials。家計が買った資材から。足りなければ次のターンに買う）。
        施設の通勤圏でなくてよい。開いた村には、次のターンの移り住みで上限を超えた人が入る。
        """
        over: dict[Pos, int] = {}
        room = 0
        for tile in island.tiles:
            if tile.population <= 0 and (tile.x, tile.y) not in homes:
                continue
            pos = (tile.x, tile.y)
            capacity = fac.housing_capacity(defs, homes[pos]) if pos in homes else 0
            if tile.population > capacity:
                over[pos] = tile.population - capacity
            else:
                room += capacity - tile.population
        excess = sum(over.values()) - room
        size = defs.settlements[level]["capacity"]
        for pos in sorted(over, key=lambda p: (-over[p], p[1], p[0])):
            if excess <= 0 or limit <= 0:
                return
            site = self._empty_site(island, defs, kind, within(island, pos, h["settle_radius"]))
            if site is None:
                continue
            missing = self._pay(island, defs, level, public)
            if missing:
                short.update(missing)
                return
            homes[site] = self._new_village(island, defs, kind, site, level, public)
            island.log.append(LogEntry("village_founded", {"x": site[0], "y": site[1]}))
            excess -= size
            limit -= 1

    @classmethod
    def _pay(cls, island: IslandState, defs: Defs, level: str, public: bool = False) -> list[str]:
        """段階 level の床を丸ごと新しく建てる資材（村が建つとき）を、家計が買った資材（public なら
        国が買った資材）から使う。"""
        floor = defs.settlements[level]["capacity"]
        return cls._pay_goods(island, fac.floor_materials(defs, 0, floor), public)

    @staticmethod
    def _pay_goods(island: IslandState, need: dict[str, int], public: bool = False) -> list[str]:
        """資材 need を、家計が買った資材（public なら国が住宅費で買った資材）から使う。

        足りなければ何も使わず、要る資材を次のターンに買う分（want）に足して、足りない資材を返す。
        """
        housing = pub.state(island) if public else island.economy.housing
        held = housing["stock"]
        missing = [g for g, q in sorted(need.items()) if stock.total(held, g) < q]
        if not missing:
            for good, qty in sorted(need.items()):
                stock.take(held, good, qty)
        else:  # 次のターンに買うのは1か所分まで（育たなかった町の数だけ積み上げない。回3）
            want = housing["want"]
            for good, qty in sorted(need.items()):
                want[good] = max(want.get(good, 0), qty)
        return missing

    @staticmethod
    def _new_village(
        island: IslandState, defs: Defs, kind: str, pos: Pos, level: str, public: bool
    ) -> Facility:
        """新しい村を建てる。public なら床はみな国の床。"""
        home = fac.new_housing(island, kind, pos, level)
        if public:
            pub.set_floor(home, fac.floor_of(defs, home))
        return home

    @staticmethod
    def _add_floor(
        island: IslandState, defs: Defs, home: Facility, floor: int, public: bool = False
    ) -> None:
        """町の床を floor にする（public なら足した分は国の床）。段階が上がったターンに知らせる
        （見た目も変わる）。"""
        before = home.ext.get("level")
        added = floor - fac.floor_of(defs, home)
        fac.set_floor(defs, home, floor)
        if public and added > 0:
            pub.set_floor(home, pub.floor_of(home) + added)
        if home.ext["level"] != before and before is not None:
            args = {"x": home.x, "y": home.y, "level": home.ext["level"]}
            island.log.append(LogEntry("settlement_grew", args))

    @staticmethod
    def _maintain(
        island: IslandState,
        defs: Defs,
        h: dict[str, Any],
        homes: dict[Pos, Facility],
    ) -> None:
        """家の傷みと手入れ（回3）: 町の家は年に housing.wear ずつ傷む。傷んだ分の資材（持っている
        床を新しく建てる資材 × 傷んだ割合）を、家計が買った資材から先に使って直す。足りない分
        は economy.housing["repair"] に書き、次のターンの市場で一般の層が預金から買う。傷みが
        housing.livable を下回った町は、住める上限がその割合で下がる。"""
        full = fac.HOUSING_FULL
        step = round(h["wear"] * full / YEAR)
        owners = (
            (island.economy.housing["stock"], {}),  # 家計の床の手入れ
            (pub.state(island)["stock"], {}),  # 国の床の手入れ（住宅費で買った資材から）
        )
        order = sorted(
            homes.items(),
            key=lambda kv: (kv[1].economy.get(fac.HOUSING_CONDITION, full), kv[0][1], kv[0][0]),
        )
        for _, home in order:
            cond = max(0, home.economy.get(fac.HOUSING_CONDITION, full) - step)
            lost = full - cond
            floor = fac.floor_of(defs, home)
            state_part = pub.share(home, floor)
            mended = 0.0
            for (held, repair), part in zip(owners, (1 - state_part, state_part), strict=True):
                need = {
                    g: int(q * lost * part) // full
                    for g, q in fac.housing_materials(defs, floor).items()
                }
                need = {g: q for g, q in need.items() if q > 0}
                share = 1.0
                if need:
                    share = min(1.0, *(stock.total(held, g) / q for g, q in need.items()))
                    for good, qty in sorted(need.items()):
                        used = int(qty * share)
                        stock.take(held, good, used)
                        if used < qty:
                            repair[good] = repair.get(good, 0) + qty - used
                mended += part * share
            cond += int(lost * mended)
            home.economy[fac.HOUSING_CONDITION] = cond
            livable = round(h["livable"] * full)
            home.economy[fac.HOUSING_ROOM] = min(fac.FULL, cond * fac.FULL // max(1, livable))
        island.economy.housing["repair"] = owners[0][1]
        pub.state(island)["repair"] = owners[1][1]

    @staticmethod
    def _collect_rent(
        island: IslandState,
        config: GameConfig,
        homes: dict[Pos, Facility],
        status: dict[Pos, Dwelling],
    ) -> None:
        """国の床の家賃（PO-55）: 国の床は所得の低い層から入り、使われている国の床 1 人分につき
        housing.public_rent を毎ターン国庫に払う。払えない分は減免する（国の未収にしない）。"""
        min_space = config.get("housing.space")["min"]
        used: dict[str, float] = {}
        for pos, home in homes.items():
            units = pub.floor_of(home)
            if units <= 0 or pos not in status:
                continue
            for name, taken in pub.tenants(status[pos].layers, units, min_space).items():
                used[name] = used.get(name, 0.0) + taken
        paid = 0
        for name, due in sorted(pub.rent(config, used).items()):
            if name not in island.economy.households:
                continue
            amount = min(due, ledger.money(island, household(name)))
            if amount > 0:
                ledger.pay(island, household(name), ledger.GOV, amount, pub.RENT)
                paid += amount
        island.stats["housing.rent"] = paid
        island.stats["housing.public_floor"] = pub.tile_total(island)

    def _run_works(
        self,
        island: IslandState,
        defs: Defs,
        config: GameConfig,
        h: dict[str, Any],
        homes: dict[Pos, Facility],
        status: dict[Pos, Dwelling],
        dry: set[Pos],
        short: set[str],
    ) -> None:
        """命令 build_housing の工事の列（PO-55）を、国が住宅費で買った資材で進める。1つの工事は
        1ターンに床を housing.floor_step だけ足す（目標の床まで）。通勤圏に空いた平地があっても
        高層へ育つ。上下水道が足りない段階には入れず、資材が足りなければ次のターンに買う。
        開発を止めた・ほかの施設が建ったマスの工事は取り下げる。"""
        works = pub.works_pending(island)
        if not works:
            return
        kind = fac.housing_kind(config)
        keep = []
        for work in works:
            pos = (work["x"], work["y"])
            home = homes.get(pos)
            tile = island.tile(*pos)
            if home is None:
                if not self._site_open(island, defs, kind, pos):
                    island.log.append(LogEntry("housing_works_dropped", {"x": pos[0], "y": pos[1]}))
                    continue
                home = fac.new_housing(island, kind, pos, defs.settlement_levels()[0])
                fac.set_floor(defs, home, 0)
                homes[pos] = home
                status[pos] = _with_home(defs, config, status.get(pos), home)
            floor = fac.floor_of(defs, home)
            target = min(int(work["floor"]), fac.max_floor(defs))
            if floor >= target:
                island.log.append(LogEntry("housing_works_done", {"x": pos[0], "y": pos[1]}))
                continue
            step = min(target, _next_floor(defs, floor, h["floor_step"]))
            upper = fac.level_after(defs, floor)
            if upper != fac.level_of_floor(defs, floor) and island.has_economy:
                need_water = round(defs.settlements[upper].get("water", 0) * 1000)
                if infra.water(defs, tile) < need_water:
                    dry.add(pos)
                    keep.append(work)
                    continue
            missing = self._pay_goods(island, fac.floor_materials(defs, floor, step), True)
            if missing:
                short.update(missing)
                keep.append(work)
                continue
            self._add_floor(island, defs, home, step, True)
            status[pos] = _with_home(defs, config, status.get(pos), home)
            if step < target:
                keep.append(work)
            else:
                island.log.append(LogEntry("housing_works_done", {"x": pos[0], "y": pos[1]}))
        pub.state(island)["works"] = keep

    @staticmethod
    def _site_open(island: IslandState, defs: Defs, kind: str, pos: Pos) -> bool:
        """命令の工事で新しく町を置けるマスか（町を置ける地形で、開発を止めておらず、施設がない）。"""
        tile = island.tile(*pos)
        return (
            tile.terrain in defs.facilities[kind]["terrain"]
            and tile.zone == ZONE_NONE
            and island.facility_at(*pos) is None
        )

    @staticmethod
    def _growable(
        island: IslandState,
        defs: Defs,
        homes: dict[Pos, Facility],
        near: list[Pos],
        status: dict[Pos, Dwelling],
        fill_ratio: float,
        dry: set[Pos],
        land_open: bool = True,
    ) -> Pos | None:
        """床を足す町のマス。住む人が望む広さで使う床が今の床の fill_ratio を超えた、最上段でない
        マス。次の段階に入るのに要る上下水道（settlements.yaml の water）が足りないマスは飛ばして
        dry に入れる（お金の経済の島だけ）。land_short の段階（高層）へは、通勤圏に空いた平地が
        あれば（land_open）育たない（横に広がる。PO-54）。"""
        for pos in near:
            floor = fac.floor_of(defs, homes[pos])
            upper = fac.level_after(defs, floor)
            if upper is None:
                continue
            if status[pos].want < status[pos].nominal * fill_ratio:
                continue
            if upper != fac.level_of_floor(defs, floor):
                if land_open and defs.settlements[upper].get("land_short"):
                    continue
                need = defs.settlements[upper].get("water", 0)
                if island.has_economy and infra.water(defs, island.tile(*pos)) < round(need * 1000):
                    dry.add(pos)
                    continue
            return pos
        return None

    @staticmethod
    def _relocate(
        island: IslandState,
        ctx: StageContext,
        h: dict[str, Any],
        homes: dict[Pos, Facility],
        status: dict[Pos, Dwelling],
        dry: set[Pos],
    ) -> None:
        """住み替え（W-201）: 望む広さに足りない層は、足りない床の housing.space.move_rate（毎年
        引っ越すか建て替える世帯の割合）を、毎ターンの 12 分の 1 ずつ、住むマスに足す。失業している
        家計は買わない（広さの足りなさは幸福度に残る）。床は家計が買った資材から足し、足りなければ
        要る資材を economy.housing["move"]（層ごと）に書いて次のターンに買う。広すぎても売らない。
        """
        defs = ctx.defs
        spec = h["space"]
        rate = spec["move_rate"] / YEAR
        top = fac.max_floor(defs)
        kind = fac.housing_kind(ctx.config)
        radius = ctx.config.get("labor.commute_radius")
        housing = island.economy.housing
        held = housing["stock"]
        move: dict[str, dict[str, int]] = housing["move"]
        order = sorted(
            (pos for pos, d in status.items() if pos in homes and d.want > d.nominal),
            key=lambda p: (-(status[p].want - status[p].nominal) / status[p].want, p[1], p[0]),
        )
        for pos in order:
            d, home = status[pos], homes[pos]
            buyers = {n: w for n, w in d.layers.items() if n != JOBLESS and w > 0}
            floor = fac.floor_of(defs, home)
            if not buyers or floor >= top:
                home.ext.pop(fac.FLOOR_DUE, None)
                continue
            lack = (d.want - d.nominal) / spec["min"] * sum(buyers.values()) / d.want
            # 買えていない分は年の分までためる（資材が売りに出るまでの間に積み上げすぎない）
            due = min(home.ext.get(fac.FLOOR_DUE, 0.0) + lack * rate, lack * spec["move_rate"])
            level = fac.level_of_floor(defs, floor)
            upper = fac.level_after(defs, floor)
            room = top - floor
            if upper != level:  # 段階の上限にいる: 次の段階に入るのに上下水道が要る
                if defs.settlements[upper].get("land_short") and Housing._empty_site(
                    island, defs, kind, within(island, pos, radius)
                ):
                    home.ext[fac.FLOOR_DUE] = round(due, 6)  # 空いた平地があるうちは高く建てない
                    continue
                need_water = defs.settlements[upper].get("water", 0)
                if island.has_economy and infra.water(defs, island.tile(*pos)) < round(
                    need_water * 1000
                ):
                    dry.add(pos)
                    home.ext[fac.FLOOR_DUE] = round(due, 6)
                    continue
            else:  # 1回に段階をまたがない
                room = min(room, defs.settlements[level]["capacity"] - floor)
            count = min(int(due), room)
            if count > 0:
                need = fac.floor_materials(defs, floor, floor + count)
                have = min(1.0, *(stock.total(held, g) / q for g, q in need.items() if q > 0))
                bought = int(count * have)
                if bought > 0:
                    for good, qty in sorted(
                        fac.floor_materials(defs, floor, floor + bought).items()
                    ):
                        stock.take(held, good, qty)
                    Housing._add_floor(island, defs, home, floor + bought)
                    floor += bought
                    due -= bought
                    room -= bought
            # 足りなかった分を、次のターンに買う
            count = min(int(due), room)
            if count > 0:
                total = sum(buyers.values())
                for good, qty in sorted(fac.floor_materials(defs, floor, floor + count).items()):
                    for layer, weight in sorted(buyers.items()):
                        part = int(qty * weight / total)
                        if part > 0:
                            wanted = move.setdefault(layer, {})
                            wanted[good] = wanted.get(good, 0) + part
            home.ext[fac.FLOOR_DUE] = round(due, 6)

    @staticmethod
    def _watch_water(
        island: IslandState, defs: Defs, homes: dict[Pos, Facility], dry: set[Pos]
    ) -> None:
        """上下水道が足りなくて育たなかった町を、マスごとに1度だけ知らせる（整備度が足りたら印を
        外す）。印 ext["water_short"] は、育つのに要る整備度（1000 分の 1）。"""
        for pos, home in sorted(homes.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            upper = fac.level_after(defs, fac.floor_of(defs, home))
            if upper is None:
                home.ext.pop("water_short", None)
                continue
            need = round(defs.settlements[upper].get("water", 0) * 1000)
            if pos in dry and not home.ext.get("water_short"):
                home.ext["water_short"] = need
                now = infra.water(defs, island.tile(*pos))
                args = {"x": pos[0], "y": pos[1], "water": now / 10, "need": need / 10}
                island.log.append(LogEntry("settlement_needs_water", args))
            elif infra.water(defs, island.tile(*pos)) >= need:
                home.ext.pop("water_short", None)

    @staticmethod
    def _empty_site(island: IslandState, defs: Defs, kind: str, area: list[Pos]) -> Pos | None:
        terrains = defs.facilities[kind]["terrain"]
        occupied = island.facilities_by_tile()
        for pos in area:
            tile = island.tile(*pos)
            if tile.terrain in terrains and tile.zone == ZONE_NONE and pos not in occupied:
                return pos
        return None


def _room(d: Dwelling, population: int, general: float) -> float:
    """町のマスの、望む広さで数えた空き（人数。W-190 の PO-17）: 床 ÷ 住む人の望む広さ − 住む人。"""
    per_person = d.want / population if population > 0 else general
    return max(0.0, d.floor / per_person - population) if per_person > 0 else 0.0


def _with_home(defs: Defs, config: GameConfig, old: Dwelling | None, home: Facility) -> Dwelling:
    """建ったばかりの町の床を、そこに住む人の望み（なければ 0）に合わせた Dwelling にする。"""
    min_space = config.get("housing.space")["min"]
    want = old.want if old else 0.0
    layers = old.layers if old else {}
    floor = fac.housing_capacity(defs, home) * min_space
    return Dwelling(floor, fac.nominal_housing_capacity(defs, home) * min_space, want, layers)


def _next_floor(defs: Defs, floor: int, step: float) -> int:
    """床 floor から 1 回で足したあとの床。足す段階の幅の step の割合で、段階の上限を超えない。"""
    for _, low, high in fac.level_bounds(defs):
        if floor < high:
            return min(high, floor + max(1, math.ceil(step * (high - low))))
    return floor


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


# --- 命令 build_housing（PO-55）-----------------------------------------------------


def _housing_target(defs: Defs, island: IslandState, payload: dict[str, Any]) -> tuple[Pos, int]:
    """命令 build_housing の場所と目標の床。町のマスか、町を置ける空いた平地を選び、足す床の数
    （floors）か、床を届かせる段階（level。その段階の上限まで）のどちらか1つを決める。"""
    pos = (payload["x"], payload["y"])
    if not (0 <= pos[0] < island.size and 0 <= pos[1] < island.size):
        raise OrderFailed("reason_out_of_bounds")
    if ("floors" in payload) == ("level" in payload):
        raise OrderFailed("reason_housing_target")
    level = payload.get("level")
    if level is not None and level not in defs.settlements:
        raise OrderFailed("reason_housing_target")
    kind = next(k for k, f in defs.facilities.items() if f.get("category") == "housing")
    home = island.facility_at(*pos)
    if home is not None and home.kind != kind:
        raise OrderFailed("reason_tile_occupied")
    tile = island.tile(*pos)
    if home is None and (
        tile.terrain not in defs.facilities[kind]["terrain"] or tile.zone != ZONE_NONE
    ):
        raise OrderFailed("reason_housing_site", x=pos[0], y=pos[1])
    floor = fac.floor_of(defs, home) if home is not None else 0
    target = floor + payload["floors"] if level is None else defs.settlements[level]["capacity"]
    top = fac.max_floor(defs)
    if floor >= top:
        raise OrderFailed("reason_housing_top", x=pos[0], y=pos[1])
    if target <= floor:
        raise OrderFailed("reason_housing_enough", x=pos[0], y=pos[1])
    return pos, min(target, top)


def check_build_housing(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _housing_target(defs, island, payload)


def build_housing(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """国の床を足す工事を列に並べる（同じマスの工事は置き換える）。資材は国が住宅費で市場で買い、
    工事の列は住宅の段（Housing）が 1 ターンに床を housing.floor_step ずつ進める。"""
    pos, target = _housing_target(ctx.defs, island, payload)
    works = [w for w in pub.works_pending(island) if (w["x"], w["y"]) != pos]
    works.append({"x": pos[0], "y": pos[1], "floor": target})
    pub.state(island)["works"] = works
    island.log.append(
        LogEntry("housing_works_started", {"x": pos[0], "y": pos[1], "floor": target})
    )


def register(registry: Registry) -> None:
    registry.add_stage(Population())
    registry.add_stage(Housing())
    registry.add_order(
        OrderHandler("build_housing", execute=build_housing, check=check_build_housing)
    )
    registry.add_formula("food_need", food_need)
    registry.add_formula("happiness", happiness)
    registry.add_formula("hunger_deaths", hunger_deaths)
    registry.add_formula("birth_rate", birth_rate)
    registry.add_formula("death_rate", death_rate)
    registry.add_check(check_population_defs)
