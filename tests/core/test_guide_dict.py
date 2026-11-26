"""案内係の辞書の生成（tools/build_guide_dict.py）を確かめる。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "build_guide_dict", REPO / "tools" / "build_guide_dict.py"
)
build_guide_dict = importlib.util.module_from_spec(_spec)
sys.modules["build_guide_dict"] = build_guide_dict
_spec.loader.exec_module(build_guide_dict)


def test_dictionary_covers_all_goods():
    documents = build_guide_dict.build_documents(REPO)
    goods_def = yaml.safe_load((REPO / "packs/base/data/goods.yaml").read_text(encoding="utf-8"))
    for gid in goods_def:
        assert f"（{gid}）" in documents["dictionary.md"]


def test_dictionary_covers_scoped_facilities_only():
    documents = build_guide_dict.build_documents(REPO)
    notes = yaml.safe_load((REPO / "tools/guide_notes.yaml").read_text(encoding="utf-8"))
    for fid in notes["facility_scope"]:
        assert f"（{fid}）" in documents["dictionary.md"]
    # 範囲の外の施設（次の枝で広げる分）はまだ出ない
    assert "（shipyard）" not in documents["dictionary.md"]


def test_memos_have_question_answer_basis():
    documents = build_guide_dict.build_documents(REPO)
    memos = yaml.safe_load((REPO / "tools/guide_memos.yaml").read_text(encoding="utf-8"))["memos"]
    assert memos
    for memo in memos:
        assert memo["question"] in documents["memos.md"]
        for basis in memo["basis"]:
            assert (REPO / basis.split()[0]).exists()


def test_generated_documents_are_fresh():
    documents = build_guide_dict.build_documents(REPO)
    for name, text in documents.items():
        path = REPO / "docs/guide" / name
        assert path.exists(), f"docs/guide/{name} がありません（生成してください）"
        assert path.read_text(encoding="utf-8") == text, f"docs/guide/{name} が古くなっています"


def test_unknown_note_id_fails(tmp_path):
    notes = {
        "facility_scope": ["farm"],
        "goods": {"no_such_good": {"note": "x"}},
        "facilities": {},
    }
    memos = {
        "memos": [
            {"id": "m1", "question": "q", "answer": "a", "basis": ["packs/base/data/goods.yaml"]}
        ]
    }
    with pytest.raises(build_guide_dict.GuideError):
        build_guide_dict.validate(REPO, notes, memos)


def test_note_outside_scope_fails():
    notes = {
        "facility_scope": ["farm"],
        "goods": {},
        "facilities": {"shipyard": {"note": "x"}},
    }
    memos = {
        "memos": [
            {"id": "m1", "question": "q", "answer": "a", "basis": ["packs/base/data/goods.yaml"]}
        ]
    }
    with pytest.raises(build_guide_dict.GuideError):
        build_guide_dict.validate(REPO, notes, memos)


def test_duplicate_key_in_notes_fails(tmp_path):
    path = tmp_path / "dup.yaml"
    path.write_text("goods:\n  grain: {note: a}\n  grain: {note: b}\n", encoding="utf-8")
    with pytest.raises(build_guide_dict.GuideError):
        build_guide_dict.load_yaml(path, unique=True)
