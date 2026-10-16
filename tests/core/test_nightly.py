"""夜の点検（hakoniwa_core/nightly.py）: でたらめな命令・止まったときの記録・長い年月・まとめ。

- でたらめな命令は、画面と同じ確かめ（check_order）を通ったものだけを並べる
- ターンの処理が例外で止まったら、そのターンと並べた命令を書き残して終える
- 長い年月の世界は、測りの範囲（〜500 ターン）では出ずに後でだけ出た目安を拾う
- まとめは、前の夜までに見つけたものと同じなら「新」を付けない
"""

from __future__ import annotations

import json
import random

import pytest

from hakoniwa_core import nightly
from hakoniwa_core.game import load_game
from hakoniwa_core.orders import check_order
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


def test_random_orders_pass_the_same_check_as_the_screen(game):
    world = generate_world(5, 6, game.config, game.defs)
    rng = random.Random(1)
    maker = nightly.OrderMaker(game, world, rng)
    kinds = set()
    for _ in range(300):
        island = world.islands[rng.choice(world.island_ids())]
        orders, errors = maker.orders(island, 2)
        assert errors == []
        for kind, payload in orders:
            assert check_order(game.defs, game.registry, island, kind, payload).ok
            kinds.add(kind)
    # 建国したばかりの島でも、ほとんどの種類の命令を出せる（民営化や破綻した銀行の処理は後で出る）
    assert len(kinds) >= len(maker.kinds) - 5


def test_random_world_runs_and_counts_the_orders(game):
    result = nightly.run_one("random", "small", 7, 12, game)
    assert result["stopped"] is None
    assert result["turns_done"] == 12
    assert len(result["random_islands"]) == 4  # 少ない島の世界は全部の島
    orders = result["orders"]
    assert orders["queued"] > 0
    assert orders["done"] + orders["failed"] <= orders["queued"]
    assert sum(orders["kinds"].values()) == orders["queued"]


def test_random_world_is_the_same_for_the_same_seed(game):
    a = nightly.run_one("random", "small", 11, 6, game)
    b = nightly.run_one("random", "small", 11, 6, game)
    assert a["orders"] == b["orders"]


def test_a_stopped_turn_is_written_down_with_its_orders(game, monkeypatch):
    run_turn = game.engine.run_turn

    def broken(world):
        if world.turn + 1 == 3:
            raise RuntimeError("帳簿が合わない")
        return run_turn(world)

    monkeypatch.setattr(game.engine, "run_turn", broken)
    result = nightly.run_one("random", "small", 7, 10, game)
    stopped = result["stopped"]
    assert result["turns_done"] == 2
    assert stopped["turn"] == 3
    assert stopped["error"] == "RuntimeError: 帳簿が合わない"
    assert stopped["signature"].startswith("RuntimeError @ ")
    assert all({"island", "kind", "payload"} <= set(o) for o in stopped["orders"])


class FakeRecorder:
    def __init__(self):
        self.asked = []

    def counts(self, start, end):
        self.asked.append((start, end))
        late = start >= nightly.MEASURED_TURNS
        return {kind: (1 if late and kind == "population_floor" else 0) for kind in nightly.KINDS}


def test_long_runs_are_split_after_the_measured_range():
    recorder = FakeRecorder()
    found = nightly.windows(recorder, None, 1200)
    assert recorder.asked == [(nightly.START, 500), (500, 850), (850, 1200)]
    assert nightly.late_kinds(found) == ["population_floor"]
    # 測りの範囲までしか回していなければ、後の期間はない
    assert nightly.late_kinds(nightly.windows(FakeRecorder(), None, 300)) == []


def test_late_kinds_skip_what_was_already_seen_in_the_measured_range():
    found = [
        {"counts": {"price": 0, "fx": 2, "firms": 0}},
        {"counts": {"price": 3, "fx": 2, "firms": 0}},
        {"counts": {"price": 0, "fx": 0, "firms": 1}},
    ]
    assert nightly.late_kinds(found) == ["price", "firms"]


def test_scenario_kinds_limit_what_is_counted():
    found = nightly.windows(FakeRecorder(), ("price",), 1200)
    assert all(set(w["counts"]) == {"price"} for w in found)


def test_summary_marks_only_new_findings(tmp_path):
    out = tmp_path / "20261017"
    out.mkdir()
    stopped = {"turn": 41, "error": "KeyError: 'x'", "signature": "KeyError @ a.py:1 f"}
    run = {"kind": "random", "scenario": "plan", "seed": 3, "turns": 300, "stopped": stopped}
    (out / "random-plan-s3-t300.json").write_text(json.dumps(run), encoding="utf-8")
    (out / "random-plan-s3-t300.log").write_text("", encoding="utf-8")
    (out / "long-plan-s1-t1200.log").write_text("MemoryError\n", encoding="utf-8")  # 結果なし
    (out / "nightly.log").write_text("", encoding="utf-8")  # 夜の予定や pip の記録は組に数えない
    (out / "pip.log").write_text("", encoding="utf-8")
    known = tmp_path / "known.json"

    first = nightly.summarize(out, known, "main abc1234")
    assert first["runs"] == 2
    assert [f["type"] for f in first["findings"]] == ["crash", "stop"]
    assert first["new"] == 2
    text = (out / "summary.md").read_text(encoding="utf-8")
    assert "【新】ターンが止まった: KeyError @ a.py:1 f" in text
    assert "--seed 3 --turns 300" in text

    again = nightly.summarize(out, known)
    assert again["new"] == 0
    assert "【新】" not in (out / "summary.md").read_text(encoding="utf-8")


def test_the_night_starts_with_the_long_big_worlds():
    runs = nightly.plan(20261017, 1200, 300, 3)
    assert [r[0] for r in runs] == ["long"] * 6 + ["random"] * 3
    assert {r[1] for r in runs[:4]} == {"plan", "none"}
    assert len({nightly.run_name(*r) for r in runs}) == len(runs)  # 結果のファイルは重ならない
