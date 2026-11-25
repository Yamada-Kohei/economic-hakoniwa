"""「更新履歴:」の行の禁止語チェック（tools/spec/check_changelog.py）を確かめる。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_changelog", REPO / "tools" / "spec" / "check_changelog.py"
)
check_changelog = importlib.util.module_from_spec(_spec)
sys.modules["check_changelog"] = check_changelog
_spec.loader.exec_module(check_changelog)


def test_player_facing_line_passes():
    message = "海運の品薄を直す\n\n更新履歴: 船の品薄が解消されやすくなりました。\n"
    assert check_changelog.problems_in_message(message) == []


def test_no_changelog_line_passes():
    message = "中の直しだけ\n\n本文に理由を書く。\n"
    assert check_changelog.problems_in_message(message) == []


def test_note_about_not_adding_is_caught():
    # 2026-11-26 の事故: 書かない理由のメモがそのまま本番に出た形
    message = "試験だけを足す\n\n更新履歴: 試験のみの追加のため足さない。\n"
    words = {word for _, word in check_changelog.problems_in_message(message)}
    assert "足さない" in words
    assert "試験" in words


def test_inner_words_are_caught():
    for word in ("足しません", "PR", "コミット", "テスト"):
        message = f"直し\n\n更新履歴: {word} の都合で直しました。\n"
        words = {w for _, w in check_changelog.problems_in_message(message)}
        assert word in words, word


def test_forbidden_word_outside_changelog_line_is_ignored():
    # 禁止語の対象は「更新履歴:」の行だけ。本文の説明は落とさない。
    message = "試験を足す\n\nこの PR では試験だけを足す。コミットの文の説明。\n"
    assert check_changelog.problems_in_message(message) == []


def test_fullwidth_colon_line_is_checked():
    message = "直し\n\n更新履歴：テストの都合で直しました。\n"
    assert check_changelog.problems_in_message(message)
