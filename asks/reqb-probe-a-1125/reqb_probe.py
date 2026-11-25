"""要望 B（見込み需要 MK-48、#500 c0dd979c）の probe: 切/入で #2030 の 3 か所が動くかを見る。

quick（reqb-off-1125 / reqb-on-1125、受付の仕事 290/291）では、財ごとの値段・産出も家計の
代わりの買いも記録に残らず、崩れの数しか比べられなかった（掲示板 #4839）。この台本は
毎ターン・島ごと・財ごとに、3 か所（作る量の計画・見込みの値段 prices.expected_price・
参入と作り方の見直し）が読む値と、その結果を残す。

    py -3.12 -X utf8 reqb_probe.py probe --scenario plan --seed 1 --off --turns 300 \
        --out p-off-plan-s1.jsonl.gz
    py -3.12 -X utf8 reqb_probe.py probe --scenario plan --seed 1 --on --turns 300 \
        --out p-on-plan-s1.jsonl.gz
    py -3.12 -X utf8 reqb_probe.py report p-off-*.jsonl.gz p-on-*.jsonl.gz --out report.md

版は c0dd979c（#500。main にはまだ入っていない）。切/入は --off/--on で
config の market.expected_demand をその場で切り替える（game.yaml は書き換えない）。

1 行 = 1 島 × 1 ターン。goods は、そのターンに市場の記録（island.markets）に出た財だけ。
量は市場の記録の単位（1000 分の 1）のまま。production だけ島の統計の単位（個）。
  price: 市場の値段（MarketRecord.price）, demand: 買いたかった量, supply: 売りに出た量,
  sold: 売れた量, shortage: max(0, demand - supply)
  expected: 見込み需要（MarketRecord.expected_demand。切では null）,
  added: expected - demand（本来ほしかった量のうち見込みに足した量）,
  shortage_exp: max(0, expected - supply)（入のときだけ）
  first: 家計の第一希望の献立の量（scratch の expected_first）,
  placed: 家計が実際に注文した食料の量（expected_placed）,
  wanted_off: 売りに出ていなくて注文しなかった家計の量（expected_wanted）,
  subst: max(0, placed - first)（第一希望を超えて注文した量 = 代わりに買った量の推定）,
  unmet_first: max(0, first - placed)（第一希望から減った量）
    ※ 代わりの買いは献立の組み直しで起きるので、「どの財の代わりにどの財」と 1 対 1 には
      決まらない。島×ターンで減った財（unmet_first）と増えた財（subst）を両方残し、
      集計では両側の合計で見る。
  exp_price: prices.expected_price(島, 財, ahead=1) をターン後に計算したもの。
    品切れ（shortage か shortage_exp）があった財だけ数える（重いので）。
    作り方の見直しは作物ごとに grow ターン先を見るが、比べの物差しとして 1 ターン先で統一。
  view_demand / view_shortage: firms._demand_view が返す系列の最後の値（参入・作り方の
    見直し・建て増しが読む買いたい量と品切れ。入では見込み需要に替わる）。
  plan_goal: 計画経済の島の生産目標（island.ext["plan"]["goals"]。次のターンの目標。
    市場の島には明示の計画量がないので null。その島の「作る量の計画」は view_demand で見る）
  production: そのターンに作った量（stats の production.<財>）
島の行には entries（新しくできた施設の数）・recipe_changes（作り方を替えた施設の数）・
exits（消えた施設の数）も置く（参入と作り方の見直しの結果）。
終わりに <out>.gate.json（崩れの gate.Recorder の counts と found）を書く。
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def _open(path: str, mode: str):
    if path.endswith(".gz"):
        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def probe(args: argparse.Namespace) -> int:
    from hakoniwa_core import measure
    from hakoniwa_core.economy.prices import expected_price
    from hakoniwa_core.game import load_game
    from hakoniwa_core.gate import Recorder
    from hakoniwa_core.modules import firms as firms_mod
    from hakoniwa_core.modules import market as market_mod
    from hakoniwa_core.state.generate import generate_world

    game = load_game(None)
    defs, config = game.defs, game.config
    if "expected_demand" not in config.raw.get("market", {}):
        raise SystemExit(
            "この版には market.expected_demand がありません（c0dd979c 以後で回してください）"
        )
    config.raw["market"]["expected_demand"] = bool(args.on)
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)
    recorder = Recorder(world, defs, config)
    prev_facilities: dict[int, dict[int, str]] = {}
    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            recorder.record(world)
            for iid, island in sorted(world.islands.items()):
                if not island.has_economy:
                    continue
                now_fac = {f.id: f.recipe for f in island.facilities if f.built}
                prev = prev_facilities.get(iid)
                if prev is None:
                    entries = recipe_changes = exits = 0
                else:
                    entries = sum(1 for fid in now_fac if fid not in prev)
                    recipe_changes = sum(
                        1 for fid, r in now_fac.items() if fid in prev and prev[fid] != r
                    )
                    exits = sum(1 for fid in prev if fid not in now_fac)
                prev_facilities[iid] = now_fac
                view_demand, view_shortage = firms_mod._demand_view(island, config)
                goals = island.ext.get("plan", {}).get("goals", {})
                first = island.scratch.get(market_mod.EXPECTED_FIRST, {})
                placed = island.scratch.get(market_mod.EXPECTED_PLACED, {})
                wanted_off = island.scratch.get(market_mod.EXPECTED_WANTED, {})
                goods: dict[str, dict] = {}
                for m in island.markets:
                    g = m.good
                    shortage = max(0, m.demand - m.supply)
                    shortage_exp = (
                        max(0, m.expected_demand - m.supply)
                        if m.expected_demand is not None
                        else None
                    )
                    rec: dict = {
                        "price": m.price,
                        "demand": m.demand,
                        "supply": m.supply,
                        "sold": m.volume,
                        "shortage": shortage,
                        "expected": m.expected_demand,
                        "added": (m.expected_demand - m.demand)
                        if m.expected_demand is not None
                        else None,
                        "shortage_exp": shortage_exp,
                        "first": first.get(g, 0),
                        "placed": placed.get(g, 0),
                        "wanted_off": wanted_off.get(g, 0),
                        "subst": max(0, placed.get(g, 0) - first.get(g, 0)),
                        "unmet_first": max(0, first.get(g, 0) - placed.get(g, 0)),
                        "view_demand": (view_demand.get(g) or [None])[-1],
                        "view_shortage": (view_shortage.get(g) or [None])[-1],
                        "plan_goal": goals.get(g),
                        "production": island.stats.get(f"production.{g}"),
                    }
                    if shortage > 0 or (shortage_exp or 0) > 0:
                        rec["exp_price"] = expected_price(
                            island, g, 1, defs, config, turn=world.turn
                        )
                    goods[g] = rec
                if not goods:
                    continue
                row = {
                    "t": world.turn,
                    "i": iid,
                    "regime": island.regime.get("economy", ""),
                    "entries": entries,
                    "recipe_changes": recipe_changes,
                    "exits": exits,
                    "goods": goods,
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    gate_path = args.out.removesuffix(".gz").removesuffix(".jsonl") + ".gate.json"
    with open(gate_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"counts": recorder.collapse_counts(), "found": recorder.collapses()},
            fh,
            ensure_ascii=False,
        )
    return 0


def _load(path: str) -> tuple[dict, dict]:
    """(scenario, seed, on) はファイル名 p-<on|off>-<場面>-s<種>.jsonl.gz から取る。"""
    name = Path(path).name
    stem = name.removesuffix(".gz").removesuffix(".jsonl")
    parts = stem.split("-")
    on = parts[1] == "on"
    scenario = parts[2]
    seed = int(parts[3].lstrip("s"))
    rows: dict[tuple, dict] = {}
    with _open(path, "rt") as fh:
        for line in fh:
            row = json.loads(line)
            for g, rec in row["goods"].items():
                rows[(row["i"], row["t"], g)] = rec
    gate_file = Path(path).parent / (stem + ".gate.json")
    gate = json.loads(gate_file.read_text(encoding="utf-8")) if gate_file.exists() else {}
    return (scenario, seed, on), {"rows": rows, "gate": gate}


def report(args: argparse.Namespace) -> int:
    runs: dict[tuple, dict] = {}
    for path in args.files:
        key, data = _load(path)
        runs[key] = data
    lines: list[str] = []
    lines.append("# 要望 B probe の集計（切/入、場面 × 種 3 本、300 ターン）")
    lines.append("")
    # --- 崩れ: 種ごとの合計と幅、会社の連鎖（firm_cascade） ---
    lines.append("## 崩れの数（当たった島の数。種ごとの合計と最小〜最大）")
    by_side: dict[bool, dict[str, list[int]]] = {False: defaultdict(list), True: defaultdict(list)}
    totals: dict[bool, list[int]] = {False: [], True: []}
    for (_scenario, _seed, on), data in sorted(runs.items()):
        counts = data["gate"].get("counts", {})
        totals[on].append(sum(counts.values()))
        for kind, n in counts.items():
            by_side[on][kind].append(n)
    lines.append(f"- 合計: 切 {totals[False]} / 入 {totals[True]}")
    for kind in sorted(set(by_side[False]) | set(by_side[True])):
        off, on_ = by_side[False].get(kind, []), by_side[True].get(kind, [])
        lines.append(
            f"- {kind}: 切 合計 {sum(off)}"
            f"（{min(off) if off else '-'}〜{max(off) if off else '-'}）"
            f" / 入 合計 {sum(on_)}"
            f"（{min(on_) if on_ else '-'}〜{max(on_) if on_ else '-'}）"
        )
    lines.append("  会社の連鎖は firm_cascade。差が種 3 本の幅の中なら、ゆらぎの内と読む。")
    lines.append("")
    # --- 財ごと: 品切れのある島×ターンで、入の exp_price と計画が上がったか、品切れが減ったか ---
    lines.append("## 財ごと（品切れのあった島×ターンの組で比べる）")
    lines.append(
        "| 財 | 組の数 | exp_price の差（入−切）の中央値 | 上がった組の割合"
        " | view_demand の差の中央値 | plan_goal の差の中央値"
        " | 品切れ量の合計 切 → 入 | 代替購入 subst の合計（入）"
        " | 第一希望の減り unmet_first の合計（入） |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    goods_all = set()
    for data in runs.values():
        goods_all |= {g for (_, _, g) in data["rows"]}
    for g in sorted(goods_all):
        pairs = []
        shortage_off = shortage_on = 0
        subst_on = unmet_on = 0
        keys = set()
        for (scenario, seed, on), data in runs.items():
            if on:
                continue
            other = runs.get((scenario, seed, True))
            if other is None:
                continue
            for (iid, t, good), rec in data["rows"].items():
                if good != g:
                    continue
                o = other["rows"].get((iid, t, good))
                if o is None:
                    continue
                keys.add((iid, t))
                shortage_off += rec["shortage"]
                shortage_on += o["shortage"]
                subst_on += o.get("subst", 0)
                unmet_on += o.get("unmet_first", 0)
                if rec["shortage"] > 0 or (o.get("shortage_exp") or 0) > 0:
                    pairs.append((rec, o))
        if not pairs:
            continue
        dep = [
            o["exp_price"] - r["exp_price"]
            for r, o in pairs
            if "exp_price" in r and "exp_price" in o
        ]
        dvd = [
            o["view_demand"] - r["view_demand"]
            for r, o in pairs
            if r.get("view_demand") is not None and o.get("view_demand") is not None
        ]
        dpg = [
            o["plan_goal"] - r["plan_goal"]
            for r, o in pairs
            if r.get("plan_goal") is not None and o.get("plan_goal") is not None
        ]
        med = lambda xs: f"{statistics.median(xs):,.0f}" if xs else "-"  # noqa: E731
        share = f"{sum(1 for x in dep if x > 0) / len(dep):.0%}" if dep else "-"
        lines.append(
            f"| {g} | {len(pairs)} | {med(dep)} | {share} | {med(dvd)} | {med(dpg)} | "
            f"{shortage_off:,} → {shortage_on:,} | {subst_on:,} | {unmet_on:,} |"
        )
    lines.append("")
    lines.append(
        "読み方: exp_price と view_demand / plan_goal が入で上がり、"
        "それでも品切れ量が減らなければ、"
    )
    lines.append(
        "伝わった先（参入・作り方・産出のどこか）で止まっている。上がらなければ、記録の足し方か"
    )
    lines.append("3 か所の読み方に問題がある。")
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--scenario", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--turns", type=int, default=300)
    p.add_argument("--on", action="store_true")
    p.add_argument("--off", action="store_true")
    p.add_argument("--out", required=True)
    p.set_defaults(func=probe)
    r = sub.add_parser("report")
    r.add_argument("files", nargs="+")
    r.add_argument("--out")
    r.set_defaults(func=report)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
