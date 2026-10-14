"""標準の計画: 島の様子を見て、自動で命令を並べる簡単な方針。

一括シミュレーション（関門のテスト）と、開発用のサンプルの島で、プレイヤーの代わりに使います。
ゲームのルールではないので、ここの数値は game.yaml ではなくこのファイルに置いています。

方針（毎ターン、工事中の施設が少なければ、上から見て最初に足りないものを1つ建てる。同じ物を作る
施設に動ききっていないものがあれば、それは建てない）:
1. 食料の生産が食べる量に届かなければ、農場か漁港
2. 発電の余裕が少なければ、発電所（石炭があれば火力、なければ水力）
3. 工事の資材が国の倉庫になく、島に作る施設もなければ、それを作る施設（工事が止まらないように、
   同時に進める工事の数を超えても建てる）。作る施設がどれも原料を待って止まっていれば、その原料を
   作る施設
4. 投入財の作る量が使う量に届かなければ、それを作る施設
5. 消費財の生産が住民1人あたりの目安に届かないか、前のターンの市場で売りに出た量が買いたい量に
   届かなければ、消費財工場（加工食品も、売りに出た量が買いたい量に届かなければ食品工場）
6. 生産の連鎖（鉄鉱石 → 鋼鉄 → 機械 → 新しい工場）を太くする: 製鉄所と機械工場を増やす（失業率が
   ふつうより高く、人手の足りない施設が少ない間だけ）
教育（フェーズ2-3）: 学校の定員が子どもの数の SCHOOL_TARGET に届かなければ、食料・発電・工事の資材の
次に学校を建てます。建っている施設（工事中を含む）が要る技術者が、島の技術者の ENGINEER_TARGET 倍を
超え、大学がなければ大学を建てます。どちらも、資材が倉庫にあるか島で作っているときだけで、同じ資材を
待っている工事があるあいだは建てません（大学は、民営工場の工事に要る分の資材を倉庫に残します）。
資材が倉庫に足りず島のどの施設も作っていなければ、先にその資材を作る施設を建てます。
4〜6 では、投入財に余裕のない施設は建てません（鉱山を増やせないのに製鉄所を増やすと、鉄鉱石の
取り合いで値段が跳ね上がるだけなので）。
ほかの工事が待っている資材を建った施設がどれも作っていなければ、それを使う施設は、その資材を作る
施設でない限り建てません（初めの倉庫の機械を発電所に使うと、製鉄所と機械工場が互いを待って止まる）。
まだ島のどの施設も作っていない工事の資材（鋼鉄と機械）は、それを作る施設を1つ建てる分を国の倉庫に
残し、発電所のほかの工事に使わず、売りにも出しません。
失業率が高い間は、同時に進める工事を1つ増やします（工事の賃金と資材の買い物が働き口になる）。
お金の経済の島では、国の倉庫にある財のうち島の施設が仕入れに困っている物（建国のときの石炭など）を、
毎ターン市場に売ります（建てる命令と一緒に出します）。
民営工場が建てられる島では、島で作っていない工事の資材（機械など）も、国の工事に取っておく分を除い
て、民営の工事が待っている量か1つの工事に要る量まで売りに出します（国の倉庫の物は、売りに出さなけれ
ば民間は買えないので）。民営の工事が待っていなければ、OFFER_TURNS + 1 ターンに一度にします。
建てた施設は、陸地を通る一番短い送電線で送電網につなぎ、施設のマスと周りの陸のマスのうち交通の
整備度が ROAD_LEVEL に届かないマスに道路を引きます（フェーズ2-3。インフラの係数を 1 以上にする。
同じ資材を待っている工事があるあいだは引きません）。
上下水道が足りずに育たない町があれば、そのマスに上下水道を引きます。

お金の経済の島では、初めに失業手当を平均賃金の BENEFIT % にし、BUDGET_TURNS ターンごとに
公共事業費（国の工事とインフラの年の予算）と教育費を見直します。教育費は、学校と大学を全力で
動かす年の費用（今の賃金と、値幅の上端の教材の値段で）の EDUCATION_MARGIN 倍です。
住民1人あたり PUBLIC_WORKS を基本に、失業率がふつう（5%）より高ければ増やし、物価上昇率が高ければ
減らします（額を物価や GDP に合わせて動かさないので、物価が下がり続けたり上がり続けたりしにくい）。
政府の借金の対年GDP比が DEBT_LIMIT を超えた分に応じても減らします（利回りが上がって利払いが
膨らみ続けないように）。
電気料金が初めの値段の POWER_CAP 倍を超えたら、そこを公共料金の上限にします。
民営工場が建てられる島（市場経済と混合経済）では、空いた工業団地のマスが ZONE_SPARE より少なければ、
町の通勤圏の空いた平地と、地下資源のある島では山を ZONE_BATCH マスまで工業団地に指定します（町の
すぐ隣は町が育つ余地として残す）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy.prices import initial_prices, unit_cost
from hakoniwa_core.geo import components, distance, shortest_path, within
from hakoniwa_core.modules.firms import private_allowed
from hakoniwa_core.state import (
    ORDER_PENDING,
    OWNER_PRIVATE,
    OWNER_STATE,
    ZONE_INDUSTRIAL,
    ZONE_NONE,
    IslandState,
    OrderState,
)
from hakoniwa_core.units import QUANTITY_SCALE, money_of, to_milli

MAX_SITES = 2  # 同時に進める工事の数
POWER_MARGIN = 1.25  # 発電の能力を、使う量のこの倍に保つ
FOOD_MARGIN = 1.1  # 食料の生産を、食べる量のこの倍に保つ
CONSUMER_MARGIN = 1.0  # 消費財の生産を、住民1人あたりの目安のこの倍に保つ
MARKET_SHORT = 0.9  # 前のターンの市場で、売りに出た量が買いたい量のこの割合より少なければ足りない
BUSY_UNEMPLOYMENT = 0.1  # 失業率がこれを超えている間は、同時に進める工事を1つ増やす
MAX_MISSING_WORKERS = 1000  # 足りない労働者がこの人数より多い間は、連鎖を太くしない
BUDGET_TURNS = 6  # 公共事業費を見直す間隔
PUBLIC_WORKS = 600  # 公共事業費の基本（住民1人あたりの年の額）
UNEMPLOYMENT_RESPONSE = 5.0  # 失業率がふつうより 1% 高いごとに、基本のこの % だけ増やす
INFLATION_RESPONSE = 5.0  # 物価上昇率が INFLATION_LIMIT より 1% 高いごとに、基本のこの % だけ減らす
INFLATION_LIMIT = 0.05
NATURAL_UNEMPLOYMENT = 0.05
DEBT_LIMIT = 1.0  # 政府の借金の対年GDP比がこれを超えたら、公共事業費を減らす
DEBT_RESPONSE = 0.5  # 対年GDP比が DEBT_LIMIT より 0.1 高いごとに、基本のこの × 10 % だけ減らす
WORKS_RANGE = (0.2, 2.0)  # 基本に対する倍率の下限と上限
UNDERUSED = 0.7  # 同じ物を作る施設の稼働率がこれより低い間は、増やさない
POWER_CAP = 2.0  # 電気料金が初めの値段のこの倍を超えたら、そこを公共料金の上限にする
BENEFIT = 40  # 失業手当（平均賃金に対する %）
SALE_TURNS = 2  # 国の倉庫から一度に売りに出す量（島の施設が使う量の何ターン分か）
OFFER_TURNS = 5  # 民営の工事が待っていない資材は、直近このターン数に売りに出ていなければ売りに出す
ZONE_SPARE = 3  # 空いた工業団地のマスがこれより少なければ、工業団地を足す
ZONE_BATCH = 6  # 一度に工業団地に指定するマスの数
# 連鎖を太くするときに、この順で数をそろえていく施設
GROWTH = ("steel_mill", "machinery_plant", "building_materials_plant")
# 教育とインフラ（フェーズ2-3）
SCHOOL = "school"
UNIVERSITY = "university"
SCHOOL_TARGET = 0.8  # 学校の定員を、子どもの数のこの割合に保つ
ENGINEER_TARGET = 0.8  # 要る技術者が、島の技術者のこの倍を超えたら大学を建てる
EDUCATION_MARGIN = 1.3  # 教育費を、学校と大学を全力で動かす年の費用のこの倍にする
ROAD_LEVEL = 0.6  # 新しい施設の周りに、交通の整備度がこれに届かないマスがあれば道路を引く


def plan_turn(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> list[tuple[str, dict]]:
    """ターン turn の処理の前に足す命令（種類, 中身）の並び。"""
    if any(o.status == ORDER_PENDING for o in island.orders):
        return []
    return _warehouse_sales(island, defs, config) + _plan_rest(island, defs, config, turn)


def _plan_rest(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> list[tuple[str, dict]]:
    policies = (
        _labor_policy_order(island),
        _budget_order(island, defs, config, turn),
        _price_cap_order(island, defs, config),
        _zone_order(island, defs, config),
        _water_order(island, defs, config),
    )
    for policy in policies:
        if policy is not None:
            return [policy]
    sites = [f for f in island.facilities if not f.built]
    limit = MAX_SITES + (1 if island.stats.get("unemployment", 0) > BUSY_UNEMPLOYMENT else 0)
    if len(sites) >= limit + 1:
        return []
    if len(sites) >= limit:  # 資材が手に入らずに止まっている工事のためだけに、もう1つ建てる
        candidates: Iterator[tuple[str, str]] = _material_producers(island, defs, config)
    else:
        candidates = _candidates(island, defs, config, turn)
    for kind, recipe in candidates:
        if _underused(island, defs, config, kind, recipe) or not _materials_ok(
            island, defs, config, kind
        ):
            continue
        pos = _site_for(island, defs, config, kind, recipe)
        if pos is not None:
            break
    else:
        return []
    payload: dict[str, Any] = {"facility": kind, "x": pos[0], "y": pos[1]}
    if fac.default_recipe(defs, kind) is None:
        payload["recipe"] = recipe
    orders: list[tuple[str, dict]] = [("build", payload)]
    line = _power_line(island, defs, pos)
    if line:
        orders.append(("build_power_line", {"tiles": [list(p) for p in line[:64]]}))
    road = _road_order(island, defs, pos)
    if road is not None:
        orders.append(road)
    return orders


def queue_orders(island: IslandState, orders: list[tuple[str, dict]]) -> None:
    """命令を島の命令の列の後ろに足す。"""
    next_id = max((o.id for o in island.orders), default=0) + 1
    next_seq = max((o.seq for o in island.orders), default=0) + 1
    for i, (kind, payload) in enumerate(orders):
        order = OrderState(id=next_id + i, seq=next_seq + i, kind=kind, payload=payload)
        island.orders.append(order)


# --- 国の倉庫 ---------------------------------------------------------------------


def _warehouse_sales(island: IslandState, defs: Defs, config: GameConfig) -> list[tuple[str, dict]]:
    """国の倉庫にある財のうち、島の施設が仕入れに困っている物を市場に売る。

    指値は今の値段（初めの値段より安ければ初めの値段）。

    建国のときに倉庫にあった石炭などを、火力発電所が使えるようにする（工事に取っておく分と、まだ誰も
    作っていない資材を作る施設を建てる分は売らない）。
    """
    if not island.has_economy:
        return []
    need: dict[str, int] = {}
    stuck: set[str] = set()
    for f in island.facilities:
        if f.built:
            for good, qty in fac.recipe_of(defs, f)["inputs"].items():
                need[good] = need.get(good, 0) + qty
            if fac.short_of_inputs(f.status):
                stuck.add(f.status.split(":", 1)[1])
    short = {m.good for m in island.markets if m.supply < m.demand}
    reserved = _reserved_materials(island, defs)
    for good, qty in _chain_keep(island, defs, config).items():
        reserved[good] = reserved.get(good, 0) + qty
    initial = initial_prices(defs, config, island.resources)
    orders = []
    sold: dict[str, int] = {}
    for good, qty in sorted(need.items()):
        spare = stock.total(island.warehouse, good) - reserved.get(good, 0)
        if spare <= 0 or (good not in stuck and good not in short):
            continue
        sold[good] = min(spare, qty * SALE_TURNS)
    for good, qty in sorted(_private_materials(island, defs).items()):
        spare = stock.total(island.warehouse, good) - reserved.get(good, 0) - sold.get(good, 0)
        if spare > 0:
            sold[good] = sold.get(good, 0) + min(spare, qty)
    for good, qty in sorted(sold.items()):
        payload = {
            "good": good,
            "side": "sell",
            "quantity": qty / QUANTITY_SCALE,
            "limit": max(island.economy.prices.get(good, 0), initial.get(good, 0)),
        }
        orders.append(("warehouse_trade", payload))
    return orders


def _private_materials(island: IslandState, defs: Defs) -> dict[str, int]:
    """民営工場が建てられる島で、島で作っていない工事の資材の、売りに出す量（民営の工事が待っている
    量と、民間が建てられる施設の1つの工事に要る量の大きい方）。民営の工事が待っていない資材は、直近
    OFFER_TURNS ターンに売りに出ていなければ出す（売りに出ていることが民間に見えれば足りるので、行政
    力を毎ターンは使わない）。"""
    amounts = _private_needs(island, defs)
    if not amounts:
        return {}
    made = {g for f in island.facilities if f.built for g in fac.recipe_of(defs, f)["outputs"]}
    waiting = _reserved_materials(island, defs, OWNER_PRIVATE)
    for good, qty in waiting.items():
        amounts[good] = max(amounts.get(good, 0), qty)
    offered = island.economy.finance.get("supply", {})
    return {
        g: q
        for g, q in amounts.items()
        if g not in made and (waiting.get(g, 0) > 0 or not any(offered.get(g, [])[-OFFER_TURNS:]))
    }


def _private_needs(island: IslandState, defs: Defs) -> dict[str, int]:
    """民営工場が建てられる島で、民間が建てられる施設の1つの工事に要る資材の量（資材ごとに一番
    多い施設の量）。民営工場が建てられなければ空。"""
    amounts: dict[str, int] = {}
    for kind, fdef in sorted(defs.facilities.items()):
        if private_allowed(defs, island, kind):
            for good, qty in fdef["construction"].get("materials", {}).items():
                amounts[good] = max(amounts.get(good, 0), qty)
    return amounts


# --- 公共事業費 -------------------------------------------------------------------


def _labor_policy_order(island: IslandState) -> tuple[str, dict] | None:
    """失業手当がなければ、平均賃金の BENEFIT % にする。

    失業者の暮らしと、物の売れ行きを支える。
    """
    if not island.has_economy or island.policy.unemployment_benefit > 0:
        return None
    return ("set_labor_policy", {"unemployment_benefit": BENEFIT})


def _budget_order(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> tuple[str, dict] | None:
    """公共事業費と教育費を見直す命令（どちらも変えるほどの差がなければ None）。"""
    if not island.has_economy or turn % BUDGET_TURNS:
        return None
    stats = island.stats
    scale = 1.0
    scale += UNEMPLOYMENT_RESPONSE * (stats.get("unemployment", 0) - NATURAL_UNEMPLOYMENT)
    scale -= INFLATION_RESPONSE * max(0.0, stats.get("inflation", 0) - INFLATION_LIMIT)
    scale -= DEBT_RESPONSE * max(0.0, stats.get("debt_ratio", 0) - DEBT_LIMIT)
    scale = min(WORKS_RANGE[1], max(WORKS_RANGE[0], scale))
    targets = {
        "construction": int(population_of(island) * PUBLIC_WORKS * scale),
        "education": _education_cost(island, defs, config),
    }
    payload = {}
    for item, target in targets.items():
        current = island.policy.budget.get(item, 0)
        if not current or abs(target - current) > current // 10:
            payload[item] = target
    return ("set_budget", payload) if payload else None


def _education_cost(island: IslandState, defs: Defs, config: GameConfig) -> int:
    """学校と大学（工事中を含む）を全力で動かす年の費用 × EDUCATION_MARGIN。"""
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    band = 1 + config.get("market.band")
    per_turn = 0
    for f in island.facilities:
        if not defs.facilities[f.kind].get("education"):
            continue
        per_turn += fac.workers_needed(defs, f.kind) * wage
        for good, qty in fac.recipe_of(defs, f)["inputs"].items():
            per_turn += money_of(int(island.economy.prices.get(good, 0) * band), qty)
    return int(per_turn * 12 * EDUCATION_MARGIN)


def _price_cap_order(
    island: IslandState, defs: Defs, config: GameConfig
) -> tuple[str, dict] | None:
    """電気料金が初めの値段の POWER_CAP 倍を超えたら、そこを公共料金の上限にする（1度だけ）。"""
    power = config.get("power.good")
    if not island.has_economy or power in island.policy.price_cap:
        return None
    initial = initial_prices(defs, config, island.resources).get(power, 0)
    if initial <= 0 or island.economy.prices.get(power, 0) <= initial * POWER_CAP:
        return None
    return ("set_price_cap", {"good": power, "price": int(initial * POWER_CAP)})


# --- 何を建てるか ---------------------------------------------------------------


def _underused(island: IslandState, defs: Defs, config: GameConfig, kind: str, recipe: str) -> bool:
    """同じ物を作る施設に、動ききっていない（稼働率が UNDERUSED 未満の）ものがあるか。

    売れない・人手が足りないなど、同じ物を作る施設を増やしても役に立たないので建てない（工事が
    済んだばかりで、まだ動いていない施設も数える）。発電所はいつも使われる分だけ動くので見ない
    （足りるかは発電の余裕で決める）。投入財が手に入らずに止まっている施設も見ない（投入財の
    いらない作り方なら役に立つ）。
    """
    power = config.get("power.good")
    outputs = set(defs.facilities[kind]["recipes"][recipe]["outputs"]) - {power}
    for f in island.facilities:
        if f.built and outputs & set(fac.recipe_of(defs, f)["outputs"]):
            if fac.short_of_inputs(f.status):
                continue
            if f.operating_rate < fac.FULL * UNDERUSED or f.status == fac.STATUS_STARTING:
                return True
    return False


def _flows(island: IslandState, defs: Defs, config: GameConfig) -> tuple[dict, dict]:
    """全力で動いたときの、財ごとの作る量と使う量（工事中の施設も含める）。

    投入財が手に入らずに止まっている施設と、投入財を島の中で作れない施設（石炭のない島の火力発電所
    など。国の倉庫の分を使い切れば止まる）の作る量は数えない。
    """
    supply: dict[str, int] = {}
    demand: dict[str, int] = {}
    for f in island.facilities:
        if fac.is_housing(config, f):
            continue
        recipe = fac.recipe_of(defs, f)
        stuck = f.built and fac.short_of_inputs(f.status)
        stuck = stuck or not _recipe_ok(defs, island, recipe, frozenset())
        for good, qty in recipe["outputs"].items():
            supply[good] = supply.get(good, 0) + (0 if stuck else qty)
        for good, qty in recipe["inputs"].items():
            demand[good] = demand.get(good, 0) + qty
    population = sum(t.population for t in island.tiles)
    power = config.get("power.good")
    demand[power] = demand.get(power, 0) + to_milli(population * config.get("power.household_use"))
    return supply, demand


def population_of(island: IslandState) -> int:
    return sum(t.population for t in island.tiles)


def _count(island: IslandState, kind: str) -> int:
    return sum(1 for f in island.facilities if f.kind == kind)


def _producers(
    defs: Defs, config: GameConfig, island: IslandState, good: str
) -> Iterator[tuple[str, str]]:
    """good を作れる（この島で建てて動かせる）施設と作り方を、今の値段で原価の安い順に。"""
    if island.has_economy:
        prices, wage = island.economy.prices, island.economy.average_wage
    else:
        prices = initial_prices(defs, config, island.resources)
        wage = config.get("labor.initial_wage")
    found = []
    for kind, fdef in defs.facilities.items():
        if fdef.get("buildable") is False:
            continue
        for rname, recipe in fdef["recipes"].items():
            if good in recipe["outputs"] and _feasible(defs, island, kind, rname):
                found.append((unit_cost(defs, config, kind, recipe, prices, wage), kind, rname))
    for _, kind, rname in sorted(found):
        yield kind, rname


def _feasible(defs: Defs, island: IslandState, kind: str, rname: str) -> bool:
    """この島の資源で、この作り方の投入財をどれも島の中で作れるか（元の原料までたどる）。"""
    return _recipe_ok(defs, island, defs.facilities[kind]["recipes"][rname], frozenset())


def _recipe_ok(defs: Defs, island: IslandState, recipe: Any, seen: frozenset[str]) -> bool:
    resource = recipe.get("requires_resource")
    if resource is not None and resource not in island.resources:
        return False
    return all(_makeable(defs, island, good, seen) for good in recipe["inputs"])


def _makeable(defs: Defs, island: IslandState, good: str, seen: frozenset[str]) -> bool:
    if good in seen:
        return False
    return any(
        _recipe_ok(defs, island, recipe, seen | {good})
        for fdef in defs.facilities.values()
        if fdef.get("buildable") is not False
        for recipe in fdef["recipes"].values()
        if good in recipe["outputs"]
    )


def _reserved_materials(
    island: IslandState, defs: Defs, owner: str = OWNER_STATE
) -> dict[str, int]:
    """持ち主が owner の工事中の施設が、これから使う資材のうち手元にない量（国の倉庫の資材を使える
    のは国の工事だけ。民営の工事は市場で買う）。"""
    reserved: dict[str, int] = {}
    for f in island.facilities:
        if not f.built and f.owner == owner:
            construction = defs.facilities[f.kind]["construction"]
            done = f.progress / (construction["turns"] * fac.FULL)
            for good, qty in construction.get("materials", {}).items():
                left = qty - int(qty * done) - stock.total(f.stock, good)
                reserved[good] = reserved.get(good, 0) + max(0, left)
    return reserved


def _material_producers(
    island: IslandState, defs: Defs, config: GameConfig
) -> Iterator[tuple[str, str]]:
    """工事の資材のうち、国の倉庫に足りず、島に作る施設（工事中も含む）もない物を作る施設。

    作る施設があっても、どれも原料が手に入らずに止まっていて、その原料を作る施設がなければ、原料を
    作る施設（建材工場が石材を待っているのに採石場が閉まった島の採石場）。工事が資材を待って
    同時に進める工事の数がいっぱいでも、これは建てる（資材が出てこないまま工事が詰まるので）。
    """
    made = {good for f in island.facilities for good in fac.recipe_of(defs, f)["outputs"]}
    for good, qty in sorted(_reserved_materials(island, defs).items()):
        if stock.total(island.warehouse, good) >= qty:
            continue
        if good not in made:
            yield from _producers(defs, config, island, good)
            continue
        makers = [
            f for f in island.facilities if f.built and good in fac.recipe_of(defs, f)["outputs"]
        ]
        if makers and all(fac.short_of_inputs(f.status) for f in makers):
            short = {f.status.split(":", 1)[1] for f in makers}
            for need in sorted(short - made):
                yield from _producers(defs, config, island, need)


def _materials_ok(island: IslandState, defs: Defs, config: GameConfig, kind: str) -> bool:
    """工事の資材が、倉庫にあるか（ほかの工事の残りの分と、連鎖を始める分を除いて）、島の中で作れるか。

    ほかの工事が待っている資材を建った施設がどれも作っていなければ、島で作れるはずでも、その資材を
    作る施設（を建てる工事）でない限り建てない。製鉄所と機械工場の工事は互いの鋼鉄と機械を待つので、
    初めの倉庫の分をほかの工事に使うと、連鎖がいつまでも始まらない。同じわけで、まだ誰も作っていない
    資材を作る施設を建てる分（_chain_keep）も、その施設と発電所のほかには使わない（電気はどの施設も
    使うので、発電所は先にする）。
    """
    reserved = _reserved_materials(island, defs)
    made = _made(island, defs)
    outputs = {g for r in defs.facilities[kind]["recipes"].values() for g in r["outputs"]}
    first = _missing_materials(island, defs) | {config.get("power.good")}
    keep = {} if outputs & first else _chain_keep(island, defs, config)
    short = {g for g, qty in reserved.items() if stock.total(island.warehouse, g) < qty}
    helps = bool(outputs & short)
    for good, qty in defs.facilities[kind]["construction"].get("materials", {}).items():
        have = stock.total(island.warehouse, good) - reserved.get(good, 0) - keep.get(good, 0)
        if have >= qty or good in made:
            continue
        if keep.get(good, 0) > 0 or (reserved.get(good, 0) > 0 and not helps):
            return False
        if not _makeable(defs, island, good, frozenset()):
            return False
    return True


def _missing_materials(island: IslandState, defs: Defs) -> set[str]:
    """工事の資材のうち、島のどの施設（工事中を含む）も作らない物。"""
    present = {good for f in island.facilities for good in fac.recipe_of(defs, f)["outputs"]}
    needed = {
        g for fdef in defs.facilities.values() for g in fdef["construction"].get("materials", {})
    }
    return needed - present


def _chain_keep(island: IslandState, defs: Defs, config: GameConfig) -> dict[str, int]:
    """国の倉庫に残しておく資材: 工事の資材のうち島のどの施設（工事中を含む）も作らない物ごとに、
    それを作る施設を1つ建てる分の資材のうち、建った施設が作っていない物の量（製鉄所の機械と機械工場の
    鋼鉄）。島で作れない物は残さない。"""
    made = _made(island, defs)
    keep: dict[str, int] = {}
    for good in sorted(_missing_materials(island, defs)):
        for kind, _ in _producers(defs, config, island, good):
            for g, qty in defs.facilities[kind]["construction"].get("materials", {}).items():
                if g not in made:
                    keep[g] = max(keep.get(g, 0), qty)
            break
    return keep


def _made(island: IslandState, defs: Defs) -> set[str]:
    """建った施設が作っている財。"""
    return {
        good for f in island.facilities if f.built for good in fac.recipe_of(defs, f)["outputs"]
    }


def _candidates(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> Iterator[tuple[str, str]]:
    """建てたい施設と作り方を、建てたい順に。"""
    supply, demand = _flows(island, defs, config)
    power = config.get("power.good")
    stats = island.stats

    food_supply = sum(
        qty * defs.goods[g]["food"]["nutrition"]
        for g, qty in supply.items()
        if "food" in defs.goods[g]
    )
    food_supply -= sum(qty for g, qty in demand.items() if "food" in defs.goods[g])
    if food_supply < to_milli(stats.get("food.need", 0)) * FOOD_MARGIN:
        if _count(island, "fishing_port") * 2 < _count(island, "farm"):
            yield ("fishing_port", "default")
        yield ("farm", "default")

    if supply.get(power, 0) < demand.get(power, 0) * POWER_MARGIN:
        yield from _producers(defs, config, island, power)

    yield from _material_producers(island, defs, config)

    yield from _education(island, defs, config)

    def spare(kind: str, rname: str) -> bool:
        """投入財に余裕がある（作る量が、今使う量とこの施設の使う量に届く）か。

        余裕がなければ建てない（投入財の取り合いで値段が上がるだけなので。投入財を作る施設は
        先に候補に挙がる）。まだ島で作っていない投入財は見ない（使う施設が先にできて、足りない
        投入財として作る施設が候補に挙がる）。電力は発電の余裕で別に決める。
        """
        recipe = defs.facilities[kind]["recipes"][rname]
        return all(
            supply.get(good, 0) == 0 or supply[good] >= demand.get(good, 0) + qty
            for good, qty in recipe["inputs"].items()
            if good != power
        )

    for good in sorted(demand, key=lambda g: (defs.goods[g].get("tier", 9), g)):
        if good != power and supply.get(good, 0) < demand[good]:
            yield from (c for c in _producers(defs, config, island, good) if spare(*c))

    target = population_of(island) * config.get("household.consumer_goods_target")
    markets = {m.good: m for m in island.markets}
    for good, gdef in sorted(defs.goods.items()):
        if gdef.get("consumer") and good != power:
            record = markets.get(good)
            short = record is not None and record.supply < record.demand * MARKET_SHORT
            few = "food" not in gdef and supply.get(good, 0) < to_milli(target * CONSUMER_MARGIN)
            if short or few:
                yield from (c for c in _producers(defs, config, island, good) if spare(*c))

    # 人手の足りない施設が多いか、失業者が少ないうちは増やさない
    # （住宅が育って人が移ってくるのを待つ）
    power = config.get("power.good")
    missing = sum(
        max(0, fac.labor_demand(defs, f, turn, power) - f.workers) for f in island.facilities
    )
    if missing > MAX_MISSING_WORKERS or stats.get("unemployment", 0) < NATURAL_UNEMPLOYMENT:
        return
    for kind in sorted(GROWTH, key=lambda k: (_count(island, k), GROWTH.index(k))):
        for rname in defs.facilities[kind]["recipes"]:
            if _feasible(defs, island, kind, rname) and spare(kind, rname):
                yield (kind, rname)
                break


def _education(island: IslandState, defs: Defs, config: GameConfig) -> Iterator[tuple[str, str]]:
    """学校の定員が足りなければ学校、技術者が足りなくて大学がなければ大学（お金の経済の島だけ）。

    同じ資材を待っている工事があるあいだは建てない（学校と大学が建材を使い、原料や資材の工場の工事が
    後回しになって、消費財と木材が足りなくなったため）。工事の資材が倉庫に足りず、島のどの施設も
    作っていなければ、先にその資材を作る施設を建てる（建材を作らない島に大学が建たず、技術者が引退で
    減り続けて、工場が6割ほどしか動かなくなったため）。
    """
    if not island.has_economy:
        return
    if SCHOOL in defs.facilities:
        seats = sum(
            defs.facilities[f.kind].get("education", {}).get("capacity", 0)
            for f in island.facilities
        )
        children = population_of(island) * config.get("education.children")
        if seats < children * SCHOOL_TARGET:
            yield from _build_or_supply(island, defs, config, SCHOOL)
    if UNIVERSITY in defs.facilities and _count(island, UNIVERSITY) == 0:
        needed = sum(fac.engineers_needed(defs, f.kind) for f in island.facilities)
        if needed > island.economy.workers.get(fac.ENGINEER, 0) * ENGINEER_TARGET:
            keep = _private_needs(island, defs)
            yield from _build_or_supply(island, defs, config, UNIVERSITY, keep)


def _build_or_supply(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    kind: str,
    keep: dict[str, int] | None = None,
) -> Iterator[tuple[str, str]]:
    """kind を建てる。資材が倉庫に足りず島のどの施設（工事中を含む）も作っていなければ、代わりに
    その資材を作る施設。同じ資材を待っている工事があるか、資材が手元にない（_in_hand）なら建てない。"""
    present = {good for f in island.facilities for good in fac.recipe_of(defs, f)["outputs"]}
    lacking = [
        good
        for good, qty in sorted(defs.facilities[kind]["construction"].get("materials", {}).items())
        if good not in present and stock.total(island.warehouse, good) < qty
    ]
    if lacking:
        for good in lacking:
            yield from _producers(defs, config, island, good)
    elif not _materials_waited(island, _materials_of(defs, kind)) and _in_hand(
        island, defs, kind, keep
    ):
        yield (kind, "default")


def _materials_waited(island: IslandState, goods: set[str]) -> bool:
    """goods のどれかを待っている施設の工事か、資材を待っているインフラの工事があるか。"""
    if any(not f.built and goods & set(f.ext.get("waiting", ())) for f in island.facilities):
        return True
    works = island.economy.infra
    return works.get("why") == "materials" and works.get("stalled", 0) > 0


def _materials_of(defs: Defs, kind: str) -> set[str]:
    return set(defs.facilities[kind]["construction"].get("materials", {}))


def _in_hand(
    island: IslandState, defs: Defs, kind: str, keep: dict[str, int] | None = None
) -> bool:
    """工事の資材が、国の倉庫にある（ほかの工事の残りの分と keep を除いて）か、建った施設が島で
    作っているか。

    島で作れるだけで建てると、初めの倉庫の資材を学校や大学が使い、資材を作る施設や、島で作れない
    資材（機械など）を待つ民営工場の工事が止まる。
    """
    reserved = _reserved_materials(island, defs)
    made = _made(island, defs)
    keep = keep or {}
    for good, qty in defs.facilities[kind]["construction"].get("materials", {}).items():
        have = stock.total(island.warehouse, good) - reserved.get(good, 0) - keep.get(good, 0)
        if have < qty and good not in made:
            return False
    return True


# --- インフラ ---------------------------------------------------------------------


def _road_order(island: IslandState, defs: Defs, pos: tuple[int, int]) -> tuple[str, dict] | None:
    """pos とその周りの陸のマスのうち、交通の整備度が ROAD_LEVEL に届かないマスに道路を引く。

    同じ資材を待っている工事があるあいだは引かない（道路の石材と建材を、施設の工事に回す）。
    """
    kinds = defs.infra_kinds("transport")
    if not kinds or not island.has_economy:
        return None
    goods = {
        good
        for kind in kinds
        for key in ("materials", "fallback")
        for good in defs.infrastructure[kind].get(key, {})
    }
    if _materials_waited(island, goods):
        return None
    level = round(ROAD_LEVEL * infra.FULL)
    tiles = []
    for x, y in within(island, pos, 1):
        tile = island.tile(x, y)
        if not fac.is_land(defs, tile.terrain) or infra.transport(defs, tile) >= level:
            continue
        if not infra.queued(island, kinds[0], x, y):
            tiles.append([x, y])
    if not tiles:
        return None
    return ("build_infrastructure", {"kind": kinds[0], "tiles": tiles})


def _water_order(island: IslandState, defs: Defs, config: GameConfig) -> tuple[str, dict] | None:
    """上下水道が足りずに育たない町（印 water_short）のマスに、上下水道を引く。"""
    kinds = defs.infra_kinds("water")
    if not kinds or not island.has_economy:
        return None
    housing = fac.housing_kind(config)
    tiles = [
        [f.x, f.y]
        for f in sorted(island.facilities, key=lambda f: (f.y, f.x))
        if f.kind == housing
        and f.ext.get("water_short")
        and not infra.queued(island, kinds[0], f.x, f.y)
        and infra.water(defs, island.tile(f.x, f.y)) < infra.FULL
    ]
    if not tiles:
        return None
    return ("build_infrastructure", {"kind": kinds[0], "tiles": tiles[:64]})


# --- 工業団地 ---------------------------------------------------------------------


def _zone_order(island: IslandState, defs: Defs, config: GameConfig) -> tuple[str, dict] | None:
    """民営工場が建てられる島で、空いた工業団地が少なければ、町の通勤圏に工業団地を足す。"""
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    if not island.has_economy or regime.get("private_entry", "none") == "none":
        return None
    capital = island.capital
    if capital is None:
        return None
    occupied = island.facilities_by_tile()
    spare = [t for t in island.tiles if t.zone == ZONE_INDUSTRIAL and (t.x, t.y) not in occupied]
    if len(spare) >= ZONE_SPARE:
        return None
    housing = fac.housing_kind(config)
    homes = [(f.x, f.y) for f in island.facilities if f.kind == housing]
    if not homes:
        return None
    radius = config.get("labor.commute_radius")
    terrains = {"plain"} | ({"mountain"} if island.resources else set())
    picks = []
    for t in island.tiles:
        pos = (t.x, t.y)
        if t.zone != ZONE_NONE or pos in occupied or pos == capital or t.terrain not in terrains:
            continue
        near = min(distance(pos, h) for h in homes)
        if near <= 1 or near > radius:
            continue
        picks.append(((near, distance(pos, capital)), t.terrain, pos))
    picks.sort()
    has_mountain = any(t.terrain == "mountain" for t in spare)
    mountains = [] if has_mountain else [p for _, k, p in picks if k == "mountain"][:1]
    plains = [p for _, k, p in picks if k == "plain"]
    tiles = [list(p) for p in mountains + plains[: ZONE_BATCH - len(mountains)]]
    if not tiles:
        return None
    return ("set_zone", {"zone": "industrial", "tiles": tiles})


# --- どこに建てるか ---------------------------------------------------------------


def _site_for(
    island: IslandState, defs: Defs, config: GameConfig, kind: str, recipe: str
) -> tuple[int, int] | None:
    """住宅の通勤圏の中で首都に近いマス。開発を止めたマスと、住宅の育つ余地はなるべく避ける。"""
    capital = island.capital
    if capital is None:
        return None
    housing = fac.housing_kind(config)
    homes = [(f.x, f.y) for f in island.facilities if f.kind == housing]
    radius = fac.commute_radius(defs, config, kind)
    best, best_key = None, None
    for pos in within(island, capital, island.size):
        if island.tile(*pos).zone != ZONE_NONE:
            continue
        if fac.placement_problem(defs, island, kind, pos[0], pos[1], recipe) is not None:
            continue
        near = min((distance(pos, h) for h in homes), default=island.size)
        key = (max(0, near - radius), near <= 1, distance(pos, capital))
        if best_key is None or key < best_key:
            best, best_key = pos, key
    return best


def _power_line(island: IslandState, defs: Defs, pos: tuple[int, int]) -> list[tuple[int, int]]:
    """pos を首都の送電網につなぐ送電線のマス（施設のあるマスを除く）。"""
    capital = island.capital
    if capital is None:
        return []
    nodes = {(f.x, f.y) for f in island.facilities} | {pos}
    nodes |= {(t.x, t.y) for t in island.tiles if t.power_line}
    grid = next((set(g) for g in components(island, nodes) if capital in g), {capital})
    if pos in grid:
        return []
    occupied = island.facilities_by_tile()
    path = shortest_path(island, grid, pos, lambda p: fac.is_land(defs, island.tile(*p).terrain))
    if path is None:
        return []
    return [p for p in path if p not in occupied and p != pos]
