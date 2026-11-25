"""W-308 海運再参入の集計（会議 #5526・#5475。数のみ。判定は会議でする）。

使い方: python w308_reentry_tally.py <出力フォルダ> [<出力フォルダ> ...]
フォルダ内の reentry-<組>-plan-s<種>-t1200.json（組 = Aon・Aoff・Won・Woff・
K2on・K2off・WKon・WKoff、種 17〜22）を読み、再参入の線（#5475 の (1)〜(4)）を
読むための表を出す。(5)（quick の崩れ）は従来の quick 集計で別に見る。

表 R1（倒産と復旧）: 組ごとに、151 ターン開始時の全島を分母に、初回倒産
（facility_bankrupt の判定）の島数とそのターンの中央値、900／1200 までの累積の
割合、復旧（倒産判定の後に地元の海運が初めて荷を運ぶ）までのターンの中央値、
900／1200 時点で「直近の倒産の後にまだ一度も荷を運んでいない島」の割合（#5467）。
表 R2（倒産から 100 ターンの窓。#5480）: 初回倒産から 100 ターン追える島について、
運ぶ注文があったターン数（地元の運んだ量 > 0、または船がなくて運べなかった荷 > 0、
または他国・借りの船の運賃 > 0）と、そのうち地元の海運の運んだ量が 0 だった
ターン数。造船所のある島／無い島（倒産のターンの有無）にも分ける（#5484）。
表 R3（移管の台帳）: 組ごとに shipping_transfers の件数と買い手の内訳、
0 の引き継ぎでは移管のターンから買い手のもとで初めて荷を運ぶまでのターン。
表 R4（運んだ量。(4) の裏付け）: 組ごとに会社 × ターン数、運んだ量の合計と
1 会社 × ターンの中央値。
"""

from __future__ import annotations

import glob
import json
import statistics as st
import sys
from collections import Counter, defaultdict

LABELS = ("Aoff", "Aon", "Woff", "Won", "K2off", "K2on", "WKoff", "WKon")
START = 151
END = 1200


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 2) if xs else None


def load(folders):
    groups = {}
    for folder in folders:
        for path in sorted(glob.glob(f"{folder}/reentry-*.json")):
            name = path.rsplit("reentry-", 1)[1]
            label = name.split("-plan-")[0]
            with open(path, encoding="utf-8") as fh:
                groups.setdefault(label, []).append(json.load(fh))
    return groups


def first_bankruptcies(run):
    """島 -> 初回倒産の判定ターン（151 以降の facility_bankrupt の最初）。"""
    out = {}
    for f in run["failures"]:
        if f["kind"] != "facility_bankrupt" or f["t"] < START:
            continue
        i = f["island"]
        if i not in out or f["t"] < out[i]:
            out[i] = f["t"]
    return out


def bankruptcies(run):
    """島 -> 倒産の判定ターンの並び（151 以降）。"""
    out = defaultdict(list)
    for f in run["failures"]:
        if f["kind"] == "facility_bankrupt" and f["t"] >= START:
            out[f["island"]].append(f["t"])
    return out


def carried_at(run, island, t):
    """そのターンに地元の海運が運んだ量（panel の carried_last。会社が無ければ 0）。"""
    pos = t - run["first_turn"]
    rows = run["panel"].get(str(island), [])
    if pos < 0 or pos >= len(rows) or rows[pos] is None:
        return 0
    ix = run["panel_cols"].index("carried_last")
    return rows[pos][ix]


def first_carry_after(run, island, tb, upto=END):
    """tb の後に初めて荷を運んだターン（無ければ None）。"""
    for t in range(tb + 1, upto + 1):
        if carried_at(run, island, t) > 0:
            return t
    return None


