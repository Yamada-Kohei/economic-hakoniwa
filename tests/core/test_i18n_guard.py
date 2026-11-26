"""直書き文言の歯止め（tools/spec/count_hardcoded_strings.py）を確かめる。

多言語対応の決まり（#5524・#6109）: 新しい文言は多言語の形で書く。
この試験は、直書きの日本語文言がベースライン（系統別・ファイル別）より
増えたら落ちる。直書きを減らしても落ちない。
ベースラインの作り直しは tools/spec/count_hardcoded_strings.py --write-baseline
（意図した増加のときだけ行い、理由を PR に書く）。
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "count_hardcoded_strings", REPO / "tools" / "spec" / "count_hardcoded_strings.py"
)
guard = importlib.util.module_from_spec(_spec)
sys.modules["count_hardcoded_strings"] = guard
_spec.loader.exec_module(guard)


@pytest.fixture(scope="module")
def counts() -> dict[str, dict[str, int]]:
    return guard.count_all(REPO)


def test_hardcoded_strings_do_not_exceed_baseline(counts):
    """直書き文言が、系統別・ファイル別のベースラインを超えていない。"""
    baseline = guard.load_baseline()
    excess = guard.find_excess(counts, baseline)
    assert excess == [], (
        "直書きの文言が増えています（系統, ファイル, ベースライン → 現在）: "
        f"{excess}。新しい文言は多言語の形で書くか、意図した増加なら "
        "tools/spec/count_hardcoded_strings.py --write-baseline で"
        "ベースラインを更新し、理由を PR に書いてください。"
    )


def test_baseline_totals_match_files():
    """ベースラインの合計と内訳が、ファイル別の件数と合っている。"""
    baseline = guard.load_baseline()
    assert baseline["totals"] == guard.totals(baseline["files"])
    assert baseline["areas"] == guard.areas(baseline["files"])


def test_count_python_ignores_docstrings_and_counts_fstrings_once():
    source = textwrap.dedent(
        '''\
        """モジュールの説明。"""


        def greet(name):
            """関数の説明。"""
            label = "こんにちは"
            return f"{name}さん、{label}です"
        '''
    )
    # 定数1件（label）＋ f文字列1件。docstring 2件と f文字列の断片は数えない。
    assert guard.count_python_text(source) == 2


def test_count_python_ignores_non_japanese_and_comments():
    source = textwrap.dedent(
        """\
        # コメントの日本語は数えない
        KEY = "island_name"
        FORMAT = "{name} ({turn})"
        """
    )
    assert guard.count_python_text(source) == 0


def test_count_js_strings_templates_and_regex():
    source = textwrap.dedent(
        """\
        // コメントの日本語は数えない
        const a = "閉じる";
        const b = `残り ${n} ターン`;
        const esc = "a\\"b"; // 引用符のエスケープ
        const re = /[&<>"']/g; // 正規表現の中の引用符は文字列にしない
        const plain = "close";
        """
    )
    # 通常の文字列1件＋テンプレート1件。
    assert guard.count_js_text(source) == 2


def test_count_template_plain_text_only():
    source = textwrap.dedent(
        """\
        {% translate "分離済みの文" %}
        {% blocktranslate %}分離済みの組み立て{% endblocktranslate %}
        {# コメントの日本語 #}
        <p>地の文です</p>
        <p>{{ name }} の島</p>
        """
    )
    # 地の文2件（「地の文です」と「の島」）。分離済みとコメントは数えない。
    assert guard.count_template_text(source) == 2
