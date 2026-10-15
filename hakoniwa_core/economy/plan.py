"""計画経済（フェーズ3-4）: 公定価格・割当ルール・生産目標・重点産業・配給と行列。

経済体制の特色 official_prices を持つ体制（計画経済）の島だけで効きます。

- 公定価格: 財ごとに国が決める値段。既定は自動で、建国の初めの値段と同じ解き方（原価＋1割。
  economy/prices.py）を今の島の平均賃金で planning.review_turns ターンごとに解き直す。島で作れない
  財は、前の公定価格に平均賃金の伸びを掛ける。命令 set_official_price で財ごとに上書きでき、上書き
  した財は自動の見直しで変えない。市場の段の初めに公定価格を基準の値段にし（apply_prices）、島の市場は
  公定価格で値幅 0 の板寄せになる（modules/market）。電力も生産の段で同じにする。
- 割当ルール（allocator）: 足りないときは、重点産業の施設 → ほかの国営施設と国の工事（インフラ・
  海運業・運送業・国の倉庫）→ 家計（配給）→ 輸出（商社と二国間契約の買い）の順に配る。同じ順位の
  中は量に比例。家計の中は、お金の多さでなく1人あたり同じ量まで（ration）。
- 生産目標（target）: 国営施設ごとに、前の planning.demand_turns ターンの公定価格での需要（買えな
  かった分も含む）に planning.target_markup を足した量を、同じ財を作る国営施設に設備の大きさ（全力の
  産出）で割り振る。施設は採算ではなく目標に合わせて動く（式 operating_target の代わり）。
- 行列と強制貯蓄（record）: 家計が買いたかったのに買えなかった割合を、財ごとの「行列」として出す。
  買えなかった分の代金（公定価格で）は使われずに預金に残る（強制貯蓄）。闇市はないが、後から
  モジュールで足せるように、配給で買えなかった量（scratch["plan_unmet"]）と余ったお金
  （scratch["plan_forced"]）を段の間に残す。

島の状態は ``island.ext["plan"]``:
- prices: 公定価格（財 → 値段）、manual: 命令で上書きした財 → 値段
- review: 次に自動で見直すターン、wage: 前に見直したときの平均賃金
- priority: 重点産業（産業の分類。planning.max_priority まで）
- demand: 財ごとの直近の需要の履歴、goals: 次のターンの財ごとの生産目標（量）
- achieved: このターンの財ごとの達成率（作った量 ÷ 目標）、queues: 財ごとの行列（0〜1）
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import politics
from hakoniwa_core.allocation import proportional
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy.prices import cost_prices
from hakoniwa_core.state import OWNER_STATE, Facility, IslandState, LogEntry
from hakoniwa_core.units import money_of, round_money

KEY = "plan"

# 割当の順位（大きいほど先）
PRIORITY = 3  # 重点産業の施設
STATE = 2  # ほかの国営施設と国の工事
HOUSEHOLDS = 1  # 家計（配給）
EXPORTS = 0  # 輸出（商社・二国間契約）

UNMET = "plan_unmet"  # island.scratch: 財 → 家計が買えなかった量（このターン）
FORCED = "plan_forced"  # island.scratch: 家計が買えずに使わなかったお金（このターン）
DESIRE = "plan_desire"  # island.scratch: 財 → 家計が買いたかった量（売りに出ている分を超えた分も）


def is_planned(island: IslandState, defs: Defs) -> bool:
    """公定価格と配給で動く経済体制か（regimes.yaml の traits の official_prices）。"""
    return island.has_economy and bool(politics.trait(island, defs, "official_prices", False))


def state_of(island: IslandState) -> dict[str, Any]:
    return island.ext.setdefault(KEY, {})


def official(island: IslandState) -> dict[str, int]:
    """公定価格（計画経済でない島や、まだ決めていない島は空）。"""
    return island.ext.get(KEY, {}).get("prices", {})


def priority_industries(island: IslandState) -> list[str]:
    return list(island.ext.get(KEY, {}).get("priority", []))


def is_priority(island: IslandState, defs: Defs, f: Facility) -> bool:
    """重点産業の国営施設か（作り方の産業の分類が重点産業に入っている）。"""
    chosen = island.ext.get(KEY, {}).get("priority")
    if not chosen or f.owner != OWNER_STATE:
        return False
    return fac.recipe_of(defs, f).get("industry") in chosen


def industry_of(defs: Defs, kind: str, recipe: str | None) -> str | None:
    recipes = defs.facilities[kind]["recipes"]
    spec = recipes.get(recipe or "") or next(iter(recipes.values()), {})
    return spec.get("industry")


def build_factor(island: IslandState, defs: Defs, kind: str, recipe: str | None) -> float:
    """命令で建てる国の工事の工期の倍率: 体制の works_turns（権威主義の公共事業）と、計画経済の
    重点産業なら priority_build_turns を掛ける。"""
    found = politics.traits(island, defs)
    factor = found.get("works_turns", 1.0)
    chosen = island.ext.get(KEY, {}).get("priority")
    if chosen and industry_of(defs, kind, recipe) in chosen:
        factor *= found.get("priority_build_turns", 1.0)
    return factor


def experience_factor(island: IslandState, defs: Defs, industry: str) -> float:
    """経験の伸びの倍率（計画経済の重点産業は priority_experience）。"""
    chosen = island.ext.get(KEY, {}).get("priority")
    if not chosen or industry not in chosen:
        return 1.0
    return float(politics.trait(island, defs, "priority_experience", 1.0))


# --- 公定価格 ---------------------------------------------------------------------


def start(island: IslandState, config: GameConfig, turn: int) -> None:
    """公定価格を今の値段から始める（計画経済に移ったときと、移し替え）。自動の見直しは
    planning.review_turns ターン後から。"""
    state = state_of(island)
    state["prices"] = {g: p for g, p in sorted(island.economy.prices.items()) if p > 0}
    state.setdefault("manual", {})
    state["review"] = turn + config.get("planning.review_turns")
    state["wage"] = island.economy.average_wage
    state.setdefault("priority", [])
    state.setdefault("demand", {})


def stop(island: IslandState) -> None:
    """計画経済をやめた: 公定価格と目標を消す（重点産業も）。"""
    island.ext.pop(KEY, None)


def review(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> None:
    """公定価格を自動で見直す（原価＋1割を今の平均賃金で解く。上書きした財はそのまま）。"""
    state = state_of(island)
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    before = state.get("wage") or wage
    old = state.get("prices", {})
    fixed = {g: max(1, round_money(p * wage / before)) for g, p in old.items()}
    solved = cost_prices(defs, config, island.resources, wage, fixed)
    manual = state.get("manual", {})
    prices = {}
    for good in sorted(defs.goods):
        price = manual.get(good) or solved.get(good) or old.get(good)
        if price:
            prices[good] = int(price)
    state["prices"] = prices
    state["review"] = turn + config.get("planning.review_turns")
    state["wage"] = wage
    island.log.append(LogEntry("official_prices_reviewed", {"count": len(prices)}))


def prepare(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> dict[str, int]:
    """市場の段の初め: 見直しのターンなら公定価格を解き直し、公定価格を基準の値段にする。"""
    if not is_planned(island, defs):
        return {}
    state = state_of(island)
    if "prices" not in state:
        start(island, config, turn)
    elif turn >= state.get("review", 0):
        review(island, defs, config, turn)
    apply_prices(island)
    return official(island)


def apply_prices(island: IslandState) -> None:
    """公定価格を島の値段（基準の値段）にする。"""
    for good, price in official(island).items():
        island.economy.prices[good] = price


# --- 割当ルール -------------------------------------------------------------------


def ration(
    amount: int, requests: Sequence[tuple[int, int, str]], persons: Mapping[str, int]
) -> dict[int, int]:
    """家計の配給: requests（(注文の番号, 量, 層)）に、1人あたり同じ量まで配る。

    1人あたりの量の上限 L を「どの層も min(欲しい量, L × 人数) を受け取り、合計が amount になる」
    ように決め、層の中の注文には量に比例して分ける。"""
    layers: dict[str, list[tuple[int, int]]] = {}
    for i, qty, layer in requests:
        layers.setdefault(layer, []).append((i, qty))
    heads = {layer: max(1, persons.get(layer, 0)) for layer in layers}
    wants = {layer: sum(q for _, q in orders) for layer, orders in layers.items()}
    if sum(wants.values()) <= amount:
        return {i: q for i, q, _ in requests}
    left = amount
    shares: dict[str, int] = {}
    pending = sorted(layers, key=lambda layer: (wants[layer] / heads[layer], layer))
    while pending:
        level = left / sum(heads[layer] for layer in pending)
        first = pending[0]
        if wants[first] <= level * heads[first]:
            shares[first] = wants[first]
            left -= wants[first]
            pending.pop(0)
            continue
        # 残りの層はみな上限 L で止まる（人数に比例。どの層も欲しい量より少ない）
        total = sum(heads[layer] for layer in pending)
        for layer in pending:
            shares[layer] = left * heads[layer] // total
        rest = left - sum(shares[layer] for layer in pending)
        for layer in pending[:rest]:
            shares[layer] += 1
        break
    out: dict[int, int] = {}
    for layer, orders in sorted(layers.items()):
        out.update(proportional(shares.get(layer, 0), orders))
    return out


def allocator(tiers: Sequence[int], layers: Sequence[str | None], persons: Mapping[str, int]):
    """買い注文の割当ルール（auction.clear の allocate_buys）。

    tiers[i] は注文 i の順位、layers[i] は家計の注文なら層の名前（ほかは None）、persons は層ごとの
    人数。"""

    def allocate(volume: int, side: Sequence[tuple[int, int, int]]) -> dict[int, int]:
        out: dict[int, int] = {}
        left = volume
        for tier in sorted({tiers[i] for i, _, _ in side}, reverse=True):
            group = [(i, q) for i, q, _ in side if tiers[i] == tier]
            want = sum(q for _, q in group)
            if want <= left:
                out.update(group)
                left -= want
                continue
            if tier == HOUSEHOLDS:
                got = ration(left, [(i, q, layers[i] or "") for i, q in group], persons)
            else:
                got = proportional(left, group)
            out.update(got)
            left -= sum(got.values())
        return out

    return allocate


# --- 生産目標 ---------------------------------------------------------------------


def capacities(island: IslandState, defs: Defs, config: GameConfig) -> dict[str, int]:
    """財ごとの、動いている国営施設の全力の産出の合計（このターンの値を scratch に置く）。"""
    found = island.scratch.get("plan_capacity")
    if found is not None:
        return found
    power = config.get("power.good")
    found = {}
    for f in island.facilities:
        if not f.built or f.owner != OWNER_STATE or fac.is_housing(config, f):
            continue
        for good, qty in fac.outputs_of(defs, f).items():
            if good != power and qty > 0:
                found[good] = found.get(good, 0) + qty
    island.scratch["plan_capacity"] = found
    return found


def goal_of(island: IslandState, config: GameConfig, good: str) -> int | None:
    """財 good の次のターンの生産目標（量）。需要の記録がなければ None。"""
    history = island.ext.get(KEY, {}).get("demand", {}).get(good)
    if not history:
        return None
    average = sum(history) / len(history)
    return round_money(average * (1 + config.get("planning.target_markup")))


def target(island: IslandState, f: Facility, defs: Defs, config: GameConfig) -> int | None:
    """国営施設 f の操業目標（1000 分の 1）。需要の記録のない財しか作らなければ None（今のまま）。

    目標の量を、同じ財を作る国営施設に全力の産出で割り振るので、どの施設も「目標 ÷ 全力の産出の
    合計」の割合で動く。産出が複数なら一番多く要る財に合わせる。1ターンに変えるのは全力の
    market.max_target_change まで（人の出入りと仕入れを急に変えない）。"""
    power = config.get("power.good")
    caps = capacities(island, defs, config)
    plan = None
    for good, full in sorted(fac.outputs_of(defs, f).items()):
        if good == power or full <= 0:
            continue
        goal = goal_of(island, config, good)
        if goal is None:
            continue
        share = fac.ratio(goal, caps.get(good, full))
        plan = share if plan is None else max(plan, share)
    if plan is None:
        return None
    now = f.economy.get("target", fac.FULL)
    limit = round_money(config.get("market.max_target_change") * fac.FULL + 0.5)
    return max(0, max(now - limit, min(now + limit, plan)))


# --- 行列と記録 ---------------------------------------------------------------------


def record(
    island: IslandState,
    config: GameConfig,
    demand: Mapping[str, int],
    household: Mapping[str, tuple[int, int]],
) -> None:
    """市場の段の終わり: 需要の履歴、生産目標、達成率、行列、強制貯蓄を残す。

    demand は財ごとの公定価格での需要（買えなかった分も含む）、household は財ごとの家計の
    (買いたかった量, 買えた量)。"""
    state = state_of(island)
    keep = config.get("planning.demand_turns")
    history = state.setdefault("demand", {})
    for good in sorted(set(history) | set(demand)):
        history[good] = [*history.get(good, []), demand.get(good, 0)][-keep:]
        if not any(history[good]):
            del history[good]
    goals_before = state.get("goals", {})
    achieved = {}
    for good, goal in sorted(goals_before.items()):
        made = island.stats.get(f"production.{good}")
        if goal > 0 and made is not None:
            achieved[good] = round(made * 1000 / goal, 3)
    state["achieved"] = achieved
    goals = {}
    for good in sorted(history):
        goal = goal_of(island, config, good)
        if goal:
            goals[good] = goal
    state["goals"] = goals
    prices = official(island)
    queues: dict[str, float] = {}
    unmet: dict[str, int] = {}
    forced = 0
    wanted = bought = 0
    for good, (want, got) in sorted(household.items()):
        if want <= 0:
            continue
        short = max(0, want - got)
        queues[good] = round(short / want, 3)
        if short:
            unmet[good] = short
            forced += money_of(prices.get(good, 0), short)
        wanted += money_of(prices.get(good, 0), want)
        bought += money_of(prices.get(good, 0), min(want, got))
    state["queues"] = {g: q for g, q in queues.items() if q > 0}
    island.scratch[UNMET] = unmet
    island.scratch[FORCED] = forced
    island.stats["plan.forced_saving"] = forced
    island.stats["plan.queue"] = 1 - bought / wanted if wanted else 0.0
