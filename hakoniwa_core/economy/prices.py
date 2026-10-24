"""値段の計算: 初めの値段（定義のレシピから「原価＋1割」で解く）と、施設の変動費。

原価は、投入財、賃金、設備の利子と減価償却（建設費 ×（年の利率 ÷ 12 ＋ 1 ÷ 減価償却のターン数））
の合計です。建設費は、工事の資材と、工事の人手（1ターンあたりの人数 × ターン数）の賃金です。
作り方が複数あれば安い方を使い、game.yaml の market.initial_prices.reference で施設を決めた財
（電力は火力発電所）はその施設の原価で決めます。追加パックで財を足しても同じ計算で値段が付きます。
人手だけで出すサービス（game.yaml の services。バランス調整）は「賃金 ÷ 働く人1人の量」に
上乗せします。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.defs.kinds import AUTOMATED
from hakoniwa_core.economy import equipment
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


def capital_cost(defs: Defs, config: GameConfig, kind: str, prices: Prices, wage: float) -> float:
    """1ターンの設備の費用: 建設費の利子と、設備の傷み（回6。economy/equipment.py。設備を持たない
    施設は建設費を firms.depreciation_turns ターンで割った分）。"""
    rate = config.get("market.initial_prices.capital_rate")
    cost = construction_cost(defs, kind, prices, wage)
    worn = equipment.yearly_wear(defs, config, kind, prices) / TURNS_PER_YEAR
    if worn <= 0:
        worn = cost / config.get("firms.depreciation_turns")
    return cost * rate / TURNS_PER_YEAR + worn


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
    labor = fac.labor_cost(defs, config, kind, wage, labor=recipe.get("labor", 1.0))
    capital = capital_cost(defs, config, kind, prices, wage)
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


def cost_prices(
    defs: Defs,
    config: GameConfig,
    resources: Iterable[str],
    wage: float,
    fixed: Prices,
) -> dict[str, int]:
    """初めの値段と同じ解き方（原価＋1割）を、賃金 wage と島の地下資源 resources で解き直した値段
    （計画経済の公定価格の自動の見直し。フェーズ3-4）。島で作れない財は fixed の値段のまま。"""
    return _solve_prices(defs, config, set(resources), fixed, wage)


def _usable(recipe: Mapping[str, Any], resources: set[str], made: set[str]) -> bool:
    resource = recipe.get("requires_resource")
    return (resource is None or resource in resources) and set(recipe["inputs"]) <= made


def _solve_prices(
    defs: Defs,
    config: GameConfig,
    resources: set[str] | None = None,
    fixed: Prices | None = None,
    wage: float | None = None,
) -> dict[str, int]:
    """resources を渡すと、その島で使える作り方だけで解く。作る施設のない財は fixed の値段。
    wage を省くと初めの賃金（labor.initial_wage）。"""
    settings = config.get("market.initial_prices")
    if wage is None:
        wage = config.get("labor.initial_wage")
    markup = 1 + settings["markup"]
    reference = settings.get("reference") or {}
    made = None if resources is None else fac.producible_goods(defs, resources)
    producers: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for kind, fdef in defs.facilities.items():
        for rname, recipe in fdef["recipes"].items():
            if (
                rname == AUTOMATED
            ):  # 電子機器を使う作り方は、買える島が選ぶ作り方。値段の基準にしない
                continue
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
    # 人手だけで出す物（サービス。game.yaml の services）は、賃金 ÷ 1人の量に上乗せ
    services = config.raw.get("services")
    if services and services["good"] in prices:
        prices[services["good"]] = wage / services["per_worker"] * markup
    return {g: max(1, round_money(p + 0.5)) for g, p in prices.items()}


def variable_profit(
    defs: Defs, config: GameConfig, f: Facility, prices: Prices, wage: int, freight: int
) -> int:
    """全力で1ターン動いたときの変動費の利益（売上 − 投入財 − 賃金 − 電気代 − 運賃）。
    賃金は一般の賃金 wage と、施設の技術者の賃金（facilities.labor_cost）。

    freight は施設の運賃の率（1万分の1）。電力は送電網で送るので運賃がかからない。産出は
    生産性（インフラ・教育・汚染の係数）を掛けた量で見る。

    商業・サービス業（game.yaml の services）はレシピを持たないので、雇う人数の目安の分だけ
    人を雇ったときの売上 − 賃金で見る（銀行の審査と持ち主の出資の判断に使う）。
    """
    services = config.raw.get("services")
    if services and f.kind == services["facility"]:
        per_seat = prices.get(services["good"], 0) * services["per_worker"] - wage
        return round_money(f.economy.get("seats", 0) * per_seat)
    power = config.get("power.good")
    f = fac.steady(f)  # 畑を作り替えている途中なら、今の作物で見る（回3）
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
    steady = fac.steady(f)  # 畑を作り替えている途中なら、今の作物で見る（回3）
    recipe = fac.recipe_of(defs, steady)
    if recipe["inputs"].get(good, 0) <= 0 and steady is not f:
        # 前の作物の畑にだけ要る原料（肥料に化学品を使う綿の畑を、肥料のいらない穀物に替えている
        # 途中など）は、その畑の作り方で払える値段で買う（畑がみなその作り方とみた写しで見る）。
        # 原料あたりの採算は畑の割合によらない（産出・原料・その畑の働き手の賃金が、みな割合に
        # 比べて決まる）。
        # 新しい作物で見ると上限が 0 になり、前の畑が「原料が割高」で止まった（Codex の試し遊び、
        # ターン 250〜300）。今の畑を混ぜた作り方で見ても、蒔いて育っている畑の分の賃金まで前の畑に
        # 負わせるので、作り替えの半ばから上限が 0 になる
        recipes = defs.facilities[f.kind]["recipes"]
        old = [
            (share, name)
            for name, share in f.economy[fac.FIELDS].items()
            if share > 0 and recipes[name].get("inputs", {}).get(good, 0) > 0
        ]
        if old:
            steady = fac.steady(replace(f, recipe=max(old)[1]))
            recipe = fac.recipe_of(defs, steady)
    qty = recipe["inputs"].get(good, 0)
    if qty <= 0:
        return 0
    amount = money_of(prices.get(good, 0), qty)
    if good != config.get("power.good"):
        amount += part(amount, freight)
    raised = dict(prices)
    for g in recipe["outputs"]:
        raised[g] = scale_price(prices.get(g, 0), 1 + config.get("market.band"))
    room = variable_profit(defs, config, steady, raised, wage, freight) + amount
    return max(0, room * QUANTITY_SCALE // qty)


def unit_floor(
    defs: Defs, config: GameConfig, f: Facility, good: str, prices: Prices, wage: int, freight: int
) -> int:
    """独立採算の売り手が出せる一番安い希望価格: 1単位あたりの変動費（投入財の運賃を含む）と、
    全力で動いたときの1単位あたりの設備の傷み（回6）。

    希望価格は運賃を払った後に手元に残る額なので、売るときの運賃はここでは足さない（板寄せに
    出すときに足す。hakoniwa_core/modules/market の seller_limit）。作る物が複数なら、変動費を
    初めの値段（地下資源によらない世界の値段）で割り振る（今の値段で割り振ると、余った物の値段が
    下がるほどその物の取り分が減って値段が 0 に向かい、もう一方の値段は上がり続けた。フェーズ3-2
    の製油所の燃料と化学品）。
    """
    power = config.get("power.good")
    f = fac.steady(f)  # 畑を作り替えている途中なら、今の作物で見る（回3）
    recipe = fac.recipe_of(defs, f)
    outputs = fac.outputs_of(defs, f)
    if good not in outputs or outputs[good] <= 0:
        return 0
    cost = fac.labor_cost(defs, config, f.kind, wage, f)
    for g, qty in recipe["inputs"].items():
        amount = money_of(prices.get(g, 0), qty)
        cost += amount if g == power else amount + part(amount, freight)
    # 設備が1か月に傷む値打ち（回6）。使わなくても傷むが、売り値で取り戻さなければ直せない（国の
    # 水力発電所が賃金だけの値段で売り、電気料金が初めの 0.3 倍まで下がって火力発電所が閉じた）
    cost += round_money(equipment.yearly_wear(defs, config, f.kind, prices) / TURNS_PER_YEAR)
    if len(outputs) > 1:
        reference = initial_prices(defs, config)
        worth = {g: max(1, reference.get(g, 0)) * q for g, q in outputs.items()}
        share = cost * worth[good] / sum(worth.values())
    else:
        share = cost
    return round_money(share * QUANTITY_SCALE / outputs[good])


# economy.ext: 財 → {"stall": 売りに出ていて買われなかったターン数, "low": その間の一番安い指値,
# "ref": 慣れた値段}
USUAL = "usual"


def usual_price(island: IslandState, good: str, turns: int, reprice: int = 0) -> int:
    """家計が見る「いつもの値段」: 直近 turns ターンの値段の平均（記録がなければ今の値段）。

    家計が買った量の記録のある財（消費財）は、家計が買ったターンの値段だけで平均し、その間に1度も
    買っていなければ今の値段にする。売り買いのなかったターンの値段（気配や、作る費用に向けて上げた
    値段）は誰も払っていないので、いつもの値段に数えない（数えると、作り始めた財の指値が何ターンも
    売りの値段に届かず、売れないまま作る施設が閉じた。試し遊び4回目）。

    reprice（household.reprice_turns）が 1 以上なら、値上がりに慣れた値段（adapt_usual）を下限に
    する（回7 後半。W-77）。"""
    economy = island.economy
    fixed = island.ext.get("plan", {}).get("prices", {}).get(good)
    if fixed:  # 計画経済の公定価格（フェーズ3-4。economy/plan.py）がいつもの値段
        return fixed
    history = economy.price_history.get(good, [])[-turns:]
    if not history:
        return economy.prices.get(good, 1)
    bought = economy.bought_history.get(good)
    if bought is not None:
        paid = [p for p, q in zip(reversed(history), reversed(bought), strict=False) if q > 0]
        usual = max(1, round_money(sum(paid) / len(paid))) if paid else economy.prices.get(good, 1)
        if reprice > 0:
            usual = max(usual, economy.ext.get(USUAL, {}).get(good, {}).get("ref", 0))
        return usual
    return max(1, round_money(sum(history) / len(history)))


def adapt_usual(island: IslandState, config: GameConfig, asks: dict[str, int]) -> None:
    """値上がりに慣れる（市場の段の終わり。回7 後半。W-77）。

    前は、いつもの値段が買った月の古い値段のままなので、費用が上がって値上がりした自動車や燃料を
    1年近く買わず、売り手も買い手もいるのに取引が止まった。売りに出ていたのに家計が1つも買わなかった
    ターンが household.reprice_turns 続いたら、いつもの値段をその間の売りの一番安い指値（asks。
    店に並んだ値段）との半ばまで上げる（慣れた値段 ref。人は値上がりに少しずつ慣れる）。売りの
    なかったターンは数えない。家計が買えば数え直し、買った値段の平均が慣れた値段に届いたら
    慣れた値段は要らなくなる。"""
    turns = config.get("household.reprice_turns")
    if turns <= 0:
        return
    economy = island.economy
    table = economy.ext.setdefault(USUAL, {})
    for good in sorted(asks):
        ask = asks[good]
        bought = economy.bought_history.get(good, [])
        entry = table.get(good, {})
        if bought and bought[-1] > 0:
            entry["stall"] = 0
            plain = usual_price(island, good, config.get("household.usual_price_turns"))
            if plain >= entry.get("ref", 0):
                entry.pop("ref", None)
        elif ask > 0:
            entry["stall"] = entry.get("stall", 0) + 1
            entry["low"] = min(ask, entry.get("low", ask)) if entry["stall"] > 1 else ask
            if entry["stall"] >= turns:
                usual = usual_price(island, good, config.get("household.usual_price_turns"), 1)
                if entry["low"] > usual:
                    entry["ref"] = usual + (entry["low"] - usual + 1) // 2
                entry["stall"] = 0
        if entry.get("stall") or "ref" in entry:
            table[good] = entry
        else:
            table.pop(good, None)


def material_limit(island: IslandState, config: GameConfig, good: str, width: int) -> int:
    """工事とインフラの資材の買いの指値: 値幅の上端と、いつもの値段の一番上の段
    （household.consumer_goods_tiers）の安い方（フェーズ3-2。値幅の上端で買い続けると、品薄の
    島で機械と鋼鉄の値段が初めの数十〜数百倍まで上がった。高ければ工事が待つ）。"""
    settings = config.get("household")
    usual = usual_price(island, good, settings["usual_price_turns"], settings["reprice_turns"])
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
