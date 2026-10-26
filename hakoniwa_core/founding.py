"""建国: プレイヤーが描いた島の形から地形を配り、首都と初めの施設を置いて島を作る。

建国の画面（サーバー）と、試験用の島づくり（state/generate.py）の両方がここを使います。
問題は (理由, 引数) の組で返し、文面は messages の表示名（founding_...）から作ります。
地形は「描いた形」と「種」だけで決まるので、画面で見せた地形と建国後の地形は同じになります。
"""

from __future__ import annotations

import hashlib
import random
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, nature, stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    default_accounting,
    experience,
    productivity,
    public_housing,
    reserve,
    services,
)
from hakoniwa_core.economy.start import start_economy
from hakoniwa_core.geo import NEIGHBORS4, NEIGHBORS8, components, distance, shortest_path, within
from hakoniwa_core.naming import (
    assign_serial,
    default_place_style,
    generate_places,
    place_count,
    place_index,
    place_styles,
)
from hakoniwa_core.state import (
    OWNER_PRIVATE,
    OWNER_STATE,
    Facility,
    IslandState,
    LogEntry,
    Tile,
)
from hakoniwa_core.trade import setup as trade_setup
from hakoniwa_core.units import QUANTITY_SCALE, to_milli

Pos = tuple[int, int]
Problem = tuple[str, dict[str, Any]]

NAME_FIELDS = ("country", "capital", "currency", "currency_symbol", "leader")


class FoundingError(Exception):
    def __init__(self, problems: list[Problem]):
        self.problems = problems
        super().__init__(", ".join(reason for reason, _ in problems))


@dataclass
class FoundingPlan:
    """建国の画面で選んだもの。"""

    land: set[Pos]  # 陸地のマス
    capital: Pos
    regime: dict[str, str]  # 体制の軸ごとの選択
    names: dict[str, str]  # NAME_FIELDS の名前（国名・首都名・通貨名と記号・国家元首）
    resources: list[str] = field(default_factory=list)  # 鉱業資源
    terrain_edits: dict[Pos, str] = field(default_factory=dict)  # 描き直した地形
    seed: int = 0  # 地形を配るときの種
    place_style: str = ""  # 地名の作風（naming.yaml の places）。空なら既定の作風


# --- 島の形と地形 ---------------------------------------------------------------


def shape_problems(land: Iterable[Pos], config: GameConfig) -> list[Problem]:
    """陸地の形の問題。縁の1マスは海、マス数は上限と下限の間。

    陸地は1つにつながっていなくてよい（同じ国の離れ小島も描ける）。
    """
    land = set(land)
    size = config.get("island.size")
    problems: list[Problem] = []
    if any(not (1 <= x < size - 1 and 1 <= y < size - 1) for x, y in land):
        problems.append(("founding_edge", {}))
        land = {(x, y) for x, y in land if 1 <= x < size - 1 and 1 <= y < size - 1}
    low, high = config.get("island.land_min"), config.get("island.land_max")
    if len(land) < low:
        problems.append(("founding_too_small", {"count": len(land), "min": low}))
    if len(land) > high:
        problems.append(("founding_too_large", {"count": len(land), "max": high}))
    return problems


def auto_terrain(land: Iterable[Pos], config: GameConfig, seed: int) -> dict[Pos, str]:
    """描いた形から、全マスの地形を決める。

    陸地は既定の地形（平地）で埋め、founding.land_terrain の順に、陸地のうち share の割合を
    置き換える。inland: true の地形（山）は海から遠いマスから、ほかの地形（森）はまとまりで散らす。
    陸地に接する海は浅瀬、残りは海にする。
    散らし方の乱れはマスごとに種と位置から決めるので、陸地を描き足しても、離れたマスの地形は
    変わらない（変わるのは描き足したところの近くと、割合の境目のマスだけ）。
    """
    land = set(land)
    settings = config.get("founding")
    size = config.get("island.size")
    terrain: dict[Pos, str] = {}
    for y in range(size):
        for x in range(size):
            if (x, y) in land:
                terrain[(x, y)] = settings["base_terrain"]
            elif any((x + dx, y + dy) in land for dx, dy in NEIGHBORS8):
                terrain[(x, y)] = settings["coast_terrain"]
            else:
                terrain[(x, y)] = settings["sea_terrain"]
    inland = _distance_to_sea(land)
    for rule in settings["land_terrain"]:
        free = sorted(p for p in land if terrain[p] == settings["base_terrain"])
        count = min(len(free), round(len(land) * rule["share"]))
        if rule.get("inland"):
            score = {p: inland[p] + _noise(seed, rule["terrain"], p) * 1.5 for p in free}
        else:
            noise = {p: _noise(seed, rule["terrain"], p) for p in land}
            score = {
                p: sum(noise.get((p[0] + dx, p[1] + dy), 0.5) for dx, dy in ((0, 0), *NEIGHBORS8))
                for p in free
            }
        for p in sorted(free, key=lambda p: (-score[p], p[1], p[0]))[:count]:
            terrain[p] = rule["terrain"]
    return terrain


