"""自給の農業ができる村かを、建った住宅地の段階で決める（W-272。PO-61。population.farming_by_housing）。

人数だけで毎ターン決めると、町の下限の前後のマスは人数のわずかな上下で村と町を行き来し、仕事の
ない人が自給の農家と失業者を行き来した。
"""

from __future__ import annotations

import pytest

from hakoniwa_core.modules.population import FARMING, farming, refresh_farming, village_size
from tests.core.test_phase3_3 import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import home, make_island


@pytest.fixture
def switch(game, monkeypatch):  # noqa: F811
    def turn_on(value=True) -> None:
        monkeypatch.setitem(game.config.get("population"), "farming_by_housing", value)

    return turn_on


def _village(game, population):  # noqa: F811
    island = make_island(game)
    home(game, island, 6, 6, population, level="village")
    return island, island.tile(6, 6)


def test_village_housing_keeps_farming_above_the_town_size(game, switch):  # noqa: F811
    """村の段階の住宅地は、人数が町の下限を超えても自給の農業を続ける（切なら人数で町になる）。"""
    town = village_size(game.defs) + 1
    switch(False)
    island, tile = _village(game, town + 10)
    refresh_farming(game.defs, game.config, island)
    assert FARMING not in tile.ext
    assert not farming(game.defs, tile)

    switch()
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is True
    assert farming(game.defs, tile)
    tile.population = town - 1  # 人数が上下しても入れ替わらない
    refresh_farming(game.defs, game.config, island)
    assert farming(game.defs, tile)


def test_tile_without_housing_keeps_its_mark(game, switch):  # noqa: F811
    """住宅地のないマスは前の印を引き継ぐ。村から町へは人数が町の下限に届いたときだけ移り、町の印は
    人がいなくなるまで残す（固定の幅は持たない）。"""
    switch()
    town = village_size(game.defs) + 1
    island = make_island(game)
    tile = island.tile(6, 6)
    tile.population = town - 1
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is True  # 初めは人数で（村）
    tile.population = town
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is False  # 町の下限に届いて町
    tile.population = town - 1
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is False  # 下限を切っても町のまま
    tile.population = 0
    refresh_farming(game.defs, game.config, island)
    assert FARMING not in tile.ext  # 人がいなくなれば印を消す


def test_switching_off_clears_the_marks(game, switch):  # noqa: F811
    switch(False)
    island, tile = _village(game, 100)
    tile.ext[FARMING] = False
    refresh_farming(game.defs, game.config, island)
    assert FARMING not in tile.ext


def test_the_world_runs_with_the_switch_on(game, switch):  # noqa: F811
    """入にして標準の計画で回しても止まらず（帳簿は記録の段が確かめる）、人のいるマスに印があり、
    失業者の層の人数は失業率と同じ数え方の人数のまま（W-294）。"""
    from hakoniwa_core.economy import JOBLESS
    from hakoniwa_core.modules.population import jobless_persons
    from hakoniwa_core.state.generate import generate_world
    from tests.core.test_finance import run

    switch()
    world = generate_world(1, 3, game.config, game.defs)
    run(game, world, 5)
    for island in world.islands.values():
        people = [t for t in island.tiles if t.population > 0]
        assert people and all(FARMING in t.ext for t in people)
        if island.has_economy and JOBLESS in island.economy.households:
            layer = island.stats[f"layer.{JOBLESS}.persons"]
            assert layer == jobless_persons(game.defs, island)


def test_town_housing_stays_a_town_whatever_its_size(game, switch):  # noqa: F811
    """町の段階の住宅地は、人数が村の住宅地の上限や町の下限を切っても町のまま（人数の幅は使わない。
    会議 #4109）。人の減った町の仕事のない人は、帰農（population.return_to_land）で村へ移る。"""
    from hakoniwa_core.modules.population import village_capacity

    switch()
    cap, town = village_capacity(game.defs), village_size(game.defs) + 1
    island = make_island(game)
    home(game, island, 6, 6, town, level="town")
    tile = island.tile(6, 6)
    for population in (town, town - 1, cap, cap - 1, 1):
        tile.population = population
        refresh_farming(game.defs, game.config, island)
        assert tile.ext[FARMING] is False, population
        assert not farming(game.defs, tile)


def test_two_months_of_the_same_size_follow_the_housing_stage(game, switch):  # noqa: F811
    """人数は両月とも町の下限のすぐ下のまま、前月は村の住宅地・当月は町の住宅地なら、前月は村・当月は
    町（段階が変わったときの一度の入れ替わりは、意図した動き）。切なら両月とも人数で村。"""
    town = village_size(game.defs) + 1
    island, tile = _village(game, town - 100)
    marks = {}
    for value in (True, False):
        switch(value)
        island.facilities[-1].ext["level"] = "village"
        refresh_farming(game.defs, game.config, island)
        before = farming(game.defs, tile)
        island.facilities[-1].ext["level"] = "town"
        refresh_farming(game.defs, game.config, island)
        marks[value] = (before, farming(game.defs, tile))
    assert marks[True] == (True, False)
    assert marks[False] == (True, True)
