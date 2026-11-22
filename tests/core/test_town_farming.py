"""町のマスの、仕事のない人が少しずつ自給の農業に戻る（W-272。PO-62。population.town_farming_percent）。

村か町かは人数で毎ターン決める。町の下限の前後のマスで、仕事のない人が全員、自給の農家と失業者の間を
行き来しないように、町でも畑の受け入れの余りまで少しずつ畑に戻れるようにする（会議 #2745〜#2759）。
"""

from __future__ import annotations

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core.modules.population import (
    TOWN_FARMERS,
    farm_room,
    settle_town_farmers,
    subsistence_persons,
    tile_jobless,
    town_farm_moves,
    village_size,
)
from tests.core.test_phase3_3 import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import home, make_island


@pytest.fixture
def rate(game, monkeypatch):  # noqa: F811
    def set_rate(value) -> None:
        monkeypatch.setitem(game.config.get("population"), "town_farming_percent", value)

    return set_rate


def _town(game, population, employed=0):  # noqa: F811
    island = make_island(game)
    island.economy.started = 0  # お金の経済が始まった島
    home(game, island, 6, 6, population, level="town")
    tile = island.tile(6, 6)
    tile.ext["employed"] = employed
    return island, tile


def _idle(game, island, tile):  # noqa: F811
    share = fac.labor_share(game.defs, island)
    return fac.labor_force(game.defs, island, tile, share) - tile.ext["employed"]


def test_off_keeps_towns_without_farmers(game, rate):  # noqa: F811
    """0 なら今までどおり、町では誰も自給せず、前の印も消す。"""
    rate(0)
    town = village_size(game.defs) + 10_000
    island, tile = _town(game, town)
    tile.ext[TOWN_FARMERS] = 100
    settle_town_farmers(game.defs, game.config, island)
    assert TOWN_FARMERS not in tile.ext
    assert subsistence_persons(game.defs, island, tile) == 0


