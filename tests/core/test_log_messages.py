"""ターンログの文の種類に、どれも文面があるか（2026-10-16 の Codex のレビューの U03: 暴動で止まった
施設のログが facility_stopped_riot のまま出ていた）。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hakoniwa_core.game import load_game
from hakoniwa_core.log import format_log

CORE = Path(__file__).resolve().parents[2] / "hakoniwa_core"
# 施設の status のうち、動いている施設の止まった理由でないもの（工事中・直している・動く前）
NOT_RUN_CAUSES = ("construction", "repair", "starting")


@pytest.fixture(scope="module")
def defs():
    return load_game().defs


def _messages(defs) -> set[str]:
    return set(defs.locales["ja"]["messages"])


def test_every_literal_log_kind_has_a_message(defs):
    kinds = set()
    for path in CORE.rglob("*.py"):
        kinds |= set(re.findall(r'LogEntry\(\s*"([a-z0-9_]+)"', path.read_text(encoding="utf-8")))
    assert len(kinds) > 50
    assert sorted(kinds - _messages(defs)) == []


def test_every_stop_cause_has_a_log_message(defs):
    """施設が止まったときのログ facility_stopped_<理由> は、施設の status の理由ごとに要る。"""
    messages = _messages(defs)
    causes = {
        name.removeprefix("status_")
        for name in messages
        if name.startswith("status_")
        and not name.removeprefix("status_").startswith(NOT_RUN_CAUSES)
    }
    assert {"riot", "halted", "workers", "power"} <= causes
    assert sorted(c for c in causes if f"facility_stopped_{c}" not in messages) == []


def test_riot_stop_is_written_in_words(defs):
    text = format_log(defs, "facility_stopped_riot", {"facility": "farm", "x": 1, "y": 2})
    assert "暴動" in text and "facility_stopped" not in text
