"""測りの結果の置き場（measure batch --store、GT-45）: main の結果を 1 か所にためて使い回す。

- 置き場に書いた結果を、別の出力先の batch が回さずに写す
- 完了の印のない物・印と中身が合わない物・指紋が違う物は使わずに回す
- 札があるときは待ち、置かれたら写す。止まった札（古い札）は引き継いで回す
- 失敗した結果は置かない。抜けるときに札を外す
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from hakoniwa_core import measure, reuse

NAME = "small-s1-t300"


@pytest.fixture(scope="module")
def fingerprint():
    return reuse.code_fingerprint()


def write_run(folder, name=NAME, fingerprint="F", series=False, **fields):
    """simulate が書いた形の結果を folder に作る。"""
    folder.mkdir(parents=True, exist_ok=True)
    scenario, seed, turns = name.split("-")
    data = {
        "scenario": scenario,
        "seed": int(seed[1:]),
        "turns": int(turns[1:]),
        "digest": "d",
        "code_fingerprint": fingerprint,
        **fields,
    }
    (folder / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    if series:
        (folder / f"{name}.jsonl").write_text('{"t": 1}\n{"t": 2}\n', encoding="utf-8")
    return folder / f"{name}.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def age(path, seconds):
    """ファイルの時刻を seconds 秒だけ昔にする。"""
    past = time.time() - seconds
    os.utime(path, (past, past))


# --- 鍵 -----------------------------------------------------------------------------------


def test_the_key_carries_the_fingerprint_and_the_versions(tmp_path):
    tag = reuse.environment_tag()
    assert tag.startswith(f"py{sys.version_info.major}.{sys.version_info.minor}-")
    assert "PyYAML" in tag and "jsonschema" in tag
    assert reuse.store_key("F" * 64) == f"{'F' * 64}-{tag}"
    store = reuse.Store(tmp_path / "store", "F" * 64)
    assert store.folder == tmp_path / "store" / reuse.store_key("F" * 64)
    assert store.folder.is_dir()


# --- 置く・写す ---------------------------------------------------------------------------


def stored(store, kind="json", name=NAME):
    """完了の印が指す、置き場の中のファイル。"""
    return store.folder / read(store.folder / f"{name}.done.json")["files"][kind]["file"]


def stamp(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def test_a_published_result_is_copied_into_another_folder(tmp_path):
    made, other = tmp_path / "made", tmp_path / "other"
    source = write_run(made, marker="kept")
    store = reuse.Store(tmp_path / "store", "F", owner="スレッドA")

    assert store.fetch(NAME, other, False)[0] is False  # まだ何も置いていない
    assert not (other / f"{NAME}.json").exists()
    assert store.publish(NAME, made, False) == (True, "")

    done = read(store.folder / f"{NAME}.done.json")
    assert done["name"] == NAME
    assert done["fingerprint"] == "F"
    assert done["environment"] == reuse.environment_tag()
    assert done["owner"] == "スレッドA"
    # 結果は、中身のハッシュ入りの名前で置く
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert done["files"] == {
        "json": {"file": f"{NAME}.{digest[:16]}.json", **stamp(source.read_bytes())}
    }
    assert stored(store).read_bytes() == source.read_bytes()

    reader = reuse.Store(tmp_path / "store", "F", owner="スレッドB")
    other.mkdir()
    assert reader.fetch(NAME, other, False) == (True, "")
    assert (other / f"{NAME}.json").read_bytes() == source.read_bytes()
    assert str(store.folder) in (other / f"{NAME}.log").read_text(encoding="utf-8")
    # 一時のファイルが残らない
    assert not [p.name for p in store.folder.iterdir() if ".tmp-" in p.name]
    assert not [p.name for p in other.iterdir() if ".tmp-" in p.name]


def test_a_result_without_the_done_mark_is_not_used(tmp_path):
    made, out = tmp_path / "made", tmp_path / "out"
    write_run(made)
    out.mkdir()
    store = reuse.Store(tmp_path / "store", "F")
    assert store.publish(NAME, made, False)[0]
    (store.folder / f"{NAME}.done.json").unlink()  # 書きかけ: 結果はあるが完了の印がない
    ok, why = store.fetch(NAME, out, False)
    assert not ok and why
    assert not (out / f"{NAME}.json").exists()


def test_a_result_that_differs_from_its_mark_is_not_used(tmp_path):
    made, out = tmp_path / "made", tmp_path / "out"
    write_run(made)
    out.mkdir()
    store = reuse.Store(tmp_path / "store", "F")
    assert store.publish(NAME, made, False)[0]
    result = stored(store)
    result.write_text(result.read_text(encoding="utf-8")[:-5], encoding="utf-8")  # 途中で切れた
    ok, why = store.fetch(NAME, out, False)
    assert not ok and "合わない" in why
    assert not (out / f"{NAME}.json").exists()
    assert not list(out.iterdir())  # 一時のファイルも残さない
    # 印が壊れていても使わない
    again = reuse.Store(tmp_path / "store2", "F")
    assert again.publish(NAME, made, False)[0]
    (again.folder / f"{NAME}.done.json").write_text('{"name": "sma', encoding="utf-8")
    assert again.fetch(NAME, out, False)[0] is False
    # 印が置き場の外のファイルを指していても使わない
    third = reuse.Store(tmp_path / "store3", "F")
    assert third.publish(NAME, made, False)[0]
    done = read(third.folder / f"{NAME}.done.json")
    done["files"]["json"]["file"] = "../../made/" + f"{NAME}.json"
    (third.folder / f"{NAME}.done.json").write_text(json.dumps(done), encoding="utf-8")
    assert third.fetch(NAME, out, False)[0] is False


def test_another_fingerprint_does_not_see_the_result(tmp_path):
    made, out = tmp_path / "made", tmp_path / "out"
    write_run(made)
    out.mkdir()
    assert reuse.Store(tmp_path / "store", "F").publish(NAME, made, False)[0]
    assert reuse.Store(tmp_path / "store", "G").fetch(NAME, out, False)[0] is False
    assert reuse.Store(tmp_path / "store", "F").fetch(NAME, out, False)[0] is True


def test_publish_refuses_a_result_of_another_fingerprint_or_name(tmp_path):
    made = tmp_path / "made"
    store = reuse.Store(tmp_path / "store", "F")
    write_run(made, fingerprint="OTHER")
    assert store.publish(NAME, made, False)[0] is False
    write_run(made, name="small-s2-t300")
    (made / f"{NAME}.json").write_bytes((made / "small-s2-t300.json").read_bytes())
    assert store.publish(NAME, made, False)[0] is False  # 中身は種 2 の結果
    (made / f"{NAME}.json").write_text('{"scenario": "sma', encoding="utf-8")
    assert store.publish(NAME, made, False)[0] is False
    assert store.publish("plan-s1-t300", made, False)[0] is False  # 結果がない
    write_run(made)
    assert store.publish(NAME, made, True)[0] is False  # 時系列を頼まれたのにない
    assert not list(store.folder.iterdir())  # 何も書いていない


def test_the_series_can_be_added_later_without_touching_the_result(tmp_path):
    first, second, out = tmp_path / "first", tmp_path / "second", tmp_path / "out"
    write_run(first, seconds=1.0)
    write_run(second, seconds=2.0, series=True)
    out.mkdir()
    store = reuse.Store(tmp_path / "store", "F")
    assert store.publish(NAME, first, False)[0]
    ok, why = store.fetch(NAME, out, True)
    assert not ok and "時系列" in why
    assert not (out / f"{NAME}.json").exists()

    assert store.publish(NAME, second, True) == (True, "")
    assert read(stored(store))["seconds"] == 1.0  # 結果は書き換えない
    series = (second / f"{NAME}.jsonl").read_bytes()
    assert stored(store, "jsonl").read_bytes() == series
    assert store.fetch(NAME, out, True) == (True, "")
    assert read(out / f"{NAME}.json")["seconds"] == 1.0
    assert (out / f"{NAME}.jsonl").read_bytes() == series
    # 二重に回した方の結果のファイルは残さない
    assert sorted(p.suffix for p in store.folder.iterdir()) == [".json", ".json", ".jsonl"]
    # 時系列が印と合わなければ使わない。時系列を頼まなければ使える
    stored(store, "jsonl").write_text('{"t": 1}\n', encoding="utf-8")
    assert store.fetch(NAME, tmp_path, True)[0] is False
    plain = tmp_path / "plain"
    plain.mkdir()
    assert store.fetch(NAME, plain, False)[0] is True
    assert not (plain / f"{NAME}.jsonl").exists()
    # もう置いてある物をもう 1 度置いても変わらない
    before = (store.folder / f"{NAME}.done.json").read_bytes()
    assert store.publish(NAME, first, False) == (True, "")
    assert (store.folder / f"{NAME}.done.json").read_bytes() == before


def test_a_late_publisher_does_not_drop_the_series_from_the_mark(tmp_path, monkeypatch):
    """時系列なしで置く batch が遅れて来ても、先に置かれた「時系列あり」の印を悪くしない。"""
    plain, full, out = tmp_path / "plain", tmp_path / "full", tmp_path / "out"
    write_run(plain, seconds=1.0)
    write_run(full, seconds=2.0, series=True)
    slow = reuse.Store(tmp_path / "store", "F", owner="遅い")
    fast = reuse.Store(tmp_path / "store", "F", owner="速い")
    put = slow._put

    def put_then_overtaken(*args):
        placed = put(*args)  # 遅い方が結果を置いた後、印を置く前に、速い方が全部置き終える
        assert fast.publish(NAME, full, True) == (True, "")
        return placed

    monkeypatch.setattr(slow, "_put", put_then_overtaken)
    assert slow.publish(NAME, plain, False) == (True, "")
    done = read(slow.folder / f"{NAME}.done.json")
    assert "jsonl" in done["files"] and done["owner"] == "速い"
    assert fast.fetch(NAME, out, True) == (True, "")
    assert read(out / f"{NAME}.json")["seconds"] == 2.0
    # 遅い方が置いた結果のファイルは、印が指さないので消してある
    assert sorted(p.suffix for p in slow.folder.iterdir()) == [".json", ".json", ".jsonl"]


def test_two_publishers_of_the_same_run_leave_a_matching_mark(tmp_path, monkeypatch):
    """二重に回した 2 本が同時に置いても、印はいつも自分の指す結果と合う。"""
    mine, theirs, out = tmp_path / "mine", tmp_path / "theirs", tmp_path / "out"
    write_run(mine, seconds=1.0)
    write_run(theirs, seconds=2.0)  # 同じ組だが、かかった秒が違う
    a = reuse.Store(tmp_path / "store", "F", owner="A")
    b = reuse.Store(tmp_path / "store", "F", owner="B")
    put = a._put

    def put_then_overtaken(*args):
        placed = put(*args)  # A が結果を置いた後、印を置く前に、B が結果と印を置く
        assert b.publish(NAME, theirs, False) == (True, "")
        return placed

    monkeypatch.setattr(a, "_put", put_then_overtaken)
    assert a.publish(NAME, mine, False) == (True, "")
    assert a.fetch(NAME, out, False) == (True, "")
    assert read(out / f"{NAME}.json")["seconds"] == 2.0  # 先に印を置いた B の結果


def test_publish_repairs_a_store_whose_mark_and_result_differ(tmp_path):
    made, out = tmp_path / "made", tmp_path / "out"
    write_run(made, seconds=1.0)
    out.mkdir()
    store = reuse.Store(tmp_path / "store", "F")
    assert store.publish(NAME, made, False)[0]
    # 結果が壊れているときは、置き直す
    stored(store).write_text('{"scenario": "sma', encoding="utf-8")
    assert store.fetch(NAME, out, False)[0] is False
    assert store.publish(NAME, made, False) == (True, "")
    assert store.fetch(NAME, out, False) == (True, "")
    assert read(out / f"{NAME}.json")["seconds"] == 1.0
    # 結果のファイルがなくなったときも
    stored(store).unlink()
    assert store.fetch(NAME, out, False)[0] is False
    assert store.publish(NAME, made, False) == (True, "")
    assert store.fetch(NAME, out, False) == (True, "")
    # 印が壊れているときも
    (store.folder / f"{NAME}.done.json").write_text('{"name": "sma', encoding="utf-8")
    assert store.fetch(NAME, out, False)[0] is False
    assert store.publish(NAME, made, False) == (True, "")
    assert store.fetch(NAME, out, False) == (True, "")
    # 時系列が印と合わないときも、置き直す
    write_run(made, seconds=1.0, series=True)
    assert store.publish(NAME, made, True)[0]
    stored(store, "jsonl").write_text("broken\n", encoding="utf-8")
    assert store.fetch(NAME, out, True)[0] is False
    assert store.publish(NAME, made, True) == (True, "")
    assert store.fetch(NAME, out, True) == (True, "")


def test_many_publishers_at_once_leave_a_usable_result(tmp_path):
    """同じ組を 8 本が同時に置くのを 10 回。どの回も、置いた後は写せる。"""
    for round_ in range(10):
        root = tmp_path / f"store{round_}"
        folders = []
        for i in range(8):
            folders.append(tmp_path / f"made{round_}-{i}")
            write_run(folders[-1], seconds=float(i), series=i % 2 == 0)
        results: list[tuple[bool, str]] = []
        gate = threading.Barrier(len(folders))

        def publish(folder, index, root=root, gate=gate, results=results):
            store = reuse.Store(root, "F")
            gate.wait()
            results.append(store.publish(NAME, folder, index % 2 == 0))

        threads = [threading.Thread(target=publish, args=(f, i)) for i, f in enumerate(folders)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert results == [(True, "")] * len(folders)
        out = tmp_path / f"out{round_}"
        assert reuse.Store(root, "F").fetch(NAME, out, False) == (True, "")
        assert not [p.name for p in reuse.Store(root, "F").folder.iterdir() if ".tmp-" in p.name]


def test_fetch_marks_the_version_as_used_and_prune_removes_old_versions(tmp_path):
    made, out = tmp_path / "made", tmp_path / "out"
    write_run(made)
    root = tmp_path / "store"
    old, new = reuse.Store(root, "a" * 64), reuse.Store(root, "b" * 64)
    for store in (old, new):
        write_run(made, fingerprint=store.fingerprint)
        assert store.publish(NAME, made, False)[0]
        age(store.folder / f"{NAME}.done.json", 20 * 86400)
    assert new.fetch(NAME, out, False)[0]  # 写すと、使った時刻が新しくなる
    leftover = new.folder / f"{NAME}.json.tmp-0123"
    leftover.write_text("x", encoding="utf-8")
    age(leftover, 2 * 86400)
    fresh = new.folder / f"{NAME}.json.tmp-4567"
    fresh.write_text("x", encoding="utf-8")
    (root / "notes").mkdir()  # 置き場の版のフォルダでない物には触らない

    removed = reuse.prune(root, 14)
    assert removed == [old.folder.name, f"{new.folder.name}/{leftover.name}"]
    assert not old.folder.exists() and (root / "notes").is_dir() and fresh.exists()
    assert new.fetch(NAME, out, False)[0]
    assert reuse.prune(root, 14) == []
    assert reuse.prune(tmp_path / "nowhere", 14) == []


def test_the_scale_override_changes_the_fingerprint(monkeypatch):
    """国の大きさの倍を環境変数で替えると結果が変わるので、指紋も変わる。"""
    monkeypatch.delenv("HK_SCALE", raising=False)
    plain = reuse.code_fingerprint()
    monkeypatch.setenv("HK_SCALE", "1")
    one = reuse.code_fingerprint()
    monkeypatch.setenv("HK_SCALE", "1.0")
    assert reuse.code_fingerprint() == one
    monkeypatch.setenv("HK_SCALE", "20")
    assert len({plain, one, reuse.code_fingerprint()}) == 3


# --- 札 -----------------------------------------------------------------------------------


def test_only_one_holds_the_ticket_and_only_the_holder_removes_it(tmp_path):
    a = reuse.Store(tmp_path / "store", "F", owner="スレッドA")
    b = reuse.Store(tmp_path / "store", "F", owner="スレッドB")
    assert a.free(NAME)
    assert a.claim(NAME) == (True, "")
    ticket = read(a.folder / f"{NAME}.lock")
    assert ticket["owner"] == "スレッドA" and ticket["pid"] == os.getpid() and ticket["started"]
    assert not b.free(NAME)
    got, who = b.claim(NAME)
    assert not got and "スレッドA" in who
    b.release(NAME)  # 持ち主でなければ外せない
    assert (a.folder / f"{NAME}.lock").exists()
    assert a.claim("small-s2-t300") == (True, "")  # 組ごとに別の札
    a.release(NAME)
    assert not (a.folder / f"{NAME}.lock").exists()
    assert b.claim(NAME) == (True, "")


def test_a_stale_ticket_is_taken_over(tmp_path):
    a = reuse.Store(tmp_path / "store", "F", owner="止まった")
    b = reuse.Store(tmp_path / "store", "F", owner="引き継ぐ")
    assert a.claim(NAME)[0]
    lock = a.folder / f"{NAME}.lock"
    age(lock, reuse.STALE_SECONDS - 60)
    assert not b.free(NAME) and b.claim(NAME)[0] is False  # まだ生きているとみる
    age(lock, reuse.STALE_SECONDS + 60)
    assert b.free(NAME)
    assert b.claim(NAME) == (True, "")
    assert read(lock)["owner"] == "引き継ぐ"
    a.beat(NAME)  # 元の持ち主が戻っても、引き継いだ人の札に触らない
    a.release(NAME)
    assert read(lock)["owner"] == "引き継ぐ"
    assert not [p.name for p in a.folder.iterdir() if "stale" in p.name]


def test_beat_keeps_the_ticket_fresh(tmp_path):
    a = reuse.Store(tmp_path / "store", "F")
    b = reuse.Store(tmp_path / "store", "F", stale_seconds=100.0)
    assert a.claim(NAME)[0]
    lock = a.folder / f"{NAME}.lock"
    age(lock, 500)
    b.beat(NAME)  # 他人の札の時刻は動かせない
    assert b.free(NAME)
    a.beat(NAME)
    assert time.time() - lock.stat().st_mtime < 60
    assert not b.free(NAME)
    a.beat("plan-s1-t300")  # 札がなくても落ちない
    a.release("plan-s1-t300")


# --- batch --------------------------------------------------------------------------------


class FakeProcess:
    """simulate の代わり。今の指紋の結果（と頼まれれば時系列）を書いて終わる。"""

    started: list[list[str]] = []
    fingerprint = ""
    code = 0  # 終わりの値
    finished = True
    writes = True  # 結果を書いてから終わるか
    killed = 0

    def __init__(self, command, stdout=None, stderr=None):
        FakeProcess.started.append(command)
        if not FakeProcess.writes:
            return
        value = {flag: command[command.index(flag) + 1] for flag in ("--scenario", "--seed")}
        data = {
            "scenario": value["--scenario"],
            "seed": int(value["--seed"]),
            "turns": int(command[command.index("--turns") + 1]),
            "code_fingerprint": FakeProcess.fingerprint,
            "fresh": True,
        }
        with open(command[command.index("--out") + 1], "w", encoding="utf-8") as f:
            json.dump(data, f)
        if "--series" in command:
            with open(command[command.index("--series") + 1], "w", encoding="utf-8") as f:
                f.write('{"t": 1}\n')

    def poll(self):
        return FakeProcess.code if FakeProcess.finished else None

    def kill(self):
        FakeProcess.killed += 1
        FakeProcess.finished = True
        FakeProcess.code = -9


@pytest.fixture
def fake_runs(monkeypatch, fingerprint):
    FakeProcess.started = []
    FakeProcess.fingerprint = fingerprint
    FakeProcess.code = 0
    FakeProcess.finished = True
    FakeProcess.writes = True
    FakeProcess.killed = 0
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(measure.time, "sleep", lambda _: None)
    return FakeProcess.started


def seeds(started):
    return [c[c.index("--seed") + 1] for c in started]


def after_sleeps(monkeypatch, count, action):
    """batch の count 回目の待ち（time.sleep）で action を呼ぶ。終わらなければ 200 回で落とす。"""
    calls = []

    def sleep(_):
        calls.append(1)
        if len(calls) == count:
            action()
        assert len(calls) < 200, "batch が終わらない"

    monkeypatch.setattr(measure.time, "sleep", sleep)
    return calls


RUNS = [("small", 1, 300), ("small", 2, 300)]


def test_the_second_batch_copies_everything_from_the_store(tmp_path, fake_runs, capsys):
    root = tmp_path / "store"
    assert measure.batch(RUNS, tmp_path / "first", 2, False, store=root, owner="A") == 0
    assert seeds(fake_runs) == ["1", "2"]
    assert "置き場に置いた: small-s1-t300" in capsys.readouterr().out

    assert measure.batch(RUNS, tmp_path / "second", 2, False, store=root, owner="B") == 0
    assert seeds(fake_runs) == ["1", "2"]  # 2 本目は 1 つも回していない
    assert "置き場から写した: small-s2-t300" in capsys.readouterr().out
    for name in ("small-s1-t300", "small-s2-t300"):
        first = (tmp_path / "first" / f"{name}.json").read_bytes()
        assert (tmp_path / "second" / f"{name}.json").read_bytes() == first
        assert "使い回し" in (tmp_path / "second" / f"{name}.log").read_text(encoding="utf-8")
    # 札は残らない
    assert not list(root.rglob("*.lock"))


def test_batch_runs_again_when_the_code_changed(tmp_path, fake_runs, fingerprint):
    made = tmp_path / "made"
    write_run(made, fingerprint="OLD")
    assert reuse.Store(tmp_path / "store", "OLD").publish(NAME, made, False)[0]
    measure.batch(RUNS[:1], tmp_path / "out", 1, False, store=tmp_path / "store")
    assert seeds(fake_runs) == ["1"]
    assert reuse.Store(tmp_path / "store", fingerprint).fetch(NAME, tmp_path, False)[0]


def test_batch_does_not_use_a_half_written_result(tmp_path, fake_runs, fingerprint):
    made = tmp_path / "made"
    write_run(made, fingerprint=fingerprint, marker="half")
    store = reuse.Store(tmp_path / "store", fingerprint)
    assert store.publish(NAME, made, False)[0]
    (store.folder / f"{NAME}.done.json").unlink()  # 結果はあるが、完了の印がない
    measure.batch(RUNS[:1], tmp_path / "out", 1, False, store=tmp_path / "store")
    assert seeds(fake_runs) == ["1"]
    assert read(tmp_path / "out" / f"{NAME}.json")["fresh"] is True


def test_batch_stops_a_run_that_hangs_and_removes_its_ticket(
    tmp_path, fake_runs, fingerprint, monkeypatch, capsys
):
    FakeProcess.finished = False  # 固まった処理
    FakeProcess.writes = False
    monkeypatch.setattr(reuse, "RUN_LIMIT_SECONDS", -1.0)
    after_sleeps(monkeypatch, 0, lambda: None)
    assert measure.batch(RUNS[:1], tmp_path / "out", 1, False, store=tmp_path / "store") == 1
    assert "止めた: small-s1-t300" in capsys.readouterr().out
    assert FakeProcess.killed == 1
    assert reuse.Store(tmp_path / "store", fingerprint).free(NAME)


def test_batch_waits_for_the_ticket_holder_and_copies_its_result(
    tmp_path, fake_runs, fingerprint, monkeypatch, capsys
):
    other = reuse.Store(tmp_path / "store", fingerprint, owner="先に始めたスレッド")
    assert other.claim(NAME)[0]
    made = tmp_path / "made"
    write_run(made, fingerprint=fingerprint, marker="theirs")

    def finish():
        assert other.publish(NAME, made, False)[0]
        other.release(NAME)

    calls = after_sleeps(monkeypatch, 3, finish)
    assert measure.batch(RUNS, tmp_path / "out", 2, False, store=tmp_path / "store") == 0
    assert seeds(fake_runs) == ["2"]  # 札のある組は回さず、ほかの組は待たずに回す
    assert len(calls) >= 3
    assert read(tmp_path / "out" / f"{NAME}.json")["marker"] == "theirs"
    out = capsys.readouterr().out
    assert "待つ: small-s1-t300" in out and "先に始めたスレッド" in out


def test_batch_takes_over_a_stale_ticket(tmp_path, fake_runs, fingerprint):
    other = reuse.Store(tmp_path / "store", fingerprint, owner="止まった")
    assert other.claim(NAME)[0]
    age(other.folder / f"{NAME}.lock", reuse.STALE_SECONDS + 60)
    assert measure.batch(RUNS[:1], tmp_path / "out", 1, False, store=tmp_path / "store") == 0
    assert seeds(fake_runs) == ["1"]
    assert other.fetch(NAME, tmp_path, False)[0]  # 引き継いで回した結果が置かれた
    assert not list((tmp_path / "store").rglob("*.lock"))


def test_batch_runs_itself_when_the_holder_gives_up(tmp_path, fake_runs, fingerprint, monkeypatch):
    other = reuse.Store(tmp_path / "store", fingerprint)
    assert other.claim(NAME)[0]
    after_sleeps(monkeypatch, 2, lambda: other.release(NAME))  # 置かずに札だけ外した（失敗）
    assert measure.batch(RUNS[:1], tmp_path / "out", 1, False, store=tmp_path / "store") == 0
    assert seeds(fake_runs) == ["1"]


def test_a_run_with_gate_findings_is_put_in_the_store(tmp_path, fake_runs, fingerprint):
    FakeProcess.code = 1  # simulate --gate は、目安の外れや崩れがあると 1 で終わる
    assert measure.batch(RUNS[:1], tmp_path / "out", 1, False, store=tmp_path / "store") == 0
    assert reuse.Store(tmp_path / "store", fingerprint).fetch(NAME, tmp_path, False)[0] is True


def test_a_failed_run_is_not_put_in_the_store(tmp_path, fake_runs, fingerprint):
    FakeProcess.code = -9  # 結果は書いたが、処理は外から止められた
    measure.batch(RUNS[:1], tmp_path / "out", 1, False, store=tmp_path / "store")
    store = reuse.Store(tmp_path / "store", fingerprint)
    assert store.fetch(NAME, tmp_path, False)[0] is False
    assert store.free(NAME)  # 札は外してある


def test_an_old_result_in_the_out_folder_does_not_hide_a_crash(tmp_path, fake_runs, fingerprint):
    out = tmp_path / "out"
    write_run(out, fingerprint=fingerprint, series=True, marker="前の回")  # 前の batch の結果
    FakeProcess.writes = False  # 今回の処理は、結果を書く前に落ちた
    FakeProcess.code = 1
    assert measure.batch(RUNS[:1], out, 1, True, store=tmp_path / "store") == 1
    store = reuse.Store(tmp_path / "store", fingerprint)
    assert store.fetch(NAME, tmp_path, True)[0] is False
    assert store.free(NAME)


def test_a_result_of_another_fingerprint_is_not_put_in_the_store(tmp_path, fake_runs, fingerprint):
    FakeProcess.fingerprint = "CHANGED-WHILE-RUNNING"  # 回している間に作業ツリーが変わった
    assert measure.batch(RUNS[:1], tmp_path / "out", 1, False, store=tmp_path / "store") == 0
    store = reuse.Store(tmp_path / "store", fingerprint)
    assert store.fetch(NAME, tmp_path, False)[0] is False
    assert store.free(NAME)


def test_batch_removes_its_tickets_when_it_is_stopped(
    tmp_path, fake_runs, fingerprint, monkeypatch
):
    FakeProcess.finished = False  # 回している途中

    def stop(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(measure.time, "sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        measure.batch(RUNS[:1], tmp_path / "out", 1, False, store=tmp_path / "store")
    assert reuse.Store(tmp_path / "store", fingerprint).free(NAME)


def test_batch_stores_and_copies_the_series(tmp_path, fake_runs, fingerprint):
    root = tmp_path / "store"
    measure.batch(RUNS[:1], tmp_path / "plain", 1, False, store=root)
    measure.batch(RUNS[:1], tmp_path / "series", 1, True, store=root)  # 時系列がないので回す
    assert len(fake_runs) == 2
    measure.batch(RUNS[:1], tmp_path / "again", 1, True, store=root)
    assert len(fake_runs) == 2
    assert (tmp_path / "again" / f"{NAME}.jsonl").read_text(encoding="utf-8") == '{"t": 1}\n'


def test_reuse_folders_are_looked_at_before_the_store(tmp_path, fake_runs, fingerprint):
    base = tmp_path / "main"
    write_run(base, fingerprint=fingerprint, marker="from-folder")
    out = tmp_path / "out"
    assert measure.batch(RUNS[:1], out, 1, False, [base], store=tmp_path / "store") == 0
    assert fake_runs == []
    assert read(out / f"{NAME}.json")["marker"] == "from-folder"
    # 写しただけの結果は置き場に置かない（回したときの版が分からない）
    assert reuse.Store(tmp_path / "store", fingerprint).fetch(NAME, tmp_path, False)[0] is False


# --- 命令 ---------------------------------------------------------------------------------


@pytest.fixture
def batch_calls(monkeypatch):
    calls = []

    def batch(names, out_dir, jobs, series, reuse_dirs=(), store=None, owner="", resume=True):
        calls.append({"store": store, "owner": owner, "reuse": list(reuse_dirs)})
        return 0

    monkeypatch.setattr(measure, "batch", batch)
    monkeypatch.setattr(measure, "show", lambda path: "")
    return calls


def test_the_store_comes_from_the_argument_or_the_environment(tmp_path, batch_calls, monkeypatch):
    monkeypatch.delenv(reuse.STORE_ENV, raising=False)
    monkeypatch.delenv(reuse.OWNER_ENV, raising=False)
    args = ["batch", "--set", "quick", "--only", "small", "--out-dir", str(tmp_path / "o")]
    assert measure.main(args) == 0
    assert batch_calls[-1]["store"] is None and batch_calls[-1]["owner"] == ""

    measure.main([*args, "--store", str(tmp_path / "s"), "--owner", "スレッドA"])
    assert batch_calls[-1]["store"] == tmp_path / "s" and batch_calls[-1]["owner"] == "スレッドA"

    monkeypatch.setenv(reuse.STORE_ENV, str(tmp_path / "env"))
    monkeypatch.setenv(reuse.OWNER_ENV, "スレッドB")
    measure.main(args)
    assert batch_calls[-1]["store"] == tmp_path / "env" and batch_calls[-1]["owner"] == "スレッドB"
    measure.main([*args, "--store", ""])  # 空を渡せば使わない
    assert batch_calls[-1]["store"] is None


def test_the_clean_command_prunes_the_store(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(reuse.STORE_ENV, raising=False)
    made = tmp_path / "made"
    write_run(made, fingerprint="c" * 64)
    store = reuse.Store(tmp_path / "store", "c" * 64)
    assert store.publish(NAME, made, False)[0]
    assert measure.main(["clean", "--store", str(tmp_path / "store")]) == 0
    assert "消す物はない" in capsys.readouterr().out
    age(store.folder / f"{NAME}.done.json", 3 * 86400)
    assert measure.main(["clean", "--store", str(tmp_path / "store"), "--days", "2"]) == 0
    assert f"消した: {store.folder.name}" in capsys.readouterr().out
    assert not store.folder.exists()
    with pytest.raises(SystemExit):
        measure.main(["clean"])  # 置き場を渡していない
