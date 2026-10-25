"""工場と企業の名前（段6 A4）: 生まれたときに決めて残し、出来事のときだけ付け直す。"""

from __future__ import annotations

import copy

from hakoniwa_core.economy import companies as co
from hakoniwa_core.naming import AUTO_BASIS, AUTO_NAME, ensure_names, facility_name
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import island_of, run
from tests.core.test_finance import warmed as warmed  # noqa: F401


def _private(island):
    return [f for f in island.facilities if f.owner == "private" and f.ext.get(AUTO_NAME)]


def test_names_are_stored_at_birth_and_firms_get_a_brand(game, warmed):  # noqa: F811
    island = island_of(copy.deepcopy(warmed), "market")
    ensure_names(game.defs, game.config, island, 100)
    named = _private(island)
    assert named
    for f in named:
        assert facility_name(game.defs, game.config, island, f) == f.ext[AUTO_NAME]
    for number, rec in island.economy.firms.items():
        if rec[co.OWNER] == "private" and co.members(island, int(number)):
            assert rec[co.AUTO] and rec[co.BRAND]
            assert co.display_name(island, game.defs, int(number)) == rec[co.AUTO]
    firm_names = [r[co.AUTO] for r in island.economy.firms.values() if r.get(co.AUTO)]
    assert len(firm_names) == len(set(firm_names))


def test_second_pass_changes_nothing_and_logs_nothing(game, warmed):  # noqa: F811
    island = island_of(copy.deepcopy(warmed), "market")
    ensure_names(game.defs, game.config, island, 100)
    before = {f.id: f.ext.get(AUTO_NAME) for f in island.facilities}
    logs = len(island.log)
    ensure_names(game.defs, game.config, island, 101)
    assert {f.id: f.ext.get(AUTO_NAME) for f in island.facilities} == before
    assert len(island.log) == logs


def test_names_do_not_move_with_the_regime_for_private_facilities(game, warmed):  # noqa: F811
    island = island_of(copy.deepcopy(warmed), "market")
    ensure_names(game.defs, game.config, island, 100)
    f = _private(island)[0]
    name = f.ext[AUTO_NAME]
    island.regime["politics"] = "totalitarian"
    ensure_names(game.defs, game.config, island, 101)
    assert f.ext[AUTO_NAME] == name


def test_state_facilities_are_renamed_when_the_regime_changes(game, warmed):  # noqa: F811
    island = island_of(copy.deepcopy(warmed), "mixed")
    ensure_names(game.defs, game.config, island, 100)
    state = [f for f in island.facilities if f.owner == "state" and f.ext.get(AUTO_NAME)]
    assert state
    before = {f.id: f.ext[AUTO_NAME] for f in state}
    island.regime["economy"] = "planned"
    ensure_names(game.defs, game.config, island, 101)
    assert any(f.ext[AUTO_NAME] != before[f.id] for f in state)
    assert island.log[-1].kind == "facilities_renamed"


def test_player_name_wins_and_clearing_returns_to_the_stored_name(game, warmed):  # noqa: F811
    island = island_of(copy.deepcopy(warmed), "market")
    ensure_names(game.defs, game.config, island, 100)
    f = _private(island)[0]
    auto = f.ext[AUTO_NAME]
    f.name = "私の工場"
    assert facility_name(game.defs, game.config, island, f) == "私の工場"
    f.name = ""
    assert facility_name(game.defs, game.config, island, f) == auto


def test_foreclosed_facility_keeps_its_name(game, warmed):  # noqa: F811
    island = island_of(copy.deepcopy(warmed), "market")
    ensure_names(game.defs, game.config, island, 100)
    f = _private(island)[0]
    name = f.ext[AUTO_NAME]
    f.economy["foreclosed"] = {"owner": "gov"}
    f.ext[AUTO_BASIS] = "stale"
    ensure_names(game.defs, game.config, island, 101)
    assert f.ext[AUTO_NAME] == name


def test_naming_does_not_change_the_economy(game, warmed, monkeypatch):  # noqa: F811
    from hakoniwa_core.modules import companies as module

    a = copy.deepcopy(warmed)
    b = copy.deepcopy(warmed)
    for s in b.islands.values():
        for f in s.facilities:
            f.ext.pop(AUTO_NAME, None)
            f.ext.pop(AUTO_BASIS, None)
    run(game, a, 6)
    monkeypatch.setattr(module, "ensure_names", lambda *args: None)
    run(game, b, 6)
    assert any(f.ext.get(AUTO_NAME) for s in a.islands.values() for f in s.facilities)
    assert not any(f.ext.get(AUTO_NAME) for s in b.islands.values() for f in s.facilities)
    assert {i: dict(s.accounts) for i, s in a.islands.items()} == {
        i: dict(s.accounts) for i, s in b.islands.items()
    }
