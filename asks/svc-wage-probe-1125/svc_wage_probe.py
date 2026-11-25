"""サービス業の値段が賃金に負ける件の probe（掲示板 #4980 の頼み、診断は 11-24）。

診断: 家計がサービスを買うときの指値は、いつもの値段（直近 12 ターンの値段の平均）の
倍率の段（household.consumer_goods_tiers = [0.95, 1.0, 1.05]）でしか出ない。つまり
買い手の上限は 1.05 × 直近 12 ターン平均で、値段の上がりは 1 ターン約 0.75% に
抑えられる。一方で店の賃金はほかの施設と歩調を合わせて上がるので、賃金 ÷ 値段が
開いていく、という読み。この台本は、その裏付けに要る物を毎ターン・島ごとに残す。

    py -3.12 -X utf8 svc_wage_probe.py probe --scenario plan --seed 1 --turns 300 \
        --out svc-plan-s1.jsonl.gz
    py -3.12 -X utf8 svc_wage_probe.py report svc-*.jsonl.gz --out report.md

版は今の main（9335f95a）。場面は quick と同じ 6 場面（plan / none / independent /
small / open / war）× 種 1、300 ターン。サービスの財は 1 島に 1 つ
（services.good = "services"、店は島に 1 つの商業・サービス業）なので、
1 行 = 1 島 × 1 ターンで、svc の中にそのターンのサービスの記録を置く。
量は市場の記録の単位（1000 分の 1）のまま。

  price: 約定した値段（市場の記録 MarketRecord.price）
  usual: いつもの値段。ターン開始時点の状態から、市場の段で家計が計算するのと
    同じ式（economy/prices.py の usual_price、household.usual_price_turns=12 と
    reprice_turns）で計算したもの。値段の履歴は記録の段で足されるので、開始時点で
    計算すれば、そのターンの市場で効いた値と一致する（計画経済の公定価格だけは
    前ターンの値での近似になる）
  cap: 買い手の上限 = scale_price(usual, max(consumer_goods_tiers)) = 1.05 × usual
    （modules/market の _service_buys が top として使う値そのもの）
  cut: 上限で切られた量。_service_buys が island.scratch["unplaced"] に残す量。
    家計のサービスの予算が「売りに出ている量の全部を上限の値段で買う額」を超えた
    とき、その超えた分が上限の値段で量に直されて記録される（注文に出なかった量）。
    cut > 0 のターンは、上限の値段でも買いたい量が売りに出た量を超えていて、
    値段が上がって捌くことが上限で止められたターンと読む
  demand / supply / sold: 市場の記録の買いたい量・売りに出た量・売れた量
  shortage: max(0, demand - supply)
  wage: 商業・サービス業の賃金（1 人あたり。施設の economy["wage"]。
    無ければ島の平均賃金で代用し、そのときは wage_fallback を true にする）
  avg_wage: 島の平均賃金（対照用）
  wage_price: wage ÷ price
  workers: 店で働いている人数。seats: 次のターンに雇う人数の目安
    （economy["seats"]。市場の後に services.plan が決めた後の値）
  labor_short: 人手の足りなさ。中心ごとの「雇う目安 − 雇えた人数」の合計
    （economy/services.py の vacancies）
  ask: 店の次のターンの希望価格（施設の economy["ask"]。値上げが買い手の
    一番高い指値で止められたかどうかを見る対照用）
終わりに <out>.gate.json（崩れの gate.Recorder の counts と found）を書く。

集計の軸（report）:
  島ごとに、賃金の上がり gw = wage(t) / wage(t-1) − 1 と値段の上がり
  gp = price(t) / price(t-1) − 1 の差 gap = gw − gp をターンごとに出す。
  見るのは、gap（特に正の gap の合計）が「上限で切られたターン」
  （cut > 0。補助の区切りとして price >= cap のターンも出す）に
  集まっているかどうか。切られたターンとそうでないターンで gap の中央値を比べ、
  正の gap の合計のうち切られたターンに載った割合を出す。
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
    from hakoniwa_core.economy import services
    from hakoniwa_core.economy.prices import usual_price
    from hakoniwa_core.game import load_game
    from hakoniwa_core.gate import Recorder
    from hakoniwa_core.state.generate import generate_world
    from hakoniwa_core.units import scale_price

    game = load_game(None)
    defs, config = game.defs, game.config
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
                    "wage": wage,
                    "wage_fallback": wage_fallback,
                    "avg_wage": island.economy.average_wage,
                    "wage_price": (
                        round(wage / record.price, 6)
                        if wage and record.price
                        else None
                    ),
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


def _load(path: str) -> tuple[tuple[str, int], dict]:
    """(場面, 種) はファイル名 svc-<場面>-s<種>.jsonl.gz から取る。"""
    name = Path(path).name
    stem = name.removesuffix(".gz").removesuffix(".jsonl")
    parts = stem.split("-")
    scenario = parts[1]
    seed = int(parts[2].lstrip("s"))
    rows: dict[tuple, dict] = {}
    with _open(path, "rt") as fh:
        for line in fh:
            row = json.loads(line)
            rows[(row["i"], row["t"])] = row["svc"]
    gate_file = Path(path).parent / (stem + ".gate.json")
    gate = json.loads(gate_file.read_text(encoding="utf-8")) if gate_file.exists() else {}
    return (scenario, seed), {"rows": rows, "gate": gate}


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
                    "gw": svc["wage"] / prev["wage"] - 1,
                    "gp": svc["price"] / prev["price"] - 1,
                    "gap": svc["wage"] / prev["wage"] - svc["price"] / prev["price"],
                    "cut": svc["cut"],
                    "at_cap": svc["cap"] is not None and svc["price"] >= svc["cap"],
                    "wage_price": svc["wage_price"],
                }
            )
    return out


def _median(values: list[float]) -> str:
    return f"{statistics.median(values):+.4f}" if values else "-"


def report(args: argparse.Namespace) -> int:
    runs: dict[tuple, dict] = {}
    for path in args.files:
        key, data = _load(path)
        runs[key] = data
    lines: list[str] = []
    lines.append("# サービス業の値段と賃金の probe の集計（6 場面 × 種 1、300 ターン）")
    lines.append("")
    # --- 崩れ ---
    lines.append("## 崩れの数（当たった島の数）")
    counts_all: dict[str, int] = defaultdict(int)
    total = 0
    for data in runs.values():
        for kind, n in data["gate"].get("counts", {}).items():
            counts_all[kind] += n
            total += n
    lines.append(f"- 合計: {total}")
    for kind in sorted(counts_all):
        lines.append(f"- {kind}: {counts_all[kind]}")
    lines.append("")
    # --- gap の集まり方 ---
    lines.append("## 賃金と値段の上がりの差（gap = 賃金の上がり − 値段の上がり）")
    lines.append("")
    lines.append(
        "| 組 | ターン数 | gap の中央値 | 正の gap の合計 | 切られたターンの数"
        " | 切られたターンの gap の中央値 | 正の gap のうち切られたターンに載った割合 |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    all_turns: list[dict] = []
    for (scenario, seed), data in sorted(runs.items()):
        turns = _gaps(data["rows"])
        all_turns.extend(turns)
        cut_turns = [x for x in turns if x["cut"] > 0]
        pos_total = sum(x["gap"] for x in turns if x["gap"] > 0)
        pos_cut = sum(x["gap"] for x in cut_turns if x["gap"] > 0)
        share = f"{pos_cut / pos_total:.1%}" if pos_total > 0 else "-"
        lines.append(
            f"| {scenario} 種{seed} | {len(turns)} | {_median([x['gap'] for x in turns])}"
            f" | {pos_total:+.3f} | {len(cut_turns)}"
            f" | {_median([x['gap'] for x in cut_turns])} | {share} |"
        )
    cut_all = [x for x in all_turns if x["cut"] > 0]
    rest_all = [x for x in all_turns if x["cut"] <= 0]
    cap_all = [x for x in all_turns if x["at_cap"]]
    pos_total = sum(x["gap"] for x in all_turns if x["gap"] > 0)
    pos_cut = sum(x["gap"] for x in cut_all if x["gap"] > 0)
    share = f"{pos_cut / pos_total:.1%}" if pos_total > 0 else "-"
    lines.append(
        f"| 全場面 | {len(all_turns)} | {_median([x['gap'] for x in all_turns])}"
        f" | {pos_total:+.3f} | {len(cut_all)}"
        f" | {_median([x['gap'] for x in cut_all])} | {share} |"
    )
    lines.append("")
    lines.append(
        f"- 切られていないターンの gap の中央値: {_median([x['gap'] for x in rest_all])}"
        f"（{len(rest_all)} ターン）"
    )
    lines.append(
        f"- 値段が上限に張り付いたターン（price >= cap）: {len(cap_all)} ターン、"
        f"gap の中央値 {_median([x['gap'] for x in cap_all])}"
    )
    lines.append(
        "- 読み方: 正の gap の合計のうち切られたターンに載った割合が大きく、"
        "切られたターンの gap の中央値が切られていないターンより大きければ、"
        "「賃金と値段の上がりの差は、上限で切られたターンに集まっている」と読む。"
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