def _noise(seed: int, salt: str, pos: Pos) -> float:
    """種とマスの位置だけで決まる、0 以上 1 未満の数（ほかのマスの有無で変わらない）。"""
    key = f"terrain:{seed}:{salt}:{pos[0]},{pos[1]}".encode()
    return int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big") / 2**64


def _distance_to_sea(land: set[Pos]) -> dict[Pos, int]:
    """陸地の各マスから、一番近い海までの歩数（上下左右）。"""
    dist = {}
    queue = deque()
    for x, y in sorted(land, key=lambda p: (p[1], p[0])):
        if any((x + dx, y + dy) not in land for dx, dy in NEIGHBORS4):
            dist[(x, y)] = 1
            queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        for dx, dy in NEIGHBORS4:
            n = (x + dx, y + dy)
            if n in land and n not in dist:
                dist[n] = dist[(x, y)] + 1
                queue.append(n)
    return dist


def land_terrains(defs: Defs, config: GameConfig) -> list[str]:
    """建国時に描き直せる陸地の地形。"""
    settings = config.get("founding")
    names = [settings["base_terrain"], *(r["terrain"] for r in settings["land_terrain"])]
    return [t for t in dict.fromkeys(names) if fac.is_land(defs, t)]


def edit_problems(
    land: set[Pos], edits: Mapping[Pos, str], defs: Defs, config: GameConfig
) -> list[Problem]:
    problems: list[Problem] = []
    limit = config.get("founding.terrain_edits")
    if len(edits) > limit:
        problems.append(("founding_too_many_edits", {"count": len(edits), "max": limit}))
    allowed = land_terrains(defs, config)
    for (x, y), terrain in sorted(edits.items()):
        if (x, y) not in land:
            problems.append(("founding_edit_not_land", {"x": x, "y": y}))
        elif terrain not in allowed:
            problems.append(("founding_unknown_terrain", {"terrain": terrain}))
    return problems


def final_terrain(plan: FoundingPlan, config: GameConfig) -> dict[Pos, str]:
    terrain = auto_terrain(plan.land, config, plan.seed)
    for pos, t in plan.terrain_edits.items():
        if pos in plan.land:
            terrain[pos] = t
    return terrain


# --- 選んだもの ---------------------------------------------------------------


def resource_problems(resources: list[str], defs: Defs, config: GameConfig) -> list[Problem]:
    problems: list[Problem] = []
    limit = config.get("founding.max_resources")
    if len(resources) > limit or len(set(resources)) != len(resources):
        problems.append(("founding_too_many_resources", {"max": limit}))
    for good in resources:
        if good not in defs.mining_resources():
            problems.append(("founding_unknown_resource", {"good": good}))
    return problems


def regime_problems(regime: Mapping[str, str], defs: Defs) -> list[Problem]:
    problems: list[Problem] = []
    for axis, choices in defs.regime_axes().items():
        chosen = regime.get(axis)
        if chosen is None:
            problems.append(("founding_regime_missing", {"axis": f"axis_{axis}"}))
        elif chosen not in choices:
            problems.append(("founding_unknown_regime", {"axis": f"axis_{axis}"}))
    return problems