def table_r1(groups):
    print("## R1. 倒産と復旧（分母は 151 ターン開始時の全島）\n")
    print("| 組 | 島数 | 初回倒産の島 | 倒産ターン中央値 | 累積〜900 | 累積〜1200 | 復旧の中央値 | 未復旧の割合@900 | 未復旧の割合@1200 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for label in LABELS:
        runs = groups.get(label, [])
        if not runs:
            continue
        islands_total = 0
        firsts = []  # (run, island, tb)
        unrec900 = 0
        unrec1200 = 0
        by900 = 0
        by1200 = 0
        recov = []
        for run in runs:
            islands_total += len(run["panel"])
            fb = first_bankruptcies(run)
            allb = bankruptcies(run)
            for i, tb in fb.items():
                firsts.append(tb)
                if tb <= 900:
                    by900 += 1
                if tb <= END:
                    by1200 += 1
                rc = first_carry_after(run, i, tb)
                if rc is not None:
                    recov.append(rc - tb)
            for i, tbs in allb.items():
                for upto, slot in ((900, "a"), (1200, "b")):
                    past = [tb for tb in tbs if tb <= upto]
                    if not past:
                        continue
                    tb = max(past)
                    if first_carry_after(run, i, tb, upto) is None:
                        if slot == "a":
                            unrec900 += 1
                        else:
                            unrec1200 += 1
        n = islands_total or 1
        print(
            f"| {label} | {islands_total} | {len(firsts)} | {med(firsts)} | "
            f"{round(by900 / n * 100, 1)}% | {round(by1200 / n * 100, 1)}% | {med(recov)} | "
            f"{round(unrec900 / n * 100, 1)}% | {round(unrec1200 / n * 100, 1)}% |"
        )
    print()


def series_at(run, island, t):
    pos = t - run["first_turn"]
    rows = run["island_series"].get(str(island), [])
    if pos < 0 or pos >= len(rows):
        return None
    return dict(zip(run["island_series_cols"], rows[pos]))


def table_r2(groups):
    print("## R2. 倒産から 100 ターンの窓（注文があったターンと、地元の運んだ量が 0 のターン。#5480）\n")
    print("| 組 | 造船所 | 追えた島 | 注文ターン | うち地元 0 | 地元 0 の割合 |")
    print("|---|---|---|---|---|---|")
    for label in LABELS:
        runs = groups.get(label, [])
        if not runs:
            continue
        agg = {True: [0, 0, 0], False: [0, 0, 0]}  # 造船所あり/なし -> [島数, 注文, 地元0]
        for run in runs:
            for i, tb in first_bankruptcies(run).items():
                if tb + 100 > END:
                    continue
                at = series_at(run, i, tb)
                if at is None:
                    continue
                slot = agg[bool(at["shipyard"])]
                slot[0] += 1
                for t in range(tb + 1, tb + 101):
                    s = series_at(run, i, t)
                    if s is None:
                        continue
                    demand = (
                        s["carried_local"] > 0
                        or (s["short_load"] is not None and s["short_load"] > 0)
                        or s["freight_paid"] > 0
                    )
                    if demand:
                        slot[1] += 1
                        if s["carried_local"] == 0:
                            slot[2] += 1
        for yard, name in ((True, "あり"), (False, "なし")):
            isl, orders, zero = agg[yard]
            share = round(zero / orders * 100, 1) if orders else None
            print(f"| {label} | {name} | {isl} | {orders} | {zero} | {share}% |")
        isl = agg[True][0] + agg[False][0]
        orders = agg[True][1] + agg[False][1]
        zero = agg[True][2] + agg[False][2]
        share = round(zero / orders * 100, 1) if orders else None
        print(f"| {label} | 計 | {isl} | {orders} | {zero} | {share}% |")
    print()


def table_r3(groups):
    print("## R3. 移管の台帳（shipping_transfers）\n")
    print("| 組 | 移管の件数 | 買い手の内訳 | 引き継ぎ→初輸送の中央値 |")
    print("|---|---|---|---|")
    for label in LABELS:
        runs = groups.get(label, [])
        if not runs:
            continue
        total = 0
        buyers = Counter()
        gaps = []
        for run in runs:
            for i, entries in run["transfers"].items():
                for e in entries:
                    total += 1
                    buyers[e["buyer"]] += 1
                    # 引き継いだ施設の番号のまま、買い手のもとで初めて運ぶまで
                    fid = e["facility"]
                    ids = run["panel_ids"].get(str(i), [])
                    for pos, pid in enumerate(ids):
                        t = run["first_turn"] + pos
                        if t <= e["turn"] or pid != fid:
                            continue
                        if carried_at(run, i, t) > 0:
                            gaps.append(t - e["turn"])
                            break
        inside = "、".join(f"{k}: {v}" for k, v in sorted(buyers.items())) or "-"
        print(f"| {label} | {total} | {inside} | {med(gaps)} |")
    print()


def table_r4(groups):
    print("## R4. 運んだ量（(4) 運んだ量は切より減らない、の裏付け）\n")
    print("| 組 | 会社×ターン | 運んだ量の合計 | 1 会社×ターンの中央値 |")
    print("|---|---|---|---|")
    for label in LABELS:
        runs = groups.get(label, [])
        if not runs:
            continue
        total = 0
        per = []
        for run in runs:
            ix = run["panel_cols"].index("carried_last")
            for rows in run["panel"].values():
                for row in rows:
                    if row is None:
                        continue
                    total += row[ix]
                    per.append(row[ix])
        print(f"| {label} | {len(per)} | {total} | {med(per)} |")
    print()


def main():
    groups = load(sys.argv[1:])
    print("# W-308 海運再参入の集計\n")
    table_r1(groups)
    table_r2(groups)
    table_r3(groups)
    table_r4(groups)
    return 0


if __name__ == "__main__":
    sys.exit(main())
