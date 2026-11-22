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


def test_a_shrunk_town_goes_back_to_farming(game, switch):  # noqa: F811
    """町の段階の住宅地でも、人数が村の住宅地の上限を切ったら村に戻り（過疎の町の失業を固定させない）、
    町の下限に届くまで村のまま。その間は前の印を引き継ぎ、人数のわずかな上下では入れ替わらない。"""
    from hakoniwa_core.modules.population import village_capacity

    switch()
    cap, town = village_capacity(game.defs), village_size(game.defs) + 1
    assert cap < town  # どちらも定義（settlements）の数
    island = make_island(game)
    home(game, island, 6, 6, town + 5000, level="town")
    tile = island.tile(6, 6)
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is False
    tile.population = cap  # 村の上限ちょうどは、まだ町
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is False
    tile.population = cap - 1
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is True  # 村の上限を切って村に戻る
    tile.population = town - 1
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is True  # 町の下限まで村のまま
    tile.population = town
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is False
