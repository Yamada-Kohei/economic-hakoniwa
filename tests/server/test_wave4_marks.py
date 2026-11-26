"""第4波の残りの材料（グラフィック強化 案18・26・15）: 地図の材料に気候帯を出し、
切り替えたときだけ、ターンログから寄せた戦闘の跡とお金の出来事を出すこと。
核の出来事は跡に入れない（#5803: 通常の戦闘だけ）。"""

from __future__ import annotations

import pytest

from server.turns.models import TurnLog
from server.world import models as m
from tests.server.test_phase1 import _own_island

pytestmark = pytest.mark.django_db


def _map(client, island: m.Island, query: str = "") -> dict:
    return client.get(f"/api/islands/{island.id}/map{query}").json()


def _log(isle: m.Island, turn: int, kind: str, **args) -> None:
    TurnLog.objects.create(island=isle, turn=turn, kind=kind, args=args)


def test_map_carries_climate(client, world):
    island = _own_island(client)
    island.ext = {**(island.ext or {}), "climate": "cold"}
    island.save()
    assert _map(client, island)["climate"] == "cold"


def test_map_climate_is_none_without_material(client, world):
    island = _own_island(client)
    ext = dict(island.ext or {})
    ext.pop("climate", None)
    island.ext = ext
    island.save()
    assert _map(client, island)["climate"] is None


def test_battles_only_when_asked(client, world):
    island = _own_island(client)
    turn = island.world.turn
    _log(island, turn, "missile_hit", x=2, y=3)
    assert "battles" not in _map(client, island)
    rows = _map(client, island, "?battles=1")["battles"]
    assert rows == [{"x": 2, "y": 3, "kind": "hit", "age": 0}]


def test_battles_kinds_window_and_no_nukes(client, world):
    island = _own_island(client)
    turn = island.world.turn
    _log(island, turn, "missile_intercepted", x=1, y=1)
    _log(island, turn - 2, "tile_occupied", x=4, y=4)  # 窓（3ターン）の内側
    _log(island, turn - 3, "missile_hit", x=5, y=5)  # 窓の外
    _log(island, turn, "nuclear_hit", x=0, y=0)  # 核は出さない
    _log(island, turn, "battle", x=3, y=3, island=island.name)  # 自分の島での戦い
    _log(island, turn, "battle", x=6, y=6, island="よその国")  # 相手の島での戦いは出さない
    _log(island, turn, "tile_occupying", x=7, y=7)  # 占領した側の記録は座標が相手の島
    rows = _map(client, island, "?battles=1")["battles"]
    by_cell = {(r["x"], r["y"]): r for r in rows}
    assert by_cell[(1, 1)]["kind"] == "intercept"
    assert by_cell[(4, 4)] == {"x": 4, "y": 4, "kind": "hit", "age": 2}
    assert by_cell[(3, 3)]["kind"] == "hit"
    assert (5, 5) not in by_cell
    assert (0, 0) not in by_cell
    assert (6, 6) not in by_cell
    assert (7, 7) not in by_cell


def test_money_marks_bank_cloud_and_crowd(client, world):
    island = _own_island(client)
    turn = island.world.turn
    assert island.capital_x is not None
    _log(island, turn, "bank_bailed_out", x=2, y=2, amount=100)
    _log(island, turn - 1, "bonds_backstop", amount=100, term=10)
    _log(island, turn, "fx_peg_broken", currency="x", rate=1)
    assert "money" not in _map(client, island)
    rows = _map(client, island, "?money=1")["money"]
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    assert by_kind["bank"] == [{"x": 2, "y": 2, "kind": "bank", "age": 0}]
    assert by_kind["cloud"] == [
        {"x": island.capital_x, "y": island.capital_y, "kind": "cloud", "age": 1}
    ]
    # 通貨の固定が崩れた印は、建っている銀行のマス（無ければ首都）に出る
    bank = island.facilities.filter(kind="bank", built=True, x__gte=0).first()
    expected = (bank.x, bank.y) if bank else (island.capital_x, island.capital_y)
    assert [(r["x"], r["y"]) for r in by_kind["crowd"]] == [expected]


def test_money_window(client, world):
    island = _own_island(client)
    turn = island.world.turn
    _log(island, turn - 6, "bank_bailed_out", x=1, y=1, amount=1)  # 窓（6ターン）の外
    rows = _map(client, island, "?money=1")["money"]
    assert rows == []
