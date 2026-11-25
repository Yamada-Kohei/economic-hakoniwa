"""国の倉庫の穀物の先行放出（下書き第 2 版）の probe（掲示板 #5091 の決定・#5093 の計画）。

下書き第 2 版: 放出の判定は第 1 版と同じ（直近の約定値が、直近 window ターンの値段の
移動中央値の multiple 倍以上か、rise_turns ターン前から rise 以上上がったか）だが、
出す量が全量でなく、「値段を基準（移動中央値）に戻すのに足りる分」（前のターンの
市場の記録の買えなかった量を price/base で割り増した見積もり）を上限に、1 ターンの
割合上限（max_fraction）と倉庫の下限（floor_turns ターン分。先行放出だけに適用）を
同時に効かせた小出し（hakoniwa_core/economy/reserve.py の price_lead_amount。
game.yaml の food_reserve.price_lead、既定は切）。

    py -3.12 -X utf8 grain_release_probe.py probe --scenario plan --seed 1 --off \
        --turns 300 --out g-off-plan-s1.jsonl.gz
    py -3.12 -X utf8 grain_release_probe.py probe --scenario plan --seed 1 --on \
        --turns 300 --out g-on-plan-s1.jsonl.gz
    py -3.12 -X utf8 grain_release_probe.py report g-*.jsonl.gz --out report.md

版は下書き第 2 版の枝 mugi/food-reserve-price-lead-v2 の commit（ask.json の commit）。
切/入は --off/--on で config の food_reserve.price_lead.enabled をその場で切り替える
（game.yaml は書き換えない）。場面は quick と同じ 6 場面（plan / none / independent /
small / open / war）× 種 1〜3、300 ターン。1 行 = 1 島 × 1 ターンで、grn の中に
そのターンの穀物（food_reserve.good）の記録を置く。量は市場の記録の単位
（1000 分の 1）のまま。

  price: 約定した値段（市場の記録 MarketRecord.price）
  usual: いつもの値段。ターン開始時点の状態から、家計と同じ式（economy/prices.py の
    usual_price、usual_price_turns と reprice_turns）で計算したもの
  base: 先行放出の基準。ターン開始時点の price_history の直近 window ターンの
    移動中央値（reserve.price_lead が市場の段で使うのと同じ並び・同じ窓）
  last: 直近の約定値 = ターン開始時点の economy.prices（price_lead の判定に使う price）
  ratio: last / base（ターン開始時点。発火しなかったターンでも残す。後の線引き
    調整用に、report で非発火ターンの最小・最大を出す。#5075）
  rise3: rise_turns ターン前からの last の変化率（ターン開始時点。ratio と同じ置き方）
  floor: 第 2 版の倉庫の下限の量（ターン開始時点。目安 × floor_turns / turns。
    ゲームの price_lead_amount が使うのと同じ式）
  warehouse_start: ターン開始時点の国の倉庫の穀物の量
  release_qty / release_reason / release_ask: このターンの備蓄の放出。市場の段の
    _reserve_orders が island.scratch[RESERVE_RELEASE] に残す量・わけ・指値。
    わけは hunger（飢えの線）/ price（(1 + release) の線）/ lead_multiple（倍率の線）/
    lead_rise（上がりの速さの線）。放出しなかったターンは qty 0・reason null
  release_need / release_floor / release_cap / release_stock: 先行放出のときだけ。
    量の見積もり（need。記録がなく見積もりが立たなければ null）・下限の量・
    どの上限で量が決まったか（need / fraction / floor）・放出を決めた時点の倉庫の量
  rotate_due: 入れ替えで売る量。ターン開始時点の reserve.rotating の値（傷む前に
    売る分。放出とは別に毎ターン出ることがある）
  warehouse: ターン後の国の倉庫の穀物の量
  demand / supply / sold: 市場の記録の買いたい量・売りに出た量・売れた量
  fed_min: 家計の層の FED（前のターンに食べられた割合、1000 分の 1）の最小。
    ターン後に読むので、そのターンに食べた結果
  hungry: ターン後の状態で _anyone_hungry と同じ判定（次のターンの放出の線に
    届くか）。層の FED が household.hungry.threshold 未満か、still_hungry
終わりに <out>.gate.json（崩れの gate.Recorder の counts と found）を書く。

集計の軸（report）:
  場面 × 種ごとに切/入を並べる表に加え、会議の合格線（#5091）での判定を出す:
  「飢えターンと値段の最大が、切より大きくならず、少なくとも半分の場面で小さく
  なる」。判定は場面ごと・種の中央値で行い（#5097）、値段の最大の最悪種は参考と
  して併記する（判定には使わない）。あわせて、下限の効き方（下限で量が決まった
  発火の回数、倉庫が下限以下で終わったターンの数＝下限に張り付いたターン数と
  到達の有無）と、発火の分類（放出後に値段がさらに上がったか＝スパイクの続きか、
  下限まで出して終わったか。#5075）、非発火ターンの ratio / rise3 の最小・最大
  （線までの距離。後の線引き調整用）を出す。
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
    from hakoniwa_core import measure, stock
    from hakoniwa_core.economy import reserve
    from hakoniwa_core.economy.prices import usual_price
    from hakoniwa_core.game import load_game
    from hakoniwa_core.gate import Recorder
    from hakoniwa_core.modules.market import RESERVE_RELEASE
    from hakoniwa_core.modules.population import FED, still_hungry
    from hakoniwa_core.state.generate import generate_world
    from hakoniwa_core.units import to_milli

    game = load_game(None)
    defs, config = game.defs, game.config
    spec = config.get("food_reserve")
    if "price_lead" not in spec:
        print("この版には food_reserve.price_lead がありません（下書きの枝の版で回すこと）")
        return 2
    config.raw["food_reserve"]["price_lead"]["enabled"] = bool(args.on)
    lead = spec["price_lead"]
    window = int(lead.get("window", 12))
    rise_turns = int(lead.get("rise_turns", 3))
    floor_turns = int(lead.get("floor_turns", 1))
    spec_turns = int(spec.get("turns", 0))
    good = spec["good"]
    settings = config.get("household")
    threshold = to_milli(settings["hungry"]["threshold"])
    hunger_settings = config.get("population.hunger")
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)
    recorder = Recorder(world, defs, config)
    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            # 基準と直近値はターン開始時点の状態で計算する（市場の段の判定が見る値と一致）
            starts: dict[int, dict] = {}
            for iid, island in world.islands.items():
                if not island.has_economy:
                    continue
                history = island.economy.price_history.get(good, [])
                base = round(statistics.median(history[-window:])) if history else 0
                last = island.economy.prices.get(good, 0)
                before = history[-1 - rise_turns] if len(history) > rise_turns else 0
                goal = reserve.target(island, defs, config)
                starts[iid] = {
                    "usual": usual_price(
                        island, good, settings["usual_price_turns"], settings["reprice_turns"]
                    ),
                    "base": base,
                    "last": last,
                    "ratio": round(last / base, 4) if base > 0 and last > 0 else None,
                    "rise3": round((last - before) / before, 4) if before > 0 and last > 0 else None,
                    "floor": goal * floor_turns // spec_turns if spec_turns > 0 else 0,
                    "warehouse_start": stock.total(island.warehouse, good),
                    "rotate_due": reserve.rotating(island, defs, config, world.turn),
                }
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            recorder.record(world)
            for iid, island in sorted(world.islands.items()):
                if not island.has_economy:
                    continue
                record = next((m for m in island.markets if m.good == good), None)
                if record is None:
                    continue
                note = island.scratch.get(RESERVE_RELEASE) or {}
                layers = island.economy.households.values()
                fed_min = min((s.get(FED, 1000) for s in layers), default=1000)
                hungry = any(
                    s.get(FED, 1000) < threshold or still_hungry(s.get("hunger", 0), hunger_settings)
                    for s in layers
                )
                start = starts.get(iid, {})
                grn = {
                    "price": record.price,
                    "usual": start.get("usual"),
                    "base": start.get("base"),
                    "last": start.get("last"),
                    "ratio": start.get("ratio"),
                    "rise3": start.get("rise3"),
                    "floor": start.get("floor"),
                    "warehouse_start": start.get("warehouse_start"),
                    "release_qty": note.get("qty", 0),
                    "release_reason": note.get("reason"),
                    "release_ask": note.get("price"),
                    "release_need": note.get("need"),
                    "release_floor": note.get("floor"),
                    "release_cap": note.get("cap"),
                    "release_stock": note.get("stock"),
                    "rotate_due": start.get("rotate_due", 0),
                    "warehouse": stock.total(island.warehouse, good),
                    "demand": record.demand,
                    "supply": record.supply,
                    "sold": record.volume,
                    "fed_min": fed_min,
                    "hungry": hungry,
                }
                row = {"t": world.turn, "i": iid, "grn": grn}
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    gate_path = args.out.removesuffix(".gz").removesuffix(".jsonl") + ".gate.json"
    with open(gate_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"counts": recorder.collapse_counts(), "found": recorder.collapses()},
            fh,
            ensure_ascii=False,
        )
    return 0


def _load(path: str) -> list[dict]:
    rows = []
    with _open(path, "rt") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _kind_of(path: str) -> tuple[str, bool, int]:
    """ファイル名 g-<on|off>-<場面>-s<種>.jsonl.gz から (場面, 入か, 種) を取る。"""
    name = Path(path).name
    stem = name.removesuffix(".jsonl.gz").removesuffix(".jsonl")
    parts = stem.split("-")
    on = parts[1] == "on"
    scenario = parts[2] if len(parts) > 2 else "?"
    seed = int(parts[3][1:]) if len(parts) > 3 and parts[3].startswith("s") else 0
    return scenario, on, seed


def _summary(rows: list[dict]) -> dict:
    """1 本分（場面 × 種 × 切/入）の集計。"""
    rel = [r for r in rows if r["grn"]["release_qty"] > 0]
    lead = [r for r in rel if str(r["grn"]["release_reason"]).startswith("lead_")]
    hunger_rel = [r for r in rel if r["grn"]["release_reason"] == "hunger"]
    price_rel = [r for r in rel if r["grn"]["release_reason"] == "price"]
    prices = [r["grn"]["price"] for r in rows if r["grn"]["price"]]
    last_turn = max(r["t"] for r in rows) if rows else 0
    return {
        "rel_turns": len(rel),
        "lead_n": len(lead),
        "lead_qty": sum(r["grn"]["release_qty"] for r in lead),
        "hunger_n": len(hunger_rel),
        "price_n": len(price_rel),
        "rel_qty": sum(r["grn"]["release_qty"] for r in rel),
        "hungry_turns": sum(1 for r in rows if r["grn"]["hungry"]),
        "fed_min": min((r["grn"]["fed_min"] for r in rows), default=None),
        "price_median": round(statistics.median(prices)) if prices else None,
        "price_max": max(prices) if prices else None,
        "warehouse_last": sum(r["grn"]["warehouse"] for r in rows if r["t"] == last_turn),
        "floor_turns": sum(
            1
            for r in rows
            if r["grn"].get("floor") is not None and r["grn"]["warehouse"] <= r["grn"]["floor"]
        ),
        "cap_counts": {
            cap: sum(1 for r in lead if r["grn"].get("release_cap") == cap)
            for cap in ("need", "fraction", "floor")
        },
    }


def _fmt(value) -> str:
    return "-" if value is None else f"{value:,}"


def report(args: argparse.Namespace) -> int:
    runs: dict[tuple[str, int, bool], list[dict]] = {}
    for path in args.files:
        scenario, on, seed = _kind_of(path)
        runs[(scenario, seed, on)] = _load(path)
    scenarios = sorted({s for s, _, _ in runs})
    seeds = sorted({sd for _, sd, _ in runs})
    lines = ["# 穀物の先行放出（第 2 版）の probe の集計", ""]

    # 場面 × 種ごとの表（第 1 版と同じ物差し）
    for scenario in scenarios:
        for seed in seeds:
            if (scenario, seed, False) not in runs and (scenario, seed, True) not in runs:
                continue
            lines.append(f"## {scenario}（種 {seed}）")
            lines.append("")
            lines.append(
                "| 側 | 放出ターン | 先行放出（回・量） | 飢えの線の放出（回） "
                "| 値段の線の放出（回） | 放出量の合計 | 飢えターン（島×ターン） "
                "| FED 最小 | 値段の中央値 | 値段の最大 | 倉庫の最後 |"
            )
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
            for on in (False, True):
                rows = runs.get((scenario, seed, on), [])
                if not rows:
                    continue
                s = _summary(rows)
                lines.append(
                    f"| {'入' if on else '切'} | {s['rel_turns']} "
                    f"| {s['lead_n']}・{s['lead_qty']:,} "
                    f"| {s['hunger_n']} | {s['price_n']} "
                    f"| {s['rel_qty']:,} "
                    f"| {s['hungry_turns']} "
                    f"| {_fmt(s['fed_min'])} "
                    f"| {_fmt(s['price_median'])} "
                    f"| {_fmt(s['price_max'])} "
                    f"| {s['warehouse_last']:,} |"
                )
            lines.append("")

    # 合格線での判定（#5091。場面ごと・種の中央値で判定し、最悪種は参考。#5097）
    lines.append("## 合格線での判定（場面ごと・種の中央値）")
    lines.append("")
    lines.append(
        "合格の線（#5091）: 飢えターンと値段の最大が、切より大きくならず、少なくとも"
        "半分の場面で小さくなる。判定は種の中央値で行い、値段の最大の最悪種は参考に"
        "併記するだけで判定には使わない（#5097）。"
    )
    lines.append("")
    lines.append(
        "| 場面 | 飢えターン 切→入（中央値） | 値段の最大 切→入（中央値） "
        "| 値段の最大・入の最悪種（参考） | 大きくならない | 小さくなった |"
    )
    lines.append("|---|---|---|---|---|---|")
    not_bigger_all = True
    smaller_count = 0
    for scenario in scenarios:
        med: dict[str, dict[bool, float | None]] = {}
        worst_on_max = None
        for metric in ("hungry_turns", "price_max"):
            med[metric] = {}
            for on in (False, True):
                values = [
                    _summary(runs[(scenario, sd, on)])[metric]
                    for sd in seeds
                    if (scenario, sd, on) in runs
                ]
                values = [v for v in values if v is not None]
                med[metric][on] = statistics.median(values) if values else None
            if metric == "price_max":
                on_values = [
                    _summary(runs[(scenario, sd, True)])["price_max"]
                    for sd in seeds
                    if (scenario, sd, True) in runs
                ]
                on_values = [v for v in on_values if v is not None]
                worst_on_max = max(on_values) if on_values else None
        ok = all(
            med[m][True] is not None and med[m][False] is not None and med[m][True] <= med[m][False]
            for m in ("hungry_turns", "price_max")
        )
        smaller = all(
            med[m][True] is not None and med[m][False] is not None and med[m][True] < med[m][False]
            for m in ("hungry_turns", "price_max")
        )
        not_bigger_all = not_bigger_all and ok
        smaller_count += 1 if smaller else 0
        lines.append(
            f"| {scenario} | {_fmt(med['hungry_turns'][False])} → {_fmt(med['hungry_turns'][True])} "
            f"| {_fmt(med['price_max'][False])} → {_fmt(med['price_max'][True])} "
            f"| {_fmt(worst_on_max)} | {'○' if ok else '×'} | {'○' if smaller else '×'} |"
        )
    lines.append("")
    half = (len(scenarios) + 1) // 2
    verdict = "合格" if (not_bigger_all and smaller_count >= half) else "不合格"
    lines.append(
        f"判定: {verdict}（大きくならない場面: {'全部' if not_bigger_all else '一部に増えた場面あり'}、"
        f"小さくなった場面: {smaller_count} / {len(scenarios)}、要る数: {half} 以上）"
    )
    lines.append("")

    # 下限の効き方と発火の分類（#5095・#5097。種は合計）
    lines.append("## 下限の効き方と発火の分類（種の合計）")
    lines.append("")
    lines.append(
        "| 場面 | 側 | 先行放出（回） | 量が決まった上限 need / fraction / floor "
        "| 下限に張り付いたターン | 下限到達 | 発火後に値段がさらに上がった（回） "
        "| 下限まで出して終わった発火（回） | 放出割合の中央値 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for scenario in scenarios:
        for on in (False, True):
            rows_all = [
                r for sd in seeds for r in runs.get((scenario, sd, on), [])
            ]
            if not rows_all:
                continue
            s = _summary(rows_all)
            caps = s["cap_counts"]
            # 発火の分類: 同じ本・同じ島の次のターンの値段と比べる
            rose = emptied = 0
            shares = []
            for sd in seeds:
                rows = runs.get((scenario, sd, on), [])
                by_key = {(r["i"], r["t"]): r for r in rows}
                for r in rows:
                    g = r["grn"]
                    if not str(g["release_reason"]).startswith("lead_"):
                        continue
                    nxt = by_key.get((r["i"], r["t"] + 1))
                    if nxt is not None and g["release_ask"] and nxt["grn"]["price"] > g["release_ask"]:
                        rose += 1
                    if g.get("floor") is not None and g["warehouse"] <= g["floor"]:
                        emptied += 1
                    if g.get("release_stock"):
                        shares.append(g["release_qty"] / g["release_stock"])
            lines.append(
                f"| {scenario} | {'入' if on else '切'} | {s['lead_n']} "
                f"| {caps['need']} / {caps['fraction']} / {caps['floor']} "
                f"| {s['floor_turns']} | {'あり' if s['floor_turns'] > 0 else 'なし'} "
                f"| {rose} | {emptied} "
                f"| {round(statistics.median(shares), 3) if shares else '-'} |"
            )
    lines.append("")

    # 非発火ターンの線までの距離（入だけ。後の線引き調整用。#5075）
    lines.append("## 非発火ターンの線までの距離（入・種の合計）")
    lines.append("")
    lines.append(
        "| 場面 | 非発火ターン | 倍率 ratio の最小 / 最大 | 3 ターン変化 rise3 の最小 / 最大 |"
    )
    lines.append("|---|---|---|---|")
    for scenario in scenarios:
        rows_all = [r for sd in seeds for r in runs.get((scenario, sd, True), [])]
        quiet = [
            r
            for r in rows_all
            if not str(r["grn"]["release_reason"]).startswith("lead_")
        ]
        ratios = [r["grn"]["ratio"] for r in quiet if r["grn"].get("ratio") is not None]
        rises = [r["grn"]["rise3"] for r in quiet if r["grn"].get("rise3") is not None]
        lines.append(
            f"| {scenario} | {len(quiet)} "
            f"| {min(ratios) if ratios else '-'} / {max(ratios) if ratios else '-'} "
            f"| {min(rises) if rises else '-'} / {max(rises) if rises else '-'} |"
        )
    lines.append("")
    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--scenario", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--turns", type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--on", action="store_true")
    p.add_argument("--off", action="store_true")
    p.set_defaults(func=probe)
    r = sub.add_parser("report")
    r.add_argument("files", nargs="+")
    r.add_argument("--out")
    r.set_defaults(func=report)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
