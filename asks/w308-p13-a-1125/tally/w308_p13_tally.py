"""probe13 の集計（W-308 設計 3 の条件 5 の裏付け。会議 #4828・#4834・#4835）。

使い方: python w308_p13_tally.py <probe13 の出力フォルダ>
フォルダ内の probe13-<組>-plan-s<種>-t1200.json（組 = A・W・K2・WK、種 17〜24）を読み、
4 組とも同じ列・同じ単位で表を出す。金額はその島の通貨（種が同じ組どうしの比べでは
島がそろうので、中央値の比べは目安になる）。

出す物:
1. 倒産した会社（facility_bankrupt の件だけ。取り壊しは同じ会社の続き）の直前ターンの様子:
   一生の買い増し cum_added、直近 100 ターンの買い増し added_100、船団、借金、利息（1 ターン）、
   資本費（1 ターン）、使い具合、営業現金収支の直近 6 ターン合計、年齢。
2. 倒れなかった会社との比べ: panel の会社 × ターンを、後に倒産した会社と最後まで残った会社に分け、
   時期帯ごとに added_100・借金・資本費・使い具合の中央値。
3. 集中: 倒産の時期帯・船団の規模帯・島（上位の島で何割か）。
4. 船団の動き（観測）: 時期帯ごとの船団の中央値、船団 0.5 隻未満の島ターンの割合、
   freight_paid が 0 より大きい島ターンの割合（他国・借りの船に払った島ターン）。

「買いすぎ」と読む材料: 1 で倒産した会社の added_100・cum_added が、2 の残った会社の
買い増しの速さと、もう一方の組の倒産した会社を、はっきり上回り、借金・利息・資本費の
重さが倒産の前に積み上がっていること。逆に、買い増しが目立たず使い具合と現金収支が
市場全体で落ちているなら、運賃・価格側の読みになる。判定は会議でする（この台本は数のみ）。

条件 4 の 2 件（K2→WK の n の増えの残り、上位 2 割の外貨切れの相手先別）は、
この集計でも閉じない（#4835 の 4）。4 の船団 0.5 隻未満の割合は観測として出すだけ。
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
    for path in sorted(glob.glob(f"{folder}/probe13-*.json")):
        name = path.rsplit("probe13-", 1)[1]
        grp = name.split("-")[0]
        with open(path, encoding="utf-8") as fh:
            groups.setdefault(grp, []).append(json.load(fh))
    return groups


def main():
    groups = load(sys.argv[1])
    print("# W-308 probe13 の集計（条件 5 の裏付け）\n")
    print("## 1. 倒産した会社の直前ターンの様子（会社ごとの中央値）\n")
    print("| 組 | 会社数 | 一生の買い増し（隻） | 直近100Tの買い増し（隻） | 船団（隻） | 借金 | 利息/1T | 資本費/1T | 使い具合 | 現金収支 直近6T | 年齢（T） |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    failed_ids = {}
    for grp in GROUPS:
        runs = groups.get(grp, [])
        fails = [f for r in runs for f in r["failures"] if f["kind"] == "facility_bankrupt" and f["t"] >= START]
        failed_ids[grp] = {(f["island"], f.get("id")) for f in fails}
        print(
            f"| {grp} | {len(fails)} | {med([f.get('cum_added') for f in fails])} | {med([f.get('added_100') for f in fails])} "
            f"| {med([f.get('fleet') for f in fails])} | {med([f.get('debt') for f in fails])} | {med([f.get('interest_est') for f in fails])} "
            f"| {med([f.get('capital_last') for f in fails])} | {med([f.get('use') for f in fails])} "
            f"| {med([sum(f.get('ebitda') or []) for f in fails])} | {med([f.get('age') for f in fails])} |"
        )
    print("\n## 2. 倒れた会社と残った会社の比べ（panel の会社 × ターン、中央値）\n")
    print("| 組 | 時期帯 | 区分 | 会社×ターン | 直近100Tの買い増し（隻） | 借金 | 資本費/1T | 使い具合 |")
    print("|---|---|---|---|---|---|---|---|")
    for grp in GROUPS:
        runs = groups.get(grp, [])
        for lo, hi in BANDS:
            for label in ("後に倒産", "残った"):
                a100, debts, caps, uses = [], [], [], []
                for r in runs:
                    dead = {(f["island"], f.get("id")) for f in r["failures"] if f["kind"] == "facility_bankrupt"}
                    cols = r["panel_cols"]
                    ix = {c: k for k, c in enumerate(cols)}
                    for i, rows in r["panel"].items():
                        ids = r["panel_ids"].get(i, [])
                        for pos, row in enumerate(rows):
                            t = r["first_turn"] + pos
                            if row is None or not (lo <= t <= hi) or pos >= len(ids):
                                continue
                            if ((int(i), ids[pos]) in dead) != (label == "後に倒産"):
                                continue
                            a100.append(row[ix["added_100"]] / 1000)
                            debts.append(row[ix["debt"]])
                            caps.append(row[ix["capital_last"]])
                            uses.append(row[ix["use"]] / 1000)
                print(f"| {grp} | {lo}-{hi} | {label} | {len(a100)} | {med(a100)} | {med(debts)} | {med(caps)} | {med(uses)} |")
    print("## 3. 倒産の集中（倒産＋取り壊しの件数）\n")
    for grp in GROUPS:
        runs = groups.get(grp, [])
        fails = [f for r in runs for f in r["failures"] if f["t"] >= START]
        bands = Counter(band_of(f["t"]) for f in fails)
        fleets = Counter(
            "0-1隻" if (f.get("fleet") or 0) < 1 else "1-2隻" if f["fleet"] < 2 else "2-4隻" if f["fleet"] < 4 else "4隻以上"
            for f in fails
        )
        isl = Counter(f["island"] for f in fails)
        top5 = sum(c for _, c in isl.most_common(5))
        print(f"- {grp}: 件数 {len(fails)}、時期帯 {dict(sorted(bands.items()))}、規模帯 {dict(fleets)}、"
              f"島 {len(isl)} 島に分布、上位 5 島で {top5 / max(1, len(fails)):.0%}")
    print("\n## 4. 船団の動き（観測。panel の島 × ターン）\n")
    print("| 組 | 時期帯 | 海運業のある島ターン | 船団の中央値（隻） | 0.5 隻未満の割合 | 他国・借りの運賃を払った割合 |")
    print("|---|---|---|---|---|---|")
    for grp in GROUPS:
        runs = groups.get(grp, [])
        for lo, hi in BANDS:
            fleets, small, freight = [], 0, 0
            for r in runs:
                cols = r["panel_cols"]
                fi, fri = cols.index("fleet"), cols.index("freight_paid")
                for rows in r["panel"].values():
                    for pos, row in enumerate(rows):
                        t = r["first_turn"] + pos
                        if row is None or not (lo <= t <= hi):
                            continue
                        fleets.append(row[fi] / 1000)
                        small += row[fi] < 500
                        freight += row[fri] > 0
            n = len(fleets)
            print(f"| {grp} | {lo}-{hi} | {n} | {med(fleets)} | {small / max(1, n):.1%} | {freight / max(1, n):.1%} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
