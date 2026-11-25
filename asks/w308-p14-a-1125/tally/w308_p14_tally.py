"""probe14 の集計（W-308 設計 3・運賃側の裏付け。会議 #5109）。

使い方: python w308_p14_tally.py <probe14 の出力フォルダ>
フォルダ内の probe14-<組>-plan-s<種>-t1200.json（組 = A・W・K2・WK、種 17・18）を読み、
probe13 の集計（表 1〜4）に、運賃の側の表 5・6 を足して出す。金額はその島の通貨
（種が同じ組どうしの比べでは島がそろうので、中央値の比べは目安になる）。

表 5（運賃の側の主表）: 時期帯ごとと全期間（固定窓 151〜1200 の通し）に、船団 0.5 隻以上の
会社 × ターンについて、1 所有隻あたりの稼ぎ（sales ÷ 船団）と 1 所有隻あたりの費用
（cost_last ÷ 船団）の中央値、「稼ぎ < 費用」の会社 × ターンの割合、運賃の水準
（rate_used ÷ rate_base）の中央値、1 所有隻あたりの運搬んだ量（carried_last ÷ 船団）の
中央値、使い具合の中央値、空で走った割合（1 − 使い具合。観測）、
1 稼働隻あたりの稼ぎ（sales ÷ 稼働隻。稼働隻 = busy_last ÷ 1000 ÷ ship_capacity。
#5114 の列で、所有隻あたりと分けて出す）。
表 6: 費用の内訳（1 所有隻あたりの資本費・人件費・燃料の中央値）。
表 7: 倒産した会社の倒産直前 100 ターン（その会社の panel の行だけ。会社 id で分けるので
倒産後のターンは混ざらない）について、表 5 と同じ物差し（#5114）。

判定の線は README.md に、結果を見る前に置いてある（2026-11-25、#5109 の 3）。
この台本は数のみで、判定は会議でする。

条件 4 の 2 件は、この集計でも閉じない（probe13 と同じ）。
"""

from __future__ import annotations

import glob
import json
import statistics as st
import sys
from collections import Counter, defaultdict

GROUPS = ("A", "W", "K2", "WK")
BANDS = ((151, 300), (301, 600), (601, 900), (901, 1200))
START = 151


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 2) if xs else None


def band_of(t):
    for lo, hi in BANDS:
        if lo <= t <= hi:
            return f"{lo}-{hi}"
    return "?"


def load(folder):
    groups = {}
    for path in sorted(glob.glob(f"{folder}/probe14-*.json")):
        name = path.rsplit("probe14-", 1)[1]
        grp = name.split("-")[0]
        with open(path, encoding="utf-8") as fh:
            groups.setdefault(grp, []).append(json.load(fh))
    return groups


def rows_of(runs):
    """(組の runs) -> 会社 × ターンの行 [(t, row, ix)]。"""
    for r in runs:
        cols = r["panel_cols"]
        ix = {c: k for k, c in enumerate(cols)}
        for rows in r["panel"].values():
            for pos, row in enumerate(rows):
                if row is None:
                    continue
                yield r["first_turn"] + pos, row, ix


