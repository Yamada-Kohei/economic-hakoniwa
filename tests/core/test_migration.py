"""国をまたぐ移民（バランス調整の回3。hakoniwa_core/modules/migration）。

段を直に呼ぶ試験は、標準の計画で回した6島の世界を写し、賃金の高い島と低い島の組で確かめる。
ターンを回す試験は、test_phase3_3 の run_checked で毎ターン帳簿・島をまたぐお金・国際収支の
釣り合いを確かめる。
"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import GENERAL, household
from hakoniwa_core.modules import migration
from hakoniwa_core.modules.politics import amend, events_bonus
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import money as fxmoney
from tests.core.test_finance import books_ok, context, open_books
from tests.core.test_phase3_3 import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_phase3_3 import run_checked

TURNS = 30


@pytest.fixture(scope="module")
def base(game):
    """標準の計画で TURNS ターン回した6島。試験ごとに写す。"""
    world = generate_world(3, 6, game.config, game.defs)
    run_checked(game, world, TURNS)
    return world


def _ctx(game, world):
    return dataclasses.replace(context(game, world, "migration"), before=world)


def _pair(world):
    """移民の出入りする島のうち、実質賃金のいちばん低い島と高い島。高い島の賃金は4倍にする
    （行き先の見込みがはっきり良いように）。"""
    ids = [i for i in world.island_ids() if migration.active(world.islands[i])]
    assert len(ids) >= 2
    ids.sort(key=lambda i: world.islands[i].economy.average_wage)
    poor, rich = world.islands[ids[0]], world.islands[ids[-1]]
    rich.economy.average_wage *= 4
    return poor, rich


def _persons(island):
    return sum(h["persons"] for h in island.economy.households.values())


def _residents(island):
    return sum(t.population for t in island.tiles)


def test_nobody_moves_while_every_island_is_closed(game, base):
    """受け入れを決めていない島は受け入れない（カードで決定）。"""
    world = copy.deepcopy(base)
    poor, rich = _pair(world)
    before = {i: _residents(world.islands[i]) for i in world.island_ids()}
    migration.run(world, _ctx(game, world))
    assert {i: _residents(world.islands[i]) for i in world.island_ids()} == before
    assert poor.stats["migration.left"] == 0


def test_people_move_to_an_open_island_with_their_deposits(game, base):
    world = copy.deepcopy(base)
    poor, rich = _pair(world)
    rich.policy.immigration = migration.OPEN
    rich.policy.immigration_cap = 10000
    for island in (poor, rich):
        open_books(island)
    residents = {poor.id: _residents(poor), rich.id: _residents(rich)}
    migration.run(world, _ctx(game, world))
    left = poor.stats["migration.left"]
    assert left > 0
    assert rich.stats["migration.arrived"] >= left
    assert _residents(poor) == residents[poor.id] - left
    for island in (poor, rich):  # 住民と家計の層の人数がそろい、帳簿が合う
        assert _persons(island) == _residents(island)
        books_ok(game, island)
    # 預金は元の島の通貨のまま、行き先の家計の外貨になる（次のターンの為替の市場で売る）
    held = fxmoney.holding(rich, household(GENERAL), poor.id)
    held += fxmoney.holding(rich, household("engineers"), poor.id)
    assert held > 0
    assert migration.ties_of(rich)[str(poor.id)] >= left - 1
    assert any(e.kind == "migration_left" for e in poor.log)
    assert any(e.kind == "migration_arrived" for e in rich.log)


def test_jobs_mode_admits_only_as_many_as_the_vacancies(game, base):
    world = copy.deepcopy(base)
    poor, rich = _pair(world)
    rich.policy.immigration = migration.JOBS
    rich.policy.immigration_cap = 10000
    for f in rich.facilities:
        f.economy.pop("wanted", None)
        f.economy["wanted"] = f.workers  # 人手の足りない施設がない
    migration.run(world, _ctx(game, world))
    assert poor.stats["migration.left"] == 0


def test_closed_capital_controls_leave_the_deposits_to_the_state(game, base):
    world = copy.deepcopy(base)
    poor, rich = _pair(world)
    rich.policy.immigration = migration.OPEN
    rich.policy.immigration_cap = 10000
    poor.policy.capital_controls = "closed"
    treasury = ledger.money(poor, ledger.GOV)
    migration.run(world, _ctx(game, world))
    assert poor.stats["migration.left"] > 0
    assert poor.stats["migration.seized"] > 0
    assert ledger.money(poor, ledger.GOV) == treasury + poor.stats["migration.seized"]
    assert fxmoney.holding(rich, household(GENERAL), poor.id) == 0


def test_a_ban_traps_people_and_lowers_stability(game, base, monkeypatch):
    """国境の見張りは警察の人手しだい（警察のない島では、禁止しても出ていく）。"""
    world = copy.deepcopy(base)
    poor, rich = _pair(world)
    rich.policy.immigration = migration.OPEN
    rich.policy.immigration_cap = 10000
    poor.regime["politics"] = "totalitarian"
    poor.policy.emigration = migration.BAN
    ctx = _ctx(game, world)
    open_world = copy.deepcopy(world)
    open_world.islands[poor.id].policy.emigration = migration.FREE
    migration.run(open_world, _ctx(game, open_world))
    wanted = open_world.islands[poor.id].stats["migration.left"]
    monkeypatch.setattr(migration, "police_coverage", lambda island, ctx: 1.0)
    migration.run(world, ctx)
    # 止められた人のうち、国境をすり抜けた人だけが出ていく（預金は国のもの）
    assert poor.stats["migration.left"] == poor.stats["migration.escaped"] < wanted
    assert poor.stats["migration.trapped"] > 0
    assert migration.trapped_share(poor) > 0
    assert events_bonus(poor, ctx)["trapped"] < 0


def test_democracy_cannot_restrict_exit_and_amending_relaxes_it(game, base):
    world = copy.deepcopy(base)
    island = world.islands[world.island_ids()[0]]
    island.regime["politics"] = "democracy"
    with pytest.raises(OrderFailed) as e:
        migration.check_emigration(island, {"mode": migration.PERMIT}, game.defs)
    assert e.value.reason == "reason_exit_control_forbidden"
    island.regime["politics"] = "authoritarian"
    migration.check_emigration(island, {"mode": migration.PERMIT}, game.defs)
    with pytest.raises(OrderFailed):
        migration.check_emigration(island, {"mode": migration.BAN}, game.defs)
    island.policy.emigration = migration.PERMIT
    amend(island, {"politics": "democracy"}, context(game, world))
    assert island.policy.emigration == migration.FREE
    assert any(e.kind == "emigration_relaxed" for e in island.log)


def test_migrants_send_money_home_and_return_when_home_gets_better(game, base):
    world = copy.deepcopy(base)
    poor, rich = _pair(world)
    rich.policy.immigration = migration.OPEN
    rich.policy.immigration_cap = 10000
    migration.run(world, _ctx(game, world))
    assert rich.stats["migration.remittance_out"] > 0
    assert poor.stats["migration.remittance_in"] > 0
    assert fxmoney.holding(poor, household(GENERAL), rich.id) > 0
    # 長く移り住んできた人が多くいるとする。元の島の見込みの方がはっきり良くなった: 帰る
    # （受け入れの政策によらず）
    abroad = rich.economy.households[GENERAL]["persons"] / 2
    migration.ties_of(rich)[str(poor.id)] = abroad
    poor.economy.average_wage = rich.economy.average_wage * 4
    migration.run(world, _ctx(game, world))
    assert poor.stats["migration.returned"] > 0
    assert migration.ties_of(rich)[str(poor.id)] < abroad


def test_turns_with_open_borders_keep_the_books(game, base):
    """移民の出入りがあるターンも、帳簿・島をまたぐお金・国際収支の釣り合いが崩れない。"""
    world = copy.deepcopy(base)
    poor, rich = _pair(world)
    for island in world.islands.values():
        island.policy.immigration = migration.OPEN
        island.policy.immigration_cap = 1000
    moved = []

    def each(w):
        moved.append(sum(w.islands[i].stats.get("migration.left", 0) for i in w.island_ids()))
        w.islands[rich.id].economy.average_wage *= 4

    run_checked(game, world, 3, each)
    assert sum(moved) > 0
