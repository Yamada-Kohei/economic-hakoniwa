"""ターンごとの指紋で、計算の結果と写しからの続きが変わっていないか確かめる。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hakoniwa_core import measure, simulate
from hakoniwa_core.game import Game, load_game
from hakoniwa_core.state import WorldState
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game() -> Game:
    return load_game()


def read_digests(path: Path) -> list[dict[str, int | str]]:
    """出力を行ごとに読み、途中のターンも確かめられる形にする。"""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_simulate_writes_each_turn_digest(game: Game, tmp_path: Path, capsys) -> None:
    path = tmp_path / "digests.jsonl"
    assert simulate.main(["--scenario", "small", "--turns", "3", "--digests", str(path)]) == 0
    output = capsys.readouterr().out
    rows = read_digests(path)
    assert len(rows) == 3
    scenario = measure.SCENARIOS["small"]
    world = generate_world(1, scenario.islands, game.config, game.defs)
    expected = []
    for _ in range(3):
        measure.queue_scenario_orders(world, scenario, game.defs, game.config)
        game.engine.run_turn(world)
        expected.append({"turn": world.turn, "digest": simulate.world_digest(world)})
    assert rows == expected
    assert [row["turn"] for row in rows] == [1, 2, 3]
    assert f"ターン 3 の状態の指紋: {rows[-1]['digest']}" in output


def test_loaded_digests_start_after_the_snapshot(tmp_path: Path, capsys) -> None:
    snapshot = tmp_path / "snapshot.json"
    full = tmp_path / "full.jsonl"
    resumed = tmp_path / "resumed.jsonl"
    assert simulate.main(["--scenario", "small", "--turns", "1", "--save", str(snapshot)]) == 0
    capsys.readouterr()
    assert (
        simulate.main(
            [
                "--scenario",
                "small",
                "--turns",
                "2",
                "--load",
                str(snapshot),
                "--digests",
                str(resumed),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert simulate.main(["--scenario", "small", "--turns", "3", "--digests", str(full)]) == 0
    rows = read_digests(resumed)
    assert [row["turn"] for row in rows] == [2, 3]
    assert rows == read_digests(full)[1:]
    assert f"ターン 3 の状態の指紋: {rows[-1]['digest']}" in output


def test_simulate_without_digests_only_hashes_the_final_turn(tmp_path: Path, monkeypatch) -> None:
    turns = []
    original = simulate.world_digest

    def counted_digest(world: WorldState) -> str:
        turns.append(world.turn)
        return original(world)

    monkeypatch.setattr(simulate, "world_digest", counted_digest)
    assert simulate.main(["--scenario", "small", "--turns", "3"]) == 0
    assert turns == [3]


@pytest.mark.parametrize(
    ("before", "after", "code", "message", "only_a", "only_b"),
    [
        ([(1, "a"), (2, "b")], [(1, "a"), (2, "b")], 0, "全部同じ（2 ターン）", 0, 0),
        (
            [(10, "a"), (2, "b"), (1, "a")],
            [(10, "c"), (2, "c"), (1, "a")],
            1,
            "最初にずれたターン: 2",
            0,
            0,
        ),
        ([(1, "a"), (2, "b"), (4, "a")], [(2, "b"), (3, "c")], 0, "全部同じ（1 ターン）", 2, 1),
    ],
    ids=["same", "first_difference", "one_side_only"],
)
def test_measure_compares_shared_turns(
    tmp_path: Path, capsys, before, after, code: int, message: str, only_a: int, only_b: int
) -> None:
    paths = [tmp_path / "a.jsonl", tmp_path / "b.jsonl"]
    for path, rows in zip(paths, (before, after), strict=True):
        path.write_text(
            "".join(
                json.dumps({"turn": turn, "digest": digest * 64}) + "\n" for turn, digest in rows
            ),
            encoding="utf-8",
        )
    assert measure.main(["digests", *(str(path) for path in paths)]) == code
    output = capsys.readouterr().out
    assert message in output
    assert f"A にしかないターン: {only_a}" in output
    assert f"B にしかないターン: {only_b}" in output
