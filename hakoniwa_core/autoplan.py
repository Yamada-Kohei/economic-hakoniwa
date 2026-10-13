"""標準の計画: 島の様子を見て、自動で命令を並べる簡単な方針。

一括シミュレーション（関門のテスト）と、開発用のサンプルの島で、プレイヤーの代わりに使います。
ゲームのルールではないので、ここの数値は game.yaml ではなくこのファイルに置いています。

方針（毎ターン、工事中の施設が少なければ、上から見て最初に足りないものを1つ建てる。同じ物を作る
施設に動ききっていないものがあれば、それは建てない）:
1. 食料の生産が食べる量に届かなければ、農場か漁港
2. 発電の余裕が少なければ、発電所（石炭があれば火力、なければ水力）
3. 工事の資材が国の倉庫になく、島に作る施設もなければ、それを作る施設（工事が止まらないように、
   同時に進める工事の数を超えても建てる）
4. 投入財の作る量が使う量に届かなければ、それを作る施設
5. 消費財の生産が住民1人あたりの目安に届かないか、前のターンの市場で売りに出た量が買いたい量に
   届かなければ、消費財工場（加工食品も、売りに出た量が買いたい量に届かなければ食品工場）
6. 生産の連鎖（鉄鉱石 → 鋼鉄 → 機械 → 新しい工場）を太くする: 製鉄所と機械工場を増やす（失業率が
   ふつうより高く、人手の足りない施設が少ない間だけ）
4〜6 では、投入財に余裕のない施設は建てません（鉱山を増やせないのに製鉄所を増やすと、鉄鉱石の
取り合いで値段が跳ね上がるだけなので）。
失業率が高い間は、同時に進める工事を1つ増やします（工事の賃金と資材の買い物が働き口になる）。
お金の経済の島では、国の倉庫にある財のうち島の施設が仕入れに困っている物（建国のときの石炭など）を、
毎ターン市場に売ります（建てる命令と一緒に出します）。
建てた施設は、陸地を通る一番短い送電線で送電網につなぎます。

お金の経済の島では、初めに失業手当を平均賃金の BENEFIT % にし、BUDGET_TURNS ターンごとに
公共事業費（国の工事の年の予算）を見直します。
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
from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy.prices import initial_prices, unit_cost
from hakoniwa_core.geo import components, distance, shortest_path, within
from hakoniwa_core.state import (
    ORDER_PENDING,
    ZONE_INDUSTRIAL,
    ZONE_NONE,
    IslandState,
    OrderState,
)
from hakoniwa_core.units import QUANTITY_SCALE, to_milli

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
ZONE_SPARE = 3  # 空いた工業団地のマスがこれより少なければ、工業団地を足す
ZONE_BATCH = 6  # 一度に工業団地に指定するマスの数
# 連鎖を太くするときに、この順で数をそろえていく施設
GROWTH = ("steel_mill", "machinery_plant", "building_materials_plant")


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
        _budget_order(island, turn),
        _price_cap_order(island, defs, config),
        _zone_order(island, defs, config),
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
        if _underused(island, defs, config, kind, recipe) or not _materials_ok(island, defs, kind):
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

    建国のときに倉庫にあった石炭などを、火力発電所が使えるようにする（工事に取っておく分は売らない）。
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
    initial = initial_prices(defs, config, island.resources)
    orders = []
    for good, qty in sorted(need.items()):
        spare = stock.total(island.warehouse, good) - reserved.get(good, 0)
        if spare <= 0 or (good not in stuck and good not in short):
            continue
        payload = {
            "good": good,
            "side": "sell",
            "quantity": min(spare, qty * SALE_TURNS) / QUANTITY_SCALE,
            "limit": max(island.economy.prices.get(good, 0), initial.get(good, 0)),
        }
        orders.append(("warehouse_trade", payload))
    return orders


# --- 公共事業費 -------------------------------------------------------------------


def _labor_policy_order(island: IslandState) -> tuple[str, dict] | None:
    """失業手当がなければ、平均賃金の BENEFIT % にする。

    失業者の暮らしと、物の売れ行きを支える。
    """
    if not island.has_economy or island.policy.unemployment_benefit > 0:
        return None
    return ("set_labor_policy", {"unemployment_benefit": BENEFIT})


def _budget_order(island: IslandState, turn: int) -> tuple[str, dict] | None:
    """公共事業費を見直す命令（変えるほどの差がなければ None）。"""
    if not island.has_economy or turn % BUDGET_TURNS:
        return None
    stats = island.stats
    scale = 1.0
    scale += UNEMPLOYMENT_RESPONSE * (stats.get("unemployment", 0) - NATURAL_UNEMPLOYMENT)
    scale -= INFLATION_RESPONSE * max(0.0, stats.get("inflation", 0) - INFLATION_LIMIT)
    scale -= DEBT_RESPONSE * max(0.0, stats.get("debt_ratio", 0) - DEBT_LIMIT)
    scale = min(WORKS_RANGE[1], max(WORKS_RANGE[0], scale))
    target = int(population_of(island) * PUBLIC_WORKS * scale)
    current = island.policy.budget.get("construction", 0)
    if current and abs(target - current) <= current // 10:
        return None
    return ("set_budget", {"construction": target})


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


def _reserved_materials(island: IslandState, defs: Defs) -> dict[str, int]:
    """工事中の施設が、これから使う資材のうち手元にない量。"""
    reserved: dict[str, int] = {}
    for f in island.facilities:
        if not f.built:
            construction = defs.facilities[f.kind]["construction"]
            done = f.progress / (construction["turns"] * fac.FULL)
            for good, qty in construction.get("materials", {}).items():
                left = qty - int(qty * done) - stock.total(f.stock, good)
                reserved[good] = reserved.get(good, 0) + max(0, left)
    return reserved


def _material_producers(
    island: IslandState, defs: Defs, config: GameConfig
) -> Iterator[tuple[str, str]]:
    """工事の資材のうち、国の倉庫に足りず、島に作る施設（工事中も含む）もない物を作る施設。"""
    made = {good for f in island.facilities for good in fac.recipe_of(defs, f)["outputs"]}
    for good, qty in sorted(_reserved_materials(island, defs).items()):
        if good not in made and stock.total(island.warehouse, good) < qty:
            yield from _producers(defs, config, island, good)


def _materials_ok(island: IslandState, defs: Defs, kind: str) -> bool:
    """工事の資材が、倉庫にあるか（ほかの工事の残りの分を除いて）、島の中で作れるか。"""
    reserved = _reserved_materials(island, defs)
    for good, qty in defs.facilities[kind]["construction"].get("materials", {}).items():
        have = stock.total(island.warehouse, good) - reserved.get(good, 0)
        if have < qty and not _makeable(defs, island, good, frozenset()):
            return False
    return True


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
