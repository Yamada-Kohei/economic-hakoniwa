"""W-308 条件 4 の n の数え直し: 島の「戻り」有無で分ける（probe13 の船団 panel と
probe12 の n 記録。掲示板 #5074 の決め・#5076 の提案・#5081/#5083 の定義固定）。

使い方:
  python w308_c4_split_tally.py <probe12 のフォルダ> <probe13 のフォルダ> --out w308-c4-split.md
（フォルダ内に probe12-<組>-plan-s<種>-t1200.json / probe13-<組>-plan-s<種>-t1200.json。
組は K2・WK、種は 17〜24 を想定。probe12 と probe13 は同じ commit・種・パッチで
同じ世界を回した物なので（probe13 の note）、組・種・島で突き合わせて使う）

## 定義と手順（n を見る前に固定。#5081/#5083。結果を見て動かさない）

- 「戻った」の定義（島内回復）: 島ごとに、直し後の直近 100T 窓の船団が、その島の
  直し直前の直近 100T 窓の中央値以上に戻っている島を「戻った」とする。A/K2 基準との
  横比べは使わない。
- 窓の置き方（この集計での固定）: 記録は 151〜1200T。船団の痩せと n の増えは後半
  （601T 以降、特に 901T 以降、#4847 の区間）に集中するので、直し直前に当たる基準窓は
  後半の直前の直近 100T = 501〜600T、直し後に当たる最終窓は記録の最後の 100T =
  1101〜1200T とする。列は probe13 panel の ``fleet``（1000 分の 1 隻）で、両窓とも
  同じ列・同じ単位。
- 中央値: 窓内のターンごとの船団（海運業が無いターンは観測なし = null を除く）を
  並べた通常の中央値。偶数個なら中央 2 値の平均。
- 最終窓の代表値も中央値とする（主判定）。参考として最終ターンの値と平均でも分けた
  結果を付録に出すが、主判定は動かさない。
- サンプル不足: 基準窓・最終窓のどちらかで観測が 100T 分（100 件）に満たない島は、
  「戻った／戻っていない」のどちらにも数えず判定対象から外す。
- 判定順序: (1) 島ごとに基準窓の中央値を出す → (2) 最終窓で「戻った島／戻っていない
  島」に分ける → (3) その後に各群で条件 4 の n が「消えた／残った」を数える。
- n の数え方（#4847・kn 再集計と同じ）: probe12 の記録で nt=1 かつ印 n、kn があって
  1 <= kn <= 3.0 の島ターンを n と数える。検算として組ごとの合計が #4847 の集計
  （K2 16,422 / WK 28,184）と合うことを先に出す。
- n の消えた／残った（島ごと）: 前 = 151〜1100T の n、最終 = 1101〜1200T の n で、
  残った（前 >= 1 かつ最終 >= 1）／消えた（前 >= 1 かつ最終 = 0）／新たに出た
  （前 = 0 かつ最終 >= 1）／なし（両方 0）。参考に 901〜1200T の n の合計も出す。
- 単位は種 × 島（種 17〜24 × 50 島 = 400 単位 / 組）。
"""

import argparse
import glob
import json
import os
import statistics as st
import sys
from collections import defaultdict

REF_WIN = (501, 600)      # 基準窓（直し直前）
FINAL_WIN = (1101, 1200)  # 最終窓（直し後）
LATE_WIN = (901, 1200)    # 参考: n が積み上がる後半
BEFORE_SPAN = (151, 1100)  # n の「前」
GROUPS = ("WK", "K2")     # 条件 4 は K2→WK。WK を主、K2 を参考に出す


def load(folder, probe, group):
    runs = {}
    for path in sorted(glob.glob(os.path.join(folder, f"{probe}-{group}-plan-s*-t1200.json"))):
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        runs[d["seed"]] = d
    return runs


def fleet_windows(p13):
    """(島 -> (基準窓の観測数, 基準中央値, 最終窓の観測数, 最終中央値, 最終値, 最終平均))"""
    start = p13.get("first_turn", 151)
    fi = p13["panel_cols"].index("fleet")
    out = {}
    for i, rows in p13["panel"].items():
        ref, fin = [], []
        for pos, row in enumerate(rows):
            if row is None:
                continue
            t = start + pos
            if REF_WIN[0] <= t <= REF_WIN[1]:
                ref.append(row[fi])
            elif FINAL_WIN[0] <= t <= FINAL_WIN[1]:
                fin.append(row[fi])
        out[i] = (
            len(ref),
            st.median(ref) if ref else None,
            len(fin),
            st.median(fin) if fin else None,
            fin[-1] if fin else None,
            (sum(fin) / len(fin)) if fin else None,
        )
    return out


