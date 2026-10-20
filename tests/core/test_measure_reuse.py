"""測りの結果の使い回し（measure batch --reuse）: 計算の中身が前と同じときだけ回さずに写す。

- 指紋は、中身が変わると変わり、Python の写しと改行の違いでは変わらない
- simulate の結果に今の指紋が入る
- batch は、指紋が同じ結果は回さずに写し、違う・書いていない・時系列がない・壊れているときは回す
"""

from __future__ import annotations

import json
import subprocess

import pytest

from hakoniwa_core import measure, reuse
from hakoniwa_core.simulate import main as simulate_main

# --- 指紋 --------------------------------------------------------------------------------


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text("x = 1\n")
    (tmp_path / "game.yaml").write_text("packs: [base]\n")
    return tmp_path


def fingerprint(tree):
    return reuse.fingerprint_paths([tree / "core", tree / "game.yaml"], tree)


def test_fingerprint_changes_with_content_and_names(tree):
    before = fingerprint(tree)
    assert fingerprint(tree) == before
    (tree / "core" / "a.py").write_text("x = 2\n")
    changed = fingerprint(tree)
    assert changed != before
    (tree / "core" / "a.py").write_text("x = 1\n")
    assert fingerprint(tree) == before
    (tree / "core" / "b.py").write_text("")  # 空のファイルを足しても変わる
    assert fingerprint(tree) != before


def test_fingerprint_ignores_python_caches_and_line_endings(tree):
    before = fingerprint(tree)
    (tree / "core" / "__pycache__").mkdir()
    (tree / "core" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\0\1\2")
    (tree / "core" / "a.pyc").write_bytes(b"\0")
    assert fingerprint(tree) == before
    (tree / "core" / "a.py").write_bytes(b"x = 1\r\n")  # Windows の作業ツリー
    assert fingerprint(tree) == before


def test_fingerprint_of_the_running_code_covers_the_core_and_settings():
    assert reuse.code_fingerprint() == reuse.code_fingerprint()
    assert len(reuse.code_fingerprint()) == 64


def test_simulate_result_carries_the_fingerprint(tmp_path):
    out = tmp_path / "small.json"
    args = ["--scenario", "small", "--islands", "2", "--turns", "3", "--seed", "1"]
    assert simulate_main([*args, "--out", str(out)]) == 0
    assert json.loads(out.read_text())["code_fingerprint"] == reuse.code_fingerprint()


# --- find ---------------------------------------------------------------------------------


def write(folder, name, **fields):
    folder.mkdir(exist_ok=True)
    data = {"scenario": "small", "seed": 1, "turns": 300, "digest": "d", **fields}
    (folder / f"{name}.json").write_text(json.dumps(data))
    return folder / f"{name}.json"


def test_find_accepts_only_the_same_fingerprint(tmp_path):
    name = "small-s1-t300"
    old, no_print, same, broken = (tmp_path / d for d in ("old", "no_print", "same", "broken"))
    write(old, name, code_fingerprint="A")
    write(no_print, name)
    broken.mkdir()
    (broken / f"{name}.json").write_text('{"scenario": "sma')
    write(same, name, code_fingerprint="B")

    assert reuse.find(name, "B", [old], False)[0] is None
    assert "違う" in reuse.find(name, "B", [old], False)[1]
    assert "書いていない" in reuse.find(name, "B", [no_print], False)[1]
    assert "読めない" in reuse.find(name, "B", [broken], False)[1]
    assert "前の結果がない" == reuse.find("plan-s1-t300", "B", [same], False)[1]
    # 先に挙げたフォルダが使えなくても、あとのフォルダで見つかれば使う
    assert reuse.find(name, "B", [old, broken, same], False) == (same / f"{name}.json", "")
    # 時系列を頼まれたら、時系列のファイルが揃っているものだけ
    assert "時系列がない" in reuse.find(name, "B", [same], True)[1]
    (same / f"{name}.jsonl").write_text("{}\n")
    assert reuse.find(name, "B", [same], True)[0] == same / f"{name}.json"


# --- batch --------------------------------------------------------------------------------


class FakeProcess:
    """simulate の代わり。結果の JSON を書いて終わる。"""

    started: list[list[str]] = []

    def __init__(self, command, stdout=None, stderr=None):
        FakeProcess.started.append(command)
        out = command[command.index("--out") + 1]
        with open(out, "w") as f:
            json.dump({"scenario": "small", "seed": 1, "turns": 300, "fresh": True}, f)

    def poll(self):
        return 0


@pytest.fixture
def fake_runs(monkeypatch):
    FakeProcess.started = []
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(measure.time, "sleep", lambda _: None)
    return FakeProcess.started


def test_batch_copies_reusable_results_and_runs_the_rest(tmp_path, fake_runs):
    fingerprint = reuse.code_fingerprint()
    base = tmp_path / "main"
    write(base, "small-s1-t300", code_fingerprint=fingerprint, marker="old")
    write(base, "small-s2-t300", code_fingerprint="stale")
    out = tmp_path / "new"

    failed = measure.batch(
        [("small", 1, 300), ("small", 2, 300), ("small", 3, 300)], out, 2, False, [base]
    )

    assert failed == 0
    started = [c[c.index("--seed") + 1] for c in fake_runs]
    assert started == ["2", "3"]  # 1 は回していない
    assert json.loads((out / "small-s1-t300.json").read_text())["marker"] == "old"
    assert json.loads((out / "small-s2-t300.json").read_text())["fresh"] is True
    assert str(base) in (out / "small-s1-t300.log").read_text()


def test_batch_without_reuse_runs_everything(tmp_path, fake_runs):
    base = tmp_path / "main"
    write(base, "small-s1-t300", code_fingerprint=reuse.code_fingerprint())
    measure.batch([("small", 1, 300)], tmp_path / "new", 1, False)
    assert len(fake_runs) == 1


def test_batch_reusing_its_own_folder_runs_nothing(tmp_path, fake_runs):
    folder = tmp_path / "again"
    write(folder, "small-s1-t300", code_fingerprint=reuse.code_fingerprint(), marker="kept")
    assert measure.batch([("small", 1, 300)], folder, 1, False, [folder]) == 0
    assert fake_runs == []
    assert json.loads((folder / "small-s1-t300.json").read_text())["marker"] == "kept"


def test_batch_copies_the_series_when_asked(tmp_path, fake_runs):
    base = tmp_path / "main"
    write(base, "small-s1-t300", code_fingerprint=reuse.code_fingerprint())
    (base / "small-s1-t300.jsonl").write_text('{"t": 1}\n')
    out = tmp_path / "new"
    measure.batch([("small", 1, 300)], out, 1, True, [base])
    assert fake_runs == []
    assert (out / "small-s1-t300.jsonl").read_text() == '{"t": 1}\n'
    # 時系列がなければ回す
    (base / "small-s1-t300.jsonl").unlink()
    measure.batch([("small", 1, 300)], tmp_path / "other", 1, True, [base])
    assert len(fake_runs) == 1
