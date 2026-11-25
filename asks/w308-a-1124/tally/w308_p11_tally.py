"""W-308 の分かれの線と 4 分け（probe11 の結果を読むだけ。会議の W-308 レビューの締めの案 1・中間のまとめ 4）。

使い方: python w308_p11_tally.py A=probe11-A-...json ... K2=... [--ref A] [--cmp K2] --out p11.md

1. 分かれの線（締めの案 1）: cmp だけ届かない島ターン（印 s・w、ref は届いた）を、
   「注文を出す前に切られた」（po 0。量 = 割り振りで扱える重さとお金で削った重さ ac + am）と
   「注文は出たが約定しなかった」（po ≥ 1。量 = 割り振った重さ aq）に分ける。po ≥ 1 で ac + am > 0 の島ターンは、
   量の大きい側に帰す。件数と量の両方の割合を出し、食い違えば量を使う。判定は板寄せの前の量（ac・am）と po だけで行う。
   線: 「注文を出す前に切られた」が半分以上 → 案 1（建て増し）、「約定しなかった」が半分以上 → 案 4。
2. 4 分け（中間のまとめ 4）: ref で扱える重さに切られた島ターン（ac > 0）を固定し、cmp の同じ島ターンを
   「C だけ」（ac > 0、am = 0）・「M だけ」（ac = 0、am > 0）・「両方」（ac > 0、am > 0）・「切られていない」に分ける。
   量の基準は ref の ac。cmp の割り振った重さ（aq）と扱った重さ（iv、輸出も入る）は別の列。検算: 4 分けの件数と量の合計が
   コホート全体と一致すること。本番は ref = A、cmp = W・WK（W-308 の版）。A と K2 で回すのは試し。
"""

import argparse
import json
import sys
from collections import Counter, defaultdict

SPANS = (("151〜600T", 151, 600), ("601〜900T", 601, 900), ("901〜1200T", 901, 1200))
KEYS = ("ac", "am", "aq", "iv", "hw")


def load(items):
    runs, order = defaultdict(dict), []
    for item in items:
        name, path = item.split("=", 1)
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if "ac" not in d:
            print(f"{path}: probe11 の結果ではない（飛ばす）", file=sys.stderr)
            continue
        if name not in order:
            order.append(name)
        runs[name][d["seed"]] = d
    return runs, order


def cells(d):
    """(島, k) → dict(印, ターン, po, ac, am, aq, iv, hw)。"""
    start = d.get("first_turn", 151)
    out = {}
    for i, marks in d["marks"].items():
        po = d["po"].get(i, [])
        cols = {k: d[k].get(i, []) for k in KEYS}
        for k, m in enumerate(marks):
            row = {"m": m, "t": start + k, "po": po[k] if k < len(po) else ""}
            for key, v in cols.items():
                row[key] = v[k] if k < len(v) else 0
            out[(i, k)] = row
    return out


