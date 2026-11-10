"""家計の必需品・ぜいたく品・耐久消費財（フェーズ3-2）。

財の定義の need で、家計にとっての種類を分けます（goods.yaml）。

- 必需品（necessity）: 食べ物・電力・衣料。必需品に使う額の上限は、使う額に対する割合で
  household.necessity の share から、実質の使う額（1人あたり、物価指数で割る）が base_income の
  倍になるごとに fall の割合だけ下がります（下限 floor）。上限は、安い食料との差の分だけ質の
  高い食料に替える予算に効きます（食べる量そのものは減らさない）。
- 衣料（必需品のうち household.targets のある物。LB-44）: 層ごとに持ち物として持ち
  （``households[層]["clothes"]``、財ごとに 1000 分の 1）、1人あたりの目安は household.targets。毎年
  household.clothing.wear の割合が傷み、「傷んだ分 ＋ 目安との差 × catch_up」を、値段が高いほど
  少なく買う曲線（household.price_elasticity、modules/market の elastic_orders）で、必需品の予算の
  household.clothing.share まで買います。高ければ買い替えを延ばし、持つ量が減ります。
- ぜいたく品（luxury）: 消費財・嗜好品。必需品の残りを、いま売りに出ている物の間で
  household.luxury_weights の重みで割り振ります。売りに出ている量で買える額（層の予算の割合の分）を
  超える分は、ほかのぜいたく品に回します（split）。売りに出ていない物は、買いたい量だけ記録します。
- 耐久消費財（durable）: 自動車。層ごとに台数を持ち（``households[層]["cars"]``、1000 分の 1 台）、
  欲しい台数との差の household.cars.catch_up の割合と、傷んで使えなくなる分（年 wear）を買います。
  車は1台1ターンに cars.fuel の燃料を使い、燃料が買えないと、その分だけ車の満足が下がります。

- サービス（service。バランス調整）: 商店・飲食・理美容など。必需品の残りのうち所得に応じた割合を
  使います（economy/services.py の budget_share。注文は modules/market の _service_buys）。

幸福度（modules/population）: 衣料は目安に対して持っている割合、ぜいたく品は種類ごとに「目安に対して
買えた割合（自動車は欲しい台数に対して持っている台数と、燃料の買えた割合の小さい方）」を1で
頭打ちにして、種類の数で平均します（種類が多いほど満足が上がる）。サービスもぜいたく品の種類に数え
ます（目安は household.targets）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from hakoniwa_core import politics
from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import IslandState
from hakoniwa_core.units import QUANTITY_SCALE, round_money, to_milli

NECESSITY = "necessity"
LUXURY = "luxury"
DURABLE = "durable"
SERVICE = "service"  # サービス（バランス調整。economy/services.py）
RETAIL = "retail"  # 小売り（回7。economy/retail.py。品物を買うときに一緒に買う）
CARS = "cars"  # households[層] の、持っている自動車（1000 分の 1 台）
PLAN = "cars_plan"  # island.scratch: 層ごとの、このターンの欲しい台数と燃料の要る量
CLOTHES = "clothes"  # households[層] の、持っている衣料（財ごと、1000 分の 1。LB-44）


def goods_of(defs: Defs, need: str) -> list[str]:
    """need の種類の財（名前の順）。"""
    return sorted(g for g, d in defs.goods.items() if d.get("need") == need)


def real_income(island: IslandState, config: GameConfig, budget: int, persons: int) -> float:
    """層の実質の使う額の、household.base_income に対する倍率（1人あたり、物価指数で割る）。"""
    if persons <= 0:
        return 0.0
    history = island.economy.cpi_history
    cpi = history[-1] / 100 if history and history[-1] > 0 else 1.0
    return budget / persons / cpi / config.get("household.base_income")


def necessity_share(config: GameConfig, income: float) -> float:
    """必需品に使う額の上限（使う額に対する割合）。"""
    settings = config.get("household.necessity")
    if income <= 0:
        return settings["share"]
    value = settings["share"] * (1 - settings["fall"]) ** math.log2(income)
    return max(settings["floor"], min(settings["share"], value))


def target_of(config: GameConfig, good: str) -> float:
    """1人1ターンあたりの目安（household.targets。書いていなければ 0）。"""
    return config.get("household.targets").get(good, 0.0)


def clothing_goods(defs: Defs, config: GameConfig) -> list[str]:
    """持ち物として持つ衣料（必需品のうち、食べ物と電力でなく目安のある物。LB-44）。"""
    power = config.get("power.good")
    return [
        g
        for g in goods_of(defs, NECESSITY)
        if "food" not in defs.goods[g] and g != power and target_of(config, g) > 0
    ]


def per_turn_target(defs: Defs, config: GameConfig, good: str) -> float:
    """1人1ターンあたりのふだんの買う量: 衣料は持つ量の目安のうち傷む分、ほかは目安
    （島で作る量の見込みに使う。autoplan）。"""
    target = target_of(config, good)
    if defs.goods.get(good, {}).get("need") == NECESSITY:
        return target * config.get("household.clothing.wear") / YEAR
    return target


def clothes_held(config: GameConfig, h: Mapping[str, Any], good: str) -> int:
    """層 h の持っている衣料 good（1000 分の 1）。記録のない層（衣料を持ち物にする前の世界と、
    新しい島）は、目安どおり持っていることにする。"""
    held = h.get(CLOTHES, {})
    if good in held:
        return held[good]
    return to_milli(h["persons"] * target_of(config, good))


def clothes_wear(config: GameConfig, held: int) -> int:
    """持っている衣料 held のうち、このターンに傷む量（1000 分の 1）。"""
    return round_money(held * config.get("household.clothing.wear") / YEAR + 0.5)


def clothes_wanted(config: GameConfig, h: Mapping[str, Any], good: str) -> tuple[int, int]:
    """このターンに真ん中の値段で買いたい衣料と、目安まで全部埋める量（どちらも傷む分を含む）。
    目安より多く持っている層（人が減った層）は、傷んだ分から多い分を引いて買う（多い分が傷んで
    減るまで買い替えない）。"""
    held = clothes_held(config, h, good)
    full = to_milli(h["persons"] * target_of(config, good))
    wear = max(0, clothes_wear(config, held) - max(0, held - full))
    gap = max(0, full - held)
    catch = round_money(gap * config.get("household.clothing.catch_up") + 0.5)
    return wear + min(gap, catch), wear + gap


def close_clothes(island: IslandState, config: GameConfig, defs: Defs) -> None:
    """市場の後に、層ごとの持っている衣料を、傷んだ分を引いて買った分を足したものにする。"""
    goods = clothing_goods(defs, config)
    if not goods:
        return
    bought = island.scratch.get("household_goods", {})
    for layer, h in sorted(island.economy.households.items()):
        held = {}
        for good in goods:
            now = clothes_held(config, h, good)
            got = bought.get(layer, {}).get(good, 0)
            held[good] = max(0, now - clothes_wear(config, now)) + got
        h[CLOTHES] = held


def move_clothes(source: dict[str, Any], target: dict[str, Any], moved: int, persons: int) -> None:
    """層を移った moved 人の持っている衣料を、元の層の1人あたりの量で移った先へ移す（persons は
    移る前の元の層の人数。economy/households の move_people）。片方の層にまだ記録がなければ
    （新しく置いた層と、衣料を持ち物にした後の最初の市場の段の前）、記録のある層と同じ1人あたりの
    量を持っていたことにして記録を作る。どちらにもなければ、どちらも目安どおり持っていると見るので
    移さない。"""
    held, into = source.get(CLOTHES), target.get(CLOTHES)
    stayed = max(0, target["persons"] - moved)  # 移った先に前からいた人
    if persons <= 0 or moved <= 0 or (held is None and into is None):
        return
    if held is None:
        rate = {g: q / stayed for g, q in into.items()} if stayed > 0 else {}
        held = source[CLOTHES] = {g: round_money(r * persons) for g, r in rate.items()}
    if into is None:
        into = target[CLOTHES] = {g: q * stayed // persons for g, q in held.items()}
    for good in sorted(held):
        qty = held[good] * moved // persons
        held[good] -= qty
        into[good] = into.get(good, 0) + qty


def centre_price(island: IslandState, config: GameConfig, good: str) -> int:
    """家計が値段の曲線の真ん中に置く値段（いまは「いつもの値段」。お金を刷り放題の件の回3 で
    物価の見通しを入れたら、ここを見通しの値段に差し替える）。"""
    from hakoniwa_core.economy.prices import usual_price

    settings = config.get("household")
    return usual_price(island, good, settings["usual_price_turns"], settings["reprice_turns"])


def elasticity_of(config: GameConfig, good: str) -> float:
    """財 good の値段の弾力性（household.price_elasticity。書いていなければ 1）。"""
    return config.get("household.price_elasticity").get(good, 1.0)


def luxury_weight(config: GameConfig, good: str) -> float:
    return config.get("household.luxury_weights").get(good, 1.0)


def cars_owned(island: IslandState, layer: str) -> int:
    return island.economy.households.get(layer, {}).get(CARS, 0)


def desired_cars(config: GameConfig, layer: str, persons: int, income: float) -> int:
    """層の欲しい台数（1000 分の 1 台）。"""
    spec = config.get("household.cars.layers").get(layer)
    if not spec or persons <= 0:
        return 0
    low, high = spec["from"], spec["full"]
    level = 1.0 if high <= low else (income - low) / (high - low)
    level = max(0.0, min(1.0, level))
    return to_milli(persons * spec["per_person"] * level)


def car_fuel(config: GameConfig, cars: int) -> dict[str, int]:
    """cars 台（1000 分の 1）の車が1ターンに使う燃料（財ごと、1000 分の 1）。"""
    return {g: round_money(cars * q + 0.5) for g, q in config.get("household.cars.fuel").items()}


def wear_per_turn(config: GameConfig, cars: int) -> int:
    """cars 台のうち、このターンに傷んで使えなくなる台数（1000 分の 1）。"""
    return round_money(cars * config.get("household.cars.wear") / YEAR + 0.5)


def cars_wanted(config: GameConfig, owned: int, desired: int) -> int:
    """このターンに買いたい台数: 欲しい台数との差の catch_up の割合と、傷む分。"""
    gap = max(0, desired - owned)
    catch = round_money(gap * config.get("household.cars.catch_up") + 0.5)
    return min(gap + wear_per_turn(config, owned), catch + wear_per_turn(config, owned))


def close_cars(island: IslandState, config: GameConfig) -> None:
    """市場の後に、層ごとの台数を、傷んだ分を引いて買った分を足したものにする。"""
    good = config.get("household.cars.good")
    bought = island.scratch.get("household_goods", {})
    plans = island.scratch.get(PLAN, {})
    total = 0
    for layer, h in sorted(island.economy.households.items()):
        owned = h.get(CARS, 0)
        got = bought.get(layer, {}).get(good, 0)
        owned = max(0, owned - wear_per_turn(config, owned)) + got
        if owned or CARS in h:
            h[CARS] = owned
        total += owned
        plan = plans.get(layer)
        if plan is not None:
            fuel = plan.get("fuel", {})
            used = [
                bought.get(layer, {}).get(g, 0) * QUANTITY_SCALE // need
                for g, need in fuel.items()
                if need > 0
            ]
            plan["fuel_ratio"] = min(QUANTITY_SCALE, sum(used)) if used else QUANTITY_SCALE
    island.stats["cars.households"] = total / QUANTITY_SCALE


def satisfaction(
    island: IslandState, config: GameConfig, defs: Defs, layer: str, persons: int
) -> dict[str, Any]:
    """層の衣料とぜいたく品の満足（1000 分の 1）。市場の段の後に呼ぶ。"""
    goods = island.scratch.get("household_goods", {}).get(layer, {})
    out: dict[str, Any] = {}
    # 衣料は持っている量で見る（買えない月が続くと、持つ量が減って満足が下がる。LB-44）
    h = island.economy.households.get(layer, {"persons": persons})
    out["clothing"] = _average(
        [
            _ratio(clothes_held(config, h, g), persons * target_of(config, g))
            for g in clothing_goods(defs, config)
        ]
    )
    kinds = [
        _ratio(goods.get(g, 0), persons * target_of(config, g))
        for g in goods_of(defs, LUXURY) + goods_of(defs, SERVICE)
    ]
    plan = island.scratch.get(PLAN, {}).get(layer)
    if plan is not None and plan.get("desired", 0) > 0:
        owned = island.economy.households[layer].get(CARS, 0)
        have = min(QUANTITY_SCALE, owned * QUANTITY_SCALE // plan["desired"])
        kinds.append(min(have, plan.get("fuel_ratio", QUANTITY_SCALE)))
    # 計画経済はぜいたく品の種類から来る満足が少ない（体制の特色 luxury_variety。フェーズ3-4）
    variety = politics.trait(island, defs, "luxury_variety", 1.0)
    out["luxury"] = round(_average(kinds) * variety)
    return out


def _ratio(have: int, target: float) -> int:
    need = to_milli(target)
    if need <= 0:
        return QUANTITY_SCALE
    return min(QUANTITY_SCALE, have * QUANTITY_SCALE // need)


def _average(values: list[int]) -> int:
    return sum(values) // len(values) if values else QUANTITY_SCALE


def weights_of(config: GameConfig, goods: list[str]) -> Mapping[str, float]:
    return {g: luxury_weight(config, g) for g in goods}


def split(spend: int, weights: Mapping[str, float], caps: Mapping[str, int]) -> dict[str, int]:
    """ぜいたく品の予算 spend を重みで割り振る。財ごとの上限 caps（売りに出ている量で買える額）を
    超える分は、まだ上限に届かない財に重みで回す。どれも上限に届いたら、残りは重みで足す（買えない
    分は買いたい量として市場に残り、作る施設が増える合図になる）。"""
    out = dict.fromkeys(weights, 0)
    left = spend
    active = [g for g in weights if weights[g] > 0]
    while left > 0 and active:
        total = sum(weights[g] for g in active)
        capped = [g for g in active if out[g] + left * weights[g] / total >= caps.get(g, 0)]
        if not capped:
            for g in active:
                out[g] += int(left * weights[g] / total)
            return out
        for g in capped:
            left -= max(0, caps.get(g, 0) - out[g])
            out[g] = max(out[g], caps.get(g, 0))
            active.remove(g)
    total = sum(weights.values())
    if left > 0 and total > 0:
        for g in weights:
            out[g] += int(left * weights[g] / total)
    return out
