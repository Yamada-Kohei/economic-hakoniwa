"""島の中の運賃の率。

物は売り手の施設から買い手まで、陸のマスを上下左右にたどって運ばれます。運賃は道のり
1 あたり game.yaml の freight.per_step × 約定価格で、売り手と買い手が半分ずつ島の運送業に払います
（1人が払う率の上限は freight.max_rate）。近い施設どうしの取引ほど安くなります。

道のりは、陸のマス1つを越えるのに1かかり、道路と鉄道で短くなります（フェーズ2-3）。マスを越える
重さは「1 − 整備度 ×（1 − 定義の path）」（道路 100% で 0.5、鉄道 100% で 0.3。マスごとに軽い方。
hakoniwa_core/infra.py の path_weight）で、隣のマスへ移る重さは2マスの重さの平均です。道のりは
この重さで一番短い道で測ります（内部では1マスを STEP として整数で持つ）。

板寄せでは取引の相手が決まる前に指値を出すので、率は施設ごとに見込みで決めます。施設が売る物と
買う物のそれぞれについて、取引の相手になりうる所（売る物なら、それを買う施設・工事と住民。買う物
なら、それを作る施設）を近い順に、全力で動いたときの自分の量に届くまで数えた平均の道のりを出し、
財の金額（量 × 今の値段）で平均します。住民は量の限りのない相手で、一番近い住んでいるマスまで
運びます。相手で埋まらない分は拠点（首都と、freight.hub_level 以上の町）までの道のりで数え、
陸でつながらない相手は数えません。拠点にも行けない分は上限の率です。
家計の率は、住んでいるマスごとに食料と消費財の一番近い売り手までの道のりの率を平均し、人口で
平均します。市場では、近い相手から順に約定を割り当てます（hakoniwa_core/modules/market）。

運送業の人手が足りないときは、足りない割合だけ率を割り増します（economy.freight["surcharge"]、
1万分の1、最大 freight.max_surcharge 倍）。
"""

from __future__ import annotations

import heapq
from array import array

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.units import BASIS, part, to_basis

Pos = tuple[int, int]

# 道のりの単位: 陸のマス1つを越える道のり（重さ 10 のマスどうしを移る 10 + 10）
STEP = 20

# 陸の形とマスの重さごとの、出発のマスから各マスまでの道のり（インフラが変わればキーが変わる
# ので、ターンをまたいで使い回せる）
_PATHS: dict[bytes, dict[Pos, array]] = {}
_PATHS_MAX = 256


WEIGHTS = "freight_weights"  # island.scratch: マスごとの重さ（整備度が変わったら捨てる）


def _weights(island: IslandState, defs: Defs) -> bytes:
    """マスごとの重さ（0 は海。陸は 1〜10 で、10 が道のない陸のマス）。

    ターンの中では、整備度が変わるまで island.scratch に置いて使い回す（forget と forget_paths）。
    """
    cached = island.scratch.get(WEIGHTS)
    if cached is None:
        cached = island.scratch[WEIGHTS] = bytes(
            infra.path_weight(defs, t) if fac.is_land(defs, t.terrain) else 0 for t in island.tiles
        )
    return cached


def _search(size: int, weights: bytes, starts: list[int]) -> array:
    """starts から各マスまでの一番短い道のり（STEP で1マス）。行けないマスは -1。"""
    dist = array("i", [-1]) * (size * size)
    heap = []
    for start in starts:
        dist[start] = 0
        heap.append((0, start))
    heapq.heapify(heap)
    while heap:
        d, p = heapq.heappop(heap)
        if d > dist[p]:
            continue
        x = p % size
        wp = weights[p]
        for n in (
            p - 1 if x > 0 else -1,
            p + 1 if x < size - 1 else -1,
            p - size,
            p + size,
        ):
            if 0 <= n < size * size and weights[n]:
                nd = d + wp + weights[n]
                if dist[n] < 0 or nd < dist[n]:
                    dist[n] = nd
                    heapq.heappush(heap, (nd, n))
    return dist


