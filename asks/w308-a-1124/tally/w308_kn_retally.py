"""W-308 条件 4 の判定し直し: probe12 の記録だけで、物差し kn の上限を締めたとき
n がどう変わるか（掲示板 #5061 の頼み 3、#4847 の続き。読むだけ。回し直しはしない）。

使い方: python w308_kn_retally.py K2=probe12-K2-...json WK=probe12-WK-...json --out kn.md
（組ごとに種の本数だけファイルを並べる。A・W も同じ形で数えられる）

数え方（#4847 の集計と同じ）:
- 対象は条件 4 の島ターン（記録の ``nt`` が 1）で印が ``n`` の物。
- ``kn`` は probe12 が GRID（0.25〜3.0 の段）の中から「標準の見積もり運賃のその倍まで
  引き合う財があった」一番大きい倍を、印 n の島ターンだけに残した物（probe5）。
  記録の kn は上限で切られた値ではなく GRID の段そのものなので、上限を 1.5・2.0 に
  締めた数え直しが記録だけでできる（#5067 の確認への答え: 判定時の kn 相当値は
  残っている）。
- 集計の n（上限 3.0）= kn があって 1 <= kn <= 3.0 の数。#4847 の集計
  （K2 16,422 / WK 28,184）と一致するはずで、検算として先に出す。
- 締めた n（上限）= 1 <= kn <= 上限 の数。上限は 1.5 と 2.0。
- 参考: kn の記録が無い・kn < 1 の n（nt=1 かつ印 n の全数との差）も出す。
区間は #4847 と同じ 151〜600 / 601〜900 / 901〜1200（と全部）。ターンは
first_turn + marks の位置。
"""

import argparse
import json
import sys
from collections import defaultdict

SPANS = (("151〜600T", 151, 600), ("601〜900T", 601, 900), ("901〜1200T", 901, 1200), ("全部", 151, 1200))
CAPS = (1.5, 2.0, 3.0)


def load(items):
    runs, order = defaultdict(dict), []
    for item in items:
        name, path = item.split("=", 1)
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if "kn" not in d or "nt" not in d:
            print(f"{path}: probe12 の結果ではない（kn・nt が無い。飛ばす）", file=sys.stderr)
            continue
        if name not in order:
            order.append(name)
        runs[name][d["seed"]] = d
    return runs, order


def cells(d):
    """(島, 位置) -> (ターン, 印, nt, kn)。kn が無い印 n は None。"""
    start = d.get("first_turn", 151)
    out = {}
    kn = d.get("kn", {})
    for i, marks in d["marks"].items():
        nt = d["nt"].get(i, "")
        island_kn = {int(k): v for k, v in kn.get(i, {}).items()}
        for k, m in enumerate(marks):
            out[(i, k)] = (start + k, m, nt[k] if k < len(nt) else "0", island_kn.get(k))
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runs, order = load(a.files)
    seeds = sorted(set.intersection(*(set(runs[g]) for g in order))) if order else []
    per = {g: {s: cells(runs[g][s]) for s in seeds} for g in order}

    out = ["# W-308 条件 4 の n の判定し直し（probe12 の記録だけ。kn の上限を締める）", ""]
    out.append(f"組: {', '.join(order)}。種: {', '.join(map(str, seeds))}（plan）。")
    out.append("対象は条件 4 の島ターン（nt=1）で印 n の物。n（上限）= kn があって 1 <= kn <= 上限の数。")
    out.append("")
    out.append("| 区間 | 組 | nt=1 かつ印 n（全数） | kn なし・kn<1 | n（上限 3.0、今の集計） | n（上限 2.0） | n（上限 1.5） |")
    out.append("|---|---|---|---|---|---|---|")
    for title, lo, hi in SPANS:
        for g in order:
            total = no_kn = 0
            counts = {cap: 0 for cap in CAPS}
            for s in seeds:
                for (_i, _k), (t, m, nt, kn) in per[g][s].items():
                    if not lo <= t <= hi or nt != "1" or m != "n":
                        continue
                    total += 1
                    if kn is None or kn < 1:
                        no_kn += 1
                        continue
                    for cap in CAPS:
                        if kn <= cap:
                            counts[cap] += 1
            out.append(
                f"| {title} | {g} | {total:,} | {no_kn:,} | {counts[3.0]:,} | {counts[2.0]:,} | {counts[1.5]:,} |"
            )
    out.append("")
    out.append("検算: 上限 3.0 の列が #4847 の集計（K2 16,422 / WK 28,184、区間「全部」）と合えば、")
    out.append("記録の kn で数え直せていることの裏付けになる。")
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
