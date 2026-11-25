"""W-307 import_first の比べの集計（読むだけ）: offtake_probe_first.py の記録から、石炭で止まった
施設の数を、全部の島／輸入に頼る島／作る施設のある島に分けて出す（会議 #4759 の確かめ）。

止まりは probe の short（石炭が手に入らずに止まった・絞った施設の数）。島の振り分けは、その
ターンの石炭を作る建った施設の数 mn で見る（mn = 0 が輸入に頼る島、mn > 0 が作る施設のある島。
実装 trade.offtake.import_first の判定と同じ物差し）。値は期間内の 1 ターン平均（島の合計）。

    py -3.12 -X utf8 w307_first_tally.py w307if-probe-off-s1.jsonl.gz w307if-probe-on-s1.jsonl.gz
"""

from __future__ import annotations

import gzip
import json
import sys

WINDOWS = ((51, 150), (151, 300), (301, 500))
CLASSES = (
    ("all", "止まりの計（全部の島）"),
    ("dep", "輸入に頼る島の止まり（作る施設なし）"),
    ("maker", "作る施設のある島の止まり"),
)


def _open(path: str):
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")


def _window(rows: list[dict], lo: int, hi: int) -> dict[str, float]:
    w = [r for r in rows if lo <= r["t"] <= hi]
    n = max(1, len(w))
    tot = dict.fromkeys((k for k, _ in CLASSES), 0.0)
    for r in w:
        for v in r["i"].values():
            short = v.get("short", 0)
            tot["all"] += short
            tot["maker" if v.get("mn", 0) > 0 else "dep"] += short
    return {k: tot[k] / n for k in tot}


def main() -> int:
    paths = sys.argv[1:]
    if not paths:
        print(__doc__)
        return 1
    data = {p: [json.loads(line) for line in _open(p)] for p in paths}
    print("| 期間 | 物 | " + " | ".join(paths) + " |")
    print("|---|---|" + "---|" * len(paths))
    for lo, hi in WINDOWS:
        ws = {p: _window(rows, lo, hi) for p, rows in data.items()}
        for k, name in CLASSES:
            print(f"| {lo}〜{hi} | {name} | " + " | ".join(f"{ws[p][k]:.2f}" for p in paths) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