class Routes:
    """島の陸のマスの道のり（STEP で1マス）。"""

    def __init__(self, island: IslandState, defs: Defs) -> None:
        self.size = island.size
        weights = _weights(island, defs)
        key = self.size.to_bytes(2, "big") + weights
        table = _PATHS.get(key)
        if table is None:
            if len(_PATHS) >= _PATHS_MAX:
                _PATHS.clear()
            table = _PATHS[key] = {}
        self._weights = weights
        self._table = table
        self.homes = [(t.x, t.y) for t in island.tiles if t.population > 0]

    def from_(self, origin: Pos) -> array:
        """origin から各マス（y * size + x の順）までの道のり。陸でつながらないマスは -1。"""
        found = self._table.get(origin)
        if found is not None:
            return found
        dist = _search(self.size, self._weights, [origin[1] * self.size + origin[0]])
        self._table[origin] = dist
        return dist

    def from_many(self, origins: list[Pos]) -> array:
        """origins のどれかから各マスまでの一番短い道のり（拠点からの道のりなど）。"""
        return _search(self.size, self._weights, [y * self.size + x for x, y in origins])

    def between(self, a: Pos, b: Pos) -> int | None:
        """a から b までの道のり。陸でつながらなければ None。"""
        d = self.from_(a)[b[1] * self.size + b[0]]
        return d if d >= 0 else None

    def to_homes(self, origin: Pos) -> int | None:
        """origin から一番近い住んでいるマスまでの道のり。住んでいるマスに行けなければ None。"""
        dist = self.from_(origin)
        found = [dist[y * self.size + x] for x, y in self.homes]
        found = [d for d in found if d >= 0]
        return min(found) if found else None


def hubs(island: IslandState, defs: Defs, config: GameConfig) -> list[Pos]:
    """拠点のマス: 首都と、game.yaml の freight.hub_level 以上の段階の町。"""
    found = set()
    if island.capital is not None:
        found.add(island.capital)
    levels = defs.settlement_levels()
    hub_level = config.get("freight.hub_level")
    least = levels.index(hub_level) if hub_level in levels else len(levels)
    housing = fac.housing_kind(config)
    for f in island.facilities:
        if f.kind == housing and fac.on_map(f):
            level = f.ext.get("level") or levels[0]
            if levels.index(level) >= least:
                found.add((f.x, f.y))
    return sorted(found, key=lambda p: (p[1], p[0]))


