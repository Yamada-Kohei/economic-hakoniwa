"""国の倉庫の穀物の回転備蓄（下書き）の probe（掲示板 #5233 の決定・#5242 の受諾）。

下書き: 入れ替えは「買ってから売る」（買い足しが埋まって新しい組が倉庫に入った分だけ、
次のターンから入れ替えの組を売る）＋倉庫が目安の半分（floor_fraction）を割ったら買い足しの
指値をいつもの値段 × (1 + market.band) まで上げる。game.yaml の
food_reserve.rotate_first（既定は切）。

    py -3.12 -X utf8 grain_rotate_probe.py probe --scenario plan --seed 1 --off \
        --turns 300 --out r-off-plan-s1.jsonl.gz
    py -3.12 -X utf8 grain_rotate_probe.py probe --scenario plan --seed 1 --on \
        --turns 300 --out r-on-plan-s1.jsonl.gz
    py -3.12 -X utf8 grain_rotate_probe.py report r-*.jsonl.gz --out report.md

版は下書きの枝 mugi/food-reserve-rotate-first の commit（ask.json の commit）。
切/入は --off/--on で config の food_reserve.rotate_first.enabled をその場で切り替える
（game.yaml は書き換えない）。場面は第 2 版の先行放出と同じ 6 場面（plan / none /
independent / small / open / war）× 種 1〜3、300 ターン。1 行 = 1 島 × 1 ターンで、
grn の中にそのターンの穀物（food_reserve.good）の記録を置く。量は市場の記録の単位
（1000 分の 1）のまま。

  price: 約定した値段（市場の記録 MarketRecord.price）
  usual: いつもの値段。ターン開始時点の状態から、家計と同じ式で計算したもの
  target: 備蓄の目安の量（ターン開始時点）。floor_qty: 目安 × floor_fraction
  warehouse_start: ターン開始時点の国の倉庫の穀物の量
  rotate_due: ターン開始時点の reserve.rotating（入れ替えの時期の量）
  rotate_offered / rotate_sold: このターンに入れ替えで売りに出した量 / 売れた量
  credit: 入れ替えを売ってよい分（ターン開始時点の reserve.ROTATE_CREDIT。入のときだけ意味がある）
  refill_want / refill_limit / refill_raised / refill_placed: 買い足しの注文の量・指値・
    指値を上げたか・注文を出したか
  refill_filled / renew_filled: 買い足しの埋まった量 / そのうち入れ替えの分と数えた量
  release_qty / release_reason / release_ask: このターンの備蓄の放出。市場の段の
    _reserve_orders が island.scratch[RESERVE_RELEASE] に残す量・わけ・指値。
    わけは hunger（飢えの線）/ price（(1 + release) の線）。放出しなかったターンは
    qty 0・reason null
  spoiled: このターンに国の倉庫から傷んで捨てられた穀物の量（RESERVE_SPOILED）
  refill_spent: そのターンの倉庫の買い入れの支出（island.stats の warehouse.food_refill。
    stats はターンごとに消えるので、そのターンの分だけ。合計は report で足す）
  warehouse: ターン後の国の倉庫の穀物の量
  demand / supply / sold: 市場の記録の買いたい量・売りに出た量・売れた量
  fed_min: 家計の層の FED（前のターンに食べられた割合、1000 分の 1）の最小
  hungry: ターン後の状態で _anyone_hungry と同じ判定（次のターンの放出の線に届くか）
終わりに <out>.gate.json（崩れの gate.Recorder の counts と found）を書く。

集計（report）の軸と合格線は README.md に置いた（結果を見る前に固定した）。
判定は場面ごと・種の中央値で行い、値段の最大の最悪種は参考として併記する。
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
    from hakoniwa_core.modules.market import (
        RESERVE_RELEASE,
        RESERVE_ROTATE,
        RESERVE_SPOILED,
    )
    from hakoniwa_core.modules.population import FED, still_hungry
    from hakoniwa_core.state.generate import generate_world
    from hakoniwa_core.units import to_milli

    game = load_game(None)
    defs, config = game.defs, game.config
    spec = config.get("food_reserve")
    if "rotate_first" not in spec:
        print(
            "この版には food_reserve.rotate_first がありません（下書きの枝の版で回すこと）"
        )
        return 2
    config.raw["food_reserve"]["rotate_first"]["enabled"] = bool(args.on)
    fraction = float(spec["rotate_first"].get("floor_fraction", 0.5))
    good = spec["good"]
    settings = config.get("household")
    threshold = to_milli(settings["hungry"]["threshold"])
    hunger_settings = config.get("population.hunger")
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)
    recorder = Recorder(world, defs, config)
    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            starts: dict[int, dict] = {}
            for iid, island in world.islands.items():
                if not island.has_economy:
                    continue
                goal = reserve.target(island, defs, config)
                starts[iid] = {
                    "usual": usual_price(
                        island,
                        good,
                        settings["usual_price_turns"],
                        settings["reprice_turns"],
                    ),
                    "target": goal,
                    "floor_qty": int(goal * fraction),
                    "warehouse_start": stock.total(island.warehouse, good),
                    # エンジンは world.turn + 1 のターンとして回すので、入れ替えの判定も
                    # そのターン番号で計算する（ゲーム側の _reserve_orders と一致させる）
                    "rotate_due": reserve.rotating(
                        island, defs, config, world.turn + 1
                    ),
                    "credit": reserve.rotate_credit(island),
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
                rel = island.scratch.get(RESERVE_RELEASE) or {}
                rot = island.scratch.get(RESERVE_ROTATE) or {}
                layers = island.economy.households.values()
                fed_min = min((s.get(FED, 1000) for s in layers), default=1000)
                hungry = any(
                    s.get(FED, 1000) < threshold
                    or still_hungry(s.get("hunger", 0), hunger_settings)
                    for s in layers
                )
                start = starts.get(iid, {})
                grn = {
                    "price": record.price,
                    "usual": start.get("usual"),
                    "target": start.get("target"),
                    "floor_qty": start.get("floor_qty"),
                    "warehouse_start": start.get("warehouse_start"),
                    "rotate_due": start.get("rotate_due", 0),
                    "credit": start.get("credit", 0),
                    "rotate_offered": rot.get("offered", 0),
                    "rotate_sold": rot.get("rotate_sold"),
                    "refill_want": rot.get("want", 0),
                    "refill_limit": rot.get("limit", 0),
                    "refill_raised": bool(rot.get("raised")),
                    "refill_placed": bool(rot.get("placed")),
                    "refill_filled": rot.get("refill_filled"),
                    "renew_filled": rot.get("renew_filled"),
                    "release_qty": rel.get("qty", 0),
                    "release_reason": rel.get("reason"),
                    "release_ask": rel.get("price"),
                    "spoiled": island.scratch.get(RESERVE_SPOILED, 0),
                    "refill_spent": island.stats.get("warehouse.food_refill", 0),
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
    """ファイル名 r-<on|off>-<場面>-s<種>.jsonl.gz から (場面, 入か, 種) を取る。"""
    name = Path(path).name
    stem = name.removesuffix(".jsonl.gz").removesuffix(".jsonl")
    parts = stem.split("-")
    on = parts[1] == "on"
    scenario = parts[2] if len(parts) > 2 else "?"
    seed = int(parts[3][1:]) if len(parts) > 3 and parts[3].startswith("s") else 0
    return scenario, on, seed


def _blocks(rows: list[dict]) -> list[dict]:
    """島ごとに hungry が続いた並び（塊）を数える。#5209 の集計と同じ定義。
    各塊について、塊の始まりから終わりの次のターンまでの最初の放出と、その量を見る。"""
    by_island: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_island[r["i"]].append(r)
    blocks = []
    for isl, rs in by_island.items():
        rs.sort(key=lambda r: r["t"])
        byt = {r["t"]: r for r in rs}
        t, last_t = rs[0]["t"], rs[-1]["t"]
        while t <= last_t:
            r = byt.get(t)
            if r is None or not r["grn"]["hungry"]:
                t += 1
                continue
            t0 = t
            while t <= last_t and byt.get(t) is not None and byt[t]["grn"]["hungry"]:
                t += 1
            t_end = t - 1
            release = None
            for tt in range(t0, t_end + 2):
                rr = byt.get(tt)
                if rr is not None and rr["grn"]["release_reason"] is not None:
                    release = rr["grn"]
                    break
            blocks.append(
                {
                    "island": isl,
                    "t0": t0,
                    "length": t_end - t0 + 1,
                    "release_qty": release["release_qty"] if release else None,
                }
            )
    return blocks


def _summary(rows: list[dict]) -> dict:
    """1 本分（場面 × 種 × 切/入）の集計。"""
    blocks = _blocks(rows)
    long_blocks = [b for b in blocks if b["length"] >= 2]
    long_zero = sum(1 for b in long_blocks if b["release_qty"] == 0)
    long_no_release = sum(1 for b in long_blocks if b["release_qty"] is None)
    rel = [r for r in rows if r["grn"]["release_reason"] is not None]
    prices = [r["grn"]["price"] for r in rows if r["grn"]["price"]]
    last_turn = max(r["t"] for r in rows) if rows else 0
    return {
        "hungry_turns": sum(1 for r in rows if r["grn"]["hungry"]),
        "blocks": len(blocks),
        "long_blocks": len(long_blocks),
        "long_zero": long_zero,
        "long_no_release": long_no_release,
        "rel_turns": len(rel),
        "rel_zero": sum(1 for r in rel if r["grn"]["release_qty"] == 0),
        "price_median": round(statistics.median(prices)) if prices else None,
        "price_max": max(prices) if prices else None,
        "fed_min": min((r["grn"]["fed_min"] for r in rows), default=None),
        "unmet": sum(max(0, r["grn"]["demand"] - r["grn"]["sold"]) for r in rows),
        "spoiled": sum(r["grn"]["spoiled"] for r in rows),
        "refill_spent": sum(r["grn"]["refill_spent"] for r in rows),
        "rotate_offered": sum(r["grn"]["rotate_offered"] for r in rows),
        "refill_raised_turns": sum(1 for r in rows if r["grn"]["refill_raised"]),
        "warehouse_last": sum(
            r["grn"]["warehouse"] for r in rows if r["t"] == last_turn
        ),
    }


def _fmt(value) -> str:
    return "-" if value is None else f"{value:,}"


def _pct(part: int, whole: int) -> str:
    return "-" if whole <= 0 else f"{100.0 * part / whole:.1f}%"


def report(args: argparse.Namespace) -> int:
    runs: dict[tuple[str, int, bool], list[dict]] = {}
    gates: dict[tuple[str, int, bool], int | None] = {}
    for path in args.files:
        scenario, on, seed = _kind_of(path)
        runs[(scenario, seed, on)] = _load(path)
        gate_path = path.removesuffix(".gz").removesuffix(".jsonl") + ".gate.json"
        try:
            with open(gate_path, encoding="utf-8") as fh:
                gates[(scenario, seed, on)] = sum(json.load(fh)["counts"].values())
        except (OSError, KeyError, ValueError):
            gates[(scenario, seed, on)] = None
    scenarios = sorted({s for s, _, _ in runs})
    seeds = sorted({sd for _, sd, _ in runs})
    lines = ["# 穀物の回転備蓄（下書き）の probe の集計", ""]

    for scenario in scenarios:
        for seed in seeds:
            if (scenario, seed, False) not in runs and (
                scenario,
                seed,
                True,
            ) not in runs:
                continue
            lines.append(f"## {scenario}（種 {seed}）")
            lines.append("")
            lines.append(
                "| 側 | 飢えターン | 塊 / 長い塊 | 長い塊で放出時点 倉庫 0 / 放出なし "
                "| 放出回数（うち量 0） | 値段の中央値 | 値段の最大 | FED 最小 "
                "| 買えなかった量 | 腐敗 | 買い入れ支出（合計） | 入れ替えを出した量 "
                "| 指値を上げたターン | 倉庫の最後 |"
            )
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for on in (False, True):
                rows = runs.get((scenario, seed, on), [])
                if not rows:
                    continue
                s = _summary(rows)
                lines.append(
                    f"| {'入' if on else '切'} | {s['hungry_turns']} "
                    f"| {s['blocks']} / {s['long_blocks']} "
                    f"| {s['long_zero']} / {s['long_no_release']} "
                    f"| {s['rel_turns']}（{s['rel_zero']}） "
                    f"| {_fmt(s['price_median'])} | {_fmt(s['price_max'])} "
                    f"| {_fmt(s['fed_min'])} | {s['unmet']:,} | {s['spoiled']:,} "
                    f"| {s['refill_spent']:,} | {s['rotate_offered']:,} "
                    f"| {s['refill_raised_turns']} | {s['warehouse_last']:,} |"
                )
            lines.append("")

    # 合格線での判定（README.md。場面ごと・種の中央値。値段の最大の最悪種は参考）
    lines.append("## 合格線での判定（場面ごと・種の中央値）")
    lines.append("")
    med: dict[str, dict[str, dict[bool, float | None]]] = {}
    pooled_zero: dict[bool, tuple[int, int]] = {}
    scene_zero: dict[str, dict[bool, tuple[int, int]]] = {}
    for scenario in scenarios:
        med[scenario] = {}
        scene_zero[scenario] = {}
        for metric in (
            "long_blocks",
            "hungry_turns",
            "price_median",
            "price_max",
            "fed_min",
        ):
            med[scenario][metric] = {}
            for on in (False, True):
                values = [
                    _summary(runs[(scenario, sd, on)])[metric]
                    for sd in seeds
                    if (scenario, sd, on) in runs
                ]
                values = [v for v in values if v is not None]
                med[scenario][metric][on] = (
                    statistics.median(values) if values else None
                )
        for on in (False, True):
            zero = sum(
                _summary(runs[(scenario, sd, on)])["long_zero"]
                for sd in seeds
                if (scenario, sd, on) in runs
            )
            long_all = sum(
                _summary(runs[(scenario, sd, on)])["long_blocks"]
                for sd in seeds
                if (scenario, sd, on) in runs
            )
            scene_zero[scenario][on] = (zero, long_all)
            pz, pl = pooled_zero.get(on, (0, 0))
            pooled_zero[on] = (pz + zero, pl + long_all)

    def ratio(side: tuple[int, int]) -> float | None:
        return None if side[1] <= 0 else side[0] / side[1]

    lines.append(
        "| 場面 | 長い塊 切→入 | 飢えターン 切→入 | 長い塊の倉庫 0 割合 切→入 "
        "| 値段の中央値 切→入 | 値段の最大 切→入（入の最悪種・参考） | FED 最小 切→入 |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    line1_ok = True
    for scenario in scenarios:
        m = med[scenario]
        worst_on = max(
            (
                _summary(runs[(scenario, sd, True)])["price_max"]
                for sd in seeds
                if (scenario, sd, True) in runs
            ),
            default=None,
        )
        ok = (
            m["long_blocks"][True] is not None
            and m["long_blocks"][False] is not None
            and m["long_blocks"][True] <= m["long_blocks"][False]
            and m["hungry_turns"][True] <= m["hungry_turns"][False]
        )
        line1_ok = line1_ok and ok
        lines.append(
            f"| {scenario} | {_fmt(m['long_blocks'][False])} → {_fmt(m['long_blocks'][True])} "
            f"| {_fmt(m['hungry_turns'][False])} → {_fmt(m['hungry_turns'][True])} "
            f"| {_pct(*scene_zero[scenario][False])} → {_pct(*scene_zero[scenario][True])} "
            f"| {_fmt(m['price_median'][False])} → {_fmt(m['price_median'][True])} "
            f"| {_fmt(m['price_max'][False])} → {_fmt(m['price_max'][True])}（{_fmt(worst_on)}） "
            f"| {_fmt(m['fed_min'][False])} → {_fmt(m['fed_min'][True])} |"
        )
    lines.append("")

    total_hungry = {
        on: sum(med[s]["hungry_turns"][on] or 0 for s in scenarios)
        for on in (False, True)
    }
    totals: dict[str, dict[bool, float]] = {}
    for metric in ("refill_spent", "spoiled", "unmet"):
        totals[metric] = {
            on: sum(
                statistics.median(
                    [
                        _summary(runs[(s, sd, on)])[metric]
                        for sd in seeds
                        if (s, sd, on) in runs
                    ]
                )
                for s in scenarios
            )
            for on in (False, True)
        }
    gate_totals: dict[bool, int | None] = {}
    for on in (False, True):
        values = [gates[k] for k in gates if k[2] == on]
        gate_totals[on] = None if any(v is None for v in values) else sum(values)  # type: ignore[arg-type]

    z_all_off, z_all_on = ratio(pooled_zero[False]), ratio(pooled_zero[True])
    line2_ok = (
        z_all_off is not None
        and z_all_on is not None
        and z_all_on <= 0.50
        and (z_all_off - z_all_on) >= 0.15
        and all(
            ratio(scene_zero[s][True]) is None
            or ratio(scene_zero[s][False]) is None
            or ratio(scene_zero[s][True]) - ratio(scene_zero[s][False]) <= 0.05
            for s in scenarios
        )
    )
    line1_ok = line1_ok and total_hungry[True] < total_hungry[False]
    line3_ok = all(
        med[s]["price_median"][True] is not None
        and med[s]["price_median"][False]
        and med[s]["price_median"][True] <= med[s]["price_median"][False] * 1.05
        and med[s]["price_max"][True] is not None
        and med[s]["price_max"][False]
        and med[s]["price_max"][True] <= med[s]["price_max"][False] * 1.10
        for s in scenarios
    )
    line4_ok = totals["refill_spent"][True] <= totals["refill_spent"][False] * 1.5
    line5_ok = totals["spoiled"][True] <= totals["spoiled"][False] * 1.2
    line6_ok = totals["unmet"][True] <= totals["unmet"][False] * 1.1 and all(
        med[s]["fed_min"][True] is not None
        and med[s]["fed_min"][False] is not None
        and med[s]["fed_min"][True] >= med[s]["fed_min"][False]
        for s in scenarios
    )
    line7_ok = (
        gate_totals[False] is not None
        and gate_totals[True] is not None
        and gate_totals[True] <= gate_totals[False]
    )
    lines.append("合格線（README.md）:")
    lines.append(
        f"1. 長い塊・飢えターンが全場面で増えず、合計で減る: {'○' if line1_ok else '×'}"
        f"（飢えターンの中央値の合計 切 {total_hungry[False]:,.0f} → 入 {total_hungry[True]:,.0f}）"
    )
    lines.append(
        f"2. 長い塊で放出時点の倉庫 0 の割合が全体で 15 ポイント以上下がり 50% 以下、"
        f"場面別に +5 ポイントを超えて悪化しない: {'○' if line2_ok else '×'}"
        f"（全体 切 {_pct(*pooled_zero[False])} → 入 {_pct(*pooled_zero[True])}）"
    )
    lines.append(
        f"3. 値段の中央値 +5% 以内・最大 +10% 以内（場面ごと）: {'○' if line3_ok else '×'}"
    )
    lines.append(
        f"4. 倉庫の買い入れ支出の合計が切の 1.5 倍以内: {'○' if line4_ok else '×'}"
        f"（切 {totals['refill_spent'][False]:,.0f} → 入 {totals['refill_spent'][True]:,.0f}）"
    )
    lines.append(
        f"5. 倉庫の腐敗の合計が切の 1.2 倍以内: {'○' if line5_ok else '×'}"
        f"（切 {totals['spoiled'][False]:,.0f} → 入 {totals['spoiled'][True]:,.0f}）"
    )
    lines.append(
        f"6. 買えなかった量の合計が切の 1.1 倍以内・FED 最小が切を下回らない: "
        f"{'○' if line6_ok else '×'}"
        f"（買えなかった量 切 {totals['unmet'][False]:,.0f} → 入 {totals['unmet'][True]:,.0f}）"
    )
    lines.append(
        f"7. 崩れの合計が入で切以下: {'○' if line7_ok else '×'}"
        f"（切 {_fmt(gate_totals[False])} → 入 {_fmt(gate_totals[True])}）"
    )
    lines.append("")
    verdict = (
        "合格"
        if all((line1_ok, line2_ok, line3_ok, line4_ok, line5_ok, line6_ok, line7_ok))
        else "不合格"
    )
    lines.append(
        f"判定: {verdict}（1・2 は目的の線、3〜7 は副作用の線。README.md の置き方）"
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
