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
from hakoniwa_core.market import auction
from hakoniwa_core.state import Facility, IslandState
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
    labor = fac.labor_cost(defs, config, kind, wage)
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
    賃金は一般の賃金 wage と、施設の技術者の賃金（facilities.labor_cost）。

    freight は施設の運賃の率（1万分の1）。電力は送電網で送るので運賃がかからない。産出は
    生産性（インフラ・教育・汚染の係数）を掛けた量で見る。
    """
    power = config.get("power.good")
    recipe = fac.recipe_of(defs, f)
    value = 0
    for good, qty in fac.outputs_of(defs, f).items():
        amount = money_of(prices.get(good, 0), qty)
        value += amount if good == power else amount - part(amount, freight)
    for good, qty in recipe["inputs"].items():
        amount = money_of(prices.get(good, 0), qty)
        value -= amount if good == power else amount + part(amount, freight)
    return value - fac.labor_cost(defs, config, f.kind, wage, f)


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
    初めの値段（地下資源によらない世界の値段）で割り振る（今の値段で割り振ると、余った物の値段が
    下がるほどその物の取り分が減って値段が 0 に向かい、もう一方の値段は上がり続けた。フェーズ3-2
    の製油所の燃料と化学品）。
    """
    power = config.get("power.good")
    recipe = fac.recipe_of(defs, f)
    outputs = fac.outputs_of(defs, f)
    if good not in outputs or outputs[good] <= 0:
        return 0
    cost = fac.labor_cost(defs, config, f.kind, wage, f)
    for g, qty in recipe["inputs"].items():
        amount = money_of(prices.get(g, 0), qty)
        cost += amount if g == power else amount + part(amount, freight)
    if len(outputs) > 1:
        reference = initial_prices(defs, config)
        worth = {g: max(1, reference.get(g, 0)) * q for g, q in outputs.items()}
        share = cost * worth[good] / sum(worth.values())
    else:
        share = cost
    return round_money(share * QUANTITY_SCALE / outputs[good])


def usual_price(island: IslandState, good: str, turns: int) -> int:
    """家計が見る「いつもの値段」: 直近 turns ターンの値段の平均（記録がなければ今の値段）。

    家計が買った量の記録のある財（消費財）は、家計が買ったターンの値段だけで平均し、その間に1度も
    買っていなければ今の値段にする。売り買いのなかったターンの値段（気配や、作る費用に向けて上げた
    値段）は誰も払っていないので、いつもの値段に数えない（数えると、作り始めた財の指値が何ターンも
    売りの値段に届かず、売れないまま作る施設が閉じた。試し遊び4回目）。"""
    economy = island.economy
    history = economy.price_history.get(good, [])[-turns:]
    if not history:
        return economy.prices.get(good, 1)
    bought = economy.bought_history.get(good)
    if bought is not None:
        paid = [p for p, q in zip(reversed(history), reversed(bought), strict=False) if q > 0]
        if not paid:
            return economy.prices.get(good, 1)
        history = paid
    return max(1, round_money(sum(history) / len(history)))


def material_limit(island: IslandState, config: GameConfig, good: str, width: int) -> int:
    """工事とインフラの資材の買いの指値: 値幅の上端と、いつもの値段の一番上の段
    （household.consumer_goods_tiers）の安い方（フェーズ3-2。値幅の上端で買い続けると、品薄の
    島で機械と鋼鉄の値段が初めの数十〜数百倍まで上がった。高ければ工事が待つ）。"""
    settings = config.get("household")
    usual = usual_price(island, good, settings["usual_price_turns"])
    _, high = auction.band(island.economy.prices.get(good, 1), width)
    return min(high, scale_price(usual, max(settings["consumer_goods_tiers"])))


def add_missing_prices(island: IslandState, defs: Defs, config: GameConfig) -> list[str]:
    """定義にあって島の値段のない財（前のフェーズから続く島の、新しい品目）に値段を置き、置いた財を
    返す。値段は島の初めの値段に、今の物価指数の倍率を掛けたもの（フェーズ3-2。値段がないと 1 と
    見て、値幅が 1 のまま動かず、作っても売っても値段が 1 のままだった）。"""
    from hakoniwa_core.economy.stats import BASE_CPI

    prices = island.economy.prices
    missing = sorted(set(defs.goods) - set(prices))
    if not missing:
        return []
    base = initial_prices(defs, config, island.resources)
    history = island.economy.cpi_history
    level = history[-1] / BASE_CPI if history and history[-1] > 0 else 1.0
    for good in missing:
        price = max(1, round_money(base.get(good, 1) * level))
        prices[good] = price
        island.economy.price_history[good] = [price]
    return missing
