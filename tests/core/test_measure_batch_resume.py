"""measure batch が、置き場の写しから続きを回す（C3 W-236 H-3、GT-47）。

- 同じ場面・同じ種でターンの多い組がある組は、最後のターンの写しも置き場に置く
- 長い組は、置き場に短い組の結果と写しがあれば、残りのターンだけを回す
- 写しが使えない（印と合わない・simulate が断る・途中で落ちる）ときは、札を持ったまま通しで回し直す
- 置き場を使わない batch と --no-resume は、今までどおり通しで回す

batch の流れは simulate の代わりの偽物で確かめ、最後の 1 つだけ本物の simulate（small の数ターン）で
確かめる。
"""

from __future__ import annotations

import gzip
import json
import subprocess

import pytest

from hakoniwa_core import measure, reuse

SHORT, LONG = ("small", 1, 3), ("small", 1, 5)
SHORT_NAME, LONG_NAME = "small-s1-t3", "small-s1-t5"
NOT_COMPARED = ("seconds", "resumed")  # 通しと続きで違ってよい欄


@pytest.fixture(scope="module")
def fingerprint():
    return reuse.code_fingerprint()


@pytest.fixture
def sets(monkeypatch):
    """quick にあたる 3 ターンの組と、full にあたる 5 ターンの組。"""
    monkeypatch.setattr(measure, "SETS", {"a": [SHORT], "b": [LONG]})


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_snapshot(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def write_snapshot(path, turn):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"measure": {"turn": turn}}, f)


def value(command, flag):
    return command[command.index(flag) + 1]


