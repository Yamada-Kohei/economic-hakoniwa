"""崩れの表を「240 ターンの前と後」と「発行国の体制ごと」に分けて出す道具。

測りの見方には場面の偏りがあります。決済通貨の発行国がたまたま計画経済か
市場経済かで外れの出方が大きく違い、海外の商人が去る 240 ターンの前と後でも
別の世界になります（根7 の 2）。なので崩れの表は、この 2 つの分け方でも
見られるようにします（台帳 P1、根7 の 7-c）。

この道具は、既存の quick/full の集計の出力（hakoniwa_core.measure の結果の
JSON）を読むだけで、計算には触りません。

    python tools/measure/collapse_split.py runs/quick
    python tools/measure/collapse_split.py runs/quick/plan-s1-t300.json
    python tools/measure/collapse_split.py --border 240 runs/full

渡し方は、結果の JSON のファイルでも、そのフォルダでもよいです（フォルダは
中の *.json から集計の結果だけを拾います）。

分け方:
- 240 の前と後: 崩れの初めのターン（found の first）が境目より前か後か。
  境目の既定は 240（海外の商人が去るターン）。境目のターンは「後」に入れます。
- 発行国の体制ごと: 結果の横にある写し（<名前>.snap.json.gz、測りが一緒に
  書く物）から、貿易圏の決済通貨の発行国を引きます。圏が複数あるときは、
  加盟国のいちばん多い圏の発行国で代表させます。写しは最後のターンの状態
  なので、途中で圏の組み替えがあった世界では最後の姿で分けることと、
  写しの無い結果は「不明」に分けることを、表にも書きます。

試験は tests/core/test_collapse_split.py。
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Any

BORDER = 240  # 海外の商人が去るターン（根7 の 2）


def is_result(data: Any) -> bool:
    """集計の結果の JSON か（崩れの見張りと組の名前を持つか）。"""
    return (
        isinstance(data, dict)
        and isinstance(data.get("collapse"), dict)
        and "scenario" in data
        and "seed" in data
    )


def load_results(paths: list[Path]) -> list[tuple[Path, dict[str, Any]]]:
    """ファイルかフォルダの渡し方から、集計の結果を（置き場所, 中身）で集める。"""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files += sorted(path.glob("*.json"))
        else:
            files.append(path)
    results = []
    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if is_result(data):
            results.append((file, data))
    return results


def run_name(data: dict[str, Any]) -> str:
    return f"{data['scenario']}-s{data['seed']}-t{data['turns']}"


def split_by_border(
    found: list[dict[str, Any]], border: int = BORDER
) -> dict[str, tuple[int, int]]:
    """崩れを種類ごとに、境目より前（first < border）と後（first >= border）に分ける。"""
    out: dict[str, list[int]] = {}
    for event in found:
        pair = out.setdefault(event["kind"], [0, 0])
        pair[0 if event["first"] < border else 1] += 1
    return {kind: (pair[0], pair[1]) for kind, pair in out.items()}


def snapshot_path(result_file: Path) -> Path:
    """結果の横にある写しの置き場所（測りが <名前>.snap.json.gz で書く）。"""
    return result_file.with_name(result_file.name[: -len(".json")] + ".snap.json.gz")


def load_snapshot(result_file: Path) -> dict[str, Any] | None:
    path = snapshot_path(result_file)
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def issuer_of(snapshot: dict[str, Any]) -> tuple[int, str] | None:
    """写しから、決済通貨の発行国とその体制を引く。

    圏が複数あるときは加盟国のいちばん多い圏で代表させる（同数なら番号の小さい圏）。
    圏が無ければ None。
    """
    world = snapshot.get("world") or {}
    blocs = (world.get("ext") or {}).get("trade", {}).get("blocs") or []
    if not blocs:
        return None
    bloc = sorted(blocs, key=lambda b: (-len(b.get("members") or []), b.get("id", 0)))[0]
    issuer = bloc.get("currency")
    regimes = {
        i.get("id"): (i.get("regime") or {}).get("economy", "") for i in world.get("islands") or []
    }
    if issuer not in regimes:
        return None
    return issuer, regimes[issuer]


def issuer_label(result_file: Path) -> str:
    """結果の発行国の体制（写しが無い・圏が無いときは、そのわけの言葉）。"""
    snapshot = load_snapshot(result_file)
    if snapshot is None:
        return "不明（写しなし）"
    found = issuer_of(snapshot)
    if found is None:
        return "圏なし"
    issuer, regime = found
    return f"{regime}（発行国は島 {issuer}）"


def render(results: list[tuple[Path, dict[str, Any]]], border: int = BORDER) -> str:
    lines = [
        "## 崩れの分け方（240 ターンの前後・発行国の体制ごと）",
        "",
        f"境目は {border} ターン（海外の商人が去るターン。境目のターンは「後」）。",
        "発行国の体制は、結果の横の写し（最後のターンの状態）から引いた。",
        "写しの無い結果は「不明」に分け、圏が複数ある世界は加盟国のいちばん多い圏の",
        "発行国で代表させた。",
        "",
        "| 組 | 崩れの合計 | 発行国の体制 |",
        "|---|---|---|",
    ]
    labels: dict[str, str] = {}
    for file, data in results:
        label = issuer_label(file)
        labels[run_name(data)] = label
        total = sum(1 for _ in data["collapse"]["found"])
        lines.append(f"| {run_name(data)} | {total} | {label} |")

    lines += [
        "",
        f"### {border} ターンの前と後（種類ごと・全組の合計）",
        "",
        "| 種類 | 前 | 後 | 合計 |",
        "|---|---|---|---|",
    ]
    totals: dict[str, tuple[int, int]] = {}
    for _file, data in results:
        for kind, (before, after) in split_by_border(data["collapse"]["found"], border).items():
            old = totals.get(kind, (0, 0))
            totals[kind] = (old[0] + before, old[1] + after)
    for kind in sorted(totals):
        before, after = totals[kind]
        lines.append(f"| {kind} | {before} | {after} | {before + after} |")

    lines += [
        "",
        "### 発行国の体制ごと（種類ごと）",
        "",
        "| 発行国の体制 | 種類 | 件数 |",
        "|---|---|---|",
    ]
    by_label: dict[tuple[str, str], int] = {}
    for _file, data in results:
        label = labels[run_name(data)]
        for event in data["collapse"]["found"]:
            key = (label, event["kind"])
            by_label[key] = by_label.get(key, 0) + 1
    for (label, kind), count in sorted(by_label.items()):
        lines.append(f"| {label} | {kind} | {count} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="崩れの表を 240 ターンの前後と発行国の体制ごとに分ける"
    )
    parser.add_argument("paths", nargs="+", help="集計の結果の JSON か、そのフォルダ")
    parser.add_argument("--border", type=int, default=BORDER, help="前と後の境目のターン")
    args = parser.parse_args(argv)

    results = load_results([Path(p) for p in args.paths])
    if not results:
        print("集計の結果の JSON が見つかりませんでした", file=sys.stderr)
        return 1
    print(render(results, args.border))
    return 0


if __name__ == "__main__":
    sys.exit(main())
