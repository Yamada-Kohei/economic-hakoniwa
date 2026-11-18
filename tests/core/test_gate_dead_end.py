"""関門の「原料の行き止まりで作れない財」の判定（GT-46 の広げ。2026-11-18 掲示板の会議、W-294）。

当てるのは、財と止めた原料が島で作る手段も外から入る道もなく、作り手がみなその原料の品切れで
止まっている間だけ。原料が手に入るのに値段が外れる財は、今までどおり落ちる。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core.game import load_game
from tests.core.test_gate import _dead_end_turns, _dead_goods, _excuse_dead_end, _merchant_window

LAND = ["coal", "iron_ore"]  # 関門の 1 島の世界の資源（原油がない）
STUCK = ("auto_parts_factory", "input:chemicals", 1, 2, 800)  # 化学品の手元 2、全力で 800 要る


@pytest.fixture(scope="module")
def defs():
    return load_game().defs


def test_goods_without_a_way_to_make_them_are_dead(defs):
    """原油の資源がなく商人も来なければ、原油・化学品・部品は島で手に入らない。鋼鉄は石炭と鉄鉱石
    で作れる。商人が原油を運べば、化学品も部品も手に入る。"""
    dead = _dead_goods(defs, LAND, set())
    assert {"crude_oil", "chemicals", "auto_parts"} <= dead
    assert "steel" not in dead and "electricity" not in dead
    opened = _dead_goods(defs, LAND, {"crude_oil"})
    assert "chemicals" not in opened and "auto_parts" not in opened


def test_parts_are_excused_only_while_the_makers_wait_for_a_dead_input(defs):
    turns = {10: {"auto_parts": [STUCK]}}
    assert set(_dead_end_turns(defs, LAND, {}, {}, turns, 1)) == {"auto_parts"}


@pytest.mark.parametrize(
    "row",
    [
        ("auto_parts_factory", "input:steel", 1, 0, 3200),  # 鋼鉄は島で作れる
        ("auto_parts_factory", "workers", 1, 0, 0),  # 人手のせいの止まり
        ("auto_parts_factory", "power", 1, 0, 0),  # 電力のせいの止まり
        ("auto_parts_factory", "input:chemicals", 1, 800, 800),  # 手元で 1 ターン回せる
        ("auto_parts_factory", "input:chemicals", fac.FULL, 0, 800),  # 全力で動いた
    ],
)
def test_other_stops_still_fail_on_price(defs, row):
    """原料が手に入るのに止まった・人手や電力のせい・手元がある・全力で動いた作り手の財は当てない
    （値段の外れは今までどおり落ちる）。"""
    assert _dead_end_turns(defs, LAND, {}, {}, {10: {"auto_parts": [row]}}, 1) == {}


def test_one_working_maker_is_enough_to_watch_the_price(defs):
    working = ("auto_parts_factory", "", fac.FULL, 0, 0)
    assert _dead_end_turns(defs, LAND, {}, {}, {10: {"auto_parts": [STUCK, working]}}, 1) == {}


def test_a_recent_merchant_or_the_warehouse_keeps_the_input_obtainable(defs):
    """商人の不在は窓で見る。窓の間に原油を売っていれば、手に入る扱い。国の倉庫の荷も同じ。"""
    turns = {10: {"auto_parts": [STUCK]}}
    offered = {9: {"crude_oil": 500}}
    assert _dead_end_turns(defs, LAND, offered, {}, turns, 2) == {}
    assert set(_dead_end_turns(defs, LAND, offered, {}, turns, 1)) == {"auto_parts"}
    assert _dead_end_turns(defs, LAND, {}, {10: {"chemicals"}}, turns, 1) == {}


def test_the_window_is_one_longer_than_the_usual_gap():
    offered = {1: {"coal": 5}, 2: {}, 3: {}, 4: {"coal": 5}, 5: {"coal": 5}, 9: {}}
    assert _merchant_window(offered, leave=6) == (3, 2)


def test_excused_turns_leave_the_gate_and_go_to_the_notes(defs):
    """当てたターンの値段だけを目安から外し、外れ・止めた原料・稼働率・手元・回復を記録に残す。
    ほかのターンとほかの財の外れは残る。"""
    rec = SimpleNamespace(
        price_ratio={"auto_parts": {9: 4.5, 10: 4.9, 11: 4.2}, "steel": {10: 5.0}}
    )
    stuck = {"auto_parts": {10: ("chemicals", [STUCK])}}
    notes = _excuse_dead_end(rec, {"auto_parts": 23672}, stuck)
    assert rec.price_ratio == {"auto_parts": {9: 4.5, 11: 4.2}, "steel": {10: 5.0}}
    assert len(notes) == 1
    for part in ("auto_parts", "T10〜T10", "chemicals", "23672", "4.90"):
        assert part in notes[0]
    assert "T11〜T11 の比 4.20→4.20" in notes[0]
