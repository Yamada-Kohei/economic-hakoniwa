"""町のマスの、仕事のない人が少しずつ自給の農業に戻る（W-272。PO-62。population.town_farming_percent）。

村か町かは人数で毎ターン決める。町の下限の前後のマスで、仕事のない人が全員、自給の農家と失業者の間を
行き来しないように、町でも畑の受け入れの余りまで少しずつ畑に戻れるようにする（会議 #2745〜#2759）。
"""

from __future__ import annotations

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core.modules.population import (
    TOWN_FARMERS,
    farm_plots,
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


def _fields(island, plots):
    """空き地の区画を plots だけにする（ほかの人のいない平地は森に）。"""
    keep = set(plots)
    for t in island.tiles:
        if t.terrain == "plain" and t.population <= 0 and (t.x, t.y) not in keep:
            t.terrain = "forest"


def _moves(game, island, percent):  # noqa: F811
    share = fac.labor_share(game.defs, island)
    return town_farm_moves(game.defs, game.config, island, percent, share)


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


def test_plots_are_open_plains_near_nobody(game):  # noqa: F811
    """区画は、用途の指定のない、人の住んでいない、施設のない平地。"""
    island, _ = _town(game, village_size(game.defs) + 10_000)
    plots = set(farm_plots(game.defs, game.config, island))
    assert (6, 6) not in plots  # 町のマス
    assert (7, 6) in plots
    assert (0, 0) not in plots  # 海
    island.tile(7, 6).zone = "industrial"
    assert (7, 6) not in farm_plots(game.defs, game.config, island)


def test_a_share_of_the_unhired_go_back_to_the_fields(game, rate):  # noqa: F811
    """雇われず畑にもいない人（U）の割合が、通勤圏の区画の空き（V）まで移る。
    0 ≤ moved ≤ min(U, V)。"""
    rate(25)
    town = village_size(game.defs) + 10_000
    island, tile = _town(game, town, employed=1000)
    _fields(island, [(7, 6)])
    share = fac.labor_share(game.defs, island)
    before = tile_jobless(game.defs, island, tile, share)
    ((kept, unhired, space, moved, _),) = _moves(game, island, 25).values()
    assert kept == 0 and unhired == _idle(game, island, tile)
    assert space == farm_room(game.defs, share)  # 1 区画
    assert moved == min(unhired * 25 // 100, space)
    assert 0 <= moved <= min(unhired, space)
    settle_town_farmers(game.defs, game.config, island)
    assert tile.ext[TOWN_FARMERS] == moved
    assert tile_jobless(game.defs, island, tile, share) == before - moved
    assert island.stats["labor.town_farming_moved"] == moved


def test_no_open_land_no_farmers(game, rate):  # noqa: F811
    """通勤圏に空き地がなければ、誰も畑に戻れず、畑にいた人も畑を離れる（区画が埋まった）。"""
    rate(50)
    island, tile = _town(game, village_size(game.defs) + 10_000)
    _fields(island, [(12, 12)])  # 通勤圏の外
    tile.ext[TOWN_FARMERS] = 500
    ((kept, _, space, moved, _),) = _moves(game, island, 50).values()
    assert (kept, space, moved) == (0, 0, 0)
    settle_town_farmers(game.defs, game.config, island)
    assert TOWN_FARMERS not in tile.ext


def test_the_room_on_the_plots_caps_the_move(game, rate):  # noqa: F811
    rate(50)
    town = village_size(game.defs) + 10_000
    island, tile = _town(game, town)
    _fields(island, [(7, 6), (5, 6)])
    share = fac.labor_share(game.defs, island)
    room = 2 * farm_room(game.defs, share)
    tile.ext[TOWN_FARMERS] = room - 5
    ((kept, unhired, space, moved, _),) = _moves(game, island, 50).values()
    assert (kept, space, moved) == (room - 5, 5, 5)
    assert 0 <= moved <= min(unhired, space)
    settle_town_farmers(game.defs, game.config, island)
    assert tile.ext[TOWN_FARMERS] == room
    ((kept, _, space, moved, _),) = _moves(game, island, 50).values()
    assert (kept, space, moved) == (room, 0, 0)  # 満ちたら新しく移る人は 0


def test_hired_farmers_leave_the_fields(game, rate):  # noqa: F811
    """畑にいる人は、雇われれば畑を離れる（仕事のない人の数を超えない）。空いた区画には次の人が入る。"""
    rate(10)
    town = village_size(game.defs) + 10_000
    island, tile = _town(game, town)
    _fields(island, [(7, 6)])
    share = fac.labor_share(game.defs, island)
    force = fac.labor_force(game.defs, island, tile, share)
    tile.ext[TOWN_FARMERS] = 3000
    tile.ext["employed"] = force - 1000  # 仕事のない人は 1,000 人だけ
    ((kept, unhired, _, moved, _),) = _moves(game, island, 10).values()
    assert (kept, unhired, moved) == (1000, 0, 0)
    persons = subsistence_persons(game.defs, island, tile, share)
    assert persons == min(tile.population, int(1000 / share))
    assert tile_jobless(game.defs, island, tile, share) == 0


def test_towns_share_a_plot_by_how_many_want_to_come(game, rate):  # noqa: F811
    """2 つの町が 1 つの区画を分け合うときは、移りたい人数に比例して分ける。今畑にいる人が先。"""
    rate(50)
    town = village_size(game.defs) + 10_000
    island = make_island(game)
    island.economy.started = 0
    home(game, island, 5, 6, town, level="town")
    home(game, island, 9, 6, town, level="town")
    _fields(island, [(7, 6)])  # 2 つの町のどちらの通勤圏にもある
    share = fac.labor_share(game.defs, island)
    room = farm_room(game.defs, share)
    force = fac.labor_force(game.defs, island, island.tile(5, 6), share)
    island.tile(5, 6).ext["employed"] = force - 3 * room  # 移りたい人 1.5 区画分（V の 1 区画まで）
    island.tile(9, 6).ext["employed"] = force - room  # 0.5 区画分
    plan = _moves(game, island, 50)
    a, b = plan[(5, 6)][3], plan[(9, 6)][3]
    assert a + b == room
    assert abs(a - 2 * b) <= 2  # 希望 1 区画と 0.5 区画の比
    for pos in plan:
        island.tile(*pos).ext[TOWN_FARMERS] = plan[pos][3]
    plan = _moves(game, island, 50)
    assert (plan[(5, 6)][0], plan[(9, 6)][0]) == (a, b)  # 今いる人が残り
    assert plan[(5, 6)][3] == plan[(9, 6)][3] == 0  # 満ちているので新しく入れない


def test_plots_are_swapped_so_both_towns_fit(game, rate):  # noqa: F811
    """町 A は区画 P と Q に、町 B は P だけに届くとき、A が P に入っていても Q へ移って、B が P に
    入れる（区画の振り替え）。"""
    rate(100)
    town = village_size(game.defs) + 10_000
    island = make_island(game)
    island.economy.started = 0
    home(game, island, 4, 6, town, level="town")  # A
    home(game, island, 10, 6, town, level="town")  # B
    _fields(island, [(7, 6), (2, 6)])  # P（両方から 3 マス）と Q（A だけ）
    share = fac.labor_share(game.defs, island)
    room = farm_room(game.defs, share)
    force = fac.labor_force(game.defs, island, island.tile(4, 6), share)
    for pos in ((4, 6), (10, 6)):
        island.tile(*pos).ext["employed"] = force - room
    plan = _moves(game, island, 100)
    assert plan[(4, 6)][3] == room and plan[(10, 6)][3] == room


def test_a_village_that_becomes_a_town_keeps_its_farmers(game, rate):  # noqa: F811
    """村では仕事のない人が全員畑にいる。町になっても、周りの区画に入れる分は一度に失業者にならない。"""
    rate(10)
    size = village_size(game.defs)
    island, tile = _town(game, size - 1000, employed=0)
    _fields(island, [(7, 6), (5, 6)])
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
    """マスの順を反対にしても、移る人数は同じ（ターンの初めの値と位置だけで決める）。区画を分け合う
    町があっても同じ。"""
    rate(25)
    town = village_size(game.defs) + 10_000

    def marks(reverse):
        island = make_island(game)
        island.economy.started = 0
        for i, (x, y) in enumerate(((4, 4), (6, 6), (8, 8))):
            home(game, island, x, y, town + 1000 * i, level="town")
            island.tile(x, y).ext["employed"] = 500 * i
            island.tile(x, y).ext[TOWN_FARMERS] = 200 * i
        _fields(island, [(5, 5), (7, 7), (6, 4), (9, 9)])
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
    _fields(island, [(7, 6)])
    share = fac.labor_share(game.defs, island)
    force = fac.labor_force(game.defs, island, tile, share)
    tile.ext["employed"] = force - 7  # U = 7 人
    tile.ext["town_farming_carry"] = 50
    ((_, unhired, _, moved, carry),) = town_farm_moves(
        game.defs, game.config, island, 10, share
    ).values()
    assert unhired == 7 and (moved, carry) == (1, 20)  # 50 + 70 = 120 → 1 人、残り 20
    tile.ext[TOWN_FARMERS] = farm_room(game.defs, share)  # 余りなし
    tile.ext["employed"] = 0
    ((_, _, space, moved, carry),) = town_farm_moves(
        game.defs, game.config, island, 10, share
    ).values()
    assert (space, moved, carry) == (0, 0, 50)
    tile.ext.pop(TOWN_FARMERS)
    tile.ext["employed"] = force  # U = 0
    ((_, unhired, _, moved, carry),) = town_farm_moves(
        game.defs, game.config, island, 10, share
    ).values()
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