def place_style_problems(style: str, defs: Defs, config: GameConfig) -> list[Problem]:
    if style and style not in place_styles(defs, config):
        return [("founding_unknown_place_style", {})]
    return []


def name_problems(names: Mapping[str, str], config: GameConfig) -> list[Problem]:
    problems: list[Problem] = []
    limits = config.get("founding.name_max")
    for key in NAME_FIELDS:
        value = (names.get(key) or "").strip()
        if not value:
            problems.append(("founding_name_missing", {"field": f"name_{key}"}))
        elif len(value) > limits[key]:
            args = {"field": f"name_{key}", "max": limits[key]}
            problems.append(("founding_name_too_long", args))
    return problems


def capital_problems(
    capital: Pos, terrain: Mapping[Pos, str], land: set[Pos], defs: Defs, start: Mapping[str, Any]
) -> list[Problem]:
    if capital not in land:
        return [("founding_capital_not_land", {})]
    kind = start["capital"]["facility"]
    if terrain[capital] not in defs.facilities[kind]["terrain"]:
        return [("founding_capital_terrain", {"terrain": terrain[capital]})]
    return []


def start_of(defs: Defs, regime: Mapping[str, str]) -> Mapping[str, Any]:
    """経済体制で決まる、建国直後の島の中身（start_island.yaml）。"""
    economy = defs.regimes.get(regime.get("economy", ""), {})
    name = economy.get("start") or next(iter(defs.start_island))
    return defs.start_island[name]


def plan_problems(plan: FoundingPlan, defs: Defs, config: GameConfig) -> list[Problem]:
    """建国の中身の問題をすべて返す。空なら建国できる。"""
    problems = shape_problems(plan.land, config)
    problems += edit_problems(plan.land, plan.terrain_edits, defs, config)
    problems += resource_problems(plan.resources, defs, config)
    problems += regime_problems(plan.regime, defs)
    problems += name_problems(plan.names, config)
    problems += place_style_problems(plan.place_style, defs, config)
    if not problems:
        terrain = final_terrain(plan, config)
        start = start_of(defs, plan.regime)
        problems += capital_problems(plan.capital, terrain, plan.land, defs, start)
    return problems


def suggest_capital(
    terrain: Mapping[Pos, str], land: set[Pos], defs: Defs, config: GameConfig, kind: str
) -> Pos | None:
    """首都に向いたマス: 首都の施設を建てられる地形で、周りの陸地が多く、海から離れたマス。"""
    margin = config.get("founding.capital_margin")
    radius = config.get("labor.commute_radius")
    inland = _distance_to_sea(land)
    allowed = defs.facilities[kind]["terrain"]
    best, best_score = None, None
    for x, y in sorted(land, key=lambda p: (p[1], p[0])):
        if terrain[(x, y)] not in allowed:
            continue
        around = sum(
            1
            for dy in range(-radius, radius + 1)
            for dx in range(-radius, radius + 1)
            if (x + dx, y + dy) in land and terrain[(x + dx, y + dy)] in allowed
        )
        score = (min(inland[(x, y)], margin + 1), around)
        if best_score is None or score > best_score:
            best, best_score = (x, y), score
    return best


# --- 島を作る -----------------------------------------------------------------


