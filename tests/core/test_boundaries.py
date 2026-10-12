"""コアは Django も DB も import しない（設計の決まり）。"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "hakoniwa_core"
FORBIDDEN = ("django", "server", "sqlite3", "psycopg")


def test_core_source_has_no_forbidden_imports():
    offenders = []
    for path in CORE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in FORBIDDEN:
                    offenders.append(f"{path.relative_to(CORE.parent)}:{node.lineno} {name}")
    assert offenders == []


def test_running_core_does_not_load_django():
    code = (
        "import sys, hakoniwa_core.simulate as s; s.main(['--islands', '2', '--turns', '2']);"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('django', 'server')];"
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)
