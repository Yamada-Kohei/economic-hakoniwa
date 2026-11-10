"""クラウドで重い計算を始めさせない hook（.claude/hooks/cloud_heavy_guard.py。2026-11-10）。

決まり（thread-rules.md「重い試験は X870 で（必須）」）のとおり、全部の pytest・100 ターン以上の
試し回し・measure の batch を止め、変えた所の短い試験と ruff は通す。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "hooks" / "cloud_heavy_guard.py"


def _guard():
    spec = importlib.util.spec_from_file_location("cloud_heavy_guard", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_heavy_commands_are_stopped():
    guard = _guard()
    for command in (
        "python -m pytest -q -n 8 tests",
        "python -m pytest",
        "python -m hakoniwa_core.simulate --scenario plan --seed 1 --turns 300",
        "python -m hakoniwa_core.simulate --turns=120",
        "python -m hakoniwa_core.measure batch --preset full",
        "python -c \"sys.argv=['t','160']; world=1; game.engine.run_turn(world)\"",
    ):
        assert guard.problem(command), command


def test_short_checks_pass():
    guard = _guard()
    for command in (
        "python -m pytest -q tests/core/test_owners_cap.py -k carry",
        "python -m pytest tests/core/test_open_land_factory.py::test_site_cost_adds_materials",
        "python -m hakoniwa_core.simulate --scenario plan --seed 1 --turns 10",
        "ruff check hakoniwa_core tests",
        "python tools/spec/design_index.py check",
        "git log --oneline -300",
    ):
        assert guard.problem(command) is None, command


def test_turn_scripts_with_many_turns_are_stopped(tmp_path):
    guard = _guard()
    script = tmp_path / "trace.py"
    script.write_text("for _ in range(int(sys.argv[1])):\n    game.engine.run_turn(world)\n")
    assert guard.problem(f"python {script} 300 1")
    assert guard.problem(f"python {script} 24 1") is None
    fixed = tmp_path / "long.py"
    fixed.write_text("for _ in range(300):\n    game.engine.run_turn(world)\n")
    assert guard.problem(f"python {fixed}")
    # パスの中の数字はターン数に数えない
    assert guard.problem(f"python {script} 24 1 > /tmp/x-7360/out.json") is None


def test_only_in_the_cloud():
    """クラウドの外（X870 の上の Claude Code など）では何も止めない。止めるときは終わりの値 2。"""
    payload = json.dumps({"tool_input": {"command": "python -m pytest tests"}})
    for env, code in (({"CLAUDE_CODE_REMOTE": "true"}, 2), ({}, 0)):
        done = subprocess.run(
            [sys.executable, str(HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", **env},
        )
        assert done.returncode == code, (env, done.stderr)
