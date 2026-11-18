"""失業している家計の層の数え直し（W-294）: 記録の段の初めに、失業率と同じ時点・同じ数え方で層の
人数を数え直す。層を移る人は、このターンにすでに受けた所得も1人あたりの額で持って移る。"""

from __future__ import annotations

from hakoniwa_core.economy import GENERAL, JOBLESS
from hakoniwa_core.economy.households import add_income, set_jobless
from hakoniwa_core.modules.population import jobless_persons
from hakoniwa_core.modules.records import Records
from tests.core.test_dismissal import _jobless_workers, _mill
from tests.core.test_recession_round1 import context
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）


def test_records_count_the_jobless_layer_at_the_same_time_as_unemployment(game):
    """賃金の段の後に仕事のない人が減った（人口の段で村へ逃げた・雇われた）ターンも、記録の段の
    層の人数は失業率と同じ数え方の人数になる（LAN の世界 8003: 失業率がほぼ 0 のターンに層が
    数万人だった）。"""
    island, _, _ = _mill(game)
    _jobless_workers(game, island, 300)
    set_jobless(island, jobless_persons(game.defs, island))  # 賃金の段の数
    paid = island.economy.households[JOBLESS]["persons"]
    assert paid > 0
    _jobless_workers(game, island, 3)  # 人口の段の後
    Records().run_island(island, context(game, island))
    households = island.economy.households
    assert households[JOBLESS]["persons"] == jobless_persons(game.defs, island) < paid
    assert island.stats[f"layer.{JOBLESS}.persons"] == households[JOBLESS]["persons"]


def test_people_leaving_a_layer_take_this_turns_income_with_them(game):
    """層を移る人は、このターンの可処分所得を元の層の1人あたりの額で持って移る。失業手当を受けた
    人が一般の層へ移っても、残った人の1人あたりの所得は変わらない。"""
    island, _, _ = _mill(game)
    set_jobless(island, 100)
    add_income(island, JOBLESS, 10_000)  # 1人あたり 100
    general = island.scratch["income"].get(GENERAL, 0)
    set_jobless(island, 10)  # 90 人が仕事を見つけた・村へ移った
    income = island.scratch["income"]
    assert income[JOBLESS] == 1_000
    assert income[GENERAL] == general + 9_000


def _jobless_workers_on(game, island):
    """失業率と同じ数え方の、仕事のない一般の労働者の数（マスごとの tile_jobless の和）。"""
    from hakoniwa_core import facilities as fac
    from hakoniwa_core.modules.population import tile_jobless

    share = fac.labor_share(game.defs, island)
    return sum(tile_jobless(game.defs, island, t, share) for t in island.tiles if t.population > 0)


def test_the_jobless_layer_matches_unemployment_every_turn(game):
    """受け入れの条件（掲示板 #1464）: 標準の計画で 10 ターン続けて回し、どのターンも記録の時点で、
    失業している家計の層の人数が失業者の数（とその家族）と合い、失業率の統計の仕事のない人の数が
    層を数えた人数と合う。"""
    from hakoniwa_core.state.generate import generate_world
    from tests.core.test_finance import run

    world = generate_world(1, 3, game.config, game.defs)
    seen = 0
    for _ in range(10):
        run(game, world, 1)
        for island in world.islands.values():
            if not island.has_economy or JOBLESS not in island.economy.households:
                continue
            stats = island.stats
            layer = stats[f"layer.{JOBLESS}.persons"]
            counted = jobless_persons(game.defs, island)
            assert layer == counted, (
                f"数え方の不一致: ターン {world.turn} 島 {island.id} の失業している家計の層 "
                f"{layer} 人、失業者とその家族 {counted} 人"
            )
            idle = stats["labor"] - stats["employed"] - stats["subsistence"]
            workers = _jobless_workers_on(game, island)
            assert idle == workers, (
                f"数え方の不一致: ターン {world.turn} 島 {island.id} の失業率の仕事のない人 "
                f"{idle} 人、層を数えた仕事のない人 {workers} 人"
            )
            seen += layer > 0
    assert seen > 0  # 失業者のいるターンがあった
