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
