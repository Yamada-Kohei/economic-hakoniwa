"""計画経済（フェーズ3-4）: 公定価格・割当ルール・生産目標・重点産業・配給と行列。

経済体制の特色 official_prices を持つ体制（計画経済）の島だけで効きます。

- 公定価格（卸の値段）: 財ごとに国が決める値段。施設どうし・国の工事・国の倉庫・輸出が払い、売り手が
  受け取る。原則は据え置き（ソ連の値段のように、決めた値段を長く変えない）。見直すのは次のときだけ。
  - 命令 review_official_prices（公定価格の改定。政治の決断なので行政力が重く、議会のある体制では
    採決が要る）: 名指しした財（空ならすべて）を今の原価に改める。
  - planning.review_turns ターンごとの確かめで、公定価格と原価の差が幅（planning.review_band。
    命令 set_official_price_rules で変えられる）を超えた財（据え置く財も卸の値段は改める）。
  原価は、建国の初めの値段と同じ解き方（原価＋1割。economy/prices.py）を今の島の平均賃金で解いた
  値段。島で作れない財は、前の原価に平均賃金の伸びを掛ける。命令 set_official_price で財ごとに
  上書きでき、上書きした財は確かめで変えない。市場の段の初めに公定価格を基準の値段にし
  （apply_prices）、島の市場は公定価格で値幅 0 の板寄せになる（modules/market）。電力も生産の段で
  同じにする。
- 小売の値段（PL-60。retail_price）: 家計の買い物（消費税のかかる物の全部）に家計が払う値段で、
  消費税はかけない。据え置く財（planning.held_goods。既定は食料）は据え置き、命令
  set_retail_price で上書きした財はその値段、ほかは卸 ×（1 ＋ 標準の取引税の率。消費税の率の命令が
  受け持つ）。行列や売れ残りでは動かさない。家計の注文は財ごとの率 rate_of（小売 ÷ 卸 − 1）を今の
  消費税と同じ形で使う。
- 取引税と価格の補助（pay_price_subsidies）: 家計が買った量 ×（小売 − 卸）がプラスなら取引税（約定の
  ときに国庫へ）、マイナスなら価格の補助（市場の段の終わりに、取引税と差し引いて支出の項目
  price_subsidy から売り手に払う）。払いきれない分は売り手への国の未払いにして次のターンに先に払い、
  国債の段の発行の見込みに入れる。国債が売れず中央銀行の引き受けもない間だけ、売った施設の赤字にする。
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
- prices: 公定価格（卸の値段。財 → 値段）、manual: 命令で上書きした財 → 値段
- retail: 据え置く財の小売の値段、retail_manual: 命令で上書きした小売の値段（財 → 値段）
- dual: 二重の値段に移し終えた（ない島は前の版）、started: 物価の指数をつなぎ直したターン
- subsidy_owed: 払いきれなかった価格の補助（売り手 → 額）
- costs: 前に解いた原価（原価＋1割。財 → 値段）、wage: そのときの平均賃金
- review: 次に確かめるターン
- band: 見直す幅（割合。命令で決めたときだけ。ないときは planning.review_band）
- held: 据え置く財（命令で決めたときだけ。ないときは planning.held_goods）
- subsidy_short・subsidy_lost・subsidy_over: 価格の補助の知らせ（続いている間に1度だけ）
- priority: 重点産業（産業の分類。planning.max_priority まで）
- demand: 財ごとの直近の需要の履歴、goals: 次のターンの財ごとの生産目標（量）
- achieved: このターンの財ごとの達成率（作った量 ÷ 目標）、queues: 財ごとの行列（0〜1）
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, politics
from hakoniwa_core.allocation import proportional
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import Payer, books, funds
from hakoniwa_core.economy.prices import cost_prices
from hakoniwa_core.exploration import base_resources
from hakoniwa_core.state import OWNER_STATE, Facility, IslandState, LogEntry
from hakoniwa_core.units import BASIS, money_of, part, round_money

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


def hoards(island: IslandState, defs: Defs, f: Facility) -> bool:
    """人を抱える施設か（W-198。計画経済の島の、動いている国営の施設）。操業目標が下がっても
    一般の働き手を解雇せず（辞める人の分 planning.attrition ずつ減る）、作る量は操業目標までに
    抑える（余った人は隠れた失業）。現実のソ連・東欧の国営企業は解雇せず人を抱え込んだ。"""
    return f.owner == OWNER_STATE and f.built and is_planned(island, defs)


# --- 人員計画（PL-59） ---------------------------------------------------------------

# f.ext: 人員計画の枠（一般の労働者・技術者・動員した人を合わせた人数の上限）
STAFF_CAP = "staff_cap"
STAFF_CAPPED = "staff_capped"  # f.ext: 人員の枠で、生産目標に要る人を雇えなかった


def staff_cap(island: IslandState, defs: Defs, f: Facility) -> int | None:
    """施設 f の人員の枠（命令 set_staff_plan）。計画経済の島の建て終わった国営施設だけ。決めて
    いなければ None。暴動などで壊れて直している間は枠をかけない（直す工事の作業員を縛らない。
    直し終わると枠が戻る）。"""
    cap = f.ext.get(STAFF_CAP)
    if cap is None or not f.built or f.owner != OWNER_STATE or not is_planned(island, defs):
        return None
    return int(cap)


def staff_limit(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    f: Facility,
    prior: int | None = None,
) -> int | None:
    """このターンに施設 f が雇える人数の上限（人員の枠と、枠に向けて減らす速さ）。枠がなければ
    None。

    前のターンの人数 prior（動員した人を除く）が枠以下なら枠まで。枠より多い施設は、前のターンに
    働いていなかった人（前のターンの稼働率で要った人数を超える人: 抱えられた余りの人と休業中の人）を
    その月に枠まで手放し、それでも多ければ働いていた人を前のターンの人数の planning.staff_release
    （端数は切り上げ）まで減らす。店（商業・サービス業）は雇った人がみな働いている。"""
    cap = staff_cap(island, defs, f)
    if cap is None:
        return None
    if prior is None:
        prior = f.workers - f.economy.get("mobilized", 0)
    if prior <= cap:
        return cap
    full = fac.workers_of(defs, f)
    worked = prior
    if full > 0 and f.kind != config.get("services.facility"):
        worked = min(prior, -(-full * max(0, f.operating_rate) // fac.FULL))
    left = max(cap, worked)
    if left > cap:
        left = max(cap, left - math.ceil(prior * config.get("planning.staff_release")))
    return left


def build_factor(island: IslandState, defs: Defs) -> float:
    """命令で建てる国の工事の工期の倍率（体制の works_turns。権威主義の公共事業）。計画経済の
    重点産業は、工期の倍率ではなく、資材を先に受け取ることで早く建つ（割当ルール）。"""
    return float(politics.traits(island, defs).get("works_turns", 1.0))


# --- 公定価格（卸の値段）と小売の値段（PL-24〜26・PL-60） ------------------------------------

RETAIL = "retail"  # 状態: 据え置く財の小売の値段（財 → 値段）
RETAIL_MANUAL = "retail_manual"  # 状態: 命令で上書きした小売の値段（財 → 値段）
DUAL = "dual"  # 状態: 二重の値段に移し終えた（PL-60。ない島は前の版で、市場の段の初めに移す）
FRESH = "fresh"  # 状態: 計画経済に移ったばかり（市場の段の初めに卸の値段を今の原価にする）
OWED = "subsidy_owed"  # 状態: 払いきれなかった価格の補助（売り手 → 額。次のターンに先に払う）
GAP = "plan_gap"  # island.scratch: このターンの取引税（tax）と、売り手ごとの価格の補助（owed）
PRICE_SUBSIDY = "price_subsidy"  # 政府の支出の項目（価格の補助。人件費と運営費の組の中）
SAFE_RATE = -9000  # 小売 ÷ 卸 − 1 の下限（1万分の1）。指値の割り戻しが壊れないための守り


def start(island: IslandState, config: GameConfig, turn: int, moved: bool = False) -> None:
    """公定価格を今の値段から始める（計画経済に移ったときと、建国・移し替え）。小売の値段は卸 ×
    （1 ＋ 消費税の率）から始める（移ったときの価格の補助は 0）。計画経済に移ったとき（moved）は、
    市場の段の初めに原価を解いて卸の値段を今の原価にする（PL-60。建国の値段はもともと原価から解いて
    ある）。見直しの確かめは planning.review_turns ターン後から。"""
    state = state_of(island)
    state["prices"] = {g: p for g, p in sorted(island.economy.prices.items()) if p > 0}
    state.setdefault("manual", {})
    for key in ("costs", RETAIL, RETAIL_MANUAL, OWED, "started"):
        state.pop(key, None)
    state[DUAL] = True
    if moved:
        state[FRESH] = True
    state["review"] = turn + config.get("planning.review_turns")
    state["wage"] = island.economy.average_wage
    state.setdefault("priority", [])
    state.setdefault("demand", {})


def stop(island: IslandState) -> None:
    """計画経済をやめた: 公定価格と目標を消す（重点産業も）。小売の値段は消えて消費税に戻るので、
    物価の指数の前の値段を卸の値段につなぎ直す（税の分の段差を物価の下がりに数えない）。"""
    state = island.ext.get(KEY)
    if state and state.get(DUAL):
        _chain_cpi(island, {g: p for g, p in state.get("prices", {}).items() if p > 0})
        _write_off(island, state.pop(OWED, {}))  # 払っていない価格の補助は売った施設の赤字に
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
    solved = cost_prices(defs, config, base_resources(island), wage, fixed)
    state["costs"] = {g: int(solved[g]) for g in sorted(defs.goods) if solved.get(g)}
    state["wage"] = wage
    return state["costs"]


def outside(price: int, cost: int, band: float) -> bool:
    """公定価格 price と原価 cost の差が幅 band（割合）を超えたか。"""
    return abs(price - cost) > band * cost


def taxed(island: IslandState, price: int) -> int:
    """卸の値段 price に標準の取引税の率（計画経済の島の消費税の率）を掛けた小売の値段。"""
    return max(1, price + part(price, island.policy.consumption_tax))


def review(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> list[str]:
    """planning.review_turns ターンごとの確かめ: 原価を解き直し、原価との差が幅を超えた財だけ卸の
    値段を原価に改める。据え置く財も卸の値段は改める（据え置くのは小売の値段だけ。PL-60）。命令で
    上書きした財はそのまま。新しく出てきた財は原価から始める。改めた財を返す。"""
    state = state_of(island)
    costs = solve_costs(island, defs, config)
    band, _ = rules(island, config)
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
        elif outside(price, cost, band):
            prices[good] = cost
            revised.append(good)
    state["prices"] = prices
    state["review"] = turn + config.get("planning.review_turns")
    if revised:
        args = {"goods": revised, "band": round(band * 100)}
        island.log.append(LogEntry("official_prices_revised", args))
    return revised


def revise(island: IslandState, defs: Defs, config: GameConfig, goods: Sequence[str]) -> list[str]:
    """公定価格の改定（命令）: goods（空なら卸の値段を上書きしていない財すべて）の卸の値段を今の
    原価に改める。上書きしていた財を名指ししたら、上書きもやめる。名指しした据え置く財は小売の
    値段も卸 ×（1 ＋ 標準の率）に改め、小売を上書きした財を名指ししたら小売の上書きもやめる
    （PL-26・PL-60）。卸か小売の値段が変わった財を返す。"""
    state = state_of(island)
    costs = solve_costs(island, defs, config)
    manual = state.setdefault("manual", {})
    prices = state.setdefault("prices", {})
    named = list(goods)
    chosen = named or [g for g in sorted(defs.goods) if g not in manual]
    _, held = rules(island, config)
    changed = []
    for good in chosen:
        cost = costs.get(good)
        if not cost:
            continue
        before = retail_price(island, config, good)
        manual.pop(good, None)
        moved = prices.get(good) != cost
        prices[good] = cost
        if good in named:
            state.get(RETAIL_MANUAL, {}).pop(good, None)
            if good in held:
                state.setdefault(RETAIL, {})[good] = taxed(island, cost)
        if moved or retail_price(island, config, good) != before:
            changed.append(good)
    return changed


def _chain_cpi(island: IslandState, now: Mapping[str, int]) -> None:
    """物価の指数の前の値段（約定した値段の記録）を now につなぎ直す（体制や値段の数え方が変わった
    ターンの段差を、物価の上がり下がりに数えない）。"""
    from hakoniwa_core.economy.stats import PAID

    paid = island.economy.ext.get(PAID)
    if paid is None:
        return
    # まだ約定の記録のない財も（初めて約定したターンに段差が出ない）
    for good, price in now.items():
        if price > 0:
            paid[good] = price


def _migrate(island: IslandState, config: GameConfig) -> None:
    """前の版の計画経済の島を二重の値段に移す（PL-60）: 小売 ＝ 今の公定価格 ×（1 ＋ 消費税の
    率）、卸 ＝ 据え置く財は今の原価（前の据え置きの補助を足した額）・ほかの財は今の公定価格。家計の
    払う額と売り手の受け取る額は変わらず、変わるのは据え置く財を施設が買う値段だけ。"""
    state = state_of(island)
    prices = state.setdefault("prices", {})
    costs = state.get("costs", {})
    _, held = rules(island, config)
    retail = state.setdefault(RETAIL, {})
    for good in held:
        price = prices.get(good)
        if not price:
            continue
        retail[good] = taxed(island, price)
        if good not in state.get("manual", {}):
            prices[good] = max(price, costs.get(good, 0))
    state[DUAL] = True


def _fix_held_retail(island: IslandState, config: GameConfig) -> None:
    """据え置く財の小売の値段を決める（まだなければ今の卸 ×（1 ＋ 標準の率）から据え置く）。据え置く
    財から外した財の小売は消し、式の値段に戻す。"""
    state = state_of(island)
    _, held = rules(island, config)
    prices = state.get("prices", {})
    retail = state.setdefault(RETAIL, {})
    for good in [g for g in retail if g not in held]:
        retail.pop(good)
    for good in held:
        if good not in retail and prices.get(good):
            retail[good] = taxed(island, prices[good])


def prepare(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> dict[str, int]:
    """市場の段の初め: 確かめのターンなら原価を解き直して幅を超えた財を改め、公定価格を基準の
    値段にする。原価をまだ解いていなければ（計画経済に移ったターンと、前の版の島）解く。移った
    ばかりの島は卸の値段を今の原価に、前の版の島は二重の値段に移し、物価の指数の前の値段を小売に
    つなぎ直す（PL-60）。"""
    if not is_planned(island, defs):
        return {}
    state = state_of(island)
    if "prices" not in state:
        start(island, config, turn)
    if turn >= state.get("review", 0):
        review(island, defs, config, turn)
    elif "costs" not in state:
        solve_costs(island, defs, config)
    if state.pop(FRESH, False):
        manual = state.get("manual", {})
        for good, cost in state["costs"].items():
            if good not in manual and good in state["prices"]:
                state["prices"][good] = cost
    if not state.get(DUAL):
        _migrate(island, config)
    _fix_held_retail(island, config)
    if "started" not in state:  # 移ったターン: 物価の指数を小売の値段につなぎ直す
        state["started"] = turn
        _chain_cpi(island, retail_prices(island, config))
    apply_prices(island)
    return official(island)


def apply_prices(island: IslandState) -> None:
    """公定価格を島の値段（基準の値段）にする。"""
    for good, price in official(island).items():
        island.economy.prices[good] = price


def retail_price(island: IslandState, config: GameConfig, good: str) -> int:
    """家計が good に払う小売の値段（PL-60）: 命令で上書きした財はその値段、据え置く財は据え置いた
    値段、ほかは卸の値段 ×（1 ＋ 標準の取引税の率）。卸の値段がなければ 0。"""
    state = island.ext.get(KEY, {})
    whole = state.get("prices", {}).get(good, 0)
    if whole <= 0:
        return 0
    # 卸の値段の確かめで卸だけが上がり続けても、小売 ÷ 卸 − 1 は SAFE_RATE より下げない（家計の
    # 注文の率と、画面・物価の指数の小売の値段を食い違わせない）
    least = math.ceil(whole * (BASIS + SAFE_RATE) / BASIS)
    manual = state.get(RETAIL_MANUAL, {}).get(good)
    if manual:
        return max(least, int(manual))
    fixed = state.get(RETAIL, {}).get(good)
    if fixed:
        _, held = rules(island, config)
        if good in held:
            return max(least, int(fixed))
    return taxed(island, whole)


def retail_prices(island: IslandState, config: GameConfig) -> dict[str, int]:
    """卸の値段のある財すべての小売の値段（財 → 値段）。"""
    return {g: retail_price(island, config, g) for g in sorted(official(island))}


def is_dual(island: IslandState) -> bool:
    """二重の値段で動いている島か（計画経済の島で、市場の段の初めに移し終えた）。"""
    return bool(island.ext.get(KEY, {}).get(DUAL))


def rate_of(island: IslandState, config: GameConfig, good: str) -> int:
    """家計が good を買うときに、卸の値段の代金に足す率（1万分の1）。計画経済の島は小売 ÷ 卸 − 1
    （取引税ならプラス、価格の補助ならマイナス）、ほかの島は消費税の率。"""
    tax = island.policy.consumption_tax
    if not is_dual(island):
        return tax
    whole = official(island).get(good, 0)
    if whole <= 0:
        return tax
    retail = retail_price(island, config, good)
    return max(SAFE_RATE, round((retail - whole) * BASIS / whole))


def rates(island: IslandState, config: GameConfig, goods: Iterable[str]) -> dict[str, int]:
    """財ごとの rate_of（家計の注文に使う）。"""
    return {g: rate_of(island, config, g) for g in goods}


def retail_floor(island: IslandState, config: GameConfig, good: str) -> int:
    """小売の値段の上書きの下限（卸の値段 × planning.retail.floor、切り上げ）。"""
    whole = official(island).get(good, 0)
    return max(1, math.ceil(whole * config.get("planning.retail.floor")))


# --- 取引税と価格の補助（PL-60） -----------------------------------------------------------


def split_gap(gap: int) -> tuple[int, int]:
    """約定の差 gap（家計が払う額 − 卸の額）を (取引税, 価格の補助) に分ける。消費税もここを通る
    （計画経済でない島の gap は消費税で、いつもプラス）。"""
    return max(0, gap), max(0, -gap)


def note_gap(
    island: IslandState, levy: int, short: int, seller: str, facility: int | None = None
) -> None:
    """家計の買い物の差を数えておく: levy は国庫に入れた取引税、short は売り手 seller（施設なら
    番号 facility）に足りない価格の補助（市場の段の終わりにまとめて払う）。計画経済の島だけ。"""
    if not is_dual(island) or (levy <= 0 and short <= 0):
        return
    gap = island.scratch.setdefault(GAP, {"tax": 0, "owed": {}})
    gap["tax"] += levy
    if short > 0:
        key = f"{seller}|{'' if facility is None else facility}"
        gap["owed"][key] = gap["owed"].get(key, 0) + short


def owed_total(island: IslandState) -> int:
    """前のターンまでに払いきれなかった価格の補助の合計（国債の段の発行の見込みと予算に入れる）。"""
    return sum(island.ext.get(KEY, {}).get(OWED, {}).values())


def _payable(island: IslandState, config: GameConfig, turn: int) -> bool:
    """払いきれない価格の補助を国の未払いにして国債で埋められるか。新しい国債が売れず（不履行の後の
    締め出し）、中央銀行の引き受けもない間は埋められない。"""
    from hakoniwa_core.economy import debt

    if not debt.market_closed(island, config, turn):
        return True
    policy = island.policy
    return policy.bond_unsold == "underwrite" and not policy.cb_independent


def _write_off(island: IslandState, owed: Mapping[str, int]) -> int:
    """払えない価格の補助を、売った施設の損にする（受け取った補助金をマイナスで付ける。売上は付加
    価値なので触らない）。取り壊した施設と施設でない売り手の分は帳簿に付けない。合計の額を返す。"""
    total = 0
    found = {f.id for f in island.facilities}
    for key, amount in owed.items():
        number = key.split("|", 1)[1]
        if number and int(number) in found:
            books.add(island, int(number), books.SUBSIDY, -amount)
        total += amount
    return total


def pay_price_subsidies(island: IslandState, config: GameConfig, turn: int) -> None:
    """市場の段の終わり: 取引税と価格の補助を差し引き、補助を売り手に払う（PL-60）。

    前のターンまでの未払いを先に、次にこのターンの分を、支出の項目 price_subsidy（人件費と運営費の
    組の取り置き）と国庫の残りから払う。払いきれない分は売り手への国の未払いにして持ち越し、国債の
    段の発行の見込みに入れる（表の 2-1）。新しい国債が売れず中央銀行の引き受けもない間だけ、払えない
    分を売った施設の赤字にする。国庫直轄の施設と国の倉庫（売り手が国庫）は帳簿にだけ残す。取引税で
    埋まった分は項目の支出に数えない（次のターンの取り置きは差し引いた不足に余裕を足した額）。"""
    state = island.ext.get(KEY)
    gap = island.scratch.pop(GAP, None) or {"tax": 0, "owed": {}}
    # 経済体制の変更は命令の段（生産と市場の前）なので、数えた差が体制の外で残ることはない
    if not state or not state.get(DUAL):
        return
    levy = gap["tax"]
    now = {k: v for k, v in sorted(gap["owed"].items()) if v > 0}
    carried = {k: v for k, v in sorted(state.pop(OWED, {}).items()) if v > 0}
    found = {f.id for f in island.facilities}

    def entity(key: str) -> str | None:
        name, number = key.split("|", 1)
        if number and int(number) not in found:
            return None  # 取り壊した施設には払わない
        return name

    payer = Payer(ledger.GOV, PRICE_SUBSIDY)
    unpaid: dict[str, int] = {}
    paid_now = 0
    for batch, is_now in ((carried, False), (now, True)):
        paying = {k: v for k, v in batch.items() if entity(k) not in (None, ledger.GOV)}
        money = funds.available(island, payer)
        wanted = sum(paying.values())
        for key, amount in paying.items():
            paid = amount if wanted <= money else amount * money // wanted
            funds.pay(island, payer, entity(key) or "", paid, "subsidy")
            if amount > paid:
                unpaid[key] = amount - paid
            if is_now:
                paid_now += paid
    fiscal = funds.fiscal_state(island)
    used, reserved = fiscal["used"], fiscal["reserved"]
    covered = min(used.get(PRICE_SUBSIDY, 0), levy, paid_now)
    if covered > 0:  # 取引税で埋まった分は項目の支出に数えない（取り置きも同じだけ外し、残りの
        # 取り置きの額を変えない）
        used[PRICE_SUBSIDY] -= covered
        reserved[PRICE_SUBSIDY] = max(0, reserved.get(PRICE_SUBSIDY, 0) - covered)
    subsidy = sum(now.values())
    short = sum(unpaid.values())
    stats = island.stats
    stats["plan.turnover_tax"] = levy
    stats["plan.subsidy"] = subsidy
    stats["plan.subsidy_unpaid"] = short
    if unpaid and _payable(island, config, turn):
        state[OWED] = unpaid
        state.pop("subsidy_lost", None)
        if not state.get("subsidy_short"):
            island.log.append(LogEntry("plan_subsidy_short", {"amount": short}))
        state["subsidy_short"] = True
    elif unpaid:  # 国債で埋められない: 施設の売り手の分は売った施設の赤字にする
        mine = {k: v for k, v in unpaid.items() if k.split("|", 1)[1]}
        _write_off(island, mine)
        # 施設でない売り手（海外の商人の売りを受け取る中央銀行）の分は持ち越して、払えるときに払う
        others = {k: v for k, v in unpaid.items() if k not in mine}
        if others:
            state[OWED] = others
        if not state.get("subsidy_lost"):
            island.log.append(LogEntry("plan_subsidy_lost", {"amount": short}))
        state["subsidy_lost"] = True
    else:
        state.pop("subsidy_lost", None)
        state.pop("subsidy_short", None)
    if subsidy > levy and not state.get("subsidy_over"):
        args = {"paid": subsidy, "levied": levy}
        island.log.append(LogEntry("plan_subsidy_over", args))
        state["subsidy_over"] = True
    elif levy >= subsidy:
        state.pop("subsidy_over", None)


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


def staff_ratio(island: IslandState, defs: Defs, config: GameConfig, f: Facility) -> int:
    """人員の枠（PL-59）のある施設が、その月に残す人数で動ける稼働率の上限（1000 分の 1）。枠に
    向けて減らしている途中は、その月に手放した後の人数で見る。枠がなければ 1000。"""
    limit = staff_limit(island, defs, config, f)
    if limit is None:
        return fac.FULL
    return fac.ratio(limit, fac.workers_of(defs, f))


def fill_share(island: IslandState, defs: Defs, config: GameConfig, good: str) -> int | None:
    """財 good の生産目標を同じ財を作る国営施設に割り振るときの、枠のない施設の稼働率
    （1000 分の 1）。

    どの施設も同じ割合 s で動き、人員の枠のある施設は残す人数で動ける割合（staff_ratio）までで止め、
    その残りを枠のない施設に回す（Σ min(s, 枠の割合) × 全力の産出 = 目標）。回す先がなければ全力の
    1000 を返す（その財は足りなくなり、行列が伸びる）。需要の記録がなければ None。このターンの値を
    scratch に置く。"""
    cache = island.scratch.setdefault("plan_fill", {})
    if good in cache:
        return cache[good]
    goal = goal_of(island, config, good)
    if goal is None:
        cache[good] = None
        return None
    power = config.get("power.good")
    units = []
    for f in island.facilities:
        if not f.built or f.owner != OWNER_STATE or fac.is_housing(config, f):
            continue
        qty = fac.outputs_of(defs, f).get(good, 0)
        if good == power or qty <= 0:
            continue
        units.append((staff_ratio(island, defs, config, f), fac.scale(qty, fac.equipment_cap(f))))
    rest = sum(full for _, full in units)
    if rest <= 0:
        cache[good] = fac.FULL
        return fac.FULL
    left = goal
    share = fac.FULL
    for cap, full in sorted(units):
        share = fac.ratio(max(0, left), rest)
        if share <= cap:
            break
        left -= fac.scale(full, cap)
        rest -= full
        share = fac.FULL
        if rest <= 0:
            break
    cache[good] = share
    return share


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
    own = staff_ratio(island, defs, config, f)
    plan = None
    for good, full in sorted(fac.outputs_of(defs, f).items()):
        if good == power or full <= 0:
            continue
        share = fill_share(island, defs, config, good)
        if share is None:
            continue
        share = min(share, own)
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
    # 行列の重みと強制貯蓄は、家計が払う小売の値段で数える（PL-60）
    prices = retail_prices(island, config) if is_dual(island) else official(island)
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