def t(v):
    return f"{v / 1000:,.0f}"


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--ref", default="A")
    ap.add_argument("--cmp", default="K2")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runs, order = load(a.files)
    ref, cmp_ = a.ref, a.cmp
    if ref not in order or cmp_ not in order:
        print(f"{ref} と {cmp_} の両方が要る（あるのは {order}）", file=sys.stderr)
        return 1
    seeds = sorted(set(runs[ref]) & set(runs[cmp_]))
    per = {g: {s: cells(runs[g][s]) for s in seeds} for g in (ref, cmp_)}
    out = [f"# W-308 の分かれの線と 4 分け（probe11、{ref} と {cmp_}）", ""]
    out.append(f"種: {', '.join(map(str, seeds))}（plan）。比べるのは両方で u でない島ターン。量はトン。")
    out.append("")
    out.append(f"## 1. 分かれの線（{cmp_} だけ届かない島ターン）")
    out.append("")
    out.append("| 区間 | 島ターン | 前で切られた 件数 | 約定しなかった 件数 | 前で切られた 量 | 約定しなかった 量 | 混ざった島ターン | 件数で | 量で | 使う方 |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for title, lo, hi in SPANS:
        n = Counter()
        w = Counter()
        mixed = 0
        for s in seeds:
            for key, x in per[cmp_][s].items():
                y = per[ref][s].get(key)
                if y is None or x["m"] == "u" or y["m"] == "u" or not lo <= x["t"] <= hi:
                    continue
                if x["m"] not in "sw" or y["m"] in "sw":
                    continue
                pre = x["ac"] + x["am"]
                post = x["aq"] if x["po"] not in ("", "0") else 0
                if pre > 0 and post > 0:
                    mixed += 1
                side = "pre" if pre >= post else "post"
                if pre == 0 and post == 0:
                    side = "none"
                n[side] += 1
                w[side] += pre if side == "pre" else post
        tot_n = n["pre"] + n["post"]
        tot_w = w["pre"] + w["post"]
        by_n = "前" if n["pre"] * 2 >= tot_n else "約定"
        by_w = "前" if w["pre"] * 2 >= tot_w else "約定"
        use = by_w
        out.append(f"| {title} | {tot_n + n['none']}（どちらも 0 が {n['none']}） | {n['pre']}（{n['pre'] / max(1, tot_n):.1%}） | "
                   f"{n['post']}（{n['post'] / max(1, tot_n):.1%}） | {t(w['pre'])}（{w['pre'] / max(1, tot_w):.1%}） | "
                   f"{t(w['post'])}（{w['post'] / max(1, tot_w):.1%}） | {mixed} | {by_n} | {by_w} | "
                   f"{'前 → 案 1' if use == '前' else '約定 → 案 4'}{'（件数と食い違い、量を使う）' if by_n != by_w else ''} |")
    out.append("")
    out.append(f"## 2. 4 分け（{ref} で扱える重さに切られた島ターンを固定し、{cmp_} の同じ島ターンを分ける）")
    out.append("")
    out.append(f"| 区間 | 分け | 件数 | 量（{ref} の C で切った量） | {cmp_} の割り振った重さ | {cmp_} の扱った重さ | {cmp_} の人の足りなさ（人・延べ） |")
    out.append("|---|---|---|---|---|---|---|")
    for title, lo, hi in SPANS:
        c, base, aq, iv, hw = Counter(), Counter(), Counter(), Counter(), Counter()
        whole_n = whole_w = 0
        for s in seeds:
            for key, y in per[ref][s].items():
                x = per[cmp_][s].get(key)
                if x is None or x["m"] == "u" or y["m"] == "u" or not lo <= y["t"] <= hi or y["ac"] <= 0:
                    continue
                whole_n += 1
                whole_w += y["ac"]
                k = ("両方" if x["am"] > 0 else "C だけ") if x["ac"] > 0 else ("M だけ" if x["am"] > 0 else "切られていない")
                c[k] += 1
                base[k] += y["ac"]
                aq[k] += x["aq"]
                iv[k] += x["iv"]
                hw[k] += x["hw"]
        for k in ("C だけ", "M だけ", "両方", "切られていない"):
            out.append(f"| {title} | {k} | {c[k]} | {t(base[k])} | {t(aq[k])} | {t(iv[k])} | {hw[k]} |")
        ok = sum(c.values()) == whole_n and sum(base.values()) == whole_w
        out.append(f"| {title} | 合計（検算: コホート {whole_n} 件・{t(whole_w)} トン） | {sum(c.values())} | {t(sum(base.values()))} | "
                   f"{t(sum(aq.values()))} | {t(sum(iv.values()))} | {sum(hw.values())}（{'一致' if ok else '**不一致**'}） |")
    out.append("")
    out.append("注: 扱った重さ（iv）は商社の輸出と輸入の合計。島ターンごとの輸入だけの重さは probe11 に無い。")
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
