"""生産の基盤（インフラ・教育・技術者）の初めの値（フェーズ2-3）。

建国の島と、前のフェーズから続く島の移し替え（modules/upgrade）の両方がここを使います。
インフラと教育の係数がほぼ 1 になる値から始め、生産量が移し替えの前と変わらないようにします。

- 送電線: フェーズ1・2 の {"power": 1} を、送電網 100% の形に直す。
- 道路: 施設（町を含む）のマスと、その周り8マスの陸のマスを infrastructure.start_road にする。
- 上下水道: 育つのに上下水道の要る段階（都市・大都市）まで育った町のマスを
  infrastructure.start_water にする（育った町が縮まないように）。
- 教育: 教育水準を education.start にし、首都の近くの空いたマスに学校（education.start_school）を
  建てた状態で1つ置き、教育費の年の予算を「学校を動かす費用 × education.start_budget_years」にする。
  学校には最初のターンに全力で動ける分の教材を持たせる（国の倉庫にあればそこから、なければ新しく。
  置いたターンの生産の段は市場より前なので、持たせないと最初のターンに教材不足で止まる）。
- 技術者: 一般の労働者の education.engineer_share か、建っている施設が全力で要る人数の多い方を
  技術者にする（技術者の層を作り、預金を人数の割合で一般の層から移す）。
"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import EDUCATION, ENGINEERS, is_public_service, layers
from hakoniwa_core.economy.households import family_of, new_worker_layer, set_workers
from hakoniwa_core.geo import NEIGHBORS8, within
from hakoniwa_core.naming import assign_serial
from hakoniwa_core.state import (
    ACCOUNTING_TREASURY,
    OWNER_STATE,
    ZONE_NONE,
    Facility,
    IslandState,
)
from hakoniwa_core.units import money_of, round_money

VERSION = 3  # economy.ext["version"]: 3 はフェーズ2-3 の基盤を置いた島


def prepare(island: IslandState, defs: Defs, config: GameConfig, turn: int = 0) -> None:
    """島に生産の基盤の初めの値を置く（1度だけ）。turn は置くターン（学校の教材の作られた
    ターンにする）。"""
    economy = island.economy
    if economy.ext.get("version", 0) >= VERSION:
        return
    economy.ext["version"] = VERSION
    for tile in island.tiles:
        infra.normalize(tile)
    school = _school(island, defs, config, turn)
    _roads(island, defs, config)
    _water(island, defs, config)
    if economy.education <= 0:
        economy.education = round(config.get("education.start") * infra.FULL)
    if school is not None and EDUCATION not in island.policy.budget:
        island.policy.budget[EDUCATION] = _school_budget(island, defs, config, school)
    _engineers(island, defs, config)


def _school(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> Facility | None:
    """首都の近くの空いたマスに、学校を建てた状態で置く（もう学校があれば置かない）。"""
    from hakoniwa_core.economy.start import init_facility  # start.py がこのモジュールを読むため

    kind = config.get("education.start_school")
    if not kind or kind not in defs.facilities:
        return None
    for f in island.facilities:
        if f.kind == kind:
            return f
    center = island.capital or (island.size // 2, island.size // 2)
    occupied = island.facilities_by_tile()
    for x, y in within(island, center, island.size):
        tile = island.tile(x, y)
        if (x, y) in occupied or tile.zone != ZONE_NONE or tile.population > 0:
            continue
        if fac.placement_problem(defs, island, kind, x, y) is not None:
            continue
        f = Facility(
            id=island.next_facility_id(),
            kind=kind,
            x=x,
            y=y,
            recipe=fac.default_recipe(defs, kind) or "default",
            owner=OWNER_STATE,
            accounting=ACCOUNTING_TREASURY,
            built=True,
            status=fac.STATUS_STARTING,
        )
        assign_serial(island, f)
        island.facilities.append(f)
        wage = island.economy.average_wage or config.get("labor.initial_wage")
        init_facility(island, defs, config, f, island.economy.prices, wage)
        _first_materials(island, defs, config, f, turn)
        return f
    return None


def _first_materials(
    island: IslandState, defs: Defs, config: GameConfig, f: Facility, turn: int
) -> None:
    """学校に、最初のターンに全力で動ける分の教材を持たせる（国の倉庫にある分はそこから）。"""
    power = config.get("power.good")
    for good, qty in sorted(fac.recipe_of(defs, f)["inputs"].items()):
        if good == power:
            continue
        moved = stock.take_batches(island.warehouse, good, qty)
        stock.put_batches(f.stock, good, moved)
        stock.add(f.stock, good, qty - sum(q for _, q in moved), turn, defs.goods)


def _school_budget(island: IslandState, defs: Defs, config: GameConfig, f: Facility) -> int:
    """学校を全力で動かす年の費用（教員の賃金と教材）× education.start_budget_years。"""
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    prices = island.economy.prices
    turn = fac.workers_of(defs, f) * wage
    for good, qty in fac.recipe_of(defs, f)["inputs"].items():
        turn += money_of(prices.get(good, 0), qty)
    return round_money(turn * YEAR * config.get("education.start_budget_years"))


def _engineers(island: IslandState, defs: Defs, config: GameConfig) -> None:
    """技術者の層を作り、技術者の数を決める（もういれば変えない）。"""
    economy = island.economy
    if ENGINEERS not in layers(config) or fac.ENGINEER not in defs.workers:
        return
    if ENGINEERS not in economy.households:
        economy.households[ENGINEERS] = new_worker_layer(fac.ENGINEER)
    if economy.workers.get(fac.ENGINEER, 0) > 0:
        return
    population = sum(t.population for t in island.tiles)
    general = population * fac.labor_share(defs, island)
    count = round(general * config.get("education.engineer_share"))
    needed = sum(
        fac.engineers_of(defs, f)
        for f in island.facilities
        if f.built and not is_public_service(defs, f)
    )
    count = max(count, needed)
    # 技術者の家族が一般の労働者の数を超えないようにする（人の少ない島で一般の働き手が尽きない
    # ように）
    while count > 0 and family_of(defs, fac.ENGINEER, count) > general:
        count //= 2
    set_workers(island, defs, fac.ENGINEER, count)


def _roads(island: IslandState, defs: Defs, config: GameConfig) -> None:
    kinds = defs.infra_kinds("transport")
    if not kinds:
        return
    road = kinds[0]
    level = round(config.get("infrastructure.start_road") * infra.FULL)
    for f in island.facilities:
        if not fac.on_map(f):
            continue
        for dx, dy in ((0, 0), *NEIGHBORS8):
            x, y = f.x + dx, f.y + dy
            if not island.in_bounds(x, y):
                continue
            tile = island.tile(x, y)
            if fac.is_land(defs, tile.terrain) and infra.level(tile, road) < level:
                infra.set_level(tile, road, level)


def _water(island: IslandState, defs: Defs, config: GameConfig) -> None:
    kinds = defs.infra_kinds("water")
    if not kinds:
        return
    level = round(config.get("infrastructure.start_water") * infra.FULL)
    housing = fac.housing_kind(config)
    for f in island.facilities:
        if f.kind != housing or not fac.on_map(f):
            continue
        settlement = defs.settlements.get(f.ext.get("level", ""), {})
        tile = island.tile(f.x, f.y)
        if settlement.get("water", 0) > 0 and infra.water(defs, tile) < level:
            infra.set_level(tile, kinds[0], level)
