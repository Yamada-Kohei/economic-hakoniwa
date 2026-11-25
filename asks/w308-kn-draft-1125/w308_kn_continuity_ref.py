"""W-308 条件 4 の n の「続いたターン数」での数え直し（参考数。kn の下書きの材料）。

probe12 の記録だけを使う（回し直しはしない）。数え方は w308_kn_retally.py と同じ
（nt=1 かつ印 n、kn があって 1 <= kn <= 3.0 を n とする）。そのうえで、同じ島で
n が連番の位置に続く区間（連続ターン）に分け、区間の長さが L 以上の区間に属する
n ターンを数える。検算として n の合計が #4847（K2 16,422 / WK 28,184）と合うこと。

使い方: python w308_kn_continuity_ref.py K2=probe12-K2-...json,... WK=...
（組ごとに種の本数だけファイルをカンマで並べる）
"""

import json
import sys
from collections import defaultdict

MINLEN = (2, 3, 5, 10)


def cells(d):
    start = d.get("first_turn", 151)
    out = {}
    kn = d.get("kn", {})
    for i, marks in d["marks"].items():
        nt = d["nt"].get(i, "")
        ikn = {int(k): v for k, v in kn.get(i, {}).items()}
        for k, m in enumerate(marks):
            out[(i, k)] = (m, nt[k] if k < len(nt) else "0", ikn.get(k))
    return out


def main(argv):
    for item in argv[1:]:
        name, paths = item.split("=", 1)
        base = 0
        hist = defaultdict(int)
        by_minlen = defaultdict(int)
        islands = set()
        for path in paths.split(","):
            with open(path, encoding="utf-8") as fh:
                per = cells(json.load(fh))
            seed = path.rsplit("/", 1)[-1]
            by_island = defaultdict(list)
            for (i, k), (m, nt, kn) in per.items():
                if nt == "1" and m == "n" and kn is not None and 1 <= kn <= 3.0:
                    by_island[i].append(k)
            for i, ks in by_island.items():
                islands.add((seed, i))
                ks.sort()
                base += len(ks)
                run = 1
                for a, b in zip(ks, ks[1:] + [None]):
                    if b is not None and b == a + 1:
                        run += 1
                    else:
                        hist[run] += 1
                        for L in MINLEN:
                            if run >= L:
                                by_minlen[L] += run
                        run = 1
        print(f"== {name}: n 合計 {base:,} / n のある種x島 {len(islands)}")
        for L in MINLEN:
            print(f"   L>={L}: {by_minlen[L]:,} ({by_minlen[L] / base:.1%})")
        print(f"   区間数 {sum(hist.values()):,}、最長区間 {max(hist)}、長さ1の区間 {hist[1]:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
