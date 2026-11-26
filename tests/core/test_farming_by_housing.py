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
    """村の段階の住宅地は、人数が町の下限を超えても自給の農業を続ける（印は段階が変わるまで
    人数で動かない。切なら人数で町になる）。"""
    town = village_size(game.defs) + 1
    switch()
    island, tile = _village(game, town - 1)
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is True
    tile.population = town + 10  # 人数が町の大きさを超えても入れ替わらない
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is True
    assert farming(game.defs, tile)

    switch(False)
    refresh_farming(game.defs, game.config, island)
    assert FARMING not in tile.ext
    assert not farming(game.defs, tile)


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


def test_switching_on_does_not_recount_the_whole_island(game, switch):  # noqa: F811
    """入にした最初のターンに、全マスの数え直し（建った段階での決め直し）は起きない（#6011）。
    初めて決めるマスは前の決め方（人数）で決めるので、建った段階と人数が食い違うマスも、
    人数で決めた印のままになる。"""
    town = village_size(game.defs) + 1
    island = make_island(game)
    home(game, island, 6, 6, town - 1, level="town")  # 建てば町、人数は村
    home(game, island, 8, 8, town + 10, level="village")  # 建てば村、人数は町
    switch(False)
    refresh_farming(game.defs, game.config, island)
    assert FARMING not in island.tile(6, 6).ext
    switch()
    refresh_farming(game.defs, game.config, island)
    assert island.tile(6, 6).ext[FARMING] is True  # 人数（村）のまま。建った段階なら False
    assert island.tile(8, 8).ext[FARMING] is False  # 人数（町）のまま。建った段階なら True


def test_only_tiles_whose_stage_changed_are_redetermined(game, switch):  # noqa: F811
    """建った段階で決め直すのは、段階が変わったマスだけ（#6011）。人数だけが動いたマスは
    前の印のまま。"""
    town = village_size(game.defs) + 1
    switch()
    island = make_island(game)
    home(game, island, 6, 6, town - 1, level="village")
    home(game, island, 8, 8, town + 10, level="town")
    home(game, island, 10, 10, town - 1, level="town")
    refresh_farming(game.defs, game.config, island)
    assert island.tile(6, 6).ext[FARMING] is True
    assert island.tile(8, 8).ext[FARMING] is False
    assert island.tile(10, 10).ext[FARMING] is True
    island.facility_at(6, 6).ext["level"] = "town"  # 段階の変更（村→町）
    island.facility_at(8, 8).ext["level"] = "village"  # 段階の変更（町→村）
    island.tile(10, 10).population = town + 10  # 人数だけの変更
    refresh_farming(game.defs, game.config, island)
    assert island.tile(6, 6).ext[FARMING] is False  # 建った段階（町）で決め直した
    assert island.tile(8, 8).ext[FARMING] is True  # 建った段階（村）で決め直した
    assert island.tile(10, 10).ext[FARMING] is True  # 段階は同じなので前の印のまま


def test_flipped_tiles_are_restored_once(game, switch):  # noqa: F811
    """一度だけの戻し（#6011）: 数え替えで倒れたマス（人数は村・建った段階は町・印 False）を
    村の印に戻し、戻した人数を島の ext とターンログに 1 行残す。二度はやらない。"""
    from hakoniwa_core.modules.population import FARMING_RESTORED

    town = village_size(game.defs) + 1
    switch()
    island = make_island(game)
    home(game, island, 6, 6, 1000, level="town")
    island.tile(6, 6).ext[FARMING] = False  # 倒れたマス
    home(game, island, 8, 8, 500, level="town")
    island.tile(8, 8).ext[FARMING] = False  # 倒れたマス
    home(game, island, 10, 10, town + 10, level="town")
    island.tile(10, 10).ext[FARMING] = False  # 人数も町なので倒れていない
    home(game, island, 12, 12, 1000, level="village")
    island.tile(12, 12).ext[FARMING] = True  # 村のまま
    refresh_farming(game.defs, game.config, island)
    assert island.tile(6, 6).ext[FARMING] is True
    assert island.tile(8, 8).ext[FARMING] is True
    assert island.tile(10, 10).ext[FARMING] is False
    assert island.tile(12, 12).ext[FARMING] is True
    assert island.ext[FARMING_RESTORED] == 1500  # 倒れていた人口と一致する
    logs = [e for e in island.log if e.kind == "farming_restored"]
    assert len(logs) == 1 and logs[0].args["count"] == 1500
    # 二度はやらない: 戻したマスの印を手で倒しても、もう戻らない
    island.tile(6, 6).ext[FARMING] = False
    refresh_farming(game.defs, game.config, island)
    assert island.tile(6, 6).ext[FARMING] is False
    assert island.ext[FARMING_RESTORED] == 1500
    assert len([e for e in island.log if e.kind == "farming_restored"]) == 1
