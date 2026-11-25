"""po63_watch.py の記録の集計（読むだけ）。使い方: python po63_watch_sum.py *.jsonl.gz

出す物: 本ごとに、作れる量が食べる量に届かない村のマスの延べと、それが出たターン数・最初のターン、
作れる量 ÷ 食べる量（世界の計）の 50 ターンごとの平均とマスごとの比の最小、下がり続けているか
（50 ターンごとの平均が前の窓より下がった窓の数）。
"""

import gzip
import json
import sys

print("| 本 | 入 | 村のマス（延べ） | 届かないマス（延べ） | 出たターン数 | 最初 | マスに書かれた値で届かない（延べ） | 50 ターンごとの比（世界の計） | 比の最小 | 下がった窓 |")
print("|---|---|---|---|---|---|---|---|---|---|")
for path in sys.argv[1:]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        meta = json.loads(f.readline())
        rows = [json.loads(x) for x in f]
    tiles = short = short_ext = turns_short = 0
    first = None
    mins = []
    windows = {}
    for r in rows:
        s = sum(x["short"] for x in r["islands"])
        tiles += sum(x["tiles"] for x in r["islands"])
        short += s
        short_ext += sum(x["short_ext"] for x in r["islands"])
        if s:
            turns_short += 1
            first = first if first is not None else r["t"]
        cap = sum(x["cap"] for x in r["islands"])
        need = sum(x["need"] for x in r["islands"])
        if need:
            windows.setdefault((r["t"] - 1) // 50, []).append(cap / need)
        mins += [x["min"] for x in r["islands"] if x["min"] is not None]
    avg = [sum(v) / len(v) for _, v in sorted(windows.items())]
    down = sum(1 for p, q in zip(avg, avg[1:]) if q < p)
    name = f"{meta['scenario']}-s{meta['seed']}-t{meta['turns']}"
    print(f"| {name} | {meta['on']} | {tiles} | {short} | {turns_short} | {first} | {short_ext} | "
          f"{' '.join(f'{v:.2f}' for v in avg)} | {min(mins) if mins else '-':.3g} | {down}/{max(0, len(avg) - 1)} |")
