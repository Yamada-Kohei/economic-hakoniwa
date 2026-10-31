"""後回しの台帳の経済の現象（W 番号）が、measure batch の結果に出ているかを数える。読むだけ。

    py -3.12 wcheck.py <measure の out-dir> wcheck.txt

out-dir の *.json（measure batch の結果）と、あれば *.jsonl（--series の時系列）を読む。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PRICE = re.compile(r"島 (\d+)（(\w+)）: (\w+) の値段が (\d+) ターン外れた（初めの ([\d.]+)〜([\d.]+) 倍）")
INFL = re.compile(r"島 (\d+)（(\w+)）: 物価上昇率が (\d+) ターン外れた")
REGIME = re.compile(r"島 \d+（(\w+)）")


def load(out: Path) -> dict[str, dict]:
    runs = {}
    for p in sorted(out.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and "scenario" in d and "windows" in d:
            runs[p.stem] = d
    return runs


def last_window(d: dict) -> dict:
    ws = d.get("windows", {})
    if not ws:
        return {}
    return ws[max(ws, key=int)]


def found(runs: dict) -> list[tuple[str, dict]]:
    out = []
    for name, d in runs.items():
        for c in d.get("collapse", {}).get("found", []):
            m = REGIME.search(c.get("line", ""))
            out.append((name, c | {"regime": m.group(1) if m else "?"}))
    return out


def fmt_found(items: list[tuple[str, dict]], limit: int = 12) -> list[str]:
    lines = [
        f"    {n} 島{c['island']}({c['regime']}) {c['kind']} {c['first']}T〜 {c['turns']}T "
        + "・".join(c.get("what", [])[:4])
        for n, c in items[:limit]
    ]
    if len(items) > limit:
        lines.append(f"    …ほか {len(items) - limit} 件")
    return lines


def pick(items, kind=None, goods=None, scen=None, what_has=None):
    res = []
    for n, c in items:
        if kind and c["kind"] not in kind:
            continue
        if scen and not any(n.startswith(s + "-") for s in scen):
            continue
        what = c.get("what", [])
        if goods and not any(g in what for g in goods):
            continue
        if what_has and not any(any(h in w for h in what_has) for w in what):
            continue
        res.append((n, c))
    return res


def prices(runs: dict) -> list[tuple[str, int, str, str, int, float, float]]:
    out = []
    for name, d in runs.items():
        for line in last_window(d).get("gate", {}).get("lines", []):
            m = PRICE.search(line)
            if m:
                i, reg, good, t, lo, hi = m.groups()
                out.append((name, int(i), reg, good, int(t), float(lo), float(hi)))
    return out


def series_pop(out: Path, name: str) -> dict[int, list[tuple[int, float]]]:
    p = out / f"{name}.jsonl"
    pops: dict[int, list[tuple[int, float]]] = defaultdict(list)
    if not p.exists():
        return pops
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if "island" in r and "population" in r:
                pops[r["island"]].append((r["t"], r["population"]))
    return pops


def main(out: Path) -> None:
    runs = load(out)
    items = found(runs)
    pr = prices(runs)
    w = print
    w(f"# W の確かめ（{out}、{len(runs)} 組）")
    w("")
    w("## 組ごとの崩れの数（島の数）と秒")
    for n, d in runs.items():
        cc = d.get("collapse", {}).get("counts", {})
        s = "、".join(f"{k} {v}" for k, v in cc.items() if v) or "なし"
        w(f"- {n}: {s}（{d.get('seconds', 0):.0f} 秒、{d.get('islands')} 島）")
    w("")

    def meas(key):
        return {n: last_window(d).get("measure", {}).get(key) for n, d in runs.items()}

    def summ(key):
        return {n: last_window(d).get("summary", {}).get(key) for n, d in runs.items()}

    def row(title, vals, f="{:.3f}"):
        w(f"  {title}: " + "、".join(f"{n} {f.format(v)}" for n, v in vals.items() if v is not None))

    w("## W-10 売れずに動かない設備・財の買い物・自動車の買い")
    row("売れ行きで全力でない席 idle.sales", meas("idle.sales"))
    row("1人あたりの財の消費 real_goods_per_person", meas("households.real_goods_per_person"), "{:.0f}")
    row("自動車 台/ターン", meas("buy.automobile.per_turn"), "{:.0f}")
    w("")
    w("## W-12 関門の物価上昇率")
    row("物価上昇率が目安の中の割合 inflation.inside", summ("inflation.inside"))
    row("物価上昇率の平均", summ("inflation.mean"))
    row("外れた島（counts.inflation）", {n: last_window(d).get("counts", {}).get("inflation") for n, d in runs.items()}, "{}")
    w("")
    w("## W-18 豚の循環（農場の作り方の一斉替え・食べ物の品切れ・飢饉）")
    farm = pick(items, kind={"herding"}, what_has=["farm", "ranch"])
    w(f"  農場・牧場の一斉替え: {len(farm)} 件")
    w(*fmt_found(farm), sep="\n") if farm else None
    food = pick(items, kind={"stockout", "no_trade"}, goods=["grain", "food", "meat", "fish", "luxury_crop"])
    w(f"  食べ物の品切れ・取引止まり: {len(food)} 件")
    w(*fmt_found(food), sep="\n") if food else None
    fam = pick(items, kind={"famine", "starved_without_benefit"})
    w(f"  飢饉: {len(fam)} 件")
    w(*fmt_found(fam), sep="\n") if fam else None
    w("")
    w("## W-24 失業と値崩れ")
    row("失業率の平均", summ("unemployment.mean"))
    row("値段の倍率の最小 price.ratio.min", summ("price.ratio.min"), "{:.3g}")
    low = [p for p in pr if p[5] < 0.2]
    w(f"  初めの 0.2 倍を割った値段（島×財）: {len(low)} 件 " + "、".join(f"{g}" for g, _ in Counter(p[3] for p in low).most_common(8)))
    w("")
    w("## W-25 人手不足で人を取れない")
    row("人手不足で全力でない席 idle.workers", meas("idle.workers"), "{:.4f}")
    row("埋まらない席 jobs.vacant", meas("jobs.vacant"), "{:.4f}")
    row("参入できない: 人がいない entry_block.no_workers", meas("entry_block.no_workers"), "{:.4f}")
    drop = pick(items, kind={"population_drop"})
    w(f"  人口の急減: {len(drop)} 件")
    w(*fmt_found(drop), sep="\n") if drop else None
    w("")
    w("## W-26・W-27・W-28 値段の外れ（目安の外のターン、島×財）")
    by = defaultdict(list)
    for p in pr:
        by[p[3]].append(p)
    for g in ("automobile", "chemicals", "coal", "steel", "clothing", "aluminium", "bauxite", "fuel", "machinery"):
        ps = by.get(g, [])
        if not ps:
            w(f"  {g}: 0")
            continue
        big = [p for p in ps if p[6] >= 10 or p[5] <= 0.1]
        mk = [p for p in ps if p[2] == "market"]
        w(
            f"  {g}: {len(ps)} 件（うち 10 倍超か 0.1 倍未満 {len(big)}、市場経済の島 {len(mk)}）"
            f" 外れたターンの合計 {sum(p[4] for p in ps)}、最大 {max(p[6] for p in ps):.1f} 倍"
        )
    coal = pick(items, kind={"stockout", "no_trade"}, goods=["coal"])
    w(f"  石炭の品切れ・取引止まり（W-27）: {len(coal)} 件")
    w(*fmt_found(coal), sep="\n") if coal else None
    w("")
    w("## W-78 300 ターン目の船")
    for n, d in runs.items():
        sh = d.get("ships") or {}
        if sh and d.get("turns", 0) > 300:
            w(
                f"  {n}: 船 {sh.get('ships.before', 0):.0f} → 最少 {sh.get('ships.after.min', 0):.0f}"
                f"（船のない島 {sh.get('ships.islands_without', 0):.0f}）、港の扱い "
                f"{sh.get('port.handled.before', 0):.0f} → {sh.get('port.handled.after', 0):.0f}"
            )
    w("")
    w("## W-82・W-176 自動車の取引止まり、建材・機械の品切れ")
    auto = pick(items, kind={"no_trade", "stockout"}, goods=["automobile", "auto_parts"])
    w(f"  自動車・部品: {len(auto)} 件")
    w(*fmt_found(auto), sep="\n") if auto else None
    bm = pick(items, kind={"stockout", "no_trade"}, goods=["building_materials", "machinery"])
    w(f"  建材・機械: {len(bm)} 件")
    w(*fmt_found(bm, 20), sep="\n") if bm else None
    w("")
    w("## W-179・W-180 命令なしの飢饉、戦争の場面の機械・建材・石材")
    nf = pick(items, kind={"famine", "starved_without_benefit", "population_drop"}, scen=["none"])
    w(f"  命令なしの飢饉・急減: {len(nf)} 件")
    w(*fmt_found(nf), sep="\n") if nf else None
    war = pick(items, kind={"stockout", "no_trade"}, scen=["war"], goods=["machinery", "building_materials", "stone", "steel"])
    w(f"  戦争の場面の機械・建材・石材・鉄鋼: {len(war)} 件")
    w(*fmt_found(war), sep="\n") if war else None
    w("")
    w("## W-200 石切り場など1つの作り手が閉じた後の品切れ（石材・鉄鉱石・木材など、24 ターン以上）")
    raw = [x for x in pick(items, kind={"stockout"}, goods=["stone", "iron_ore", "wood", "coal", "bauxite", "copper_ore", "crude_oil"]) if x[1]["turns"] >= 24]
    w(f"  {len(raw)} 件")
    w(*fmt_found(raw), sep="\n") if raw else None
    w("")
    w("## W-105 落ちた後に戻らない島（50 島の組、時系列から）")
    for n, d in runs.items():
        if d.get("islands", 0) < 50:
            continue
        pops = series_pop(out, n)
        if not pops:
            w(f"  {n}: 時系列なし")
            continue
        stuck = []
        for iid, seq in pops.items():
            seq.sort()
            peak, peak_t, low, low_t = 0.0, 0, None, 0
            worst = None
            for t, p in seq:
                if p > peak:
                    peak, peak_t = p, t
                    low = None
                elif peak and p < peak * 0.8 and (low is None or p < low):
                    low, low_t = p, t
                    worst = (peak, peak_t, low, low_t)
            end = seq[-1][1]
            if worst and end < worst[0] * 0.9:
                stuck.append((iid, worst, end))
        s31 = pops.get(31)
        line31 = ""
        if s31:
            ps = [p for _, p in sorted(s31)]
            line31 = f"島31 初め {ps[0]:.0f}・最少 {min(ps):.0f}・最後 {ps[-1]:.0f}"
        w(
            f"  {n}: 2 割より落ちて 9 割まで戻らない島 {len(stuck)} "
            + "、".join(f"島{i}（{wv[0]:.0f}@{wv[1]}→{wv[2]:.0f}@{wv[3]}→最後 {e:.0f}）" for i, wv, e in stuck[:6])
            + f"。{line31}"
        )
    w("")
    w("## W-177 人口の伸び")
    row("人口の伸び（年）", meas("population.growth"), "{:+.4f}")
    w("")
    w("## 崩れの全部（種類×財の数）")
    kinds = Counter((c["kind"], (c.get("what") or ["-"])[0]) for _, c in items)
    w("  " + "、".join(f"{k}:{g} {v}" for (k, g), v in kinds.most_common(40)))


if __name__ == "__main__":
    if len(sys.argv) > 2:  # 2つ目に出力のファイル（UTF-8）
        sys.stdout = open(sys.argv[2], "w", encoding="utf-8")
    main(Path(sys.argv[1]))