def n_counts(p12):
    """島 -> (n_before, n_final, n_late, n_total)。数え方は kn 再集計と同じ。"""
    start = p12.get("first_turn", 151)
    out = {}
    for i, marks in p12["marks"].items():
        nt = p12["nt"].get(i, "")
        kn = {int(k): v for k, v in p12.get("kn", {}).get(i, {}).items()}
        nb = nf = nl = na = 0
        for k, m in enumerate(marks):
            if k >= len(nt) or nt[k] != "1" or m != "n":
                continue
            v = kn.get(k)
            if v is None or not (1 <= v <= 3.0):
                continue
            t = start + k
            na += 1
            if BEFORE_SPAN[0] <= t <= BEFORE_SPAN[1]:
                nb += 1
            if FINAL_WIN[0] <= t <= FINAL_WIN[1]:
                nf += 1
            if LATE_WIN[0] <= t <= LATE_WIN[1]:
                nl += 1
        out[i] = (nb, nf, nl, na)
    return out


def fmt(x):
    return f"{x / 1000:.3f}" if x is not None else "-"


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("p12_folder")
    ap.add_argument("p13_folder")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    out = []
    out.append("# W-308 条件 4 の n の数え直し（島の戻り有無で分ける）")
    out.append("")
    out.append("材料: probe13 の船団 panel（受付の仕事 357〜388）と probe12 の n 記録（仕事 251〜282・288・289）。")
    out.append("probe12 と probe13 は同じ commit・種・パッチで同じ世界を回した物（probe13 の note）で、")
    out.append("突き合わせの検算として、両者の failures（島・ターン・種別）が K2・WK とも全 8 種で完全一致した。")
    out.append("")
    out.append("## 定義と手順（n を見る前に固定。#5081/#5083）")
    out.append("")
    out.append("- 「戻った」: 島ごとに、最終窓（1101〜1200T）の船団の中央値が、その島の基準窓")
    out.append("  （501〜600T、後半の痩せが始まる直前の直近 100T）の船団の中央値以上に戻っている島。")
    out.append("  列は probe13 panel の fleet（1000 分の 1 隻）で両窓とも同じ。A/K2 基準との横比べは使わない。")
    out.append("- 中央値: 窓内のターンごとの船団（海運業が無いターンは観測なし）を並べた通常の中央値。")
    out.append("  偶数個なら中央 2 値の平均。")
    out.append("- どちらかの窓で観測が 100 件に満たない島は判定対象から外す（理由別に数を出す）。")
    out.append("- 判定順序: (1) 基準中央値を出す → (2) 戻った／戻っていないに分ける → (3) 各群で n の")
    out.append("  消えた／残ったを数える。n は nt=1 かつ印 n、1 <= kn <= 3.0（#4847・kn 再集計と同じ）。")
    out.append("- n の消えた／残った: 前 = 151〜1100T、最終 = 1101〜1200T。残った（前 >= 1 かつ最終 >= 1）／")
    out.append("  消えた（前 >= 1 かつ最終 = 0）／新たに出た（前 = 0 かつ最終 >= 1）／なし（両方 0）。")
    out.append("")

    detail_rows = []
    for g in GROUPS:
        p12runs = load(a.p12_folder, "probe12", g)
        p13runs = load(a.p13_folder, "probe13", g)
        seeds = sorted(set(p12runs) & set(p13runs))
        # 検算: n の合計
        total_n = 0
        units = []  # (seed, island, 区分, ref_med, fin_med, nb, nf, nl, 除外理由)
        for s in seeds:
            fw = fleet_windows(p13runs[s])
            nc = n_counts(p12runs[s])
            for i in sorted(fw, key=lambda x: int(x)):
                nref, refmed, nfin, finmed, finlast, finmean = fw[i]
                nb, nf, nl, na = nc.get(i, (0, 0, 0, 0))
                total_n += na
                if nref < 100 or nfin < 100:
                    why = []
                    if nref < 100:
                        why.append(f"基準窓の観測 {nref}")
                    if nfin < 100:
                        why.append(f"最終窓の観測 {nfin}")
                    units.append((s, i, "対象外", refmed, finmed, nb, nf, nl, "・".join(why), finlast, finmean))
                else:
                    grp = "戻った" if finmed >= refmed else "戻っていない"
                    units.append((s, i, grp, refmed, finmed, nb, nf, nl, "", finlast, finmean))
        expected = {"K2": 16422, "WK": 28184}[g]
        out.append(f"## {g}（{'主' if g == 'WK' else '参考'}）")
        out.append("")
        out.append(f"検算: n の合計 {total_n:,}（#4847 の集計 {expected:,} と{'一致' if total_n == expected else '不一致'}）。単位は種 × 島 = {len(units)}。")
        out.append("")
        out.append("| 区分 | 島数 | n が残った | n が消えた | 新たに出た | n なし | 最終窓の n（ターン数） | 901〜1200T の n |")
        out.append("|---|---|---|---|---|---|---|---|")
        for grp in ("戻った", "戻っていない", "対象外"):
            us = [u for u in units if u[2] == grp]
            rem = sum(1 for u in us if u[5] >= 1 and u[6] >= 1)
            gone = sum(1 for u in us if u[5] >= 1 and u[6] == 0)
            new = sum(1 for u in us if u[5] == 0 and u[6] >= 1)
            none = sum(1 for u in us if u[5] == 0 and u[6] == 0)
            out.append(
                f"| {grp} | {len(us)} | {rem} | {gone} | {new} | {none} | "
                f"{sum(u[6] for u in us):,} | {sum(u[7] for u in us):,} |"
            )
        out.append("")
        zero_ref = [u for u in units if u[2] != "対象外" and (u[3] or 0) == 0]
        out.append(f"参考: 判定対象のうち基準中央値が 0 の島は {len(zero_ref)}（この場合「戻った」は 0 以上という形になる）。")
        excl = [u for u in units if u[2] == "対象外"]
        er = {"基準窓のみ不足": 0, "最終窓のみ不足": 0, "両方不足": 0}
        for u in excl:
            has_ref = "基準窓" in u[8]
            has_fin = "最終窓" in u[8]
            er["両方不足" if has_ref and has_fin else "基準窓のみ不足" if has_ref else "最終窓のみ不足"] += 1
        out.append(f"参考: 対象外 {len(excl)} 島の内訳: 基準窓のみ不足 {er['基準窓のみ不足']}、最終窓のみ不足 {er['最終窓のみ不足']}、両方不足 {er['両方不足']}。")
        out.append("")
        out.append(f"### {g}: 基準中央値が 0 の島と、それ以外の島（同じ集計の切り分け）")
        out.append("")
        out.append("| 基準中央値 | 区分 | 島数 | n が残った | n が消えた | 新たに出た | n なし | 最終窓の n（ターン数） |")
        out.append("|---|---|---|---|---|---|---|---|")
        for zlabel, zsel in (("0", lambda u: (u[3] or 0) == 0), ("0 より大きい", lambda u: (u[3] or 0) > 0)):
            for grp in ("戻った", "戻っていない"):
                us = [u for u in units if u[2] == grp and zsel(u)]
                rem = sum(1 for u in us if u[5] >= 1 and u[6] >= 1)
                gone = sum(1 for u in us if u[5] >= 1 and u[6] == 0)
                new = sum(1 for u in us if u[5] == 0 and u[6] >= 1)
                none = sum(1 for u in us if u[5] == 0 and u[6] == 0)
                out.append(f"| {zlabel} | {grp} | {len(us)} | {rem} | {gone} | {new} | {none} | {sum(u[6] for u in us):,} |")
        out.append("")
        # 戻った群の島ごとの内訳
        rec = [u for u in units if u[2] == "戻った"]
        out.append(f"### {g}: 戻った島の内訳（{len(rec)} 島）")
        out.append("")
        out.append("| 種 | 島 | 基準中央値（隻） | 最終中央値（隻） | n 前（151〜1100T） | n 最終（1101〜1200T） |")
        out.append("|---|---|---|---|---|---|")
        for u in sorted(rec, key=lambda u: (u[0], int(u[1]))):
            out.append(f"| {u[0]} | {u[1]} | {fmt(u[3])} | {fmt(u[4])} | {u[5]:,} | {u[6]:,} |")
        out.append("")
        detail_rows.append((g, units))

    out.append("## 付録: 最終窓の代表値の取り方を変えた場合（参考。主判定は中央値）")
    out.append("")
    out.append("| 組 | 代表値 | 戻った島数 | そのうち n が残った | そのうち最終窓の n（ターン数） |")
    out.append("|---|---|---|---|---|")
    for g, units in detail_rows:
        for label, idx in (("最終ターンの値", 9), ("平均", 10)):
            judged = []
            for u in units:
                if u[8]:  # 対象外
                    continue
                v = u[idx]
                judged.append((u, v is not None and u[3] is not None and v >= u[3]))
            rec = [u for u, ok in judged if ok]
            rem = sum(1 for u in rec if u[5] >= 1 and u[6] >= 1)
            out.append(f"| {g} | {label} | {len(rec)} | {rem} | {sum(u[6] for u in rec):,} |")
    out.append("")
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