def main():
    groups = load(sys.argv[1])
    print("# W-308 probe14 の集計（運賃側の裏付け）\n")
    print("## 5. 運賃の側（船団 0.5 隻以上の会社 × ターン）\n")
    print("| 組 | 時期帯 | 会社×ターン | 稼ぎ/所有隻 | 費用/所有隻 | 稼ぎ<費用の割合 | 運賃の水準 | 運んだ量/所有隻 | 使い具合 | 空の割合 | 稼ぎ/稼働隻 | 稼ぎ/トン |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for grp in GROUPS:
        runs = groups.get(grp, [])
        capacity = next((r.get("ship_capacity") for r in runs if r.get("ship_capacity")), None)
        for lo, hi in [*BANDS, (START, 1200)]:
            earn, cost, short, level, per, uses, earn_active, earn_ton = [], [], 0, [], [], [], [], []
            n = 0
            for t, row, ix in rows_of(runs):
                if not (lo <= t <= hi):
                    continue
                fleet = row[ix["fleet"]] / 1000
                if fleet < 0.5:
                    continue
                n += 1
                e = row[ix["sales"]] / fleet
                c = row[ix["cost_last"]] / fleet
                earn.append(e)
                cost.append(c)
                short += e < c
                if row[ix["rate_base"]] > 0:
                    level.append(row[ix["rate_used"]] / row[ix["rate_base"]])
                per.append(row[ix["carried_last"]] / 1000 / fleet)
                uses.append(row[ix["use"]] / 1000)
                if capacity:
                    active = row[ix["busy_last"]] / 1000 / capacity
                    if active >= 0.1:
                        earn_active.append(row[ix["sales"]] / active)
                if row[ix["carried_last"]] > 0:
                    earn_ton.append(row[ix["sales"]] * 1000 / row[ix["carried_last"]])
            label = f"{lo}-{hi}" if (lo, hi) != (START, 1200) else "通し（固定窓）"
            print(
                f"| {grp} | {label} | {n} | {med(earn)} | {med(cost)} | {short / max(1, n):.1%} "
                f"| {med(level)} | {med(per)} | {med(uses)} | {None if med(uses) is None else round(1 - med(uses), 2)} | {med(earn_active)} | {med(earn_ton)} |"
            )
    print("\n## 6. 費用の内訳（1 所有隻あたり、中央値。船団 0.5 隻以上の会社 × ターン）\n")
    print("| 組 | 時期帯 | 資本費/隻 | 人件費/隻 | 燃料/隻 | 利息/隻 |")
    print("|---|---|---|---|---|---|")
    for grp in GROUPS:
        runs = groups.get(grp, [])
        for lo, hi in BANDS:
            cap, wage, fuel, interest = [], [], [], []
            for t, row, ix in rows_of(runs):
                if not (lo <= t <= hi):
                    continue
                fleet = row[ix["fleet"]] / 1000
                if fleet < 0.5:
                    continue
                cap.append(row[ix["capital_last"]] / fleet)
                wage.append(row[ix["wage_last"]] / fleet)
                fuel.append(row[ix["fuel_last"]] / fleet)
                interest.append(row[ix["interest_est"]] / fleet)
            print(f"| {grp} | {lo}-{hi} | {med(cap)} | {med(wage)} | {med(fuel)} | {med(interest)} |")
    print("\n## 7. 倒産した会社の倒産直前 100 ターン（会社 id で分け、倒産後のターンは混ぜない）\n")
    print("| 組 | 会社×ターン | 稼ぎ/所有隻 | 費用/所有隻 | 稼ぎ<費用の割合 | 運賃の水準 | 稼ぎ/稼働隻 |")
    print("|---|---|---|---|---|---|---|")
    for grp in GROUPS:
        runs = groups.get(grp, [])
        capacity = next((r.get("ship_capacity") for r in runs if r.get("ship_capacity")), None)
        earn, cost, short, level, earn_active = [], [], 0, [], []
        n = 0
        for r in runs:
            cols = r["panel_cols"]
            ix = {c: k for k, c in enumerate(cols)}
            dead = {}
            for f in r["failures"]:
                if f["kind"] == "facility_bankrupt" and f["t"] >= START and f.get("id") is not None:
                    dead[(f["island"], f["id"])] = f["t"]
            for i, rows in r["panel"].items():
                ids = r["panel_ids"].get(i, [])
                for pos, row in enumerate(rows):
                    if row is None or pos >= len(ids):
                        continue
                    ft = dead.get((int(i), ids[pos]))
                    t = r["first_turn"] + pos
                    if ft is None or not (ft - 100 <= t < ft):
                        continue
                    fleet = row[ix["fleet"]] / 1000
                    if fleet < 0.5:
                        continue
                    n += 1
                    e = row[ix["sales"]] / fleet
                    c = row[ix["cost_last"]] / fleet
                    earn.append(e)
                    cost.append(c)
                    short += e < c
                    if row[ix["rate_base"]] > 0:
                        level.append(row[ix["rate_used"]] / row[ix["rate_base"]])
                    if capacity:
                        active = row[ix["busy_last"]] / 1000 / capacity
                        if active >= 0.1:
                            earn_active.append(row[ix["sales"]] / active)
        print(
            f"| {grp} | {n} | {med(earn)} | {med(cost)} | {short / max(1, n):.1%} "
            f"| {med(level)} | {med(earn_active)} |"
        )
    print("\n## 参考: 倒産した会社の直前ターンの様子（probe13 表 1 と同じ作り）\n")
    print("| 組 | 会社数 | 一生の買い増し（隻） | 船団（隻） | 使い具合 | 現金収支 直近6T |")
    print("|---|---|---|---|---|---|")
    for grp in GROUPS:
        runs = groups.get(grp, [])
        fails = [f for r in runs for f in r["failures"] if f["kind"] == "facility_bankrupt" and f["t"] >= START]
        print(
            f"| {grp} | {len(fails)} | {med([f.get('cum_added') for f in fails])} | {med([f.get('fleet') for f in fails])} "
            f"| {med([f.get('use') for f in fails])} | {med([sum(f.get('ebitda') or []) for f in fails])} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
