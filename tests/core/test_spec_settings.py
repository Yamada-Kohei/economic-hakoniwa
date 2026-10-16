"""設定の数字を一覧と差分で出す道具（tools/spec/settings.py）を確かめる。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("settings", REPO / "tools" / "spec" / "settings.py")
settings = importlib.util.module_from_spec(_spec)
sys.modules["settings"] = settings
_spec.loader.exec_module(settings)

SAMPLE = textwrap.dedent(
    """\
    # 見出しの注釈（空行の後なので、どの設定にも付かない）

    stages:
      - upgrade          # 移し替え
      - market           # 市場: 板寄せ
    fx:
      band: 0.02            # 1ターンに動ける幅
                            # （5% では振れすぎた）
      # 上の行の注釈（行の後ろに注釈がないときに使う）
      # 2行目
      keep_turns: 2
      name: "a # b"         # 引用符の中の # は値
    journal:
      privatization:   {type: journal}   # 民営化の売却
    note: |
      key: 文字列の中なので設定ではない
    regimes:
      - id: democracy       # 民主主義
        traits: [vote]
      - id: planned
    """
)


def test_flatten_names_nested_settings():
    flat = settings.flatten(yaml.safe_load(SAMPLE))
    assert flat["stages"] == ["upgrade", "market"]
    assert flat["fx.band"] == 0.02
    assert flat["fx.name"] == "a # b"
    assert flat["journal.privatization.type"] == "journal"
    assert flat["regimes[0].id"] == "democracy"
    assert flat["regimes[0].traits"] == ["vote"]
    assert flat["regimes[1].id"] == "planned"


def test_comments_follow_names_and_continuation_lines():
    notes = settings.comments(SAMPLE)
    assert notes["stages[0]"] == "移し替え"
    assert notes["stages[1]"] == "市場: 板寄せ"
    assert notes["fx.band"] == "1ターンに動ける幅（5% では振れすぎた）"
    assert notes["fx.keep_turns"] == "上の行の注釈（行の後ろに注釈がないときに使う）2行目"
    assert notes["fx.name"] == "引用符の中の # は値"
    assert notes["journal.privatization"] == "民営化の売却"
    assert notes["regimes[0].id"] == "民主主義"
    assert not any(name.startswith("note.") or name == "key" for name in notes)


def test_list_lines_filters_by_prefix_and_shows_items():
    lines = list(settings.list_lines(SAMPLE, ["fx", "stages", "journal"]))
    assert "fx.band = 0.02  # 1ターンに動ける幅（5% では振れすぎた）" in lines
    assert "fx.keep_turns = 2  # 上の行の注釈（行の後ろに注釈がないときに使う）2行目" in lines
    assert "stages:" in lines
    assert "  [1] market  # 市場: 板寄せ" in lines
    assert "journal.privatization.type = journal  # 民営化の売却" in lines
    assert not any(line.startswith("regimes") for line in lines)


def test_markdown_groups_sections_and_marks_repeated_notes():
    text = textwrap.dedent(
        """\
        # 為替（変動相場）。説明の続き
        fx:
          band: 0.02  # 幅
        household:  # 家計
          layers:  # 家計の層
            general: {c1: 0.9}
            owners: {c1: 0.6}
        locale: ja  # 言語
        """
    )
    lines = list(settings.markdown_lines(text, "見本"))
    assert lines[0] == "# 設定の数字（見本）"
    assert "## 為替（変動相場）（`fx`）" in lines
    assert "説明の続き" in lines
    assert "## `household`ほか" in lines
    assert "家計" in lines
    assert "| `household.layers.general.c1` | 0.9 | 家計の層 |" in lines
    assert "| `household.layers.owners.c1` | 0.6 | （同上） |" in lines
    assert "| `locale` | ja | 言語 |" in lines


def test_diff_reports_changed_added_and_removed():
    old = {"fx.band": 0.05, "fx.keep_turns": 2, "old": 1}
    new = {"fx.band": 0.02, "fx.keep_turns": 2, "fx.depth_turns": 12}
    changes = settings.diff(old, new)
    assert changes == [
        ("fx.band", 0.05, 0.02),
        ("old", 1, settings.MISSING),
        ("fx.depth_turns", settings.MISSING, 12),
    ]
    assert settings.diff({"a": 1}, {"a": 1.0}) == [("a", 1, 1.0)]


def test_comment_names_exist_in_game_yaml():
    """本物の game.yaml でも、注釈を付けた名前が設定の名前と合っている（読み違いがない）。"""
    text = (REPO / "game.yaml").read_text(encoding="utf-8")
    flat = settings.flatten(yaml.safe_load(text))
    notes = settings.comments(text)
    assert notes
    for name in notes:
        base = name.split("[")[0] if name.endswith("]") and name.split("[")[0] in flat else name
        assert base in flat or any(n.startswith((f"{name}.", f"{name}[")) for n in flat), name


def test_diff_against_head_runs():
    if subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True).returncode:
        return
    lines = list(settings.diff_lines("HEAD", None))
    assert all(line == "" or line.startswith("## ") or "→" in line for line in lines)
