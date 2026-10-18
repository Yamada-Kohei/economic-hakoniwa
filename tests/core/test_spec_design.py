"""設計書の索引の道具（tools/spec/design_index.py）と、docs/design/ の決まりの一覧を確かめる。"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "design_index", REPO / "tools" / "spec" / "design_index.py"
)
design_index = importlib.util.module_from_spec(_spec)
sys.modules["design_index"] = design_index
_spec.loader.exec_module(design_index)


def test_design_docs_match_index_and_have_no_errors():
    """索引と本文がずれていない。番号が重ならず、頭が合い、試験と設定の名前が実在する。"""
    tabs = design_index.load_tabs()
    assert len(tabs) >= 6
    assert design_index.problems(tabs) == []


SAMPLE = textwrap.dedent(
    """\
    # 3 物と市場と値段

    範囲の説明。

    ## 決まりの一覧

    | 番号 | 決まり（一行） | 状態 | 設定の名前 | 試験 |
    | --- | --- | --- | --- | --- |
    | MK-01 | 板寄せで決める。続きの文 | 決定 | `market.band` | tests/core/test_spec_design.py |
    | MK-02 | 値幅まで | 仮 | `market.no_such_name` | tests/core/test_spec_design.py::test_nothing |
    | OV-03 | 頭が違う | 決定 |  | tests/core/no_such_file.py |
    | MK-01 | 重なり | 決定 |  |  |

    ### 仮の決まり

    | 番号 | 何の代わりか | いつ外すか |
    | --- | --- | --- |
    | MK-02 | 本来の仕組み | 回7 |
    | MK-09 | 一覧にない | 未定 |

    ## 仕組み

    | MK-99 | 本文の表は決まりの一覧に入れない | | | |
    """
)


def test_problems_finds_each_kind_of_error(tmp_path):
    (tmp_path / "03-market.md").write_text(SAMPLE, encoding="utf-8")
    tabs = design_index.load_tabs(tmp_path)
    assert [r.number for r in tabs[0].rules] == ["MK-01", "MK-02", "OV-03", "MK-01"]
    assert [row[0] for row in tabs[0].provisional] == ["MK-02", "MK-09"]
    found = "\n".join(design_index.problems(tabs, check_index=False))
    assert "OV-03 の頭が MK でない" in found
    assert "MK-01 が重なっている" in found
    assert "no_such_file.py がない" in found
    assert "test_spec_design.py::test_nothing がない" in found
    assert "market.no_such_name が game.yaml にない" in found
    assert "market.band" not in found
    assert "仮の決まりの MK-09 が決まりの一覧にない" in found
    assert "MK-99" not in found


def test_index_shows_first_sentence_and_marks_provisional(tmp_path):
    (tmp_path / "03-market.md").write_text(SAMPLE, encoding="utf-8")
    text = design_index.render_index(design_index.load_tabs(tmp_path))
    assert "## [3 物と市場と値段](03-market.md)" in text
    assert "| MK-01 | 板寄せで決める | 決定 | tests/core/test_spec_design.py |" in text
    assert "| MK-02 | 値幅まで | 仮（仮の決まりの表あり） |" in text
    assert "続きの文" not in text