class FakeRun:
    """simulate の代わり。--load・--save・--series を、本物と同じ向きに読み書きする。"""

    started: list[list[str]] = []
    fingerprint = ""
    refuse = False  # 写しからの続きを断る（終了コード 2、何も書かない）
    crash_resumed = False  # 写しからの続きが、結果を書かずに落ちる
    crash_straight = False  # 通しも落ちる
    plain_result = (
        False  # 写しを読んでも、続きの印のない結果を書く（測りの途中の状態がない古い写し）
    )

    def __init__(self, command, stdout=None, stderr=None):
        FakeRun.started.append(command)
        self.code = 0
        before = 0
        if "--load" in command:
            if FakeRun.refuse:
                self.code = 2
                return
            if FakeRun.crash_resumed:
                if "--series" in command:  # 書きかけの時系列を残して落ちる
                    with open(value(command, "--series"), "a", encoding="utf-8") as f:
                        f.write('{"t": "half"}\n')
                self.code = 1
                return
            before = read_snapshot(value(command, "--load"))["measure"]["turn"]
            if FakeRun.plain_result:
                before = 0
        elif FakeRun.crash_straight:
            self.code = 1
            return
        turns = before + int(value(command, "--turns"))
        data = {
            "scenario": value(command, "--scenario"),
            "seed": int(value(command, "--seed")),
            "turns": turns,
            "code_fingerprint": FakeRun.fingerprint,
        }
        if before:
            data["resumed"] = {"from": before}
        with open(value(command, "--out"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        if "--series" in command:
            with open(value(command, "--series"), "a" if before else "w", encoding="utf-8") as f:
                f.writelines(f'{{"t": {t}}}\n' for t in range(before + 1, turns + 1))
        if "--save" in command:
            write_snapshot(value(command, "--save"), turns)

    def poll(self):
        return self.code

    def kill(self):
        self.code = -9


@pytest.fixture
def fake_runs(monkeypatch, fingerprint, sets):
    FakeRun.started = []
    FakeRun.fingerprint = fingerprint
    FakeRun.refuse = FakeRun.crash_resumed = FakeRun.crash_straight = False
    FakeRun.plain_result = False
    monkeypatch.setattr(subprocess, "Popen", FakeRun)
    monkeypatch.setattr(measure.time, "sleep", lambda _: None)
    return FakeRun.started


def done(root, fingerprint, name):
    return read(root / reuse.store_key(fingerprint) / f"{name}.done.json")


def stored(root, fingerprint, name, kind):
    folder = root / reuse.store_key(fingerprint)
    return folder / done(root, fingerprint, name)["files"][kind]["file"]


# --- 組の長い・短い ------------------------------------------------------------------------


def test_shorter_and_longer_runs_come_from_all_the_sets(monkeypatch):
    monkeypatch.setattr(
        measure,
        "SETS",
        {"a": [("small", 1, 3), ("small", 2, 3)], "b": [("small", 1, 5), ("small", 1, 2)]},
    )
    assert measure.shorter_runs("small", 1, 5) == [3, 2]  # 大きい順
    assert measure.shorter_runs("small", 1, 2) == []
    assert measure.shorter_runs("small", 2, 5) == [3]
    assert measure.shorter_runs("war", 1, 5) == []
    assert measure.has_longer_run("small", 1, 3) and measure.has_longer_run("small", 1, 2)
    assert not measure.has_longer_run("small", 1, 5)
    assert not measure.has_longer_run("small", 2, 3)


def test_quick_and_full_share_the_runs_we_expect():
    """本物の組: quick の 300 ターンから full の 500 ターンへ続けられるのは 7 組。"""
    resumable = [
        (scenario, seed)
        for scenario, seed, turns in measure.SETS["full"]
        if measure.shorter_runs(scenario, seed, turns)
    ]
    small = [("small", seed) for seed in (1, 2, 3, 4)]
    assert resumable == [("plan", 1), ("plan", 2), *small, ("open", 1)]
    for scenario, seed, turns in measure.SETS["quick"]:
        assert measure.has_longer_run(scenario, seed, turns) == ((scenario, seed) in resumable)


# --- 置き場の写し --------------------------------------------------------------------------


def write_run(folder, name, fingerprint, series=False, snapshot=False):
    folder.mkdir(parents=True, exist_ok=True)
    scenario, seed, turns = name.split("-")
    data = {
        "scenario": scenario,
        "seed": int(seed[1:]),
        "turns": int(turns[1:]),
        "code_fingerprint": fingerprint,
    }
    (folder / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    if series:
        (folder / f"{name}.jsonl").write_text('{"t": 1}\n', encoding="utf-8")
    if snapshot:
        write_snapshot(folder / f"{name}.snap.json.gz", int(turns[1:]))


def test_the_store_keeps_the_snapshot_beside_the_result(tmp_path):
    store = reuse.Store(tmp_path / "store", "F")
    write_run(tmp_path / "out", SHORT_NAME, "F", series=True, snapshot=True)
    assert store.publish(SHORT_NAME, tmp_path / "out", True, snapshot=True) == (True, "")
    files = read(store.folder / f"{SHORT_NAME}.done.json")["files"]
    assert set(files) == {"json", "jsonl", "snap"}
    assert files["snap"]["file"].startswith(f"{SHORT_NAME}.")
    assert files["snap"]["file"].endswith(".snap.json.gz")

    target = tmp_path / "other" / "resume" / f"{SHORT_NAME}.snap.json.gz"
    target.parent.mkdir(parents=True)
    assert store.fetch_snapshot(SHORT_NAME, target) == (True, "")
    assert target.read_bytes() == (tmp_path / "out" / f"{SHORT_NAME}.snap.json.gz").read_bytes()
    # 結果と時系列は今までどおり写せる
    assert store.fetch(SHORT_NAME, tmp_path / "other", True) == (True, "")
    assert not list(tmp_path.rglob("*.tmp-*"))


def test_a_snapshot_that_differs_from_its_mark_is_not_copied(tmp_path):
    store = reuse.Store(tmp_path / "store", "F")
    write_run(tmp_path / "out", SHORT_NAME, "F", snapshot=True)
    store.publish(SHORT_NAME, tmp_path / "out", False, snapshot=True)
    path = store.folder / read(store.folder / f"{SHORT_NAME}.done.json")["files"]["snap"]["file"]
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(bytes(raw))
    target = tmp_path / "copy.snap.json.gz"
    copied, why = store.fetch_snapshot(SHORT_NAME, target)
    assert not copied and why
    assert not target.exists()
    assert store.fetch(SHORT_NAME, tmp_path / "other", False) == (True, "")  # 結果は使える


def test_a_run_without_a_snapshot_has_none_to_copy(tmp_path):
    store = reuse.Store(tmp_path / "store", "F")
    target = tmp_path / "copy.snap.json.gz"
    assert store.fetch_snapshot(SHORT_NAME, target)[0] is False  # 印がない
    write_run(tmp_path / "out", SHORT_NAME, "F")
    # 写しを頼んでも、ファイルがなければ写しなしで置く
    assert store.publish(SHORT_NAME, tmp_path / "out", False, snapshot=True) == (True, "")
    assert set(read(store.folder / f"{SHORT_NAME}.done.json")["files"]) == {"json"}
    assert store.fetch_snapshot(SHORT_NAME, target)[0] is False
    assert not target.exists()


def test_the_snapshot_can_be_added_later_without_touching_the_result(tmp_path):
    store = reuse.Store(tmp_path / "store", "F")
    write_run(tmp_path / "first", SHORT_NAME, "F")
    store.publish(SHORT_NAME, tmp_path / "first", False)
    before = read(store.folder / f"{SHORT_NAME}.done.json")["files"]["json"]
    write_run(tmp_path / "second", SHORT_NAME, "F", snapshot=True)
    assert store.publish(SHORT_NAME, tmp_path / "second", False, snapshot=True) == (True, "")
    files = read(store.folder / f"{SHORT_NAME}.done.json")["files"]
    assert files["json"] == before and "snap" in files
    assert store.fetch_snapshot(SHORT_NAME, tmp_path / "copy.snap.json.gz") == (True, "")


# --- batch（偽物の simulate）-------------------------------------------------------------


def test_a_run_with_a_longer_partner_saves_its_snapshot(tmp_path, fake_runs, fingerprint):
    root = tmp_path / "store"
    assert measure.batch([SHORT], tmp_path / "a", 1, True, store=root) == 0
    (command,) = fake_runs
    assert value(command, "--save") == str(tmp_path / "a" / f"{SHORT_NAME}.snap.json.gz")
    assert "--load" not in command
    assert set(done(root, fingerprint, SHORT_NAME)["files"]) == {"json", "jsonl", "snap"}


def test_the_longer_run_continues_from_the_snapshot(tmp_path, fake_runs, fingerprint, capsys):
    root = tmp_path / "store"
    measure.batch([SHORT], tmp_path / "a", 1, True, store=root)
    assert measure.batch([LONG], tmp_path / "b", 1, True, store=root) == 0
    command = fake_runs[-1]
    assert len(fake_runs) == 2
    snapshot = tmp_path / "b" / "resume" / f"{SHORT_NAME}.snap.json.gz"
    assert value(command, "--load") == str(snapshot) and snapshot.exists()
    assert value(command, "--turns") == "2"
    assert value(command, "--scenario") == "small" and value(command, "--seed") == "1"
    assert "--save" not in command  # もっと長い組はないので、写しは書かない
    result = read(tmp_path / "b" / f"{LONG_NAME}.json")
    assert result["turns"] == 5 and result["resumed"] == {"from": 3}
    # 時系列は、短い組の分に書き足した物
    lines = (tmp_path / "b" / f"{LONG_NAME}.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["t"] for line in lines] == [1, 2, 3, 4, 5]
    out = capsys.readouterr().out
    assert f"続きから始める: {LONG_NAME}（3 ターンから）" in out
    assert f"続きから終わった: {LONG_NAME}" in out and "続きから回した組 1、" in out
    assert f"置き場に置いた: {LONG_NAME}" in out
    assert set(done(root, fingerprint, LONG_NAME)["files"]) == {"json", "jsonl"}
    assert not list(root.rglob("*.lock"))
    # 出力先の直下の結果は、頼んだ組だけ（短い組の物は resume/ の下）
    assert [p.name for p in (tmp_path / "b").glob("*.json")] == [f"{LONG_NAME}.json"]


def test_the_longest_stored_snapshot_is_used(tmp_path, fake_runs, monkeypatch):
    monkeypatch.setattr(measure, "SETS", {"a": [("small", 1, 2)], "b": [SHORT], "c": [LONG]})
    root = tmp_path / "store"
    measure.batch([("small", 1, 2)], tmp_path / "a", 1, False, store=root)
    measure.batch([LONG], tmp_path / "c1", 1, False, store=root)  # 3 ターンの組はまだない
    assert value(fake_runs[-1], "--load").endswith("small-s1-t2.snap.json.gz")
    assert value(fake_runs[-1], "--turns") == "3"


def test_the_middle_run_continues_and_saves_again(tmp_path, fake_runs, monkeypatch, fingerprint):
    """2 → 3 → 5 と続ける: 3 ターンの組は 2 ターンの写しから続け、自分の写しも置く。"""
    monkeypatch.setattr(measure, "SETS", {"a": [("small", 1, 2)], "b": [SHORT], "c": [LONG]})
    root = tmp_path / "store"
    measure.batch([("small", 1, 2)], tmp_path / "a", 1, False, store=root)
    measure.batch([SHORT], tmp_path / "b", 1, False, store=root)
    middle = fake_runs[-1]
    assert value(middle, "--load").endswith("small-s1-t2.snap.json.gz")
    assert value(middle, "--save") == str(tmp_path / "b" / f"{SHORT_NAME}.snap.json.gz")
    assert "snap" in done(root, fingerprint, SHORT_NAME)["files"]
    measure.batch([LONG], tmp_path / "c", 1, False, store=root)
    assert value(fake_runs[-1], "--load").endswith(f"{SHORT_NAME}.snap.json.gz")
    assert read(tmp_path / "c" / f"{LONG_NAME}.json")["resumed"] == {"from": 3}


def test_a_resumed_result_without_the_resumed_mark_is_not_kept(tmp_path, fake_runs, fingerprint):
    """写しを読んだのに、続きの印がなく残りのターン数だけの結果は、頼んだ組の結果ではない。"""
    root = tmp_path / "store"
    measure.batch([SHORT], tmp_path / "a", 1, False, store=root)
    FakeRun.plain_result = True
    assert measure.batch([LONG], tmp_path / "b", 1, False, store=root) == 0
    assert len(fake_runs) == 3 and "--load" not in fake_runs[-1]
    assert read(tmp_path / "b" / f"{LONG_NAME}.json")["turns"] == 5
    assert read(stored(root, fingerprint, LONG_NAME, "json"))["turns"] == 5


@pytest.mark.parametrize("how", ["refuse", "crash_resumed"])
def test_batch_runs_straight_when_the_resumed_run_gives_nothing(
    tmp_path, fake_runs, fingerprint, capsys, how
):
    root = tmp_path / "store"
    measure.batch([SHORT], tmp_path / "a", 1, True, store=root)
    setattr(FakeRun, how, True)
    assert measure.batch([LONG], tmp_path / "b", 1, True, store=root) == 0  # 失敗に数えない
    resumed, straight = fake_runs[1:]
    assert "--load" in resumed
    assert "--load" not in straight and value(straight, "--turns") == "5"
    result = read(tmp_path / "b" / f"{LONG_NAME}.json")
    assert result["turns"] == 5 and "resumed" not in result
    # 通しの時系列は初めから書いた物（続きの書きかけは残らない）
    lines = (tmp_path / "b" / f"{LONG_NAME}.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["t"] for line in lines] == [1, 2, 3, 4, 5]
    assert (tmp_path / "b" / f"{LONG_NAME}.resume.log").exists()
    out = capsys.readouterr().out
    assert f"続きから回せない: {LONG_NAME}（終了コード {2 if how == 'refuse' else 1}。" in out
    assert "続きにできず通しで回し直した組 1" in out
    assert "resumed" not in read(stored(root, fingerprint, LONG_NAME, "json"))
    assert not list(root.rglob("*.lock"))


def test_a_run_that_fails_straight_too_is_a_failure(tmp_path, fake_runs, fingerprint):
    root = tmp_path / "store"
    measure.batch([SHORT], tmp_path / "a", 1, False, store=root)
    FakeRun.crash_resumed = FakeRun.crash_straight = True
    assert measure.batch([LONG], tmp_path / "b", 1, False, store=root) == 1
    assert len(fake_runs) == 3  # 続き → 通し。始め直しは 1 回だけ
    assert not (root / reuse.store_key(fingerprint) / f"{LONG_NAME}.done.json").exists()
    assert not list(root.rglob("*.lock"))


def test_a_broken_snapshot_in_the_store_is_not_used(tmp_path, fake_runs, fingerprint):
    root = tmp_path / "store"
    measure.batch([SHORT], tmp_path / "a", 1, False, store=root)
    path = stored(root, fingerprint, SHORT_NAME, "snap")
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(bytes(raw))
    assert measure.batch([LONG], tmp_path / "b", 1, False, store=root) == 0
    assert "--load" not in fake_runs[-1] and value(fake_runs[-1], "--turns") == "5"


def test_a_shorter_run_without_its_series_is_not_continued_with_series(
    tmp_path, fake_runs, fingerprint
):
    """時系列も頼まれた長い組は、短い組の時系列が置き場になければ通しで回す。"""
    root = tmp_path / "store"
    measure.batch([SHORT], tmp_path / "a", 1, False, store=root)  # 時系列なし
    assert measure.batch([LONG], tmp_path / "b", 1, True, store=root) == 0
    assert "--load" not in fake_runs[-1]
    # 時系列を頼まなければ続けられる
    assert measure.batch([LONG], tmp_path / "c", 1, False, store=root, resume=True) == 0
    assert "--load" not in fake_runs[-1]  # 上の通しの結果が置き場にあるので、回さずに写す
    assert len(fake_runs) == 2


def test_no_resume_runs_straight(tmp_path, fake_runs):
    root = tmp_path / "store"
    measure.batch([SHORT], tmp_path / "a", 1, False, store=root)
    assert measure.batch([LONG], tmp_path / "b", 1, False, store=root, resume=False) == 0
    assert "--load" not in fake_runs[-1] and value(fake_runs[-1], "--turns") == "5"
    assert "resumed" not in read(tmp_path / "b" / f"{LONG_NAME}.json")


def test_batch_without_a_store_is_unchanged(tmp_path, fake_runs):
    assert measure.batch([SHORT, LONG], tmp_path / "a", 2, True) == 0
    for command in fake_runs:
        assert "--save" not in command and "--load" not in command
    assert sorted(value(c, "--turns") for c in fake_runs) == ["3", "5"]
    assert not (tmp_path / "a" / "resume").exists()


def test_the_no_resume_flag_reaches_batch(tmp_path, monkeypatch):
    calls = []

    def batch(names, out_dir, jobs, series, reuse_dirs=(), store=None, owner="", resume=True):
        calls.append(resume)
        return 0

    monkeypatch.setattr(measure, "batch", batch)
    monkeypatch.setattr(measure, "show", lambda path: "")
    monkeypatch.delenv(reuse.STORE_ENV, raising=False)
    args = ["batch", "--set", "quick", "--only", "small", "--out-dir", str(tmp_path / "o")]
    measure.main(args)
    measure.main([*args, "--no-resume"])
    assert calls == [True, False]


# --- 本物の simulate ---------------------------------------------------------------------


def test_a_resumed_batch_gives_the_same_result_as_a_straight_batch(tmp_path, sets, fingerprint):
    """small の 3 ターン → 5 ターン。続きの結果と時系列が、通しの batch と同じ。"""
    root = tmp_path / "store"
    assert measure.batch([SHORT], tmp_path / "a", 1, True, store=root) == 0
    assert "snap" in done(root, fingerprint, SHORT_NAME)["files"]
    assert measure.batch([LONG], tmp_path / "b", 1, True, store=root) == 0
    assert (
        measure.batch([LONG], tmp_path / "c", 1, True, store=tmp_path / "other", resume=False) == 0
    )
    resumed, straight = (read(tmp_path / d / f"{LONG_NAME}.json") for d in ("b", "c"))
    assert resumed["resumed"]["from"] == 3 and "resumed" not in straight
    assert {k: v for k, v in resumed.items() if k not in NOT_COMPARED} == {
        k: v for k, v in straight.items() if k not in NOT_COMPARED
    }
    assert (tmp_path / "b" / f"{LONG_NAME}.jsonl").read_bytes() == (
        tmp_path / "c" / f"{LONG_NAME}.jsonl"
    ).read_bytes()
    # 置き場から写しても同じ結果
    assert measure.batch([LONG], tmp_path / "d", 1, True, store=root) == 0
    assert read(tmp_path / "d" / f"{LONG_NAME}.json") == resumed
