"""クラウドの入れ物で重い計算を始めさせない Claude Code の hook（PreToolUse、Bash）。

決まり（/mnt/project-files/process/thread-rules.md の「重い試験は X870 で（必須）」）:
クラウドで回してよいのは、変えた所に関わる短い試験と ruff・設計書の検査だけ。全部の pytest、
100 ターン以上の試し回し、measure の batch は X870 など家の計算機で回す。

2026-11-10、スレッド「倒産した施設を動かし続ける」がクラウドで全部の pytest と 160〜300 ターンの
調べの台本を何度も回した。決まりは知っていたが、「1回 3〜12 分で済む」「測りでなく調べの台本だ」と
その場で例外にし、10 → 78 → 160 → 300 ターンと少しずつ大きくした。止める仕掛けがなかったので、
この hook で命令を出す前に止める。

止めるもの（クラウドのときだけ。X870 の上の Claude Code や desktop-commander には効かない）:
1. pytest で試験のファイル（.py）を1つも名指ししない（tests/ のまま・名指しなし = 全部）か、
   名指しが MAX_TEST_FILES より多い。
2. hakoniwa_core.simulate の --turns が TURN_LIMIT 以上。
3. hakoniwa_core.measure の batch・quick・full。
4. ターンを回す自作の台本（命令の文か、名指しした .py の中身に run_turn がある）に、
   TURN_LIMIT 以上の数を渡している。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

TURN_LIMIT = 100  # 決まりの「100ターン以上の試し回し」
MAX_TEST_FILES = 15  # 名指しする試験のファイルがこれより多ければ「全部に近い」とみる
WHERE = (
    "重い計算はクラウドで回さず X870 で回してください"
    "（thread-rules.md「重い試験は X870 で（必須）」）。"
    "X870 に届かなければコーディネーターに、命令と枝を書いて頼んでください。"
    "X870 が混んでいても、クラウドへ逃がさずに待つか、HAL・ESPRIMO・NEC-MC-5 に回します。"
)


def in_cloud() -> bool:
    return os.environ.get("CLAUDE_CODE_REMOTE") == "true" or os.environ.get(
        "CLAUDE_CODE_REMOTE_ENVIRONMENT_TYPE", ""
    ).startswith("cloud")


def words(command: str) -> list[str]:
    try:
        return shlex.split(command, comments=False, posix=True)
    except ValueError:
        return command.split()


def numbers(command: str, tokens: list[str]) -> list[int]:
    """命令に渡す数（それだけで1語の数と、-c の文の中の引用した数）。パスの中の数字は数えない。"""
    found = [int(t) for t in tokens if t.isdigit()]
    found += [int(m) for m in re.findall(r"['\"](\d+)['\"]", command)]
    return found


def scripts(command: str) -> list[Path]:
    """命令の中に出てくる .py のファイル（-c の文の中で開くものも）。"""
    return [Path(m) for m in re.findall(r"[\w./\\~-]+\.py\b", command)]


def pytest_problem(tokens: list[str]) -> str | None:
    if "pytest" not in tokens and not any(t.endswith("/pytest") for t in tokens):
        return None
    start = next(i for i, t in enumerate(tokens) if t == "pytest" or t.endswith("/pytest"))
    rest = []
    for t in tokens[start + 1 :]:
        if t in ("&&", "||", ";", "|"):
            break
        rest.append(t)
    files = {t.split("::")[0] for t in rest if ".py" in t and not t.startswith("-")}
    if not files:
        return "pytest で試験のファイルを名指ししていません（全部の pytest になります）。"
    if len(files) > MAX_TEST_FILES:
        return f"pytest で {len(files)} 個の試験のファイルを回そうとしています（全部に近い）。"
    return None


def simulate_problem(command: str, tokens: list[str]) -> str | None:
    if "hakoniwa_core.simulate" in command:
        for i, t in enumerate(tokens):
            value = None
            if t == "--turns" and i + 1 < len(tokens):
                value = tokens[i + 1]
            elif t.startswith("--turns="):
                value = t.split("=", 1)[1]
            if value is not None and value.isdigit() and int(value) >= TURN_LIMIT:
                return f"{value} ターンの試し回しです（{TURN_LIMIT} ターン以上）。"
    if "hakoniwa_core.measure" in command and re.search(r"\b(batch|quick|full)\b", command):
        return "measure の batch・quick・full です。"
    return None


def script_problem(command: str, tokens: list[str]) -> str | None:
    texts = [command]
    for path in scripts(command):
        try:
            texts.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    if not any("run_turn" in t for t in texts):
        return None
    big = [n for n in numbers(command, tokens) if n >= TURN_LIMIT]
    for text in texts[1:]:  # 台本の中に書いたターン数（range(300) など）
        found = re.findall(r"range\(\s*(\d+)\s*\)", text)
        big += [int(m) for m in found if int(m) >= TURN_LIMIT]
    if big:
        return f"ターンを回す台本に {big[0]} ターンを回させます（{TURN_LIMIT} ターン以上）。"
    return None


def problem(command: str) -> str | None:
    tokens = words(command)
    for check in (
        lambda: pytest_problem(tokens),
        lambda: simulate_problem(command, tokens),
        lambda: script_problem(command, tokens),
    ):
        found = check()
        if found:
            return found
    return None


def main() -> int:
    if not in_cloud():
        return 0
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (data.get("tool_input") or {}).get("command") or ""
    found = problem(command)
    if found is None:
        return 0
    print(f"止めました: {found}{WHERE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
