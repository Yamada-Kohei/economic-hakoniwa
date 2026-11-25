"""台帳 C3 の H-7（掲示板 #5365 の C）: 500 ターンの写しから続けても、
最初から回したのと同じになるか。

同じ場面・同じ種で 1200 ターンを 2 通りに回す:

  full:   最初から 1200 ターン
  resume: 500 ターン → 写しを取り、作り直して → 残り 700 ターン

写しは本番のバックアップと同じ道で取る: hakoniwa_core.state.serialize の
world_to_dict で dict にし、JSON を往復させて（バックアップは DB の JSON に
残る）world_from_dict で作り直す（server/turns の _make_backup / load_backup
と同じ組）。写しに入らない物（stats・scratch・journal・trades・markets・
books は serialize の _TRANSIENT で外れる）は、毎ターン作り直される前提で、
それでも 1200 ターン目の数が一致するかを見るのがこの測り。

resume では、写しを取るターンで往復の直前・直後に simulate.world_digest
（写しに入る欄の指紋）を取り、割れたらその場で止まる。指紋が合うのに
最終数が割れたら、写しの外の入力（trades を読む autoplan 等）を疑う。

比べるのは 1200 ターン目の国ごとの主な数 4 つ: GDP・人口・国庫・失業率
（島の統計の gdp / population / treasury / unemployment、そのままの値）。
合格の線は「全部の国で 4 つとも一致」（#5365 で事前に固定）。

    py -3.12 -X utf8 h7_resume.py run --mode full --scenario open --seed 1 \
        --turns 1200 --out full-open-s1.final.json
    py -3.12 -X utf8 h7_resume.py run --mode resume --scenario open --seed 1 \
        --turns 1200 --snapshot-at 500 --out resume-open-s1.final.json
    py -3.12 -X utf8 h7_resume.py report full-open-s1.final.json \
        resume-open-s1.final.json --out report.md

煙確認（短い確認。配置時にまずこれで確かめる）:

    py -3.12 -X utf8 h7_resume.py run --mode full --scenario small --seed 1 \
        --turns 12 --out smoke-full.json
    py -3.12 -X utf8 h7_resume.py run --mode resume --scenario small \
        --seed 1 --turns 12 --snapshot-at 5 --out smoke-resume.json
    py -3.12 -X utf8 h7_resume.py report smoke-full.json smoke-resume.json \
        --out smoke-report.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

KEYS = ("gdp", "population", "treasury", "unemployment")
LABELS = {"gdp": "GDP", "population": "人口", "treasury": "国庫", "unemployment": "失業率"}


def _final_stats(world) -> dict:
    """1200 ターン目（最終ターン）の国ごとの主な数。経済のない島は除く。"""
    out = {}
    for iid, island in sorted(world.islands.items()):
        if not island.has_economy:
            continue
        stats = island.stats
        out[str(iid)] = {key: stats.get(key) for key in KEYS}
    return out


def run(args: argparse.Namespace) -> int:
    from hakoniwa_core import measure
    from hakoniwa_core.game import load_game
    from hakoniwa_core.simulate import world_digest
    from hakoniwa_core.state.generate import generate_world
    from hakoniwa_core.state.serialize import world_from_dict, world_to_dict

    if args.mode == "resume" and not (0 < args.snapshot_at < args.turns):
        raise SystemExit("--snapshot-at は 1 〜 --turns-1 で指定してください")
    game = load_game(None)
    defs, config = game.defs, game.config
    scenario = measure.SCENARIOS[args.scenario]
    world = generate_world(args.seed, scenario.islands, config, defs)
    snapshotted = False
    digest_before = digest_after = None
    for step in range(1, args.turns + 1):
        measure.queue_scenario_orders(world, scenario, defs, config)
        game.engine.run_turn(world)
        if args.mode == "resume" and not snapshotted and step == args.snapshot_at:
            digest_before = world_digest(world)
            data = json.loads(json.dumps(world_to_dict(world)))
            world = world_from_dict(data)
            digest_after = world_digest(world)
            if digest_before != digest_after:
                raise SystemExit(
                    f"写しの往復で指紋が割れました（{args.snapshot_at} ターン目）: "
                    f"{digest_before} != {digest_after}"
                )
            snapshotted = True
    payload = {
        "mode": args.mode,
        "scenario": args.scenario,
        "seed": args.seed,
        "turns": args.turns,
        "snapshot_at": args.snapshot_at if args.mode == "resume" else None,
        "snapshot_digest_before": digest_before,
        "snapshot_digest_after": digest_after,
        "final_turn": world.turn,
        "islands": _final_stats(world),
    }
    Path(args.out).write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return 0


def report(args: argparse.Namespace) -> int:
    full = json.loads(Path(args.full).read_text(encoding="utf-8"))
    resume = json.loads(Path(args.resume).read_text(encoding="utf-8"))
    lines = [
        "# H-7: 500 ターンの写しから続けても同じになるか",
        "",
        f"- 場面: {full['scenario']} / 種: {full['seed']}",
        f"- full: {full['turns']} ターン（最終 {full['final_turn']}）",
        f"- resume: {resume['snapshot_at']} ターンで写し → {resume['turns']} ターン"
        f"（最終 {resume['final_turn']}）",
        "",
        "合格の線: 1200 ターン目の国ごとの主な数（GDP・人口・国庫・失業率）が"
        "全部の国で一致。",
        "",
        "| 島 | 数 | full | resume | 一致 |",
        "|---|---|---|---|---|",
    ]
    ok = full["final_turn"] == resume["final_turn"]
    digest_before = resume.get("snapshot_digest_before")
    digest_after = resume.get("snapshot_digest_after")
    if digest_before is not None:
        same_digest = digest_before == digest_after
        ok = ok and same_digest
        lines.insert(
            5,
            f"- 写しの指紋（往復前後）: {'一致' if same_digest else '不一致'}"
            f"（{digest_before}）",
        )
    if not ok:
        lines.append(
            f"| - | 最終ターン | {full['final_turn']} | {resume['final_turn']} | ✗ |"
        )
    island_ids = sorted(
        set(full["islands"]) | set(resume["islands"]), key=lambda x: int(x)
    )
    for iid in island_ids:
        f_row = full["islands"].get(iid)
        r_row = resume["islands"].get(iid)
        for key in KEYS:
            f_val = f_row.get(key) if f_row else None
            r_val = r_row.get(key) if r_row else None
            same = f_val == r_val
            ok = ok and same
            lines.append(
                f"| {iid} | {LABELS[key]} | {f_val} | {r_val} | {'○' if same else '✗'} |"
            )
    lines += ["", f"## 判定: {'合格' if ok else '不合格'}"]
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[-1])
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--mode", choices=["full", "resume"], required=True)
    p_run.add_argument("--scenario", required=True)
    p_run.add_argument("--seed", type=int, required=True)
    p_run.add_argument("--turns", type=int, default=1200)
    p_run.add_argument("--snapshot-at", type=int, default=500)
    p_run.add_argument("--out", required=True)
    p_run.set_defaults(func=run)
    p_rep = sub.add_parser("report")
    p_rep.add_argument("full")
    p_rep.add_argument("resume")
    p_rep.add_argument("--out", required=True)
    p_rep.set_defaults(func=report)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
