"""島の中の運賃の率。

物は売り手の施設から買い手まで、陸のマスを上下左右にたどって運ばれます。運賃は道のり（歩数）
1 あたり game.yaml の freight.per_step × 約定価格で、売り手と買い手が半分ずつ島の運送業に払います
（1人が払う率の上限は freight.max_rate）。近い施設どうしの取引ほど安くなります。道路と鉄道で
道のりが短くなるのはフェーズ2-3 から。

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

from array import array
from collections import deque

from hakoniwa_core import facilities as fac
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.geo import neighbors4
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.units import BASIS, to_basis

Pos = tuple[int, int]

# 陸の形ごとの、出発のマスから各マスまでの道のり（陸の形は工事で変わらないので、ターンをまたいで
# 使い回す。形が変わればキーが変わる）
_PATHS: dict[bytes, dict[Pos, array]] = {}
_PATHS_MAX = 256


class Routes:
    """島の陸のマスの道のり。"""

    def __init__(self, island: IslandState, defs: Defs) -> None:
        self.size = island.size
        land = bytes(1 if fac.is_land(defs, t.terrain) else 0 for t in island.tiles)
        key = self.size.to_bytes(2, "big") + land
        table = _PATHS.get(key)
        if table is None:
            if len(_PATHS) >= _PATHS_MAX:
                _PATHS.clear()
            table = _PATHS[key] = {}
        self._land = land
        self._table = table
        self.homes = [(t.x, t.y) for t in island.tiles if t.population > 0]

    def from_(self, origin: Pos) -> array:
        """origin から各マス（y * size + x の順）までの道のり。陸でつながらないマスは -1。"""
        found = self._table.get(origin)
        if found is not None:
            return found
        size, land = self.size, self._land
        dist = array("i", [-1]) * (size * size)
        start = origin[1] * size + origin[0]
        dist[start] = 0
        queue = deque([start])
        while queue:
            p = queue.popleft()
            d = dist[p] + 1
            x = p % size
            for n in (
                p - 1 if x > 0 else -1,
                p + 1 if x < size - 1 else -1,
                p - size,
                p + size,
            ):
                if 0 <= n < size * size and land[n] and dist[n] < 0:
                    dist[n] = d
                    queue.append(n)
        self._table[origin] = dist
        return dist

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


def distances(island: IslandState, defs: Defs, config: GameConfig) -> dict[Pos, int]:
    """拠点から陸地の各マスまでの道のり（陸でつながらないマスは入らない）。"""
    land = {(t.x, t.y) for t in island.tiles if fac.is_land(defs, t.terrain)}
    dist: dict[Pos, int] = {}
    queue: deque[Pos] = deque()
    for pos in hubs(island, defs, config):
        dist[pos] = 0
        queue.append(pos)
    while queue:
        pos = queue.popleft()
        for n in neighbors4(island, pos):
            if n in land and n not in dist:
                dist[n] = dist[pos] + 1
                queue.append(n)
    return dist


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
            goods = []
            if f.built:
                recipe = fac.recipe_of(defs, f)
                goods += [(g, q, True) for g, q in sorted(recipe["outputs"].items())]
                goods += [(g, q, False) for g, q in sorted(recipe["inputs"].items())]
            else:
                goods += [(g, q, False) for g, q in fac.step_materials(defs, f).items()]
            goods = [(g, q, sell) for g, q, sell in goods if g != power and q > 0]
            self.goods[f.id] = goods
            for good, qty, sell in goods:
                side = self.sellers if sell else self.buyers
                side.setdefault(good, []).append((f, qty))
        self.household = set(household_goods(defs, config, island))


class _Rates:
    """率の計算（1万分の1。割り増しの前）。"""

    def __init__(self, island: IslandState, defs: Defs, config: GameConfig) -> None:
        self.island = island
        self.routes = Routes(island, defs)
        self.trade = _Trade(island, defs, config)
        self.hub = distances(island, defs, config)
        self.per_step = to_basis(config.get("freight.per_step"))
        self.top = to_basis(config.get("freight.max_rate"))
        self.prices = island.economy.prices

    def rate(self, distance: int | None) -> int:
        """片側の率: 道のりの半分 × per_step（上限 top）。行けなければ上限。"""
        if distance is None:
            return self.top
        return min(self.top, distance * self.per_step // 2)

    def facility(self, f: Facility) -> int:
        goods = self.trade.goods.get(f.id, [])
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
    if not fac.on_map(f):
        return 0
    return _surcharged(island, _Rates(island, defs, config).facility(f))


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
