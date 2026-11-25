"""会議 #4635（むぎ #4634）: 豊かさの 5 分けごとの、扱える重さ（C）とお金（M）で削った量の内訳（probe11 を読むだけ）。

使い方: python w308_wealthcut_tally.py A=probe11-A-...json ... K2=... --out wealthcut.md

届かない島ターン（印 s・w、u でない）を、同じ種・同じターンの 1 人あたりの実質の産出（gp）の順位で 5 つに分け
（1 = 貧しい 2 割。段は最初に渡した組（ref）の島ターンで決め、同じ島ターンを同じ段で比べる。全部の組で u でない島ターンだけ）、割り振り（_allocate）で扱える重さで削った量（ac）とお金で削った量（am）の
合計と割合を出す。あわせて、扱える重さで削った量が 0 より大きい島ターンの割合（C が先に効いた島ターン）。計画経済の島は除く。
"""

import argparse
import json
import sys
from collections import Counter, defaultdict

SPANS = (("151〜600T", 151, 600), ("601〜900T", 601, 900), ("901〜1200T", 901, 1200))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runs, order = defaultdict(dict), []
    for item in a.files:
        name, path = item.split("=", 1)
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if name not in order:
            order.append(name)
        runs[name][d["seed"]] = d
    out = ["# 豊かさの 5 分けごとの、扱える重さ（C）とお金（M）で削った量（probe11、市場の島の届かない島ターン）", ""]
    out.append("量はトン。段 1 = 同じターンの 1 人あたりの実質の産出が一番低い 2 割（市場の島の中、A の島ターンで決める）。")
    out.append("")
    out.append("| 区間 | 組 | 段 | 届かない島ターン（率） | C で削った量 | M で削った量 | C の割合（量） | C が 0 より大きい島ターン | M が 0 より大きい島ターン |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    ref = order[0]
    seeds = sorted(set.intersection(*(set(runs[g]) for g in order)))
    for title, lo, hi in SPANS:
        rows = {g: defaultdict(Counter) for g in order}
        for s in seeds:
            d0 = runs[ref][s]
            start = d0.get("first_turn", 151)
            by_k = defaultdict(list)
            for i, marks in d0["marks"].items():
                for k, m in enumerate(marks):
                    t = start + k
                    if not lo <= t <= hi or d0["pl"][i][k] != "m" or d0["gp"][i][k] < 0:
                        continue
                    if any(runs[g][s]["marks"][i][k] == "u" for g in order):
                        continue
                    by_k[k].append((d0["gp"][i][k], i))
            for k, lst in by_k.items():
                lst.sort()
                n = len(lst)
                for r, (_, i) in enumerate(lst):
                    q = 1 + r * 5 // n  # 段は ref の島ターンで決める（同じ島ターンを同じ段で比べる）
                    for g in order:
                        d = runs[g][s]
                        c = rows[g][q]
                        c["all"] += 1
                        if d["marks"][i][k] not in "sw":
                            continue
                        ac, am = d["ac"][i][k], d["am"][i][k]
                        c["n"] += 1
                        c["ac"] += ac
                        c["am"] += am
                        c["acp"] += ac > 0
                        c["amp"] += am > 0
        for g in order:
            for q in range(1, 6):
                c = rows[g][q]
                tot = c["ac"] + c["am"]
                out.append(f"| {title} | {g} | {q} | {c['n']}（{c['n'] / max(1, c['all']):.2%}） | {c['ac'] / 1000:,.0f} | "
                           f"{c['am'] / 1000:,.0f} | {c['ac'] / max(1, tot):.1%} | {c['acp'] / max(1, c['n']):.1%} | "
                           f"{c['amp'] / max(1, c['n']):.1%} |")
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
