"""既定は切の切り替えの一覧の道具（tools/spec/default_off_switches.py）の自己確認。

game.yaml の false の設定と道具の対応表が食い違ったら落ちる（新しい切り替えの
書き忘れと、既定が入になった物の残りを防ぐ）。一覧の文が道具の作る文と
同じかも確かめる（手で表を直したら落ちる）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "default_off_switches", REPO / "tools" / "spec" / "default_off_switches.py"
)
default_off_switches = importlib.util.module_from_spec(_spec)
sys.modules["default_off_switches"] = default_off_switches
_spec.loader.exec_module(default_off_switches)


def test_find_default_off_reads_only_false_leaves():
    config = {
        "a": {"b": False, "c": True, "d": 0, "e": {"f": False}},
        "g": False,
        "h": [False],  # 並びの中は切り替えではないので拾わない
    }
    assert default_off_switches.find_default_off(config) == ["a.b", "a.e.f", "g"]


def test_every_switch_in_game_yaml_has_info():
    found = default_off_switches.load_switches()
    assert found, "game.yaml に既定は切の切り替えが 1 つも無いのはおかしい"
    assert default_off_switches.problems(found) == []


def test_doc_matches_rendered_text():
    found = default_off_switches.load_switches()
    doc = REPO / "docs" / "default-off-switches.md"
    assert doc.read_text(encoding="utf-8") == default_off_switches.render(found)
    for name in found:
        assert f"`{name}`" in doc.read_text(encoding="utf-8")
