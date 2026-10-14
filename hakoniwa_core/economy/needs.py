"""家計の必需品・ぜいたく品・耐久消費財（フェーズ3-2）。

財の定義の need で、家計にとっての種類を分けます（goods.yaml）。

- 必需品（necessity）: 食べ物・電力・衣料。必需品に使う額の上限は、使う額に対する割合で
  household.necessity の share から、実質の使う額（1人あたり、物価指数で割る）が base_income の
  倍になるごとに fall の割合だけ下がります（下限 floor）。上限は、安い食料との差の分だけ質の
  高い食料に替える予算に効きます（食べる量そのものは減らさない）。衣料は1人あたりの目安
  （household.targets）の量を買います。
- ぜいたく品（luxury）: 消費財・嗜好品。必需品の残りを、いま売りに出ている物の間で
  household.luxury_weights の重みで割り振ります。売りに出ている量で買える額（層の予算の割合の分）を
  超える分は、ほかのぜいたく品に回します（split）。売りに出ていない物は、買いたい量だけ記録します。
- 耐久消費財（durable）: 自動車。層ごとに台数を持ち（``households[層]["cars"]``、1000 分の 1 台）、
  欲しい台数との差の household.cars.catch_up の割合と、傷んで使えなくなる分（年 wear）を買います。
  車は1台1ターンに cars.fuel の燃料を使い、燃料が買えないと、その分だけ車の満足が下がります。

幸福度（modules/population）: 衣料は目安に対して買えた割合、ぜいたく品は種類ごとに「目安に対して
買えた割合（自動車は欲しい台数に対して持っている台数と、燃料の買えた割合の小さい方）」を1で
頭打ちにして、種類の数で平均します（種類が多いほど満足が上がる）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import IslandState
from hakoniwa_core.units import QUANTITY_SCALE, round_money, to_milli

NECESSITY = "necessity"
LUXURY = "luxury"
DURABLE = "durable"
CARS = "cars"  # households[層] の、持っている自動車（1000 分の 1 台）
PLAN = "cars_plan"  # island.scratch: 層ごとの、このターンの欲しい台数と燃料の要る量
YEAR = 12


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
    clothing = [g for g in goods_of(defs, NECESSITY) if target_of(config, g) > 0]
    out["clothing"] = _average(
        [_ratio(goods.get(g, 0), persons * target_of(config, g)) for g in clothing]
    )
    kinds = [
        _ratio(goods.get(g, 0), persons * target_of(config, g)) for g in goods_of(defs, LUXURY)
    ]
    plan = island.scratch.get(PLAN, {}).get(layer)
    if plan is not None and plan.get("desired", 0) > 0:
        owned = island.economy.households[layer].get(CARS, 0)
        have = min(QUANTITY_SCALE, owned * QUANTITY_SCALE // plan["desired"])
        kinds.append(min(have, plan.get("fuel_ratio", QUANTITY_SCALE)))
    out["luxury"] = _average(kinds)
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
