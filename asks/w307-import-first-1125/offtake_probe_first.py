"""W-307（FX-87）の比べ: trade.offtake.import_first（輸入に頼る島の輸入注文を先に約定させる）の
入り切りで、市場経済の島の石炭がどう変わるか（読むだけ。本体の offtake_probe_v2.py と同じ記録）。

--on で trade.offtake.enabled を、--first で trade.offtake.import_first を入れる（ゲームの設定は
替えない）。毎ターン、計画経済でない島ごとに記録する:
  pr: 石炭の産出, use: 島で使った量, px: 値段, st: 在庫,
  dem・sup・vol: 島の市場の買いたい量・売りに出た量・約定（商社の輸出の買いを含む）,
  xb: 商社の輸出の買い注文の量（島の市場。home_orders を包んで数える）,
  q・dl・un: 契約の量・納品した量・引き受け手のなかった量（offtake.* の stats）,
  users: 石炭を使う施設の数, short: そのうち石炭が手に入らずに止まった・絞った施設の数, ur: その稼働率の平均（1000 分の 1）,
  ex: 輸出の額（trade.exports、自国通貨）, hh: 家計の買った量（食べ物と消費財の合計）,
  inv: 石炭を作る施設の規模の合計（建て増し・参入で増える）
国内の B は dem − xb、S は sup − 契約で渡した分は市場に出ないので sup のまま（契約の分は dl）。

    py -3.12 -X utf8 offtake_probe.py --seed 1 --turns 500 --on --out on-s1.jsonl.gz
    py -3.12 -X utf8 offtake_probe.py --report off-s1.jsonl.gz on-s1.jsonl.gz
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter, defaultdict

GOOD = "coal"


def _open(path: str, mode: str):
    return gzip.open(path, mode, encoding="utf-8") if path.endswith(".gz") else open(path, mode, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    from hakoniwa_core import facilities as fac
    from hakoniwa_core import measure, stock, stops
    from hakoniwa_core.game import load_game
    from hakoniwa_core.state.generate import generate_world
    from hakoniwa_core.trade import traders

    game = load_game(None)
    defs, config = game.defs, game.config
    config.raw["trade"]["offtake"]["enabled"] = bool(args.on)
    config.raw["trade"]["offtake"]["import_first"] = bool(args.first)
    print(f"offtake={config.get('trade.offtake.enabled')} import_first={config.get('trade.offtake.import_first')} goods={config.get('trade.offtake.goods')}", file=sys.stderr)
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)
    xbuy: dict = defaultdict(int)
    orig = traders.home_orders

    def home_orders(island, defs_, config_, turn):
        out = orig(island, defs_, config_, turn)
        for o in out:
            if o.good == GOOD and o.side == "buy":
                xbuy[island.id] += o.qty
        return out

    traders.home_orders = home_orders
    users_kinds = {(k, r) for k, d in defs.facilities.items() for r, rec in d["recipes"].items() if GOOD in rec.get("inputs", {})}
    makers_kinds = {(k, r) for k, d in defs.facilities.items() for r, rec in d["recipes"].items() if GOOD in rec.get("outputs", {})}
    food = {"food", "grain", "fish", "meat"}
    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            xbuy.clear()
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            row = {"t": world.turn, "i": {}}
            for i, island in sorted(world.islands.items()):
                if not island.has_economy or traders.is_planned(island):
                    continue
                s = island.stats
                rec = next((m for m in island.markets if m.good == GOOD), None)
                users = [f for f in island.facilities if f.built and (f.kind, f.recipe) in users_kinds]
                short = [f for f in users if stops.caps_target(config, f.status) and stops.short_good(f.status) == GOOD]
                makers = [f for f in island.facilities if f.built and (f.kind, f.recipe) in makers_kinds]
                hh = island.scratch.get("household_bought", {})
                row["i"][str(i)] = {
                    "pr": s.get(f"production.{GOOD}", 0), "use": s.get(f"use.{GOOD}", 0), "px": s.get(f"price.{GOOD}", 0),
                    "st": s.get(f"stock.{GOOD}", 0),
                    "dem": rec.demand if rec else 0, "sup": rec.supply if rec else 0, "vol": rec.volume if rec else 0,
                    "xb": xbuy.get(i, 0),
                    "q": s.get(f"offtake.qty.{GOOD}", 0), "dl": s.get(f"offtake.delivered.{GOOD}", 0),
                    "un": s.get(f"offtake.unmet.{GOOD}", 0),
                    "users": len(users), "short": len(short),
                    "ur": sum(f.operating_rate for f in users) // max(1, len(users)),
                    "ex": s.get("trade.exports", 0),
                    "hh": sum(v for g, v in hh.items() if g in food or g == "consumer_goods"),
                    "inv": sum(fac.size_of(f) for f in makers),
                    # 作り手の側（産出が減ったわけを見る）: 稼働率・操業目標の平均、在庫、市場で売れた量、
                    # 原料か電力が足りずに絞った作り手の数と足りなかった財
                    "mur": sum(f.operating_rate for f in makers) // max(1, len(makers)),
                    "mtg": sum(f.economy.get("target", 0) for f in makers) // max(1, len(makers)),
                    "mst": sum(stock.total(f.stock, GOOD) for f in makers),
                    "msold": sum(f.economy.get("sales", {}).get(GOOD, [0, 0, 0])[1] for f in makers),
                    "mexp": sum(f.economy.get("expected", {}).get(GOOD, 0) for f in makers),
                    "mshort": dict(Counter(stops.short_good(f.status) or "?" for f in makers
                                           if stops.caps_target(config, f.status))),
                    "mn": len(makers),
                }
            out.write(json.dumps(row, separators=(",", ":")) + "\n")
    return 0


WINDOWS = ((51, 150), (151, 300), (301, 500))
KEYS = ("pr", "use", "px", "B", "sup", "SB", "xb", "q", "dl", "un", "short", "ur", "ex", "hh", "inv",
        "mn", "mur", "mtg", "mst", "msold", "mexp")
NAMES = {"pr": "産出", "use": "国内で使った量", "px": "値段", "B": "国内の買いたい量 B", "sup": "売りに出た量 S", "SB": "S/B",
         "xb": "商社の輸出の買い", "q": "契約の量", "dl": "納品", "un": "引き受け手なし", "short": "石炭で止まった施設",
         "ur": "石炭を使う施設の稼働率", "ex": "輸出の額", "hh": "家計の買った量", "inv": "石炭を作る施設の規模",
         "mn": "石炭を作る施設の数", "mur": "作り手の稼働率", "mtg": "作り手の操業目標", "mst": "作り手の石炭の在庫",
         "msold": "作り手が市場で売れた量", "mexp": "作り手の売れ行きの見込み"}


def _g(x: float) -> str:
    return f"{x:.3g}"


def _window(rows: list[dict], lo: int, hi: int) -> dict:
    w = [r for r in rows if lo <= r["t"] <= hi]
    tot = defaultdict(float)
    n = max(1, len(w))
    for r in w:
        for v in r["i"].values():
            b = max(0, v["dem"] - v["xb"])
            vals = dict(v, B=b, SB=(v["sup"] / b if b else 0))
            for k in KEYS:
                tot[k] += vals.get(k, 0)
    islands = max(1, len(w[0]["i"])) if w else 1
    out = {k: tot[k] / n for k in KEYS}
    out["SB"] = tot["SB"] / n / islands
    out["ur"] = tot["ur"] / n / islands
    for k in ("mur", "mtg"):
        out[k] = tot[k] / n / islands
    return out


def _short_reasons(rows: list[dict], lo: int, hi: int) -> str:
    """作り手が原料か電力の不足で絞ったときの、足りなかった財（島×ターンの延べ）。"""
    c: Counter = Counter()
    for r in rows:
        if lo <= r["t"] <= hi:
            for v in r["i"].values():
                c.update(v.get("mshort", {}))
    return "、".join(f"{g} {n}" for g, n in c.most_common(6)) or "なし"


def report(paths: list[str]) -> int:
    data = {p: [json.loads(line) for line in _open(p, "rt")] for p in paths}
    print("| 期間 | 物 | " + " | ".join(paths) + " |")
    print("|---|---|" + "---|" * len(paths))
    for lo, hi in WINDOWS:
        ws = {p: _window(rows, lo, hi) for p, rows in data.items()}
        for k in KEYS:
            print(f"| {lo}〜{hi} | {NAMES[k]} | " + " | ".join(_g(ws[p][k]) for p in paths) + " |")
    for lo, hi in WINDOWS:
        print(f"- {lo}〜{hi} 作り手が絞ったわけ（足りなかった財、島×ターンの延べ）: "
              + " / ".join(f"{p}: {_short_reasons(rows, lo, hi)}" for p, rows in data.items()))
    # 契約が切れた後に国内の供給が戻るか: 契約のあった島×ターンで、契約が 0 になった後 12 ターンの S/B と止まった施設
    for p, rows in data.items():
        ends = []
        for i in rows[0]["i"]:
            series = [(r["t"], r["i"].get(i)) for r in rows if r["i"].get(i)]
            for (t0, a), (t1, b) in zip(series, series[1:], strict=False):
                if a["q"] > 0 and b["q"] == 0:
                    after = [v for t, v in series if t1 <= t < t1 + 12]
                    before = [v for t, v in series if t0 - 12 < t <= t0]
                    if after and before:
                        ends.append((sum(v["short"] for v in before) / len(before), sum(v["short"] for v in after) / len(after),
                                     sum(v["pr"] for v in before) / len(before), sum(v["pr"] for v in after) / len(after)))
        if ends:
            n = len(ends)
            print(f"- {p}: 契約が切れた {n} 回。前後 12 ターンの平均: 石炭で止まった施設 {_g(sum(e[0] for e in ends) / n)} → "
                  f"{_g(sum(e[1] for e in ends) / n)}、産出 {_g(sum(e[2] for e in ends) / n)} → {_g(sum(e[3] for e in ends) / n)}")
        else:
            print(f"- {p}: 契約が切れた回はない")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="plan")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--turns", type=int, default=500)
    ap.add_argument("--on", action="store_true")
    ap.add_argument("--first", action="store_true")
    ap.add_argument("--out", default="offtake.jsonl.gz")
    ap.add_argument("--report", nargs="*")
    args = ap.parse_args()
    if args.report:
        return report(args.report)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
