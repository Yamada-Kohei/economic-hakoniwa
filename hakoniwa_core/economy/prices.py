"""値段の計算: 初めの値段（定義のレシピから「原価＋1割」で解く）と、施設の変動費。

原価は、投入財、賃金、設備の利子と減価償却（建設費 ×（年の利率 ÷ 12 ＋ 1 ÷ 減価償却のターン数））
の合計です。建設費は、工事の資材と、工事の人手（1ターンあたりの人数 × ターン数）の賃金です。
作り方が複数あれば安い方を使い、game.yaml の market.initial_prices.reference で施設を決めた財
（電力は火力発電所）はその施設の原価で決めます。追加パックで財を足しても同じ計算で値段が付きます。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import Facility
from hakoniwa_core.units import QUANTITY_SCALE, money_of, part, round_money, scale_price

Prices = Mapping[str, int]


def construction_cost(defs: Defs, kind: str, prices: Prices, wage: float) -> float:
    """施設 kind の建設費（資材の代金と、工事の人手の賃金）。"""
    construction = defs.facilities[kind]["construction"]
    materials = sum(
        prices.get(g, 0) * q / QUANTITY_SCALE for g, q in construction.get("materials", {}).items()
    )
    return materials + construction.get("labor", 0) * construction["turns"] * wage


def capital_rate(config: GameConfig) -> float:
    """1ターンの設備の費用の割合（利子と減価償却）。"""
    rate = config.get("market.initial_prices.capital_rate")
    return rate / 12 + 1 / config.get("firms.depreciation_turns")


def _recipe_cost(
    defs: Defs,
    config: GameConfig,
    kind: str,
    recipe: Mapping[str, Any],
    prices: Prices,
    wage: float,
) -> float:
    """全力で1ターン動いたときの原価。"""
    inputs = sum(prices.get(g, 0) * q / QUANTITY_SCALE for g, q in recipe["inputs"].items())
    labor = fac.workers_needed(defs, kind) * wage
    capital = construction_cost(defs, kind, prices, wage) * capital_rate(config)
    return inputs + labor + capital


def unit_cost(
    defs: Defs,
    config: GameConfig,
    kind: str,
    recipe: Mapping[str, Any],
    prices: Prices,
    wage: float,
) -> float:
    """施設 kind が作り方 recipe で作る物の、1単位あたりの原価（作る物が複数なら量で割り振る）。"""
    total = sum(recipe["outputs"].values())
    if total <= 0:
        return 0.0
    return _recipe_cost(defs, config, kind, recipe, prices, wage) * QUANTITY_SCALE / total


_INITIAL: dict[
    tuple[int, int, tuple[str, ...] | None], tuple[Defs, GameConfig, dict[str, int]]
] = {}


def initial_prices(
    defs: Defs, config: GameConfig, resources: Iterable[str] | None = None
) -> dict[str, int]:
    """財ごとの初めの値段（財 1 単位あたり）。定義と設定（と地下資源）ごとに1度だけ解く。

    resources（島の地下資源）を渡すと、その島で使える作り方（要る地下資源があり、原料をみな島で
    作れる作り方）だけで解く。石材のない島の建材は木材から作る原価で決まる（安い作り方の値段の
    ままだと、木材から作る工場が原料を買えない）。島で作れない財は、どの島でも同じ値段。
    """
    chosen = None if resources is None else tuple(sorted(set(resources)))
    key = (id(defs), id(config), chosen)
    cached = _INITIAL.get(key)
    if cached is None or cached[0] is not defs or cached[1] is not config:
        if chosen is None:
            prices = _solve_prices(defs, config)
        else:
            prices = _solve_prices(defs, config, set(chosen), initial_prices(defs, config))
        cached = (defs, config, prices)
        _INITIAL[key] = cached
    return dict(cached[2])


def _usable(recipe: Mapping[str, Any], resources: set[str], made: set[str]) -> bool:
    resource = recipe.get("requires_resource")
    return (resource is None or resource in resources) and set(recipe["inputs"]) <= made


def _solve_prices(
    defs: Defs,
    config: GameConfig,
    resources: set[str] | None = None,
    fixed: Prices | None = None,
) -> dict[str, int]:
    """resources を渡すと、その島で使える作り方だけで解く。作る施設のない財は fixed の値段。"""
    settings = config.get("market.initial_prices")
    wage = config.get("labor.initial_wage")
    markup = 1 + settings["markup"]
    reference = settings.get("reference") or {}
    made = None if resources is None else fac.producible_goods(defs, resources)
    producers: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for kind, fdef in defs.facilities.items():
        for recipe in fdef["recipes"].values():
            if resources is not None and made is not None and not _usable(recipe, resources, made):
                continue
            for good in recipe["outputs"]:
                if good in reference and reference[good] != kind:
                    continue
                producers.setdefault(good, []).append((kind, recipe))
    fixed = fixed or {}
    prices: dict[str, float] = {g: float(fixed.get(g, settings["default"])) for g in defs.goods}
    for _ in range(settings.get("iterations", 500)):
        changed = 0.0
        for good in sorted(producers):
            best = None
            for kind, recipe in producers[good]:
                unit = unit_cost(defs, config, kind, recipe, prices, wage)
                best = unit if best is None else min(best, unit)
            assert best is not None
            new = best * markup
            changed = max(changed, abs(new - prices[good]) / max(new, 1.0))
            prices[good] = new
        if changed < 1e-12:
            break
    return {g: max(1, round_money(p + 0.5)) for g, p in prices.items()}


def variable_profit(
    defs: Defs, config: GameConfig, f: Facility, prices: Prices, wage: int, freight: int
) -> int:
    """全力で1ターン動いたときの変動費の利益（売上 − 投入財 − 賃金 − 電気代 − 運賃）。

    freight は施設の運賃の率（1万分の1）。電力は送電網で送るので運賃がかからない。
    """
    power = config.get("power.good")
    recipe = fac.recipe_of(defs, f)
    value = 0
    for good, qty in recipe["outputs"].items():
        amount = money_of(prices.get(good, 0), qty)
        value += amount if good == power else amount - part(amount, freight)
    for good, qty in recipe["inputs"].items():
        amount = money_of(prices.get(good, 0), qty)
        value -= amount if good == power else amount + part(amount, freight)
    return value - wage * fac.workers_needed(defs, f.kind)


def input_limit(
    defs: Defs, config: GameConfig, f: Facility, good: str, prices: Prices, wage: int, freight: int
) -> int:
    """投入財 good に払える一番高い値段: 変動費の利益が0になる値段（0以上）。

    買い手の指値の上限に使う（品切れが続いても、作っても損になる値段までは買い上がらない）。
    産出物は今の値段から値幅（market.band）の分まで値上げできると見る（費用が上がった分を
    売る値段に回せるので、値段が費用ぎりぎりの施設も仕入れを止めない）。ほかの投入財は今の値段。
    電力は運賃がかからないので、電力のときは freight を見ない。
    """
    recipe = fac.recipe_of(defs, f)
    qty = recipe["inputs"].get(good, 0)
    if qty <= 0:
        return 0
    amount = money_of(prices.get(good, 0), qty)
    if good != config.get("power.good"):
        amount += part(amount, freight)
    raised = dict(prices)
    for g in recipe["outputs"]:
        raised[g] = scale_price(prices.get(g, 0), 1 + config.get("market.band"))
    room = variable_profit(defs, config, f, raised, wage, freight) + amount
    return max(0, room * QUANTITY_SCALE // qty)


def unit_floor(
    defs: Defs, config: GameConfig, f: Facility, good: str, prices: Prices, wage: int, freight: int
) -> int:
    """独立採算の売り手が出せる一番安い希望価格: 1単位あたりの変動費（投入財の運賃を含む）。

    希望価格は運賃を払った後に手元に残る額なので、売るときの運賃はここでは足さない（板寄せに
    出すときに足す。hakoniwa_core/modules/market の seller_limit）。作る物が複数なら、変動費を
    値段で割り振る。
    """
    power = config.get("power.good")
    recipe = fac.recipe_of(defs, f)
    outputs = recipe["outputs"]
    if good not in outputs or outputs[good] <= 0:
        return 0
    cost = wage * fac.workers_needed(defs, f.kind)
    for g, qty in recipe["inputs"].items():
        amount = money_of(prices.get(g, 0), qty)
        cost += amount if g == power else amount + part(amount, freight)
    worth = {g: max(1, prices.get(g, 0)) * q for g, q in outputs.items()}
    share = cost * worth[good] / sum(worth.values())
    return round_money(share * QUANTITY_SCALE / outputs[good])
