"""W-308 設計 3 の測りの表（probe12 の結果を読むだけ。会議 #4633・注文書 #4638）。

使い方: python w308_p12_tally.py A=probe12-A-...json W=... K2=... WK=... --out p12.md

4 分けは w308_p11_tally.py（--ref A --cmp W、--ref K2 --cmp WK）で出す。ここで出すのは:
1. 判定の列: 扱える重さで切った量（ac）と、切られた島ターン（C、ac > 0）の数。組ごと・区間ごと。
2. 観察の列（判定に使わない）: お金で切った量（am）・財の数（mc）・島ターン（M、am > 0）、扱える重さのうち
   輸出の荷の割合（ex ÷ cp）、規模 2 以上の商社（sz）の延べ、建て増しの始まり（xs）、板寄せの後の不足（mw）。
3. 建て増しの後に外貨が先に尽きる島ターン: sz > 0 で ac = 0 かつ am > 0。あわせて、比べる元（A・K2）で
   C だった島ターンのうち、建て増した版でそうなった割合。
市場の島（pl = m）と計画経済の島（p）を分ける。比べるのは両方で u でない島ターン。量はトン。
"""

import argparse
import json
import sys
from collections import defaultdict

SPANS = (("151〜600T", 151, 600), ("601〜900T", 601, 900), ("901〜1200T", 901, 1200), ("全部", 151, 1200))
KEYS = ("ac", "am", "ex", "cp", "mc", "sz", "mw")
PAIRS = (("A", "W"), ("K2", "WK"))


def load(items):
    runs, order = defaultdict(dict), []
    for item in items:
        name, path = item.split("=", 1)
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if "cp" not in d:
            print(f"{path}: probe12 の結果ではない（飛ばす）", file=sys.stderr)
            continue
        if name not in order:
            order.append(name)
        runs[name][d["seed"]] = d
    return runs, order


def cells(d):
    start = d.get("first_turn", 151)
    out = {}
    for i, marks in d["marks"].items():
        pl = d.get("pl", {}).get(i, "")
        cols = {k: d[k].get(i, []) for k in KEYS}
        for k, m in enumerate(marks):
            row = {"m": m, "t": start + k, "pl": pl[k] if k < len(pl) else "m"}
            for key, v in cols.items():
                row[key] = v[k] if k < len(v) else 0
            out[(i, k)] = row
    return out


def t(v):
    return f"{v / 1000:,.0f}"


def pct(a, b):
    return f"{a / b:.1%}" if b else "-"


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    runs, order = load(a.files)
    seeds = sorted(set.intersection(*(set(runs[g]) for g in order))) if order else []
    per = {g: {s: cells(runs[g][s]) for s in seeds} for g in order}

    def both_seen(s, key):
        return all(key in per[g][s] and per[g][s][key]["m"] != "u" for g in order)

    out = ["# W-308 設計 3 の測りの表（probe12）", ""]
    out.append(f"組: {', '.join(order)}。種: {', '.join(map(str, seeds))}（plan）。比べるのは全部の組で u でない島ターン。量はトン。")
    out.append("")
    for kind, label in (("m", "市場の島"), ("p", "計画経済の島")):
        out.append(f"## 1・2. 判定の列と観察の列（{label}）")
        out.append("")
        out.append("| 区間 | 組 | 島ターン | C の島ターン | C で切った量（判定） | M の島ターン | M で切った量 | M の財の数 | 輸出の荷 ÷ 扱える重さ | 規模 2 の商社（延べ） | 板寄せの後の不足（財・延べ） |")
        out.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for title, lo, hi in SPANS:
            for g in order:
                n = c_n = m_n = 0
                s_ac = s_am = s_mc = s_ex = s_cp = s_sz = s_mw = 0
                for s in seeds:
                    for key, x in per[g][s].items():
                        if not lo <= x["t"] <= hi or x["pl"] != kind or not both_seen(s, key):
                            continue
                        n += 1
                        c_n += x["ac"] > 0
                        m_n += x["am"] > 0
                        s_ac += x["ac"]
                        s_am += x["am"]
                        s_mc += x["mc"]
                        s_ex += x["ex"]
                        s_cp += x["cp"]
                        s_sz += x["sz"]
                        s_mw += x["mw"]
                out.append(f"| {title} | {g} | {n} | {c_n}（{pct(c_n, n)}） | {t(s_ac)} | {m_n}（{pct(m_n, n)}） | {t(s_am)} | "
                           f"{s_mc} | {pct(s_ex, s_cp)} | {s_sz} | {s_mw} |")
        out.append("")
    out.append("## 3. 建て増しの後に外貨が先に尽きる島ターン（市場の島）")
    out.append("")
    out.append("建て増した版の、規模 2 の商社がいる島ターン（sz > 0）のうち、扱える重さでは切られず（ac = 0）お金で切られた（am > 0）物。")
    out.append("")
    out.append("| 区間 | 元 → 建て増し | sz > 0 の島ターン | うち外貨が先に尽きた | うち C が残った | 元で C だった島ターン | そのうち建て増した版で外貨が先に尽きた |")
    out.append("|---|---|---|---|---|---|---|")
    for ref, cmp_ in PAIRS:
        if ref not in order or cmp_ not in order:
            continue
        for title, lo, hi in SPANS:
            sz_n = fx_n = c_left = base_c = base_fx = 0
            for s in seeds:
                for key, x in per[cmp_][s].items():
                    y = per[ref][s].get(key)
                    if y is None or not lo <= x["t"] <= hi or x["pl"] != "m" or not both_seen(s, key):
                        continue
                    fx = x["sz"] > 0 and x["ac"] == 0 and x["am"] > 0
                    if x["sz"] > 0:
                        sz_n += 1
                        fx_n += fx
                        c_left += x["ac"] > 0
                    if y["ac"] > 0:
                        base_c += 1
                        base_fx += fx
            out.append(f"| {title} | {ref} → {cmp_} | {sz_n} | {fx_n}（{pct(fx_n, sz_n)}） | {c_left}（{pct(c_left, sz_n)}） | "
                       f"{base_c} | {base_fx}（{pct(base_fx, base_c)}） |")
    out.append("")
    out.append("## 4. 建て増しの始まり（xs）")
    out.append("")
    out.append("| 組 | 種 | 建て増しの工事の数 | 建て増した島の数 | 最初のターン | 中央のターン |")
    out.append("|---|---|---|---|---|---|")
    for g in order:
        for s in seeds:
            xs = runs[g][s].get("xs", {})
            turns = sorted(v for lst in xs.values() for v in lst)
            if not turns:
                out.append(f"| {g} | {s} | 0 | 0 | - | - |")
                continue
            out.append(f"| {g} | {s} | {len(turns)} | {sum(1 for lst in xs.values() if lst)} | {turns[0]} | {turns[len(turns) // 2]} |")
    out.append("")
    out.append("注: 判定は C で切った量（ac）だけで行う。M・輸出の割合・規模・板寄せの後の不足は観察の列（#4638）。")
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
