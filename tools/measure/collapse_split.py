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
- 発行国の体制ごと（主の集計）: 崩れごとに、結果の横にある写し
  （<名前>.snap.json.gz、測りが一緒に書く物）から、崩れた島の入っている圏と
  その決済通貨の発行国を引きます。ただし体制を入れるのは、崩れが始まった
  ターン（found の first）の時点で対象圏と発行国を確認できる行だけです
  （圏がそのターンまでに作られていて、1 つに定まること）。確認できない行は
  落とさず「不明」で件数に残します。
- 発行国の体制（写し時点の最大圏）（補助）: 組ごとの列で、写し（最後の
  ターンの状態）から、加盟国のいちばん多い圏の発行国で代表させた物です。
  崩れた圏そのものの体制とは限らないので、主の集計とは混ぜません。

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


def regimes_of(snapshot: dict[str, Any]) -> dict[Any, str]:
    """写しの島ごとの体制（経済の形）。"""
    world = snapshot.get("world") or {}
    return {
        i.get("id"): (i.get("regime") or {}).get("economy", "") for i in world.get("islands") or []
    }


def blocs_of(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """写しの貿易圏の一覧。"""
    world = snapshot.get("world") or {}
    return (world.get("ext") or {}).get("trade", {}).get("blocs") or []


def issuer_of(snapshot: dict[str, Any]) -> tuple[int, str] | None:
    """写しから、決済通貨の発行国とその体制を引く（補助の最大圏の代表）。

    圏が複数あるときは加盟国のいちばん多い圏で代表させる（同数なら番号の小さい圏）。
    圏が無ければ None。
    """
    blocs = blocs_of(snapshot)
    if not blocs:
        return None
    bloc = sorted(blocs, key=lambda b: (-len(b.get("members") or []), b.get("id", 0)))[0]
    issuer = bloc.get("currency")
    regimes = regimes_of(snapshot)
    if issuer not in regimes:
        return None
    return issuer, regimes[issuer]


def largest_bloc_label(snapshot: dict[str, Any] | None) -> str:
    """補助の列の言葉: 発行国の体制（写し時点の最大圏）。

    写しが無い・圏が無いときは、そのわけの言葉にする。
    """
    if snapshot is None:
        return "不明（写しなし）"
    found = issuer_of(snapshot)
    if found is None:
        return "圏なし"
    issuer, regime = found
    return f"{regime}（発行国は島 {issuer}）"


def event_issuer(snapshot: dict[str, Any], event: dict[str, Any]) -> tuple[int, str] | None:
    """崩れ 1 件の対象圏と発行国を、崩れが始まったターン（found の first）時点で確認する。

    対象圏は、崩れた島が入っている圏のうち、first までに作られていた
    （created <= first）物です。1 つに定まるときだけ、その圏の決済通貨の
    発行国と体制を返します。定まらないとき（入っている圏が無い・first の
    後に作られた圏しかない・複数ある・created が無い）と、発行国の体制が
    引けないときは、確認できないので None（=「不明」）にします。
    """
    island = event.get("island")
    first = event.get("first")
    if island is None or first is None:
        return None
    candidates = [
        b
        for b in blocs_of(snapshot)
        if island in (b.get("members") or [])
        and b.get("created") is not None
        and b["created"] <= first
    ]
    if len(candidates) != 1:
        return None
    issuer = candidates[0].get("currency")
    regime = regimes_of(snapshot).get(issuer)
    if not regime:
        return None
    return issuer, regime


def event_label(snapshot: dict[str, Any] | None, event: dict[str, Any]) -> str:
    """主の集計の言葉: 確認できた崩れは発行国の体制、できない崩れは「不明」。"""
    if snapshot is None:
        return "不明"
    found = event_issuer(snapshot, event)
    if found is None:
        return "不明"
    return found[1]


def render(results: list[tuple[Path, dict[str, Any]]], border: int = BORDER) -> str:
    lines = [
        "## 崩れの分け方（240 ターンの前後・発行国の体制ごと）",
        "",
        f"境目は {border} ターン（海外の商人が去るターン。境目のターンは「後」）。",
        "発行国の体制ごとの主の集計は、崩れごとに、崩れた島の入っている圏とその発行国を",
        "崩れが始まったターン（found の first）の時点で確認して分けた。確認できない崩れは",
        "落とさず「不明」で数える。",
        "組ごとの「発行国の体制（写し時点の最大圏）」は補助で、結果の横の写し（最後のターンの",
        "状態）から加盟国のいちばん多い圏の発行国で代表させた物。主の集計とは混ぜていない。",
        "",
        "| 組 | 崩れの合計 | 発行国の体制（写し時点の最大圏） |",
        "|---|---|---|",
    ]
    loaded: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for file, data in results:
        snapshot = load_snapshot(file)
        loaded.append((data, snapshot))
        total = sum(1 for _ in data["collapse"]["found"])
        lines.append(f"| {run_name(data)} | {total} | {largest_bloc_label(snapshot)} |")

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
    for data, snapshot in loaded:
        for event in data["collapse"]["found"]:
            key = (event_label(snapshot, event), event["kind"])
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