def test_a_share_of_the_unhired_go_back_to_the_fields(game, rate):  # noqa: F811
    """雇われず畑にもいない人（U）の割合が、畑の受け入れの余り（V）まで移る。
    0 ≤ moved ≤ min(U, V)。"""
    rate(25)
    town = village_size(game.defs) + 10_000
    island, tile = _town(game, town, employed=1000)
    share = fac.labor_share(game.defs, island)
    before = tile_jobless(game.defs, island, tile, share)
    ((kept, unhired, space, moved, _),) = town_farm_moves(game.defs, island, 25, share).values()
    assert kept == 0 and unhired == _idle(game, island, tile)
    assert space == farm_room(game.defs, share)
    assert moved == min(unhired * 25 // 100, space)
    assert 0 <= moved <= min(unhired, space)
    settle_town_farmers(game.defs, game.config, island)
    assert tile.ext[TOWN_FARMERS] == moved
    assert tile_jobless(game.defs, island, tile, share) == before - moved
    assert island.stats["labor.town_farming_moved"] == moved


def test_the_room_on_the_fields_caps_the_move(game, rate):  # noqa: F811
    rate(50)
    town = village_size(game.defs) + 10_000
    island, tile = _town(game, town)
    share = fac.labor_share(game.defs, island)
    room = farm_room(game.defs, share)
    tile.ext[TOWN_FARMERS] = room - 5
    ((kept, unhired, space, moved, _),) = town_farm_moves(game.defs, island, 50, share).values()
    assert (kept, space, moved) == (room - 5, 5, 5)
    assert 0 <= moved <= min(unhired, space)
    settle_town_farmers(game.defs, game.config, island)
    assert tile.ext[TOWN_FARMERS] == room


def test_hired_farmers_leave_the_fields(game, rate):  # noqa: F811
    """畑にいる人は、雇われれば畑を離れる（仕事のない人の数を超えない）。"""
    rate(10)
    town = village_size(game.defs) + 10_000
    island, tile = _town(game, town)
    share = fac.labor_share(game.defs, island)
    force = fac.labor_force(game.defs, island, tile, share)
    tile.ext[TOWN_FARMERS] = 3000
    tile.ext["employed"] = force - 1000  # 仕事のない人は 1,000 人だけ
    ((kept, unhired, _, moved, _),) = town_farm_moves(game.defs, island, 10, share).values()
    assert (kept, unhired, moved) == (1000, 0, 0)
    persons = subsistence_persons(game.defs, island, tile, share)
    assert persons == min(tile.population, int(1000 / share))
    assert tile_jobless(game.defs, island, tile, share) == 0


def test_a_village_that_becomes_a_town_keeps_its_farmers(game, rate):  # noqa: F811
    """村では仕事のない人が全員畑にいる。人数が増えて町になっても、その人たちは一度に失業者にならない。"""
    rate(10)
    size = village_size(game.defs)
    island, tile = _town(game, size - 1000, employed=0)
    share = fac.labor_share(game.defs, island)
    settle_town_farmers(game.defs, game.config, island)
    farmers = tile.ext[TOWN_FARMERS]
    assert farmers == min(_idle(game, island, tile), farm_room(game.defs, share))
    tile.population = size + 1  # 町の下限に届く
    persons = subsistence_persons(game.defs, island, tile, share)
    assert persons == min(tile.population, int(farmers / share))
    settle_town_farmers(game.defs, game.config, island)
    assert tile.ext[TOWN_FARMERS] >= farmers


def test_the_order_of_the_tiles_does_not_matter(game, rate):  # noqa: F811
    """マスの順を反対にしても、移る人数は同じ（ターンの初めの値だけで決める）。"""
    rate(25)
    town = village_size(game.defs) + 10_000

    def marks(reverse):
        island = make_island(game)
        island.economy.started = 0
        for i, (x, y) in enumerate(((4, 4), (6, 6), (8, 8))):
            home(game, island, x, y, town + 1000 * i, level="town")
            island.tile(x, y).ext["employed"] = 500 * i
            island.tile(x, y).ext[TOWN_FARMERS] = 200 * i
        if reverse:
            island.tiles.reverse()
        settle_town_farmers(game.defs, game.config, island)
        return {(t.x, t.y): t.ext.get(TOWN_FARMERS) for t in island.tiles if t.population > 0}

    assert marks(False) == marks(True)


def test_the_world_runs_with_town_farming(game, rate):  # noqa: F811
    """入れて標準の計画で回しても止まらず（帳簿は記録の段が確かめる）、失業者の層の人数は失業率と
    同じ数え方の人数のまま（W-294）。"""
    from hakoniwa_core.economy import JOBLESS
    from hakoniwa_core.modules.population import jobless_persons
    from hakoniwa_core.state.generate import generate_world
    from tests.core.test_finance import run

    rate(25)
    world = generate_world(1, 3, game.config, game.defs)
    run(game, world, 5)
    for island in world.islands.values():
        if island.has_economy and JOBLESS in island.economy.households:
            layer = island.stats[f"layer.{JOBLESS}.persons"]
            assert layer == jobless_persons(game.defs, island)


def test_fractions_carry_over_in_hundredths(game, rate):  # noqa: F811
    """端数は 1/100 人で繰り越す。V が 0 なら据え置き、U が 0 なら 0 に戻す（会議 #2717）。"""
    rate(10)
    town = village_size(game.defs) + 10_000
    island, tile = _town(game, town)
    share = fac.labor_share(game.defs, island)
    force = fac.labor_force(game.defs, island, tile, share)
    tile.ext["employed"] = force - 7  # U = 7 人
    tile.ext["town_farming_carry"] = 50
    ((_, unhired, _, moved, carry),) = town_farm_moves(game.defs, island, 10, share).values()
    assert unhired == 7 and (moved, carry) == (1, 20)  # 50 + 70 = 120 → 1 人、残り 20
    tile.ext[TOWN_FARMERS] = farm_room(game.defs, share)  # 余りなし
    tile.ext["employed"] = 0
    ((_, _, space, moved, carry),) = town_farm_moves(game.defs, island, 10, share).values()
    assert (space, moved, carry) == (0, 0, 50)
    tile.ext.pop(TOWN_FARMERS)
    tile.ext["employed"] = force  # U = 0
    ((_, unhired, _, moved, carry),) = town_farm_moves(game.defs, island, 10, share).values()
    assert (unhired, moved, carry) == (0, 0, 0)


def test_a_few_town_farmers_do_not_make_the_whole_tile_employed(game, rate):  # noqa: F811
    """幸福度の「仕事」は、町では畑に戻った人の分だけ仕事のある扱いにする。前は 1 人でも畑にいれば
    マス全体が仕事のある扱いになっていた（W-272 PO-62 の測りで見つけた）。"""
    import copy

    from hakoniwa_core.modules.population import Population, _farming
    from hakoniwa_core.state.generate import generate_world
    from tests.core.test_finance import context, run

    rate(25)
    world = generate_world(1, 3, game.config, game.defs)
    run(game, world, 3)

    def towns(w):
        return [
            (i, t)
            for i in w.islands.values()
            if i.has_economy
            for t in i.tiles
            if t.population > 0 and not _farming(game.defs, t)
        ]

    island, tile = max(towns(world), key=lambda it: it[1].population)
    share = fac.labor_share(game.defs, island)
    force = fac.labor_force(game.defs, island, tile, share)
    tile.ext["employed"] = force // 2  # 半分が仕事なし

    def mood(farmers):
        w = copy.deepcopy(world)
        i = w.islands[island.id]
        t = i.tile(tile.x, tile.y)
        if farmers:
            t.ext[TOWN_FARMERS] = farmers
        else:
            t.ext.pop(TOWN_FARMERS, None)
        Population().run_island(i, context(game, w, "population"))
        return t.ext["happiness"]

    none, one, half = mood(0), mood(1), mood(force - force // 2)
    assert abs(one - none) <= 1  # 1 人だけ畑にいても、ほとんど変わらない
    assert half > one  # 仕事のない人が全員畑にいれば、仕事のある扱い