def distances(
    island: IslandState, defs: Defs, config: GameConfig, routes: Routes | None = None
) -> dict[Pos, int]:
    """拠点から陸地の各マスまでの道のり（STEP で1マス。陸でつながらないマスは入らない）。"""
    routes = routes or Routes(island, defs)
    found = hubs(island, defs, config)
    if not found:
        return {}
    dist = routes.from_many(found)
    size = island.size
    return {(i % size, i // size): d for i, d in enumerate(dist) if d >= 0}


def hub_rates(island: IslandState, defs: Defs, config: GameConfig) -> dict[Pos, int]:
    """陸のマスごとの、拠点まで運ぶときの片側の運賃の率（1万分の1、割り増しを含む）。

    地図の重ね表示に使う（施設を建てる場所の目安）。拠点に行けないマスは上限の率です。
    """
    per_step = to_basis(config.get("freight.per_step"))
    top = to_basis(config.get("freight.max_rate"))
    hub = distances(island, defs, config)
    return {
        (t.x, t.y): _surcharged(island, _one_side(hub.get((t.x, t.y)), per_step, top))
        for t in island.tiles
        if fac.is_land(defs, t.terrain)
    }


def _one_side(distance: int | None, per_step: int, top: int) -> int:
    """片側の率: 道のり（STEP で1マス）の半分 × per_step（上限 top）。行けなければ上限。"""
    if distance is None:
        return top
    return min(top, distance * per_step // (2 * STEP))


def household_goods(defs: Defs, config: GameConfig, island: IslandState) -> list[str]:
    """家計が買う財（食料と消費財。電力は送電網で送る）と、町が育つのに要る資材。"""
    power = config.get("power.good")
    goods = {g for g, d in defs.goods.items() if d.get("consumer") and g != power}
    goods |= set(island.economy.housing.get("want", {}))
    return sorted(goods)


class _Trade:
    """財ごとの、取引の相手になりうる所（マスと、全力で動いたときの量）。"""

    def __init__(self, island: IslandState, defs: Defs, config: GameConfig) -> None:
        power = config.get("power.good")
        self.sellers: dict[str, list[tuple[Facility, int]]] = {}
        self.buyers: dict[str, list[tuple[Facility, int]]] = {}
        self.goods: dict[int, list[tuple[str, int, bool]]] = {}  # 施設ごとの (財, 量, 売るか)
        for f in sorted(island.facilities, key=lambda f: f.id):
            if not fac.on_map(f) or fac.is_housing(config, f):
                continue
            goods = facility_goods(defs, power, f)
            self.goods[f.id] = goods
            for good, qty, sell in goods:
                side = self.sellers if sell else self.buyers
                side.setdefault(good, []).append((f, qty))
        self.household = set(household_goods(defs, config, island))


def facility_goods(defs: Defs, power: str, f: Facility) -> list[tuple[str, int, bool]]:
    """施設が売る物と買う物（財, 全力の量, 売るか）。工事中は次の1ターン分の資材。"""
    goods = []
    if f.built:
        recipe = fac.recipe_of(defs, f)
        goods += [(g, q, True) for g, q in sorted(recipe["outputs"].items())]
        goods += [(g, q, False) for g, q in sorted(recipe["inputs"].items())]
    else:
        goods += [(g, q, False) for g, q in fac.step_materials(defs, f).items()]
    return [(g, q, sell) for g, q, sell in goods if g != power and q > 0]


class _Rates:
    """率の計算（1万分の1。割り増しの前）。"""

    def __init__(self, island: IslandState, defs: Defs, config: GameConfig) -> None:
        self.island = island
        self.defs = defs
        self.routes = Routes(island, defs)
        self.trade = _Trade(island, defs, config)
        self.hub = distances(island, defs, config, self.routes)
        self.per_step = to_basis(config.get("freight.per_step"))
        self.top = to_basis(config.get("freight.max_rate"))
        self.prices = island.economy.prices

    def rate(self, distance: int | None) -> int:
        return _one_side(distance, self.per_step, self.top)

    def facility(self, f: Facility, power: str | None = None) -> int:
        goods = self.trade.goods.get(f.id)
        if goods is None:  # まだ建っていない施設（民営工場の参入の場所選び）
            goods = facility_goods(self.defs, power or "", f) if power is not None else []
        hub = self.hub.get((f.x, f.y))
        if not goods:
            return self.rate(hub)
        weighted = total = 0
        for good, qty, sell in goods:
            value = max(1, self.prices.get(good, 1)) * qty
            weighted += value * self._cover(f, good, qty, sell, hub)
            total += value
        return weighted // total

    def _cover(self, f: Facility, good: str, qty: int, sell: bool, hub: int | None) -> int:
        """good の量 qty を近い相手から順に埋めたときの、量で平均した率。"""
        partners = (self.trade.buyers if sell else self.trade.sellers).get(good, [])
        here = (f.x, f.y)
        found: list[tuple[int, int | None]] = []  # (道のり, 量)。量 None は限りなし（住民）
        for other, amount in partners:
            if other.id != f.id:
                d = self.routes.between(here, (other.x, other.y))
                if d is not None:
                    found.append((d, amount))
        if sell and good in self.trade.household:
            d = self.routes.to_homes(here)
            if d is not None:
                found.append((d, None))
        left, total = qty, 0
        for d, amount in sorted(found, key=lambda p: p[0]):
            take = left if amount is None else min(left, amount)
            total += take * self.rate(d)
            left -= take
            if left <= 0:
                break
        total += max(0, left) * self.rate(hub)
        return total // qty

    def household(self) -> int:
        """住んでいるマスごとの、家計が買う財の一番近い売り手までの率の平均を、人口で平均する。"""
        sellers = {
            g: [self.routes.from_((f.x, f.y)) for f, _ in self.trade.sellers.get(g, [])]
            for g in sorted(self.trade.household)
        }
        sellers = {g: s for g, s in sellers.items() if s}
        size = self.routes.size
        people = total = 0
        for tile in self.island.tiles:
            if tile.population <= 0:
                continue
            index = tile.y * size + tile.x
            rates = []
            for paths in sellers.values():
                near = [p[index] for p in paths if p[index] >= 0]
                rates.append(self.rate(min(near) if near else None))
            rate = sum(rates) // len(rates) if rates else self.rate(self.hub.get((tile.x, tile.y)))
            people += tile.population
            total += tile.population * rate
        return total // people if people else 0


def payroll(island: IslandState, config: GameConfig) -> int:
    """運送業が賃金に充てる額: 運賃の収入（割り増しの分を除く）× freight.labor_share。

    このターンに受け取った運賃（economy.freight["paid"]）があればそれで、なければ前のターンの
    収入（"revenue"）で見る。雇う人数と、会計の段で手元に残す額の両方に使う。
    """
    freight = island.economy.freight
    surcharge = freight.get("surcharge", BASIS) or BASIS
    if "paid" in freight:
        revenue = freight["paid"] * BASIS // surcharge
    else:
        revenue = freight.get("revenue", 0)
    return part(revenue, to_basis(config.get("freight.labor_share")))


def _surcharged(island: IslandState, rate: int) -> int:
    return rate * island.economy.freight.get("surcharge", BASIS) // BASIS


def compute(island: IslandState, defs: Defs, config: GameConfig) -> tuple[dict[int, int], int]:
    """施設ごとの運賃の率と、家計の率（1万分の1、割り増しを含む）。"""
    calc = _Rates(island, defs, config)
    rates = {
        f.id: _surcharged(island, calc.facility(f))
        for f in sorted(island.facilities, key=lambda f: f.id)
        if fac.on_map(f)
    }
    return rates, _surcharged(island, calc.household())


def rate_of(island: IslandState, defs: Defs, config: GameConfig, f: Facility) -> int:
    """施設 f の今の運賃の率（ターンの途中で決まり方が変わった施設の出資の見積もりなど）。"""
    return rates_of(island, defs, config, [f])[f.id]


def rates_of(
    island: IslandState, defs: Defs, config: GameConfig, facilities: list[Facility]
) -> dict[int, int]:
    """いくつかの施設の今の運賃の率（市場の段より前の見積もり。施設の数だけ計算するので、
    全部の施設の率は facility_rates で）。"""
    calc = _Rates(island, defs, config)
    return {f.id: _surcharged(island, calc.facility(f)) if fac.on_map(f) else 0 for f in facilities}


def candidate_rates(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    kind: str,
    recipe: str,
    positions: list[Pos],
) -> dict[Pos, int]:
    """まだ建っていない施設 kind を positions のマスに建てたときの運賃の率（割り増しを含む）。"""
    calc = _Rates(island, defs, config)
    power = config.get("power.good")
    out = {}
    for x, y in positions:
        probe = Facility(id=-1, kind=kind, x=x, y=y, recipe=recipe, built=True)
        out[(x, y)] = _surcharged(island, calc.facility(probe, power))
    return out


def facility_rates(island: IslandState, defs: Defs, config: GameConfig) -> dict[int, int]:
    """施設ごとの運賃の率。ターンの中では同じなので、初めに使うときに計算して island.scratch に置く
    （割り増しが決まる雇用の段より後で使う）。家計の率も一緒に置く（freight_household）。
    工事が済んで取引の相手が変わったときは forget で捨てる。"""
    cached = island.scratch.get("freight_rates")
    if cached is None:
        cached, household = compute(island, defs, config)
        island.scratch["freight_rates"] = cached
        island.scratch["freight_household"] = household
    return cached


def forget(island: IslandState) -> None:
    """このターンに計算した率を捨てる（次に使うときに計算し直す）。"""
    island.scratch.pop("freight_rates", None)
    island.scratch.pop("freight_household", None)
    forget_paths(island)


def forget_paths(island: IslandState) -> None:
    """マスの重さだけを捨てる（インフラの段の傷み。率はそのターンのものを使い続ける）。"""
    island.scratch.pop(WEIGHTS, None)