def found_island(
    defs: Defs,
    config: GameConfig,
    plan: FoundingPlan,
    island_id: int,
    wx: int,
    wy: int,
    turn: int = 0,
    climate: str | None = None,
) -> IslandState:
    """建国する。問題があれば FoundingError。

    climate は区画で決まる気候帯（フェーズ3-2。nature.zone_of）。平地のマスには肥沃度を配る。
    """
    problems = plan_problems(plan, defs, config)
    if problems:
        raise FoundingError(problems)
    size = config.get("island.size")
    terrain = final_terrain(plan, config)
    tiles = [Tile(x=x, y=y, terrain=terrain[(x, y)]) for y in range(size) for x in range(size)]
    names = {k: plan.names[k].strip() for k in NAME_FIELDS}
    island = IslandState(
        id=island_id,
        name=names.pop("country"),
        wx=wx,
        wy=wy,
        size=size,
        tiles=tiles,
        capital_x=plan.capital[0],
        capital_y=plan.capital[1],
        resources=list(plan.resources),
        regime={axis: plan.regime[axis] for axis in defs.regime_axes()},
        names=names,
    )
    if climate is not None:
        island.ext[nature.CLIMATE] = climate
    nature.spread_fertility(island, config, plan.seed)
    rng = random.Random(f"start:{plan.seed}")
    style = plan.place_style or default_place_style(defs, config)
    island.ext["place_style"] = style
    places = generate_places(defs, config, rng, place_count(size, config), style)
    places[place_index(island, *plan.capital, config) % len(places)] = names["capital"]
    island.ext["places"] = places
    place_start(island, defs, config, rng, turn)
    island.log.append(LogEntry("island_founded", {"name": island.name}))
    start_economy(island, defs, config, turn)
    island.economy.ext["nature"] = nature.VERSION  # 自然条件は建国のときに置いた（移し替えない）
    island.economy.ext["housing"] = fac.HOUSING_VERSION  # 住まいの広さも建国のときから
    return island


