"""村→町の格上げの引き金から自給の人を外す（W-272 ④、会議 #4164）。

切り替えは housing.upgrade_without_subsistence（既定は切）。

村の住宅地が町に育つ一段だけは、住む人が望む広さ want を want ×（1 − 自給の人 ÷ 人口）にして決める。
空き求人で育つ所（Housing._growable）と住み替え（Housing._relocate）の両方に同じ形で効く。町→市など
ほかの段は今のまま。切なら今の main と同じ。
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.economy import GENERAL
from hakoniwa_core.modules import population as popmod
from hakoniwa_core.modules.population import (
    Housing,
    _upgrade_want,
    dwelling_status,
    homes_of,
    subsistence_persons,
)
from tests.core.test_phase3_3 import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import home, make_island, with_economy

POS = (9, 9)


def _at_cap(game, level, employed_share):  # noqa: F811
    """段階 level の上限の床まで建った住宅地 1 つの島。働き手のうち employed_share が仕事あり。"""
    island = make_island(game)
    cap = game.defs.settlements[level]["capacity"]
    house = home(game, island, *POS, cap * 9 // 10, level=level)
    fac.set_floor(game.defs, house, cap)
    with_economy(game, island)
    tile = island.tile(*POS)
    tile.ext["employed"] = int(fac.labor_force(game.defs, island, tile) * employed_share)
    homes = homes_of(island, game.config)
    status = dwelling_status(game.defs, game.config, island, homes)
    d = status[POS]
    want = d.nominal * 1.5  # 望みが床を大きく超える（村→町に育つ合図）
    status[POS] = d._replace(want=want, layers={GENERAL: want})
    return island, homes, status


def _grow(game, island, homes, status, on):  # noqa: F811
    share = fac.labor_share(game.defs, island) if on else None
    return Housing._growable(island, game.defs, homes, [POS], status, 0.8, set(), True, share)


def test_the_want_drops_by_the_share_of_subsistence_farmers(game):  # noqa: F811
    village = game.defs.settlement_levels()[0]
    island, _, _ = _at_cap(game, village, 0.5)
    share = fac.labor_share(game.defs, island)
    tile = island.tile(*POS)
    sub = subsistence_persons(game.defs, island, tile, share)
    assert 0 < sub < tile.population
    got = _upgrade_want(game.defs, island, tile, 1000.0, share)
    assert got == 1000.0 * (1 - sub / tile.population)


def test_a_village_of_farmers_does_not_grow_into_a_town_when_on(game):  # noqa: F811
    """空き求人で育つ所: 仕事のない働き手が自給の農家として食べている村は、切なら町に育つが、入なら
    自給の人を外した望みが床に届かないので育たない。"""
    village = game.defs.settlement_levels()[0]
    island, homes, status = _at_cap(game, village, 0.0)
    assert _grow(game, island, homes, status, on=False) == POS
    assert _grow(game, island, homes, status, on=True) is None


def test_a_village_with_jobs_still_grows_when_on(game):  # noqa: F811
    """みな仕事のある村は自給の人がいないので、入でも今のとおり育つ。"""
    village = game.defs.settlement_levels()[0]
    island, homes, status = _at_cap(game, village, 1.0)
    assert _grow(game, island, homes, status, on=True) == POS


def test_other_steps_are_unchanged(game, monkeypatch):  # noqa: F811
    """町→市の段は自給の人を見ない（町には自給の農家はいない）。市に要る上下水道は足りているとする。"""
    monkeypatch.setattr(popmod.infra, "water", lambda *args: 10**9)
    town = game.defs.settlement_levels()[1]
    island, homes, status = _at_cap(game, town, 0.0)
    assert _grow(game, island, homes, status, on=True) == POS


def _relocate(game, island, homes, status, on):  # noqa: F811
    h = copy.deepcopy(game.config.get("housing"))
    h["upgrade_without_subsistence"] = on
    held = island.economy.housing.setdefault("stock", {})
    floor = fac.floor_of(game.defs, homes[POS])
    for good, qty in fac.floor_materials(game.defs, floor, floor + 500).items():
        stock.add(held, good, qty, 0, game.defs.goods)
    island.economy.housing["move"] = {}
    ctx = SimpleNamespace(defs=game.defs, config=game.config, turn=1)
    Housing._relocate(island, ctx, h, homes, status, set())
    return fac.floor_of(game.defs, homes[POS]) - floor


def test_moving_does_not_lift_a_village_of_farmers_into_a_town_when_on(game):  # noqa: F811
    """住み替え: 村の上限にいる自給の農家の村は、切なら床を足して町の段に入るが、入なら足さず、
    ためた分も残さない。"""
    village = game.defs.settlement_levels()[0]
    island, homes, status = _at_cap(game, village, 0.0)
    assert _relocate(game, island, homes, copy.copy(status), on=False) > 0
    island, homes, status = _at_cap(game, village, 0.0)
    assert _relocate(game, island, homes, status, on=True) == 0
    assert fac.FLOOR_DUE not in homes[POS].ext


def test_the_world_runs_with_the_switch(game, monkeypatch):  # noqa: F811
    from hakoniwa_core.state.generate import generate_world
    from tests.core.test_finance import run

    monkeypatch.setitem(game.config.get("housing"), "upgrade_without_subsistence", True)
    monkeypatch.setitem(game.config.get("population"), "farming_by_housing", True)
    world = generate_world(1, 3, game.config, game.defs)
    run(game, world, 5)
