"""計画経済（フェーズ3-4）: 公定価格・割当ルール・生産目標・重点産業・配給と行列。

経済体制の特色 official_prices を持つ体制（計画経済）の島だけで効きます。

- 公定価格: 財ごとに国が決める値段。原則は据え置き（ソ連の小売価格のように、決めた値段を長く
  変えない）。見直すのは次のときだけ。
  - 命令 review_official_prices（公定価格の改定。政治の決断なので行政力が重く、議会のある体制では
    採決が要る）: 名指しした財（空ならすべて）を今の原価に改める。
  - planning.review_turns ターンごとの確かめで、公定価格と原価の差が幅（planning.review_band。
    命令 set_official_price_rules で変えられる）を超えた財。ただし据え置く財（planning.held_goods。
    既定は食料。同じ命令で変えられる）は、原価を下回っていても据え置き、差を補助金で埋める。
  原価は、建国の初めの値段と同じ解き方（原価＋1割。economy/prices.py）を今の島の平均賃金で解いた
  値段。島で作れない財は、前の原価に平均賃金の伸びを掛ける。命令 set_official_price で財ごとに
  上書きでき、上書きした財は確かめで変えない。市場の段の初めに公定価格を基準の値段にし
  （apply_prices）、島の市場は公定価格で値幅 0 の板寄せになる（modules/market）。電力も生産の段で
  同じにする。
- 据え置きの補助金（subsidies）: 据え置く財を原価より安い公定価格で売った施設に、1単位あたり
  「原価 − 公定価格」を国庫（国営施設の項目）から払う。国庫直轄の施設は売上も費用も国庫なので、
  お金は動かさず帳簿にだけ残す。国庫が足りなければ払える分だけ（量に比例）払い、足りない分は
  独立採算の施設の赤字になる。
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
- costs: 前に解いた原価（原価＋1割。財 → 値段）、wage: そのときの平均賃金
- review: 次に確かめるターン
- band: 見直す幅（割合。命令で決めたときだけ。ないときは planning.review_band）
- held: 据え置く財（命令で決めたときだけ。ないときは planning.held_goods）
- subsidy_short: 据え置きの補助金を払いきれていない（知らせは続いている間に1度だけ）
- priority: 重点産業（産業の分類。planning.max_priority まで）
- demand: 財ごとの直近の需要の履歴、goals: 次のターンの財ごとの生産目標（量）
- achieved: このターンの財ごとの達成率（作った量 ÷ 目標）、queues: 財ごとの行列（0〜1）
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, politics
from hakoniwa_core.allocation import proportional
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import STATE_FACILITIES, Payer, books, entity_of, funds
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
SUBSIDY = "plan_subsidy"  # island.scratch: 施設の番号 → このターンの据え置きの補助金（払う前の額）


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
    """公定価格を今の値段から始める（計画経済に移ったときと、移し替え）。原価は市場の段の初めに
    解き、見直しの確かめは planning.review_turns ターン後から。"""
    state = state_of(island)
    state["prices"] = {g: p for g, p in sorted(island.economy.prices.items()) if p > 0}
    state.setdefault("manual", {})
    state.pop("costs", None)
    state["review"] = turn + config.get("planning.review_turns")
    state["wage"] = island.economy.average_wage
    state.setdefault("priority", [])
    state.setdefault("demand", {})


def stop(island: IslandState) -> None:
    """計画経済をやめた: 公定価格と目標を消す（重点産業も）。"""
    island.ext.pop(KEY, None)


def rules(island: IslandState, config: GameConfig) -> tuple[float, list[str]]:
    """(見直す幅（割合）, 据え置く財)。命令で決めていなければ game.yaml の planning の既定。"""
    state = island.ext.get(KEY, {})
    band = state.get("band")
    held = state.get("held")
    if band is None:
        band = config.get("planning.review_band")
    if held is None:
        held = config.get("planning.held_goods")
    return float(band), list(held)


def solve_costs(island: IslandState, defs: Defs, config: GameConfig) -> dict[str, int]:
    """原価（原価＋1割）を今の平均賃金で解き直して残す。島で作れない財は、前の原価（まだなければ
    公定価格）に平均賃金の伸びを掛ける。"""
    state = state_of(island)
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    before = state.get("wage") or wage
    old = state.get("costs") or state.get("prices", {})
    fixed = {g: max(1, round_money(p * wage / before)) for g, p in old.items()}
    solved = cost_prices(defs, config, island.resources, wage, fixed)
    state["costs"] = {g: int(solved[g]) for g in sorted(defs.goods) if solved.get(g)}
    state["wage"] = wage
    return state["costs"]


def outside(price: int, cost: int, band: float) -> bool:
    """公定価格 price と原価 cost の差が幅 band（割合）を超えたか。"""
    return abs(price - cost) > band * cost


def review(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> list[str]:
    """planning.review_turns ターンごとの確かめ: 原価を解き直し、原価との差が幅を超えた財だけ公定
    価格を原価に改める。据え置く財は、原価を下回っていれば据え置く（差は補助金）。命令で上書き
    した財はそのまま。新しく出てきた財は原価から始める。改めた財を返す。"""
    state = state_of(island)
    costs = solve_costs(island, defs, config)
    band, held = rules(island, config)
    manual = state.get("manual", {})
    prices = dict(state.get("prices", {}))
    revised = []
    for good in sorted(defs.goods):
        cost = costs.get(good)
        price = manual.get(good) or prices.get(good)
        if good in manual or not cost:
            if price:
                prices[good] = price
            continue
        if price is None:
            prices[good] = cost
        elif outside(price, cost, band) and not (good in held and price < cost):
            prices[good] = cost
            revised.append(good)
    state["prices"] = prices
    state["review"] = turn + config.get("planning.review_turns")
    if revised:
        args = {"goods": revised, "band": round(band * 100)}
        island.log.append(LogEntry("official_prices_revised", args))
    return revised


def revise(island: IslandState, defs: Defs, config: GameConfig, goods: Sequence[str]) -> list[str]:
    """公定価格の改定（命令）: goods（空なら命令で上書きしていない財すべて）を今の原価に改める。
    上書きしていた財を名指ししたら、上書きもやめる。値段が変わった財を返す。"""
    state = state_of(island)
    costs = solve_costs(island, defs, config)
    manual = state.setdefault("manual", {})
    prices = state.setdefault("prices", {})
    chosen = list(goods) or [g for g in sorted(defs.goods) if g not in manual]
    changed = []
    for good in chosen:
        cost = costs.get(good)
        if not cost:
            continue
        manual.pop(good, None)
        if prices.get(good) != cost:
            changed.append(good)
        prices[good] = cost
    return changed


def prepare(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> dict[str, int]:
    """市場の段の初め: 確かめのターンなら原価を解き直して幅を超えた財を改め、公定価格を基準の
    値段にする。原価をまだ解いていなければ（計画経済に移ったターンと、前の版の島）解く。"""
    if not is_planned(island, defs):
        return {}
    state = state_of(island)
    if "prices" not in state:
        start(island, config, turn)
    if turn >= state.get("review", 0):
        review(island, defs, config, turn)
    elif "costs" not in state:
        solve_costs(island, defs, config)
    apply_prices(island)
    return official(island)


def apply_prices(island: IslandState) -> None:
    """公定価格を島の値段（基準の値段）にする。"""
    for good, price in official(island).items():
        island.economy.prices[good] = price


# --- 据え置きの補助金 ---------------------------------------------------------------


def subsidy_rate(island: IslandState, config: GameConfig, good: str, price: int) -> int:
    """据え置く財 good を公定価格 price で売ったときの、1単位あたりの補助金（原価を下回る分。
    据え置く財でないか、原価以上なら 0）。"""
    state = island.ext.get(KEY)
    if not state or "costs" not in state:
        return 0
    _, held = rules(island, config)
    if good not in held:
        return 0
    return max(0, state["costs"].get(good, 0) - price)


def note_sale(
    island: IslandState, config: GameConfig, good: str, f: Facility, qty: int, price: int
) -> None:
    """施設 f が据え置く財を公定価格で売った: 補助金の額を数えておく（払うのは pay_subsidies）。"""
    rate = subsidy_rate(island, config, good, price)
    if rate > 0 and qty > 0:
        owed = island.scratch.setdefault(SUBSIDY, {})
        owed[f.id] = owed.get(f.id, 0) + money_of(rate, qty)


def pay_subsidies(island: IslandState, config: GameConfig) -> None:
    """市場の段の終わり: このターンの据え置きの補助金を払う。独立採算の施設には国庫の国営施設の
    項目から払い（足りなければ払える分を額に比例して分ける）、国庫直轄の施設は帳簿にだけ残す。"""
    owed = island.scratch.pop(SUBSIDY, {})
    found = {f.id: f for f in island.facilities}
    payer = Payer(ledger.GOV, STATE_FACILITIES)
    paying = {i: a for i, a in sorted(owed.items()) if entity_of(config, found[i]) != ledger.GOV}
    wanted = sum(paying.values())
    money = funds.available(island, payer)
    unpaid = 0
    for number, amount in sorted(owed.items()):
        if number in paying:
            paid = amount if wanted <= money else amount * money // wanted
            funds.pay(island, payer, entity_of(config, found[number]), paid, "subsidy")
            unpaid += amount - paid
            amount = paid
        books.add(island, number, books.SUBSIDY, amount)
    island.stats["plan.subsidy"] = sum(owed.values()) - unpaid
    island.stats["plan.subsidy_unpaid"] = unpaid
    state = state_of(island)
    if unpaid and not state.get("subsidy_short"):
        island.log.append(LogEntry("plan_subsidy_short", {"amount": unpaid}))
    if unpaid:
        state["subsidy_short"] = True
    else:
        state.pop("subsidy_short", None)


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
    """財ごとの、動いている国営施設の全力の産出の合計（このターンの値を scratch に置く）。設備が
    傷んだ施設は、傷んだ分を除いて数える（回6。ほかの施設が多めに動く）。"""
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
                found[good] = found.get(good, 0) + fac.scale(qty, fac.equipment_cap(f))
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
