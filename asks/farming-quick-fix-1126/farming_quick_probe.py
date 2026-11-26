"""自給農の直し（#6011・直しは #6036 で報告）の quick の probe（掲示板 #6038）。

直しは「建った段階が変わったマスだけ決め直す＋一度だけの戻し」
（枝 mugi/farming-recount-fix-1126 @ 850f08a8、土台は main 1a5cc83d）。
本番の 3 島で失業が 3〜4 割出ているため、本番投入の前に quick の組
（6 場面 × 種 1〜3、300 ターン）で main と直しを並べて確かめる。
包みは版ごとに 2 つ（farming-quick-main-1126 / farming-quick-fix-1126、
中身の台本は同じ）。

    py -3.12 -X utf8 farming_quick_probe.py probe --scenario plan --seed 1 \
        --turns 300 --out q-main-plan-s1.jsonl.gz
    py -3.12 -X utf8 farming_quick_probe.py report q-*.jsonl.gz --out report.md

1 行 = 1 島 × 1 ターン:

  eco.gdp / eco.pop / eco.treasury: GDP・人口・国庫（島の stats）
  eco.unemp: 失業率（島の stats の unemployment）
  eco.subsistence: 自給の農家の人数（島の stats の subsistence）
  eco.jobless / eco.labor: 失業者・働き手の人数
  farm.restored_ext: 島の ext の farming_restored（直しの版だけが書く。
    一度だけの戻しで戻した人数。main の版では常に null）
  farm.restored_log: そのターンの島のログに出た farming_restored の
    count の合計（出なければ 0）

終わりに <out>.gate.json（崩れの gate.Recorder の counts と found）を書く。
集計（report）は手元で回す形（受付の report の仕事は落ちる前例があるため、
包みには入れていない）。合格線は README.md に結果を見る前に固定してある。
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

    game = load_game(None)
    defs, config = game.defs, game.config
    if not config.get("population.farming_by_housing"):
        print("この版では population.farming_by_housing が切になっています")
        return 2
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
                s = island.stats
                restored_log = sum(
                    entry.args.get("count", 0)
                    for entry in island.log
                    if entry.kind == "farming_restored"
                )
                row = {
                    "t": world.turn,
                    "i": iid,
                    "eco": {
                        "gdp": s.get("gdp", 0),
                        "pop": s.get("population", 0),
                        "treasury": s.get("treasury", 0),
                        "unemp": s.get("unemployment", 0.0),
                        "subsistence": s.get("subsistence", 0),
                        "jobless": s.get("jobless", 0),
                        "labor": s.get("labor", 0),
                    },
                    "farm": {
                        "restored_ext": island.ext.get("farming_restored"),
                        "restored_log": restored_log,
                    },
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


def _load(path: str) -> list[dict]:
    rows = []
    with _open(path, "rt") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _kind_of(path: str) -> tuple[str, str, int]:
    """ファイル名 q-<main|fix>-<場面>-s<種>.jsonl.gz から (版, 場面, 種) を取る。"""
    name = Path(path).name
    stem = name.removesuffix(".jsonl.gz").removesuffix(".jsonl")
    parts = stem.split("-")
    version = parts[1]
    seed = int(parts[-1][1:])
    scenario = "-".join(parts[2:-1])
    return version, scenario, seed


def _summary(rows: list[dict]) -> dict:
    """1 本分（版 × 場面 × 種）の集計。"""
    last_turn = max((r["t"] for r in rows), default=0)
    last = [r for r in rows if r["t"] == last_turn]
    restored_by_island: dict[int, int] = {}
    for r in rows:
        ext = r["farm"]["restored_ext"]
        if ext is not None:
            restored_by_island[r["i"]] = ext
    return {
        "unemp_max": max((r["eco"]["unemp"] for r in rows), default=0.0),
        "unemp_last_mean": (
            sum(r["eco"]["unemp"] for r in last) / len(last) if last else 0.0
        ),
        "subsistence_last": sum(r["eco"]["subsistence"] for r in last),
        "jobless_last": sum(r["eco"]["jobless"] for r in last),
        "restored_log_total": sum(r["farm"]["restored_log"] for r in rows),
        "restored_ext_total": sum(restored_by_island.values()),
        "restored_islands": sum(1 for v in restored_by_island.values() if v > 0),
    }


def _fmt(value) -> str:
    return "-" if value is None else f"{value:,}"


def _fmtf(value) -> str:
    return "-" if value is None else f"{value:.3f}"


def report(args: argparse.Namespace) -> int:
    runs: dict[tuple[str, str, int], list[dict]] = {}
    gates: dict[tuple[str, str, int], dict | None] = {}
    for path in args.files:
        version, scenario, seed = _kind_of(path)
        key = (version, scenario, seed)
        runs[key] = _load(path)
        gate_path = path.removesuffix(".gz").removesuffix(".jsonl") + ".gate.json"
        try:
            with open(gate_path, encoding="utf-8") as fh:
                gates[key] = json.load(fh)
        except (OSError, ValueError):
            gates[key] = None
    scenarios = [s for s in SCENARIOS if any(k[1] == s for k in runs)]
    seeds = sorted({sd for _, _, sd in runs})

    def gate_total(key) -> int | None:
        g = gates.get(key)
        return None if g is None else sum(g.get("counts", {}).values())

    lines = ["# 自給農の直し quick の集計（#6038 の線）", ""]
    lines.append(
        "| 場面 | 種 | 崩れ main | 崩れ fix | 失業率の最大 main→fix "
        "| 失業率（最後・平均） main→fix | 自給（最後） main→fix "
        "| 戻し fix（ログ合計／ext 合計／島数） |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for scenario in scenarios:
        for seed in seeds:
            mk, fk = ("main", scenario, seed), ("fix", scenario, seed)
            if mk not in runs and fk not in runs:
                continue
            sm = _summary(runs[mk]) if mk in runs else None
            sf = _summary(runs[fk]) if fk in runs else None
            lines.append(
                f"| {scenario} | {seed} | {_fmt(gate_total(mk))} | {_fmt(gate_total(fk))} "
                f"| {_fmtf(sm['unemp_max']) if sm else '-'}→{_fmtf(sf['unemp_max']) if sf else '-'} "
                f"| {_fmtf(sm['unemp_last_mean']) if sm else '-'}→{_fmtf(sf['unemp_last_mean']) if sf else '-'} "
                f"| {_fmt(sm['subsistence_last']) if sm else '-'}→{_fmt(sf['subsistence_last']) if sf else '-'} "
                f"| {_fmt(sf['restored_log_total'])}/{_fmt(sf['restored_ext_total'])}/{sf['restored_islands'] if sf else '-'} |"
            )
    lines.append("")

    kinds = sorted({k for g in gates.values() if g for k in g.get("counts", {})})
    if kinds:
        lines.append("## 崩れの種類別の合計（当たった島の数）")
        lines.append("")
        lines.append("| 種類 | main（18 本） | fix（18 本） |")
        lines.append("|---|---|---|")
        for kind in kinds:
            main_sum = sum(
                (gates[k] or {}).get("counts", {}).get(kind, 0)
                for k in gates
                if k[0] == "main"
            )
            fix_sum = sum(
                (gates[k] or {}).get("counts", {}).get(kind, 0)
                for k in gates
                if k[0] == "fix"
            )
            lines.append(f"| {kind} | {main_sum} | {fix_sum} |")
        lines.append("")

    lines.append("## 線の判定（README.md。結果を見る前に固定）")
    lines.append("")
    lines.append("| 場面 | 崩れの合計 main | 崩れの合計 fix | main の種ごとの幅（最小〜最大） |")
    lines.append("|---|---|---|---|")
    totals: dict[str, int | None] = {}
    for version in ("main", "fix"):
        values = [gate_total(k) for k in gates if k[0] == version]
        totals[version] = None if any(v is None for v in values) else sum(values)  # type: ignore[arg-type]
    for scenario in scenarios:
        main_vals = [gate_total(("main", scenario, sd)) for sd in seeds]
        fix_vals = [gate_total(("fix", scenario, sd)) for sd in seeds]
        main_sum = None if any(v is None for v in main_vals) else sum(main_vals)  # type: ignore[arg-type]
        fix_sum = None if any(v is None for v in fix_vals) else sum(fix_vals)  # type: ignore[arg-type]
        spread = (
            f"{min(v for v in main_vals if v is not None)}〜{max(v for v in main_vals if v is not None)}"
            if any(v is not None for v in main_vals)
            else "-"
        )
        lines.append(
            f"| {scenario} | {_fmt(main_sum)} | {_fmt(fix_sum)} | {spread} |"
        )
    lines.append("")
    ok_gate = (
        totals["main"] is not None
        and totals["fix"] is not None
        and totals["fix"] <= totals["main"]  # type: ignore[operator]
    )
    lines.append(
        f"1. 崩れ: 合計 main {_fmt(totals['main'])} → fix {_fmt(totals['fix'])}"
        f"（fix が main 以下: {'○' if ok_gate else '×'}）。"
        "場面ごとの合計と main の種幅で、増えた分がゆらぎの範囲かを読む。判定は会議。"
    )
    spikes = []
    for scenario in scenarios:
        for seed in seeds:
            mk, fk = ("main", scenario, seed), ("fix", scenario, seed)
            if mk in runs and fk in runs:
                d = _summary(runs[fk])["unemp_max"] - _summary(runs[mk])["unemp_max"]
                if d > 0:
                    spikes.append(f"{scenario}/種{seed}: +{d:.3f}")
    lines.append(
        "2. 失業の跳ね: fix の失業率の最大が main（同場面・同種）の最大を"
        + (
            "超えた本は無し（○）。"
            if not spikes
            else "超えた本: " + "、".join(spikes) + "（幅を見て会議で判定）。"
        )
    )
    mismatch = []
    for key, rows in runs.items():
        if key[0] != "fix":
            continue
        sm = _summary(rows)
        if sm["restored_log_total"] != sm["restored_ext_total"]:
            mismatch.append(f"{key[1]}/種{key[2]}")
    lines.append(
        "3. 戻した人数のログ: fix の各本で、ログの farming_restored の合計と"
        "島の ext の farming_restored の合計が"
        + (
            "全本で一致（○）。"
            if not mismatch
            else "食い違う本: " + "、".join(mismatch) + "（×）。"
        )
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
    p.set_defaults(func=probe)
    r = sub.add_parser("report")
    r.add_argument("files", nargs="+")
    r.add_argument("--out")
    r.set_defaults(func=report)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
