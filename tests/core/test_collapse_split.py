"""崩れの表を分ける道具（tools/measure/collapse_split.py）の自己確認。

分け方（240 の前後・発行国の体制）を、小さな材料で確かめる。材料の写しは、
測りの写しと同じ形（world の ext に圏、islands に体制）で作る。
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "collapse_split", REPO / "tools" / "measure" / "collapse_split.py"
)
collapse_split = importlib.util.module_from_spec(_spec)
sys.modules["collapse_split"] = collapse_split
_spec.loader.exec_module(collapse_split)


def _found(kind: str, first: int, island: int = 1) -> dict:
    return {"kind": kind, "island": island, "first": first, "turns": 1, "what": [], "line": ""}


def test_split_by_border_counts_first_turns():
    found = [
        _found("stockout", 100),
        _found("stockout", 239),
        _found("stockout", 240),
        _found("no_trade", 300),
    ]
    assert collapse_split.split_by_border(found, 240) == {
        "stockout": (2, 1),
        "no_trade": (0, 1),
    }


def _snapshot(blocs: list[dict], regimes: dict[int, str]) -> dict:
    return {
        "world": {
            "ext": {"trade": {"blocs": blocs}},
            "islands": [
                {"id": iid, "regime": {"economy": regime}} for iid, regime in regimes.items()
            ],
        }
    }


def test_issuer_of_picks_the_largest_bloc():
    snapshot = _snapshot(
        [
            {"id": 2, "currency": 5, "members": [5, 6]},
            {"id": 1, "currency": 3, "members": [3, 4, 7]},
        ],
        {3: "planned", 5: "market"},
    )
    assert collapse_split.issuer_of(snapshot) == (3, "planned")


def test_issuer_of_without_blocs_is_none():
    assert collapse_split.issuer_of(_snapshot([], {1: "market"})) is None


def test_labels_and_render_with_files(tmp_path):
    result = {
        "scenario": "plan",
        "seed": 1,
        "turns": 300,
        "collapse": {"counts": {}, "found": [_found("stockout", 100), _found("stockout", 250)]},
    }
    file = tmp_path / "plan-s1-t300.json"
    file.write_text(json.dumps(result), encoding="utf-8")
    assert collapse_split.issuer_label(file) == "不明（写しなし）"

    snapshot = _snapshot(
        [{"id": 1, "currency": 2, "members": [2, 3]}],
        {2: "market", 3: "planned"},
    )
    with gzip.open(collapse_split.snapshot_path(file), "wt", encoding="utf-8") as f:
        json.dump(snapshot, f)
    assert collapse_split.issuer_label(file) == "market（発行国は島 2）"

    text = collapse_split.render([(file, result)], 240)
    assert "| plan-s1-t300 | 2 | market（発行国は島 2） |" in text
    assert "| stockout | 1 | 1 | 2 |" in text
    assert "| market（発行国は島 2） | stockout | 2 |" in text


def test_load_results_skips_other_json(tmp_path):
    (tmp_path / "plan-s1-t300.json").write_text(
        json.dumps({"scenario": "plan", "seed": 1, "turns": 300, "collapse": {"found": []}}),
        encoding="utf-8",
    )
    (tmp_path / "unrelated.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    results = collapse_split.load_results([tmp_path])
    assert [collapse_split.run_name(data) for _f, data in results] == ["plan-s1-t300"]
