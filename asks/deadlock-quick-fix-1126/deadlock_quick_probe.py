"""取引の行き詰まりの直し（会議 #5303・#5306）の quick の probe。

直しは「向きの無い商社でも、手元に残った荷を島の市場で売りに出せる（処分）」
（枝 mugi/trade-deadlock-1126、土台は main 6f4c4ba8）。切り替えのない直しなので、
包みは版ごとに 2 つ（deadlock-quick-main-1126 / deadlock-quick-fix-1126、
中身の台本は同じ）。quick の組（6 場面 × 種 1〜3、300 ターン）で main と
直しを並べて確かめる。合格線は README.md に結果を見る前に固定してある。

    py -3.12 -X utf8 deadlock_quick_probe.py probe --scenario plan --seed 1 \
        --turns 300 --out q-main-plan-s1.jsonl.gz
    py -3.12 -X utf8 deadlock_quick_probe.py report q-*.jsonl.gz --out report.md

1 行 = 1 島 × 1 ターン:

  eco.gdp / eco.pop / eco.treasury: GDP・人口・国庫（島の stats）
  eco.unemp: 失業率（島の stats の unemployment）
  stuck.pairs: 向きが無く（dir なし）荷が残っている商社×財の数（ターン末。
    行き詰まりそのもの。main では残り続け、直しでは売れて消えるはず）
  stuck.held: その荷の量の合計

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
    from hakoniwa_core.trade import traders, traders_of

    game = load_game(None)
    defs, config = game.defs, game.config
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
                pairs = 0
                held = 0
                for f in traders_of(island, config):
                    for item in traders.goods_of(f).values():
                        if item.get("dir") is None and item["held"][0] > 0:
                            pairs += 1
                            held += item["held"][0]
                row = {
                    "t": world.turn,
                    "i": iid,
                    "eco": {
                        "gdp": s.get("gdp", 0),
                        "pop": s.get("population", 0),
                        "treasury": s.get("treasury", 0),
                        "unemp": s.get("unemployment", 0.0),
                    },
                    "stuck": {"pairs": pairs, "held": held},
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


def _gate_total(path: str) -> int:
    gate_path = path.removesuffix(".gz").removesuffix(".jsonl") + ".gate.json"
    with open(gate_path, encoding="utf-8") as fh:
        gate = json.load(fh)
    return sum(gate.get("counts", {}).values())


def report(args: argparse.Namespace) -> int:
    lines: list[str] = []
    lines.append("# 取引の行き詰まりの直し: quick の集計（#5303 の線）")
    lines.append("")
    lines.append(
        "| 版 | 場面 | 種 | 崩れ | 行き詰まりの最大（組） | "
        "行き詰まりがあった島ターン | 最終ターンの行き詰まり（組） |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    totals: dict[str, int] = {"main": 0, "fix": 0}
    per_scenario: dict[tuple[str, str], int] = {}
    seed_span: dict[tuple[str, str], list[int]] = {}
    stuck_turns: dict[str, int] = {"main": 0, "fix": 0}
    final_pairs: dict[str, int] = {"main": 0, "fix": 0}
    for path in sorted(args.files):
        version, scenario, seed = _kind_of(path)
        rows = _load(path)
        total = _gate_total(path)
        totals[version] = totals.get(version, 0) + total
        per_scenario[(version, scenario)] = per_scenario.get((version, scenario), 0) + total
        seed_span.setdefault((version, scenario), []).append(total)
        by_island_turn: dict[tuple[int, int], int] = {}
        last_turn = 0
        for row in rows:
            by_island_turn[(row["i"], row["t"])] = row["stuck"]["pairs"]
            last_turn = max(last_turn, row["t"])
        max_pairs = max(by_island_turn.values(), default=0)
        turns_with = sum(1 for v in by_island_turn.values() if v > 0)
        final = sum(v for (i, t), v in by_island_turn.items() if t == last_turn)
        stuck_turns[version] = stuck_turns.get(version, 0) + turns_with
        final_pairs[version] = final_pairs.get(version, 0) + final
        lines.append(
            f"| {version} | {scenario} | {seed} | {total} | {max_pairs} | "
            f"{turns_with} | {final} |"
        )
    lines.append("")
    lines.append(f"- 崩れの合計: main {totals.get('main', 0)} / fix {totals.get('fix', 0)}")
    for scenario in SCENARIOS:
        span = seed_span.get(("main", scenario), [])
        span_text = f"（main の種ごとの幅 {min(span)}〜{max(span)}）" if span else ""
        lines.append(
            f"  - {scenario}: main {per_scenario.get(('main', scenario), 0)} / "
            f"fix {per_scenario.get(('fix', scenario), 0)}{span_text}"
        )
    lines.append(
        f"- 行き詰まりがあった島ターンの合計: main {stuck_turns.get('main', 0)} / "
        f"fix {stuck_turns.get('fix', 0)}"
    )
    lines.append(
        f"- 最終ターンの行き詰まり（組）の合計: main {final_pairs.get('main', 0)} / "
        f"fix {final_pairs.get('fix', 0)}"
    )
    lines.append("")
    lines.append("## 線の判定（#5303。判定は会議）")
    lines.append("")
    lines.append(
        "1. quick の崩れが種のゆらぎの内: 崩れの合計が fix で main 以下を主の物差しとし、"
        "場面ごとの合計と main の種ごとの幅を併記して読む。"
    )
    lines.append(
        "2. 行き詰まりの解消（仕組みの確かめ）: fix では最終ターンの行き詰まりが 0 に"
        "なり、行き詰まりがあった島ターンが main より大きく減ること。"
    )
    text = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--scenario", required=True, choices=SCENARIOS)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--turns", type=int, default=300)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=probe)
    r = sub.add_parser("report")
    r.add_argument("files", nargs="+")
    r.add_argument("--out")
    r.set_defaults(fn=report)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