def open_for_business(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> None:
    """建国した島に、港・海運業・商社と商業・サービス業を、建国の瞬間に置く（W-140）。

    次の更新の移し替えの段（modules/upgrade）が同じ順で置くので、呼ばなくても動くが、建国から最初の
    更新まで（最長4時間）貿易の施設のない島が見えていた。置き終えた島は、移し替えの段では何もしない。
    プレイヤーの建国（server/world/services.py）が呼ぶ。試験と一括シミュレーションの島は、これまでどおり
    最初の更新で置く（動きを変えないため）。
    """
    services.prepare(island, defs, config)
    trade_setup.prepare(island, defs, config, turn)


def refound(island: IslandState, turn: int, defs: Defs) -> None:
    """建国したばかりの島（まだ一度も更新していない島）を、ターン turn に建国したことにする。

    巻き戻しで、戻した先より後に建国した島に使う（島は残し、世界と同じ時点にそろえる）。建国の
    ときに書いたターン（経済を始めたターン・国債を出したターン・在庫の財が作られたターン）を、
    turn より後なら同じだけ前にずらす。
    """
    started = island.economy.started
    if started is None or started <= turn:
        return
    shift = turn - started
    island.economy.started = turn
    for bond in island.bonds:
        bond.issued += shift
    # 日持ちのある物の組は、どれも建国のときに作られたターンを書いてある（国の穀物の備蓄は建国より
    # 前のターンにならしてある）。日持ちのない物の組は作られたターンを書かない
    for held in [island.warehouse, *(f.stock for f in island.facilities)]:
        for good, batches in held.items():
            if "shelf_life" not in defs.goods[good]:
                continue
            for batch in batches:
                batch[0] += shift
    # 貿易の仕組みを置いたターン（海外の商人の来る期間の数え始め）と、輸送船を迎えたターン（W-140）
    trade = island.ext.get("trade", {})
    for key in ("since", "merchant_from"):
        if trade.get(key, turn) > turn:
            trade[key] += shift
    for f in island.facilities:
        for batch in f.economy.get("fleet", ()):
            if batch[0] > turn:
                batch[0] += shift


def place_start(
    island: IslandState, defs: Defs, config: GameConfig, rng: random.Random, turn: int = 0
) -> None:
    """start_island.yaml に従って、首都・町・村・初めの施設・送電線・倉庫の資材を置く。"""
    start = start_of(defs, island.regime)
    capital = island.capital
    assert capital is not None
    housing = fac.housing_kind(config)
    public = public_housing.planned(island)  # 計画経済の国の町は、はじめから国の床
    levels = defs.settlement_levels()
    homes_terrain = defs.facilities[housing]["terrain"]

    _add_facility(island, defs, start["capital"]["facility"], capital, None)

    def empty(pos: Pos, terrains: Iterable[str]) -> bool:
        return island.tile(*pos).terrain in terrains and island.facility_at(*pos) is None

    cap = start["capital"]
    town = [p for p in within(island, capital, island.size) if empty(p, homes_terrain)]
    for pos in town[: cap.get("town_tiles", 0)]:
        home = fac.new_housing(island, housing, pos, cap.get("town_level") or levels[0])
        island.tile(*pos).population = cap.get("town_population", 0)
        home.ext["level"] = cap.get("town_level") or levels[0]
        if public:
            public_housing.set_floor(home, fac.floor_of(defs, home))

    villages = start.get("villages")
    if villages:
        low = villages.get("min_distance", 1)
        high = villages.get("max_distance", island.size)
        spots = [
            p
            for p in within(island, capital, high)
            if distance(p, capital) >= low and empty(p, homes_terrain)
        ]
        rng.shuffle(spots)
        chosen: list[Pos] = []
        for pos in spots:
            if len(chosen) >= villages["count"]:
                break
            if all(distance(pos, other) >= 2 for other in chosen):
                chosen.append(pos)
        for pos in sorted(chosen, key=lambda p: (p[1], p[0])):
            home = fac.new_housing(island, housing, pos, levels[0])
            if public:
                public_housing.set_floor(home, fac.floor_of(defs, home))
            island.tile(*pos).population = villages["population"]

    # 昔からの産業の経験を先に置き、施設の数を決める見込みの生産性に入れる（島の資源がなくて置かない
    # 施設の産業は後で外す。fit で数が 0 になった施設の産業は残す: 農場を置かない島も農業は営む）
    founded = experience.founding_industries(defs)
    experience.start(island, config, set(founded))
    kept: set[str | None] = set()
    for item in start["facilities"]:
        resource = item.get("if_resource")
        if resource is not None and resource not in island.resources:
            continue
        kept.add(defs.facilities[item["kind"]].get("industry"))
        fit = item.get("fit")
        count = item.get("count", 1) if fit is None else item.get("max", FIT_MAX)
        least = count if fit is None else item.get("min", 0)
        for n in range(count):
            pos = _start_site(island, defs, config, item["kind"], item.get("recipe"))
            if pos is None:
                break
            if n >= least and not _fits(island, defs, config, item, pos):
                break
            _add_facility(island, defs, item["kind"], pos, item.get("recipe"))
    rows = experience.table(island)
    for industry in sorted(founded - kept):
        rows.pop(industry, None)

    if start.get("power_lines"):
        connect_power(island, defs)
    for good, qty in start.get("warehouse", {}).items():
        stock.add(island.warehouse, good, qty, turn, defs.goods)
    # 国の穀物の備蓄（economy/reserve.py）
    reserve.stock_up(island, defs, config, turn)
    _prime_inputs(island, defs, config)


FIT_MAX = 10  # fit を書いた施設を置く数の上限の既定（start_island.yaml の max）


def _fits(
    island: IslandState, defs: Defs, config: GameConfig, item: Mapping[str, Any], pos: Pos
) -> bool:
    """fit を書いた初めの施設を、もう1つ pos に置くか: 要る量の残りが、その施設1つの見込みの産出の
    founding.fit_share（fit ごと）以上あるとき（W-186 の直し 回1）。食べ物は 0 で、残りがあれば
    置く（住民が飢えないように切り上げる）。原料は 0.5 で四捨五入。

    fit が food なら、要る量は建国の住民の食べる量（栄養。population.food_per_person）で、もう置いた
    施設の食料の産出を引く。inputs なら、もう置いた施設が全力で使う、この施設の産出物の量から、もう
    置いた施設の産出を引く。産出は施設の場所の生産性（気候・肥沃度・インフラ・教育・経験）の見込み。
    """
    kind = item["kind"]
    name = item.get("recipe") or fac.default_recipe(defs, kind) or "default"
    recipe = defs.facilities[kind]["recipes"][name]
    placed = [
        f
        for f in island.facilities
        if not fac.is_housing(config, f) and defs.facilities[f.kind].get("recipes")
    ]
    if item["fit"] == "food":

        def value(outputs: Mapping[str, int]) -> int:
            return sum(
                q * to_milli(defs.goods[g]["food"]["nutrition"]) // QUANTITY_SCALE
                for g, q in outputs.items()
                if "food" in defs.goods[g]
            )

        population = sum(t.population for t in island.tiles)
        need = to_milli(population * config.get("population.food_per_person"))
    else:
        made = set(recipe["outputs"])

        def value(outputs: Mapping[str, int]) -> int:
            return sum(q for g, q in outputs.items() if g in made)

        need = sum(
            q for f in placed for g, q in fac.recipe_of(defs, f)["inputs"].items() if g in made
        )
    for f in placed:
        built = productivity.productivity(
            island, defs, config, fac.recipe_of(defs, f), f.x, f.y, founding=True
        )
        need -= value(
            {
                g: q * built // productivity.FULL
                for g, q in fac.recipe_of(defs, f)["outputs"].items()
            }
        )
    one = productivity.productivity(island, defs, config, recipe, pos[0], pos[1], founding=True)
    supply = value({g: q * one // productivity.FULL for g, q in recipe["outputs"].items()})
    share = config.get("founding.fit_share")[item["fit"]]
    return supply > 0 and need > 0 and need >= supply * share


def _prime_inputs(island: IslandState, defs: Defs, config: GameConfig) -> None:
    """初めの施設に、最初のターンに全力で動ける分の投入財を倉庫から渡しておく。"""
    power = config.get("power.good")
    for f in island.facilities:
        for good, qty in sorted(fac.recipe_of(defs, f)["inputs"].items()):
            if good != power:
                stock.put_batches(f.stock, good, stock.take_batches(island.warehouse, good, qty))


def _start_site(
    island: IslandState, defs: Defs, config: GameConfig, kind: str, recipe: str | None
) -> Pos | None:
    """初めの施設を置くマス。住宅の通勤圏の中で首都に近いマス、なければ首都に近いマス。"""
    capital = island.capital
    assert capital is not None
    housing = fac.housing_kind(config)
    homes = [(f.x, f.y) for f in island.facilities if f.kind == housing]
    radius = fac.commute_radius(defs, config, kind)
    fallback = None
    for pos in within(island, capital, island.size):
        if fac.placement_problem(defs, island, kind, pos[0], pos[1], recipe) is not None:
            continue
        if any(distance(pos, h) <= radius for h in homes):
            return pos
        if fallback is None:
            fallback = pos
    return fallback


def _add_facility(
    island: IslandState, defs: Defs, kind: str, pos: Pos, recipe: str | None
) -> Facility:
    economy = defs.regimes.get(island.regime.get("economy", ""), {})
    state = economy.get("owner", OWNER_STATE) == OWNER_STATE or kind in economy.get(
        "state_owned", ()
    )
    f = Facility(
        id=island.next_facility_id(),
        kind=kind,
        x=pos[0],
        y=pos[1],
        recipe=recipe or fac.default_recipe(defs, kind) or "default",
        owner=OWNER_STATE if state else OWNER_PRIVATE,
        accounting=default_accounting(defs, island, kind, OWNER_STATE if state else OWNER_PRIVATE),
        built=True,
        status=fac.STATUS_STARTING,
    )
    assign_serial(island, f)
    island.facilities.append(f)
    return f


def connect_power(island: IslandState, defs: Defs) -> int:
    """首都とつながっていない施設と住宅を、陸地を通る一番短い送電線で首都につなぐ。

    引いた送電線のマス数を返す。
    """
    capital = island.capital
    assert capital is not None
    land = {(t.x, t.y) for t in island.tiles if fac.is_land(defs, t.terrain)}
    built = 0
    for f in sorted(island.facilities, key=lambda f: (distance((f.x, f.y), capital), f.id)):
        grid = _grid_with(island, capital)
        if (f.x, f.y) in grid:
            continue
        path = shortest_path(island, grid, (f.x, f.y), lambda p: p in land)
        if path is None:
            continue
        for x, y in path:
            tile = island.tile(x, y)
            if island.facility_at(x, y) is None and not tile.power_line:
                infra.set_level(tile, infra.power_kind(defs), infra.FULL)
                built += 1
    return built


def _grid_with(island: IslandState, start: Pos) -> set[Pos]:
    nodes = {(f.x, f.y) for f in island.facilities}
    nodes |= {(t.x, t.y) for t in island.tiles if t.power_line}
    for group in components(island, nodes):
        if start in group:
            return set(group)
    return {start}
