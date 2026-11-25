"""po63_watch.py（short_zero つき）の記録の集計: 届かないマスを「働き手 0（自給の人 × 働き手の割合が 1 人に満たず、
作れる量が 0 になるマス）」と「それ以外（土地か人手で本当に届かないマス）」に分ける。使い方: python split_sum.py *.jsonl.gz"""

import gzip
import json
import sys

print("| 本 | 入 | 届かないマス（延べ） | うち働き手 0 | それ以外 | それ以外の最初のターン |")
print("|---|---|---|---|---|---|")
for path in sys.argv[1:]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        meta = json.loads(f.readline())
        rows = [json.loads(x) for x in f]
    short = sum(x["short"] for r in rows for x in r["islands"])
    zero = sum(x["short_zero"] for r in rows for x in r["islands"])
    first = next((r["t"] for r in rows if any(x["short"] > x["short_zero"] for x in r["islands"])), None)
    print(f"| {meta['scenario']}-s{meta['seed']}-t{meta['turns']} | {meta['on']} | {short} | {zero} | {short - zero} | {first} |")
