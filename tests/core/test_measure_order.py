"""測りの回す順（W-236 H-5）と、指紋を出す命令（H-2）。"""

from __future__ import annotations

from hakoniwa_core import measure, reuse


def test_longest_first_puts_heavy_runs_first():
    order = measure.longest_first(measure.SETS["full"])
    assert sorted(order) == sorted(measure.SETS["full"])  # 組は増えも減りもしない
    assert [n[0] for n in order[:2]] == ["open", "open"]  # 一番長い組（open 500）が先
    assert {n[0] for n in order[-4:]} == {"small"}  # 一番軽い組が後
    seconds = [measure.expected_seconds(s, t) for s, _, t in order]
    assert seconds == sorted(seconds, reverse=True)


def test_longest_first_keeps_order_of_equal_runs():
    names = [("plan", 3, 500), ("plan", 1, 500), ("plan", 2, 500)]
    assert measure.longest_first(names) == names


def test_expected_seconds_for_unknown_scenario_uses_islands():
    assert measure.expected_seconds("small", 100) < measure.expected_seconds("plan", 100)
    assert measure.expected_seconds("unknown", 10) == 80.0


def test_fingerprint_command_prints_code_fingerprint(capsys):
    assert measure.main(["fingerprint"]) == 0
    assert capsys.readouterr().out.strip() == reuse.code_fingerprint()
