"""崩れの表を分ける道具（tools/measure/collapse_split.py）の自己確認。

分け方（240 の前後・発行国の体制）を、小さな材料で確かめる。材料の写しは、
測りの写しと同じ形（world の ext に圏（created つき）、islands に体制）で作る。
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
    label = collapse_split.largest_bloc_label(collapse_split.load_snapshot(file))
    assert label == "不明（写しなし）"

    snapshot = _snapshot(
        [{"id": 1, "currency": 2, "members": [1, 2], "created": 10}],
        {2: "market", 3: "planned"},
    )
    with gzip.open(collapse_split.snapshot_path(file), "wt", encoding="utf-8") as f:
        json.dump(snapshot, f)
    label = collapse_split.largest_bloc_label(collapse_split.load_snapshot(file))
    assert label == "market（発行国は島 2）"

    text = collapse_split.render([(file, result)], 240)
    assert "| 組 | 崩れの合計 | 発行国の体制（写し時点の最大圏） |" in text
    assert "| plan-s1-t300 | 2 | market（発行国は島 2） |" in text
    assert "| stockout | 1 | 1 | 2 |" in text
    # 主の集計は、崩れた島 1 の圏（10 ターン目に作成）の発行国 2 の体制で数える
    assert "| market | stockout | 2 |" in text


def test_event_issuer_confirms_bloc_at_first_turn():
    snapshot = _snapshot(
        [
            {"id": 1, "currency": 2, "members": [1, 2], "created": 10},
            {"id": 2, "currency": 3, "members": [1, 3], "created": 200},
        ],
        {2: "market", 3: "planned"},
    )
    # 100 ターン目では、島 1 が入っている圏は圏 1 だけ（圏 2 はまだ無い）
    assert collapse_split.event_issuer(snapshot, _found("stockout", 100, island=1)) == (
        2,
        "market",
    )
    # 250 ターン目では 2 つの圏に入っていて、対象圏が 1 つに定まらない
    assert collapse_split.event_issuer(snapshot, _found("stockout", 250, island=1)) is None
    # 島 3 の圏 2 は 100 ターン目にはまだ作られていない
    assert collapse_split.event_issuer(snapshot, _found("stockout", 100, island=3)) is None
    assert collapse_split.event_issuer(snapshot, _found("stockout", 250, island=3)) == (
        3,
        "planned",
    )
    # どの圏にも入っていない島は確認できない
    assert collapse_split.event_issuer(snapshot, _found("stockout", 250, island=9)) is None


def test_event_issuer_without_created_is_unknown():
    snapshot = _snapshot(
        [{"id": 1, "currency": 2, "members": [1, 2]}],
        {2: "market"},
    )
    assert collapse_split.event_issuer(snapshot, _found("stockout", 100, island=1)) is None


def test_render_keeps_unconfirmed_events_as_unknown(tmp_path):
    confirmed = {
        "scenario": "plan",
        "seed": 1,
        "turns": 300,
        "collapse": {
            "counts": {},
            "found": [_found("stockout", 100, island=1), _found("stockout", 250, island=9)],
        },
    }
    file = tmp_path / "plan-s1-t300.json"
    file.write_text(json.dumps(confirmed), encoding="utf-8")
    snapshot = _snapshot(
        [{"id": 1, "currency": 2, "members": [1, 2], "created": 10}],
        {2: "market"},
    )
    with gzip.open(collapse_split.snapshot_path(file), "wt", encoding="utf-8") as f:
        json.dump(snapshot, f)

    no_snapshot = {
        "scenario": "plan",
        "seed": 2,
        "turns": 300,
        "collapse": {"counts": {}, "found": [_found("stockout", 100, island=1)]},
    }
    file2 = tmp_path / "plan-s2-t300.json"
    file2.write_text(json.dumps(no_snapshot), encoding="utf-8")

    text = collapse_split.render([(file, confirmed), (file2, no_snapshot)], 240)
    # 確認できた 1 件は体制で、確認できない 2 件は落とさず「不明」で数える
    assert "| market | stockout | 1 |" in text
    assert "| 不明 | stockout | 2 |" in text


def test_load_results_skips_other_json(tmp_path):
    (tmp_path / "plan-s1-t300.json").write_text(
        json.dumps({"scenario": "plan", "seed": 1, "turns": 300, "collapse": {"found": []}}),
        encoding="utf-8",
    )
    (tmp_path / "unrelated.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    results = collapse_split.load_results([tmp_path])
    assert [collapse_split.run_name(data) for _f, data in results] == ["plan-s1-t300"]
