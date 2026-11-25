"""サービス業の売り手の変動費下限（下書き）の probe（掲示板 #5198 の決定）。

下書き（枝 mugi/svc-cost-floor、game.yaml の services.cost_floor、既定は切）:
サービスの売り手にも、計画経済の原価の決めと同じ「賃金 ÷ 1人の量 × (1 + 上乗せ1割)」
の下限を付ける。切/入は --off/--on で config の services.cost_floor をその場で
切り替える。

    py -3.12 -X utf8 svc_floor_probe.py probe --scenario plan --seed 1 --off \
        --turns 300 --out svc-off-plan-s1.jsonl.gz
    py -3.12 -X utf8 svc_floor_probe.py report svc-*.jsonl.gz --out report.md

場面は svc-wage-probe と同じ 6 場面（plan / none / independent / small / open /
war）× 種 1〜3、300 ターン、切/入の 36 本。サービスの財は 1 島に 1 つなので、
1 行 = 1 島 × 1 ターンで、svc の中にそのターンのサービスの記録を置く。
量は市場の記録の単位（1000 分の 1）のまま。

svc-wage-probe（#5174・#5193 の集計と同じ物差し）の項目:
  price: 約定した値段（市場の記録 MarketRecord.price）
  usual: いつもの値段（ターン開始時点、家計の計算と同じ式）
  cap: 買い手の上限 = 1.05 × usual
  cut: 上限で切られた量（scratch["unplaced"]。注文に出なかった量）
  demand / supply / sold: 市場の記録の買いたい量・売りに出た量・売れた量
  shortage: max(0, demand - supply)
  wage: 商業・サービス業の賃金（1 人あたり。無ければ島の平均賃金で代用し
    wage_fallback を true にする）
  avg_wage: 島の平均賃金（対照用）
  wage_price: wage ÷ price
  workers / seats / labor_short: 店の人数・次の目安・人手の足りなさ
  ask: 店の次のターンの希望価格

この probe で足した項目（#5198 の見る物）:
  floor: そのターンの賃金での下限（services.cost_floor。切では 0、入では
    unit_price と同じ値）。ask が floor 以上に保たれているかの検算に使う
  unmet: 家計が買いたかったのに買えなかった量 = demand − sold（市場の記録の
    demand は注文しなかった分 unplaced を含む。指値が約定価格に届かなかった
    段も含むので、modules/market の _services_unmet より広い近似）
  profit: そのターンの店の儲け（island.books の、そのターンの帳簿の PROFIT。
    帳簿はターンごとに作り直されるので、そのターンの分だけ）
  cpi: そのターン終わりの物価指数（economy.cpi_history の最後）
終わりに <out>.gate.json（崩れの gate.Recorder の counts と found）を書く。

集計（report）の軸:
  - 賃金と値段の上がりの差 gap（svc-wage と同じ）を切/入で並べる
  - 賃金÷値段の中央値を序盤（1〜50 ターン）と終盤（251〜300 ターン）で切/入比較
  - unmet の合計と、unmet > 0 のターンの割合
  - 終盤（251〜300 ターン）の profit の中央値と、島ごとの終盤 profit が負の島
  - CPI の伸び（最後 ÷ 初め）の中央値
  - gate の崩れの合計
  合格の線は README.md に置いた（結果を見る前に固定）。
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
    from hakoniwa_core.economy import books, services
    from hakoniwa_core.economy.prices import usual_price
    from hakoniwa_core.game import load_game
    from hakoniwa_core.gate import Recorder
    from hakoniwa_core.state.generate import generate_world
    from hakoniwa_core.units import scale_price

    game = load_game(None)
    defs, config = game.defs, game.config
    config.raw["services"]["cost_floor"] = bool(args.on)
    settings = config.get("household")
    good = services.good_of(config)
    top_factor = max(settings["consumer_goods_tiers"])
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)
    recorder = Recorder(world, defs, config)
    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            # 上限はターン開始時点の状態で計算する（市場の段で家計が見る値と一致）
            caps: dict[int, tuple[int, int]] = {}
            for iid, island in world.islands.items():
                if not island.has_economy:
                    continue
                usual = usual_price(
                    island,
                    good,
                    settings["usual_price_turns"],
                    settings["reprice_turns"],
                )
                caps[iid] = (usual, scale_price(usual, top_factor))
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            recorder.record(world)
            for iid, island in sorted(world.islands.items()):
                if not island.has_economy:
                    continue
                record = next((m for m in island.markets if m.good == good), None)
                if record is None:
                    continue
                f = services.facility_of(island, config)
                wage = f.economy.get("wage") if f is not None else None
                wage_fallback = wage is None
                if wage_fallback:
                    wage = island.economy.average_wage
                usual, cap = caps.get(iid, (None, None))
                svc = {
                    "price": record.price,
                    "usual": usual,
                    "cap": cap,
                    "cut": island.scratch.get("unplaced", {}).get(good, 0),
                    "demand": record.demand,
                    "supply": record.supply,
                    "sold": record.volume,
                    "shortage": max(0, record.demand - record.supply),
                    "unmet": max(0, record.demand - record.volume),
                    "wage": wage,
                    "wage_fallback": wage_fallback,
                    "avg_wage": island.economy.average_wage,
                    "wage_price": (
                        round(wage / record.price, 6) if wage and record.price else None
                    ),
                    "floor": services.cost_floor(config, wage) if wage else 0,
                    "workers": f.workers if f is not None else None,
                    "seats": f.economy.get(services.SEATS) if f is not None else None,
                    "labor_short": (
                        sum(n for _, n in services.vacancies(island, config))
                        if f is not None
                        else None
                    ),
                    "ask": (
                        f.economy.get("ask", {}).get(good) if f is not None else None
                    ),
                    "profit": (
                        sum(
                            b.items.get(books.PROFIT, 0)
                            for b in island.books
                            if b.facility == f.id
                        )
                        if f is not None
                        else None
                    ),
                    "cpi": (
                        island.economy.cpi_history[-1]
                        if island.economy.cpi_history
                        else None
                    ),
                }
                row = {
                    "t": world.turn,
                    "i": iid,
                    "regime": island.regime.get("economy", ""),
                    "svc": svc,
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


def _load(path: str) -> tuple[tuple[str, bool, int], dict]:
    """(場面, 入か, 種) はファイル名 svc-<off|on>-<場面>-s<種>.jsonl.gz から取る。"""
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
            rows[(row["i"], row["t"])] = row["svc"]
    gate_file = Path(path).parent / (stem + ".gate.json")
    gate = json.loads(gate_file.read_text(encoding="utf-8")) if gate_file.exists() else {}
    return (scenario, on, seed), {"rows": rows, "gate": gate}


def _gaps(rows: dict) -> list[dict]:
    """島ごとに並べ替え、賃金と値段の上がりの差 gap を付けたターンの列を返す。"""
    by_island: dict[int, list[tuple[int, dict]]] = defaultdict(list)
    for (iid, t), svc in rows.items():
        by_island[iid].append((t, svc))
    out = []
    for iid, series in sorted(by_island.items()):
        series.sort()
        for (t0, prev), (t1, svc) in zip(series, series[1:], strict=False):
            if t1 != t0 + 1:
                continue
            if not prev["price"] or not svc["price"] or not prev["wage"] or not svc["wage"]:
                continue
            out.append(
                {
                    "i": iid,
                    "t": t1,
                    "gap": svc["wage"] / prev["wage"] - svc["price"] / prev["price"],
                    "cut": svc["cut"],
                    "wage_price": svc["wage_price"],
                }
            )
    return out


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _fmt(value: float | None, spec: str = "+.4f") -> str:
    return format(value, spec) if value is not None else "-"


def _window_median(rows: dict, key: str, lo: int, hi: int) -> float | None:
    values = [
        svc[key]
        for (_iid, t), svc in rows.items()
        if lo <= t <= hi and svc.get(key) is not None
    ]
    return _median(values)


def _final_by_island(rows: dict, key: str) -> dict[int, float]:
    """島ごとの最後のターンの値。"""
    last: dict[int, tuple[int, float]] = {}
    for (iid, t), svc in rows.items():
        if svc.get(key) is None:
            continue
        if iid not in last or t > last[iid][0]:
            last[iid] = (t, svc[key])
    return {iid: value for iid, (_t, value) in last.items()}


def _first_cpi(rows: dict) -> dict[int, float]:
    first: dict[int, tuple[int, float]] = {}
    for (iid, t), svc in rows.items():
        if svc.get("cpi") is None:
            continue
        if iid not in first or t < first[iid][0]:
            first[iid] = (t, svc["cpi"])
    return {iid: value for iid, (_t, value) in first.items()}


def report(args: argparse.Namespace) -> int:
    runs: dict[tuple, dict] = {}
    for path in args.files:
        key, data = _load(path)
        runs[key] = data
    lines: list[str] = []
    lines.append("# サービス業の変動費下限（下書き）の probe の集計（6 場面 × 種 1〜3、切/入）")
    lines.append("")
    lines.append("合格の線は README.md（結果を見る前に固定）。判定は場面ごと・種の中央値。")
    lines.append("")

    # --- 崩れ ---
    lines.append("## 崩れの数（gate、当たった島の数）")
    for on in (False, True):
        counts_all: dict[str, int] = defaultdict(int)
        total = 0
        for (scenario, run_on, _seed), data in sorted(runs.items()):
            if run_on != on:
                continue
            for kind, n in data["gate"].get("counts", {}).items():
                counts_all[kind] += n
                total += n
        label = "入" if on else "切"
        detail = "、".join(f"{k}: {v}" for k, v in sorted(counts_all.items()))
        lines.append(f"- {label}: 合計 {total}" + (f"（{detail}）" if detail else ""))
    lines.append("")

    # --- 賃金÷値段 ---
    lines.append("## 賃金÷値段の中央値（序盤 1〜50 ターン → 終盤 251〜300 ターン）")
    lines.append("")
    lines.append("| 場面 | 種 | 切 序盤 | 切 終盤 | 入 序盤 | 入 終盤 | 終盤の差（入 − 切） |")
    lines.append("|---|---|---|---|---|---|---|")
    scenarios = sorted({key[0] for key in runs})
    seeds = sorted({key[2] for key in runs})
    for scenario in scenarios:
        for seed in seeds:
            off = runs.get((scenario, False, seed))
            on = runs.get((scenario, True, seed))
            if off is None or on is None:
                continue
            off_e = _window_median(off["rows"], "wage_price", 1, 50)
            off_l = _window_median(off["rows"], "wage_price", 251, 300)
            on_e = _window_median(on["rows"], "wage_price", 1, 50)
            on_l = _window_median(on["rows"], "wage_price", 251, 300)
            diff = (
                on_l - off_l if on_l is not None and off_l is not None else None
            )
            lines.append(
                f"| {scenario} | {seed} | {_fmt(off_e, '.3f')} | {_fmt(off_l, '.3f')}"
                f" | {_fmt(on_e, '.3f')} | {_fmt(on_l, '.3f')} | {_fmt(diff)} |"
            )
    lines.append("")

    # --- gap ---
    lines.append("## 賃金と値段の上がりの差（gap、正の gap の合計と載り方）")
    lines.append("")
    lines.append("| 組 | ターン数 | gap の中央値 | 正の gap の合計 | 正の gap のうち切られたターン |")
    lines.append("|---|---|---|---|---|")
    for (scenario, on, seed), data in sorted(runs.items()):
        turns = _gaps(data["rows"])
        cut_turns = [x for x in turns if x["cut"] > 0]
        pos_total = sum(x["gap"] for x in turns if x["gap"] > 0)
        pos_cut = sum(x["gap"] for x in cut_turns if x["gap"] > 0)
        share = f"{pos_cut / pos_total:.1%}" if pos_total > 0 else "-"
        label = f"{scenario} {'入' if on else '切'} 種{seed}"
        lines.append(
            f"| {label} | {len(turns)} | {_fmt(_median([x['gap'] for x in turns]))}"
            f" | {pos_total:+.3f} | {share} |"
        )
    lines.append("")

    # --- 買えなかった量・儲け・物価 ---
    lines.append("## 買えなかった量（unmet）・店の儲け（profit）・物価（CPI）")
    lines.append("")
    lines.append(
        "| 組 | unmet の合計 | unmet > 0 のターン | 終盤の profit の中央値（1ターン）"
        " | 終盤 profit が負の島 | CPI の伸び（最後÷初め）の中央値 | ask < floor のターン |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for (scenario, on, seed), data in sorted(runs.items()):
        rows = data["rows"]
        unmet_total = sum(svc["unmet"] for svc in rows.values())
        unmet_turns = sum(1 for svc in rows.values() if svc["unmet"] > 0)
        all_turns = len(rows)
        late_profit = [
            svc["profit"]
            for (_iid, t), svc in rows.items()
            if 251 <= t <= 300 and svc.get("profit") is not None
        ]
        by_island: dict[int, list[float]] = defaultdict(list)
        for (iid, t), svc in rows.items():
            if 251 <= t <= 300 and svc.get("profit") is not None:
                by_island[iid].append(svc["profit"])
        neg = sum(
            1
            for values in by_island.values()
            if values and _median(values) is not None and _median(values) < 0
        )
        first_cpi = _first_cpi(rows)
        last_cpi = _final_by_island(rows, "cpi")
        growth = [
            last_cpi[i] / first_cpi[i]
            for i in first_cpi
            if i in last_cpi and first_cpi[i]
        ]
        below = sum(
            1
            for svc in rows.values()
            if svc.get("ask") is not None
            and svc.get("floor")
            and svc["ask"] < svc["floor"]
        )
        label = f"{scenario} {'入' if on else '切'} 種{seed}"
        lines.append(
            f"| {label} | {unmet_total} | {unmet_turns} / {all_turns}"
            f" | {_fmt(_median(late_profit), '+,.0f')}"
            f" | {neg} / {len(by_island)} | {_fmt(_median(growth), '.4f')} | {below} |"
        )
    lines.append("")
    lines.append(
        "- ask < floor のターンは検算用: 入では、賃金の記録がある島で希望価格が"
        "下限を割って残るターンは無いはず（あれば下限が届いていない）。"
    )
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--scenario", required=True)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--turns", type=int, default=300)
    p.add_argument("--off", action="store_true")
    p.add_argument("--on", action="store_true")
    p.add_argument("--out", required=True)
    p.set_defaults(func=probe)
    r = sub.add_parser("report")
    r.add_argument("files", nargs="+")
    r.add_argument("--out")
    r.set_defaults(func=report)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
