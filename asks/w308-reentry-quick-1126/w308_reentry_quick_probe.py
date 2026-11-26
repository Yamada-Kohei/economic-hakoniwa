"""W-308 海運再参入の quick の probe（線 5 の崩れ。掲示板 #6020・#6022）。

再参入（切り替え trade.shipping_reentry、既定は切）は main に入り済み
（実装 266cf73d・ship_purchase の定義漏れ直し 9ca5044b）。この包みは
main の版で、切と入を quick の組（6 場面 × 種 1〜3、300 ターン）で回し、
崩れ（gate）が種のゆらぎの内かを見る（合格線は #5475 の線 5）。

    py -3.12 -X utf8 w308_reentry_quick_probe.py probe --scenario plan --seed 1 --off \
        --turns 300 --out q-off-plan-s1.jsonl.gz
    py -3.12 -X utf8 w308_reentry_quick_probe.py probe --scenario plan --seed 1 --on \
        --turns 300 --out q-on-plan-s1.jsonl.gz
    py -3.12 -X utf8 w308_reentry_quick_probe.py report q-*.jsonl.gz --out report.md

版は ask.json の commit（main）。切/入は --off/--on で config の
trade.shipping_reentry をその場で切り替える（game.yaml は書き換えない）。
1 行 = 1 島 × 1 ターンで、ship の中にそのターンの海運の記録を置く。
量は 1000 分の 1 のまま:

  firm: 地元の海運の会社があるか（0/1）
  fid: 会社の施設の番号（無ければ 0。引き継ぎで会社が替わると変わる）
  fleet: 船団（1000 分の 1 隻）
  carried: そのターンに地元の海運が運んだ量（トン × 区画、1000 分の 1）
  freight: 他国・借りの船に払った運賃（島の stats の import.freight）
  short: そのターンに船がなくて運べなかった荷（要る船の重さ。再参入の入の
    ときだけ貿易の状態に残る reentry_short_load。切では None）
  yard: 造船所があるか（0/1）
  port: 港があるか（0/1）
  transfers: そのターンまでの移管の台帳（shipping_transfers）の累積件数

終わりに <out>.gate.json（崩れの gate.Recorder の counts と found）を書く。
集計（report）は手元で回す形（受付の report の仕事は落ちる前例があるため、
包みには入れていない）。
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

SCENARIOS = ("plan", "none", "independent", "small", "open", "war")


def _open(path: str, mode: str):
    if path.endswith(".gz"):
        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def probe(args: argparse.Namespace) -> int:
    from hakoniwa_core import measure
    from hakoniwa_core.game import load_game
    from hakoniwa_core.gate import Recorder
    from hakoniwa_core.state.generate import generate_world
    from hakoniwa_core.trade import island_state, port_of, shipping_of

    game = load_game(None)
    defs, config = game.defs, game.config
    trade = config.raw.get("trade") or {}
    if "shipping_reentry" not in trade:
        print("この版には trade.shipping_reentry がありません（main の版で回すこと）")
        return 2
    config.raw["trade"]["shipping_reentry"] = bool(args.on)
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)
    recorder = Recorder(world, defs, config)
    with _open(args.out, "wt") as out:
        for _ in range(args.turns):
            measure.queue_scenario_orders(world, scenario, defs, config)
            game.engine.run_turn(world)
            recorder.record(world)
            for iid, island in sorted(world.islands.items()):
                if not island.has_economy:
                    continue
                f = shipping_of(island, config)
                state = island_state(island)
                if f is None:
                    ship = {
                        "firm": 0,
                        "fid": 0,
                        "fleet": 0,
                        "carried": 0,
                        "freight": island.stats.get("import.freight", 0),
                        "short": state.get("reentry_short_load"),
                        "yard": 1
                        if any(fac.kind == "shipyard" for fac in island.facilities)
                        else 0,
                        "port": 1 if port_of(island, config) is not None else 0,
                        "transfers": len(state.get("shipping_transfers", [])),
                    }
                else:
                    loads = f.economy.get("carried", [])
                    ship = {
                        "firm": 1,
                        "fid": f.id,
                        "fleet": sum(q for _, q in f.economy.get("fleet", [])),
                        "carried": loads[-1] if loads else 0,
                        "freight": island.stats.get("import.freight", 0),
                        "short": state.get("reentry_short_load"),
                        "yard": 1
                        if any(fac.kind == "shipyard" for fac in island.facilities)
                        else 0,
                        "port": 1 if port_of(island, config) is not None else 0,
                        "transfers": len(state.get("shipping_transfers", [])),
                    }
                row = {"t": world.turn, "i": iid, "ship": ship}
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
    """ファイル名 q-<on|off>-<場面>-s<種>.jsonl.gz から (場面, 入か, 種) を取る。"""
    name = Path(path).name
    stem = name.removesuffix(".jsonl.gz").removesuffix(".jsonl")
    parts = stem.split("-")
    on = parts[1] == "on"
    scenario = parts[2] if len(parts) > 2 else "?"
    seed = int(parts[3][1:]) if len(parts) > 3 and parts[3].startswith("s") else 0
    return scenario, on, seed


def _summary(rows: list[dict]) -> dict:
    """1 本分（場面 × 種 × 切/入）の集計。"""
    last_turn = max((r["t"] for r in rows), default=0)
    last = [r for r in rows if r["t"] == last_turn]
    late = [r for r in rows if r["t"] > last_turn - 50]
    return {
        "firm_last": sum(1 for r in last if r["ship"]["firm"]),
        "islands_last": len(last),
        "transfers_last": sum(r["ship"]["transfers"] for r in last),
        "carried_late": sum(r["ship"]["carried"] for r in late),
        "short_turns": sum(1 for r in rows if r["ship"]["short"]),
        "no_firm_order_turns": sum(
            1
            for r in rows
            if not r["ship"]["firm"]
            and (r["ship"]["freight"] > 0 or r["ship"]["short"])
        ),
    }


def _fmt(value) -> str:
    return "-" if value is None else f"{value:,}"


def report(args: argparse.Namespace) -> int:
    runs: dict[tuple[str, int, bool], list[dict]] = {}
    gates: dict[tuple[str, int, bool], dict | None] = {}
    for path in args.files:
        scenario, on, seed = _kind_of(path)
        runs[(scenario, seed, on)] = _load(path)
        gate_path = path.removesuffix(".gz").removesuffix(".jsonl") + ".gate.json"
        try:
            with open(gate_path, encoding="utf-8") as fh:
                gates[(scenario, seed, on)] = json.load(fh)
        except (OSError, ValueError):
            gates[(scenario, seed, on)] = None
    scenarios = [s for s in SCENARIOS if any(k[0] == s for k in runs)]
    seeds = sorted({sd for _, sd, _ in runs})

    def gate_total(key) -> int | None:
        g = gates.get(key)
        return None if g is None else sum(g.get("counts", {}).values())

    lines = ["# W-308 海運再参入 quick の集計（線 5 の崩れ）", ""]
    lines.append(
        "| 場面 | 種 | 崩れ 切 | 崩れ 入 | 海運のある島（最後） 切→入 "
        "| 移管の累積（最後） 切→入 | 終盤の運んだ量 切→入 |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for scenario in scenarios:
        for seed in seeds:
            off, on = (scenario, seed, False), (scenario, seed, True)
            if off not in runs and on not in runs:
                continue
            so = _summary(runs[off]) if off in runs else None
            sn = _summary(runs[on]) if on in runs else None
            lines.append(
                f"| {scenario} | {seed} | {_fmt(gate_total(off))} | {_fmt(gate_total(on))} "
                f"| {so['firm_last'] if so else '-'}→{sn['firm_last'] if sn else '-'} "
                f"| {so['transfers_last'] if so else '-'}→{sn['transfers_last'] if sn else '-'} "
                f"| {_fmt(so['carried_late']) if so else '-'}→{_fmt(sn['carried_late']) if sn else '-'} |"
            )
    lines.append("")

    # 崩れの種類別の合計
    kinds = sorted(
        {k for g in gates.values() if g for k in g.get("counts", {})}
    )
    if kinds:
        lines.append("## 崩れの種類別の合計（当たった島の数）")
        lines.append("")
        lines.append("| 種類 | 切（18 本） | 入（18 本） |")
        lines.append("|---|---|---|")
        for kind in kinds:
            off_sum = sum(
                (gates[k] or {}).get("counts", {}).get(kind, 0)
                for k in gates
                if k[2] is False
            )
            on_sum = sum(
                (gates[k] or {}).get("counts", {}).get(kind, 0)
                for k in gates
                if k[2] is True
            )
            lines.append(f"| {kind} | {off_sum} | {on_sum} |")
        lines.append("")

    lines.append("## 線 5 の判定（README.md: 崩れの合計が入で切以下）")
    lines.append("")
    totals: dict[bool, int | None] = {}
    for on in (False, True):
        values = [gate_total(k) for k in gates if k[2] is on]
        totals[on] = None if any(v is None for v in values) else sum(values)  # type: ignore[arg-type]
    lines.append("| 場面 | 崩れの合計 切 | 崩れの合計 入 | 切の種ごとの幅（最小〜最大） |")
    lines.append("|---|---|---|---|")
    for scenario in scenarios:
        off_vals = [gate_total((scenario, sd, False)) for sd in seeds]
        on_vals = [gate_total((scenario, sd, True)) for sd in seeds]
        off_sum = None if any(v is None for v in off_vals) else sum(off_vals)  # type: ignore[arg-type]
        on_sum = None if any(v is None for v in on_vals) else sum(on_vals)  # type: ignore[arg-type]
        spread = (
            f"{min(v for v in off_vals if v is not None)}〜{max(v for v in off_vals if v is not None)}"
            if any(v is not None for v in off_vals)
            else "-"
        )
        lines.append(
            f"| {scenario} | {_fmt(off_sum)} | {_fmt(on_sum)} | {spread} |"
        )
    lines.append("")
    ok = (
        totals[False] is not None
        and totals[True] is not None
        and totals[True] <= totals[False]  # type: ignore[operator]
    )
    lines.append(
        f"崩れの合計: 切 {_fmt(totals[False])} → 入 {_fmt(totals[True])}"
        f"（線 5: {'○' if ok else '×'}）"
    )
    lines.append("")
    lines.append(
        "判定の置き方: 線 5（#5475）は「quick の崩れが種のゆらぎの内」。"
        "合計の比べが主で、場面ごとの合計と切の種ごとの幅で、増えた分が"
        "ゆらぎの範囲かを読む。"
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
