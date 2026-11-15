"""測りを途中の写しから続ける（C3 W-236 H-3）: quick の最後の写しから full の残りだけを回す土台。

- 途中の状態（関門の記録・測りの足し上げ・戦争の見張り）は、JSON を挟んでも型も値も元に戻る
- 「途中で写し → 別のプロセスで続き」の結果が、通しで回した結果と同じ（結果の JSON・時系列・指紋）
- 写しに入れない物（scratch・journal・books）は、次のターンの命令作りの前に空になっている
- 計算の中身や場面が違う写し、行数の合わない時系列からは続けない（使う側が通しで回し直す）

ここは小さな世界の数ターンだけ。全組（300 → 500 ターンなど）の比べは tools/measure/resume_check.py。
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hakoniwa_core import gate, measure, snapstate, warscene
from hakoniwa_core.game import load_game
from hakoniwa_core.simulate import (
    load_snapshot,
    read_snapshot,
    save_snapshot,
    world_digest,
)
from hakoniwa_core.simulate import main as simulate_main
from hakoniwa_core.state.generate import generate_world

REPO = Path(__file__).resolve().parents[2]
NOT_COMPARED = ("seconds", "resumed")  # 通しと続きで違ってよい欄


@pytest.fixture(scope="module")
def game():
    return load_game()


def through_json(value):
    """ファイルに書いて読んだのと同じ形にする。"""
    return json.loads(json.dumps(value, ensure_ascii=False))


# --- 途中の状態を JSON にして戻す ----------------------------------------------------------


@dataclass
class Sample:
    first: int
    what: Counter = field(default_factory=Counter)


def test_state_keeps_types_values_and_order_through_json():
    value = {
        "plain": {"b": 1, "a": [1.5, None, True, "x"]},
        "by_turn": {3: 0.1, 12: 0.30000000000000004, 2: -1.0},  # 数字の鍵は入れた順のまま
        "streak": {("no_trade", "steel"): 4, ("unpaid_wages", ""): 0},
        "made": {"grain", "steel"},
        "frozen": frozenset({2, 1}),
        "falls": [(3, 7), (4, 7)],
        "what": Counter({"steel": 2, "grain": 2, "coal": 5}),
        "hits": {"famine": Sample(first=13, what=Counter({"x": 1}))},
        "flag": True,
        "one": 1,
    }
    back = snapstate.decode(through_json(snapstate.encode(value, (Sample,))), (Sample,))
    assert back == value
    assert list(back["by_turn"]) == [3, 12, 2]
    assert list(back["what"]) == ["steel", "grain", "coal"]  # 同じ数の中身の順が変わらない
    assert type(back["what"]) is Counter and type(back["hits"]["famine"].what) is Counter
    assert type(back["falls"][0]) is tuple and type(back["frozen"]) is frozenset
    assert back["flag"] is True and type(back["one"]) is int


def test_state_writes_the_same_text_whatever_the_set_order():
    a = json.dumps(snapstate.encode({"made": {"steel", "grain", "coal"}}))
    b = json.dumps(snapstate.encode({"made": {"coal", "steel", "grain"}}))
    assert a == b


def test_state_refuses_what_it_cannot_bring_back():
    with pytest.raises(TypeError):
        snapstate.encode({"file": object()})
    with pytest.raises(TypeError):
        snapstate.encode(Sample(first=1))  # 渡していないクラス
    with pytest.raises(ValueError):
        snapstate.decode({"!": "data", "t": "Sample", "v": {"first": 1}})
    with pytest.raises(ValueError):
        snapstate.decode({"!": "unknown", "v": []})


def compared(obj, skip):
    return {k: v for k, v in vars(obj).items() if k not in skip}


def test_recorder_and_meter_come_back_the_same(game):
    """数ターン記録した recorder と meter を JSON にして、別の時点の世界から作った物に戻す。

    初めの値段・初めの人口は 1 ターン目の世界から作るので、作り直さずに写しから戻ること。
    """
    world = generate_world(1, 4, game.config, game.defs)
    scenario = measure.SCENARIOS["small"]
    recorder = gate.Recorder(world, game.defs, game.config)
    meter = measure.Measure(world, game.defs, game.config)
    for _ in range(4):
        measure.queue_scenario_orders(world, scenario, game.defs, game.config)
        game.engine.run_turn(world)
        recorder.record(world)
        meter.record(world)
    # 見張りの途中の値に、型の難しい物を足しておく（数ターンでは当たらないため）
    watch = recorder.watch[world.island_ids()[0]]
    watch.streak[("no_trade", "steel")] = 5
    watch.falls.append((3, 9))
    watch.hit("stockout", 4, "steel", since=2)
    recorder.islands[world.island_ids()[0]].start_population += 1  # 作り直すと合わなくなる値
    meter.watch.events["battle"] += 2
    meter.watch.pairs = [(1, 2)]
    meter.watch.first.setdefault("battle", {})[2] = 3

    saved = through_json({"recorder": recorder.state(), "meter": meter.state()})
    other = gate.Recorder(world, game.defs, game.config)
    other.restore(saved["recorder"])
    assert compared(other, ("defs", "config")) == compared(recorder, ("defs", "config"))
    assert other.collapses() == recorder.collapses()
    assert other.summary(1) == recorder.summary(1)

    again = measure.Measure(world, game.defs, game.config)
    again.restore(saved["meter"])
    skip = ("defs", "config", "series", "watch")
    assert compared(again, skip) == compared(meter, skip)
    assert compared(again.watch, ("defs", "config")) == compared(meter.watch, ("defs", "config"))
    assert type(again.watch.pairs[0]) is tuple
    assert again.window(1, 4) == meter.window(1, 4)
    assert again.watch.result() == meter.watch.result()


def test_war_watch_state_keeps_the_pairs(game):
    watch = warscene.Watch(True, game.defs, game.config)
    watch.pairs = [(3, 5), (8, 2)]
    watch.killed[5] += 40
    watch.series = {5: {12: {"population": 1000.0}}}
    other = warscene.Watch(True, game.defs, game.config)
    other.restore(through_json(watch.state()))
    assert other.pairs == [(3, 5), (8, 2)] and other.killed == Counter({5: 40})
    assert other.series == {5: {12: {"population": 1000.0}}}
    assert other.defs is game.defs  # defs と config は写さず、作ったときの物のまま


# --- 世界の写し ------------------------------------------------------------------------


def test_snapshot_keeps_the_last_turn_trades(game, tmp_path):
    """前のターンの約定は、標準の計画が商人から買った物を見るのに使う（autoplan）。"""
    world = generate_world(1, 4, game.config, game.defs)
    scenario = measure.SCENARIOS["small"]
    for _ in range(2):
        measure.queue_scenario_orders(world, scenario, game.defs, game.config)
        game.engine.run_turn(world)
    assert any(i.trades for i in world.islands.values())
    for name in ("w.json", "w.json.gz"):
        path = str(tmp_path / name)
        save_snapshot(world, path)
        loaded, state = read_snapshot(path)
        assert state is None
        assert world_digest(loaded) == world_digest(world)
        for iid, island in world.islands.items():
            assert loaded.islands[iid].trades == island.trades
            assert loaded.islands[iid].markets == island.markets
            assert loaded.islands[iid].stats == island.stats
    with gzip.open(tmp_path / "w.json.gz", "rt", encoding="utf-8") as f:
        assert "trades" in json.load(f)
    assert world_digest(load_snapshot(str(tmp_path / "w.json"))) == world_digest(world)


def test_planning_never_sees_what_the_snapshot_leaves_out(tmp_path, monkeypatch):
    """scratch・journal・books は写しに入らない。命令作りの前に空なら、通しと続きで同じになる。"""
    original = measure.queue_scenario_orders
    seen = []

    def checked(world, scenario, defs, config):
        seen.append(world.turn)
        for island in world.islands.values():
            if world.turn > 0:
                assert not island.journal and not island.books and not island.scratch
        return original(world, scenario, defs, config)

    monkeypatch.setattr(measure, "queue_scenario_orders", checked)
    out = str(tmp_path / "a.json")
    simulate_main(["--scenario", "small", "--turns", "3", "--gate", "--out", out])
    assert seen == [0, 1, 2]


# --- 通しと続き ------------------------------------------------------------------------


def simulate(args: list[str], hash_seed: str) -> subprocess.CompletedProcess:
    """別のプロセスで回す（覚えた物が空の所から始める。文字列の hash の種も変える）。"""
    env = os.environ | {"PYTHONHASHSEED": hash_seed, "PYTHONIOENCODING": "utf-8"}
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO), env.get("PYTHONPATH", "")) if p)
    return subprocess.run(
        [sys.executable, "-m", "hakoniwa_core.simulate", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def same_but(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in NOT_COMPARED}


def first_part(tmp_path: Path, scenario: str, turns: int) -> Path:
    """turns ターン回して、結果・時系列・写しを書く。写しのパスを返す。"""
    snap = tmp_path / "b.json.gz"
    done = simulate(
        ["--scenario", scenario, "--seed", "1", "--turns", str(turns), "--gate"]
        + ["--out", str(tmp_path / "b-first.json"), "--series", str(tmp_path / "b.jsonl")]
        + ["--save", str(snap)],
        "1",
    )
    assert snap.exists(), done.stdout + done.stderr
    return snap


@pytest.mark.parametrize("scenario", ["small", "war"])
def test_resumed_run_gives_the_same_result_as_a_straight_run(tmp_path, scenario):
    straight = simulate(
        ["--scenario", scenario, "--seed", "1", "--turns", "8", "--gate"]
        + ["--out", str(tmp_path / "a.json"), "--series", str(tmp_path / "a.jsonl")],
        "1",
    )
    assert (tmp_path / "a.json").exists(), straight.stdout + straight.stderr
    snap = first_part(tmp_path, scenario, 5)
    rest = simulate(
        ["--scenario", scenario, "--load", str(snap), "--turns", "3", "--gate"]
        + ["--out", str(tmp_path / "b.json"), "--series", str(tmp_path / "b.jsonl")],
        "2",
    )
    assert (tmp_path / "b.json").exists(), rest.stdout + rest.stderr
    a, b = read(tmp_path / "a.json"), read(tmp_path / "b.json")
    assert same_but(b) == same_but(a)
    assert list(same_but(b)) == list(same_but(a))  # 欄の順も同じ
    assert b["turns"] == 8 and b["seed"] == 1 and b["digest"] == a["digest"]
    assert b["resumed"]["from"] == 5 and b["seconds"] >= b["resumed"]["seconds"]
    assert "resumed" not in a
    assert (tmp_path / "b.jsonl").read_bytes() == (tmp_path / "a.jsonl").read_bytes()
    # 初めの 5 ターンの結果は、5 ターンだけ回した物（quick にあたる）のまま
    assert read(tmp_path / "b-first.json")["turns"] == 5


def rewrite(snap: Path, change) -> None:
    with gzip.open(snap, "rt", encoding="utf-8") as f:
        data = json.load(f)
    change(data)
    with gzip.open(snap, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def test_a_snapshot_of_other_code_or_scene_is_not_resumed(tmp_path):
    snap = first_part(tmp_path, "small", 2)
    out = ["--out", str(tmp_path / "c.json")]
    # 場面が違う
    other = simulate(["--scenario", "plan", "--load", str(snap), "--turns", "1", *out], "1")
    assert other.returncode == 2 and not (tmp_path / "c.json").exists()
    # 計算の中身が違う
    rewrite(snap, lambda d: d["measure"].update(code_fingerprint="0" * 64))
    stale = simulate(["--scenario", "small", "--load", str(snap), "--turns", "1", *out], "1")
    assert stale.returncode == 2 and not (tmp_path / "c.json").exists()
    assert stale.stderr.strip()
    # --gate だけ（崩れを調べる使い方）なら、途中の状態を使わずに今までどおり続ける
    gate_only = simulate(
        ["--scenario", "small", "--load", str(snap), "--turns", "1", "--gate"], "1"
    )
    assert gate_only.returncode in (0, 1)
    assert "ターン 3 の状態の指紋" in gate_only.stdout


def test_series_is_resumed_only_when_its_lines_match(tmp_path):
    snap = first_part(tmp_path, "small", 2)
    args = ["--scenario", "small", "--load", str(snap), "--turns", "1"]
    out = ["--out", str(tmp_path / "c.json")]
    # 時系列のファイルがない
    missing = simulate([*args, *out, "--series", str(tmp_path / "none.jsonl")], "1")
    assert missing.returncode == 2 and not (tmp_path / "none.jsonl").exists()
    # 行数が合わない（1 行多い）
    longer = tmp_path / "longer.jsonl"
    longer.write_bytes((tmp_path / "b.jsonl").read_bytes() + b"{}\n")
    wrong = simulate([*args, *out, "--series", str(longer)], "1")
    assert wrong.returncode == 2 and not (tmp_path / "c.json").exists()
    assert longer.read_bytes().endswith(b"{}\n")  # 使わないときは書き足さない
    # 時系列なしで作った写しからは、時系列を続けられない
    rewrite(snap, lambda d: d["measure"].update(series_lines=None))
    without = simulate([*args, *out, "--series", str(tmp_path / "b.jsonl")], "1")
    assert without.returncode == 2
    # 時系列を求めなければ続けられる
    plain = simulate([*args, *out], "1")
    assert (tmp_path / "c.json").exists(), plain.stdout + plain.stderr
    assert read(tmp_path / "c.json")["turns"] == 3
