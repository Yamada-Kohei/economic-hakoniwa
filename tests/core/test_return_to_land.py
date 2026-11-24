"""帰農: 町の仕事のない人が、空きのある村へ移り住んで自給の農業に戻る（W-272 ④。PO-64。会議 #4109。
population.return_to_land）。

人の減った町を人数の線でその場の全員の農家に数え替えるのではなく、町の仕事のない人のうち、働き口の
近くの町へ移らずに残った人が、少しずつ村へ移る。枠は町行きと同じ population.migration_rate を共に
使い、行き先は村の住む所の空き・village_size・村の土地が食べさせられる人数まで。
"""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core.modules import population as popmod
from hakoniwa_core.modules.population import (
    FLED,
    Population,
    _town_idle,
    village_capacity,
    village_size,
)
from tests.core.test_phase3_3 import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import home, make_island, with_economy

TOWN, NEAR, FAR = (9, 9), (12, 12), (4, 4)


def _island(game):  # noqa: F811
    """町の住宅地 1 つ（働き手の半分が仕事なし）と、村の住宅地 2 つの島。"""
    island = make_island(game)
    home(game, island, *TOWN, village_size(game.defs) + 1000, level="town")
    small = village_capacity(game.defs) // 4
    home(game, island, *NEAR, small, level="village")
    home(game, island, *FAR, small, level="village")
    with_economy(game, island)
    town = island.tile(*TOWN)
    town.ext["employed"] = fac.labor_force(game.defs, island, town) // 2
    homes = {(f.x, f.y): f for f in island.facilities if fac.is_housing(game.config, f)}
    free = {
        p: fac.housing_capacity(game.defs, f) - island.tile(*p).population for p, f in homes.items()
    }
    return island, homes, free


def _ctx(game):  # noqa: F811
    return SimpleNamespace(defs=game.defs, config=game.config, turn=1)


def test_only_town_housing_has_candidates(game):  # noqa: F811
    island, homes, _ = _island(game)
    idle = _town_idle(game.defs, island, homes, fac.labor_share(game.defs, island))
    assert set(idle) == {TOWN}
    assert 0 < idle[TOWN] < island.tile(*TOWN).population


def test_those_left_after_the_town_moves_go_to_the_nearest_village(game):  # noqa: F811
    """働き口の近くの町へ移れずに残った人（stayed）は、近い村から住む所の空きの分まで移る。町へ戻る
    印（fled）は付けない。内訳（候補・枠・移った人）を残す。"""
    island, homes, free = _island(game)
    idle = _town_idle(game.defs, island, homes, fac.labor_share(game.defs, island))
    town, near, far = (island.tile(*p).population for p in (TOWN, NEAR, FAR))
    n = free[NEAR] // 2
    island.tile(*TOWN).population -= n  # _migrate が元のマスから引いた分
    moved = Population._return_to_land(
        island, _ctx(game), random.Random(1), homes, free, {}, idle, {TOWN: n}, {TOWN: 5}
    )
    assert moved == n
    assert island.tile(*NEAR).population == near + n  # 近い村から
    assert island.tile(*FAR).population == far
    assert island.tile(*TOWN).population == town - n
    assert FLED not in island.tile(*NEAR).ext
    assert island.stats["population.return_to_land.candidates"] == idle[TOWN] - 5
    assert island.stats["population.return_to_land.quota"] == n
    assert island.stats["population.returned_to_land"] == n


def test_jobless_near_jobs_go_at_the_migration_rate_without_arrivals(game):  # noqa: F811
    """人手の足りない仕事に近い町（pull がある）は町行きがないので、候補の migration_rate の割合が
    帰農の枠。このターンに移ってきた人は候補に入らない（候補は移り住みの前に数える）。"""
    island, homes, free = _island(game)
    idle = _town_idle(game.defs, island, homes, fac.labor_share(game.defs, island))
    island.tile(*TOWN).population += 1000  # このターンに町へ移ってきた人
    town = island.tile(*TOWN).population
    rate = game.config.get("population.migration_rate")
    moved = Population._return_to_land(
        island, _ctx(game), random.Random(1), homes, free, {TOWN: 1}, idle, {}
    )
    assert 0 < moved == pytest.approx(idle[TOWN] * rate, abs=1)
    assert island.tile(*TOWN).population == town - moved


def test_the_village_takes_only_what_its_land_feeds(game, monkeypatch):  # noqa: F811
    """行き先の上限は、住む所の空き・village_size・村の土地が食べさせられる人数の小さい方。移れな
    かった人は元のマスに残る。"""
    island, homes, free = _island(game)
    monkeypatch.setattr(popmod, "_land_room", lambda *args: 3)
    idle = _town_idle(game.defs, island, homes, fac.labor_share(game.defs, island))
    town = island.tile(*TOWN).population
    island.tile(*TOWN).population -= 100
    moved = Population._return_to_land(
        island, _ctx(game), random.Random(1), homes, free, {}, idle, {TOWN: 100}
    )
    assert moved == 6
    assert island.tile(*TOWN).population == town - 6


def test_land_room_counts_the_farmers_already_there(game):  # noqa: F811
    island, _, _ = _island(game)
    share = fac.labor_share(game.defs, island)
    village = island.tile(*NEAR)
    room = popmod._land_room(game.defs, game.config, island, village, share)
    per = game.config.get("population.food_per_person")
    most = popmod._land_output(
        game.defs, game.config, island, village, fac.workers_needed(game.defs, "farm")
    )
    sub = popmod.subsistence_persons(game.defs, island, village, share)
    assert room == max(0, int(most // popmod.to_milli(per)) - sub)


@pytest.mark.parametrize("on", [False, True])
def test_the_world_runs_with_return_to_land(game, monkeypatch, on):  # noqa: F811
    """住宅地の段階で決める切り替えと帰農を入にして標準の計画で回しても止まらず、帰農の内訳が残る。
    切なら内訳を書かない。"""
    from hakoniwa_core.state.generate import generate_world
    from tests.core.test_finance import run

    pop = game.config.get("population")
    monkeypatch.setitem(pop, "farming_by_housing", on)
    monkeypatch.setitem(pop, "return_to_land", on)
    world = generate_world(1, 3, game.config, game.defs)
    run(game, world, 5)
    seen = [i for i in world.islands.values() if "population.return_to_land.quota" in i.stats]
    assert bool(seen) is on
