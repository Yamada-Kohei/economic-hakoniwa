"""家計の電気 LB-47（power.household_curve）の測り直しの probe（掲示板 #5249・#5250・#5260）。

本人の答え（#5249）: power.household_curve（今は既定は切）は「測り直してから決める」。
今の main で切と入を比べ、11-13 の悪化（取引の止まり約 2 倍・貿易の途絶え・失業 +13%）が
今も出るかを見る。切/入は --off/--on で config の power.household_curve をその場で
切り替える（game.yaml は書き換えない）。

    python3 -X utf8 lb47_household_probe.py probe --scenario plan --seed 1 --off \
        --turns 300 --out lb47-off-plan-s1.jsonl.gz
    python3 -X utf8 lb47_household_probe.py report lb47-*.jsonl.gz --out report.md

場面は quick と同じ 6 場面（plan / none / independent / small / open / war）
× 種 1〜3、300 ターン、切/入の 36 本。1 行 = 1 島 × 1 ターン。

残す物:
  turn / island: ターンと島の番号
  regime: 島の経済体制（island.regime の economy。公定価格の島は LB-47 の段の注文が
    効かないので、読み分け用）
  unemployment: そのターンの失業率（stats の unemployment。記録の段と同じ式）
  trade: 輸出＋輸入（stats の trade.exports + trade.imports。貿易の途絶えの材料）
  power_shortage: 電気の不足率（stats の power.shortage = 1 − 使えた量 ÷ 要る量）
  power_demand / power_used: 電気の要る量と使えた量（stats の power.demand / power.used）
  power_short_supply: 要る量のうち、発電が全部売れて届かなかった割合（stats。
    本当の電力不足）
  power_unaffordable: 家計の要る量のうち、発電が売れ残っているのに電気料金を払えずに
    使えなかった割合（stats の power.household_unaffordable。家計の電気の買えなかった
    量の主の物差し）
  home: 家計の層ごとに届いた割合（stats の power.household.<層>）

終わりに <out>.gate.json を書く:
  counts: 崩れの gate.Recorder の種類ごとの数（取引の止まり = no_trade）
  found: 崩れの明細
  kinds: 目安外れ（Recorder.findings を 1 ターン目から）の種類ごとの数
    （貿易の途絶え = trade_stopped）
  trade_stopped: 貿易の途絶えの明細の行

集計（report）の軸（合格線は README.md に結果を見る前に固定）:
  線 1: 取引の止まり（gate の no_trade の数）が、入で切より増えた分が種のゆらぎの内か
  線 2: 貿易の途絶え（trade_stopped）が入で出ないか
  線 3: 失業率（全ターンの島ターンの平均と終盤 251〜300 の平均）の入と切の差が
    種のゆらぎの内か
  参考: 電気の不足率・家計の買えなかった割合・本当の電力不足の割合を切/入で並べる
  基準点の再現: 11-13 の悪化（止まり約 2 倍・途絶え・失業 +13%）が今回出たか
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

SCENARIOS = ("plan", "none", "independent", "small", "open", "war")
LATE = (251, 300)  # 終盤の窓（失業率の読み用）


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
    config.raw["power"]["household_curve"] = bool(args.on)
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
                home = {
                    key.removeprefix("power.household."): value
                    for key, value in sorted(s.items())
                    if key.startswith("power.household.")
                    and key != "power.household_unaffordable"
                }
                row = {
                    "turn": world.turn,
                    "island": iid,
                    "regime": island.regime.get("economy", ""),
                    "unemployment": s.get("unemployment"),
                    "trade": s.get("trade.exports", 0) + s.get("trade.imports", 0),
                    "power_shortage": s.get("power.shortage"),
                    "power_demand": s.get("power.demand"),
                    "power_used": s.get("power.used"),
                    "power_short_supply": s.get("power.short_supply"),
                    "power_unaffordable": s.get("power.household_unaffordable"),
                    "home": home,
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    findings = recorder.findings(1, None)
    kinds = Counter(kind for kind, _ in findings)
    gate_path = args.out.removesuffix(".gz").removesuffix(".jsonl") + ".gate.json"
    with open(gate_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "counts": recorder.collapse_counts(),
                "found": recorder.collapses(),
                "kinds": dict(kinds),
                "trade_stopped": [
                    line for kind, line in findings if kind == "trade_stopped"
                ],
            },
            fh,
            ensure_ascii=False,
            indent=1,
        )
    return 0


_NAME = re.compile(r"lb47-(off|on)-([a-z]+)-s(\d+)\.jsonl(\.gz)?$")


def _load(path: str):
    m = _NAME.search(Path(path).name)
    if not m:
        return None
    on = m.group(1) == "on"
    scenario, seed = m.group(2), int(m.group(3))
    rows = []
    with _open(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    gate = None
    gate_path = path.removesuffix(".gz").removesuffix(".jsonl") + ".gate.json"
    if Path(gate_path).exists():
        with open(gate_path, encoding="utf-8") as fh:
            gate = json.load(fh)
    return (scenario, seed, on), rows, gate


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def _run_summary(rows: list[dict]) -> dict:
    return {
        "unemployment": _mean(r["unemployment"] for r in rows),
        "unemployment_late": _mean(
            r["unemployment"] for r in rows if LATE[0] <= r["turn"] <= LATE[1]
        ),
        "power_shortage": _mean(r["power_shortage"] for r in rows),
        "power_unaffordable": _mean(r["power_unaffordable"] for r in rows),
        "power_short_supply": _mean(r["power_short_supply"] for r in rows),
    }


def _fmt(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.1f}%"


def report(args: argparse.Namespace) -> int:
    runs: dict[tuple[str, int, bool], dict] = {}
    gates: dict[tuple[str, int, bool], dict | None] = {}
    for path in args.files:
        loaded = _load(path)
        if loaded is None:
            print(f"読み飛ばし（名前の形が違う）: {path}", file=sys.stderr)
            continue
        key, rows, gate = loaded
        runs[key] = _run_summary(rows)
        gates[key] = gate
    if not runs:
        print("対象の結果がありません", file=sys.stderr)
        return 1

    def gate_count(key, section: str, kind: str) -> int | None:
        g = gates.get(key)
        if g is None:
            return None
        return g.get(section, {}).get(kind, 0)

    def no_trade(key) -> int | None:
        return gate_count(key, "counts", "no_trade")

    def trade_stopped(key) -> int | None:
        return gate_count(key, "kinds", "trade_stopped")

    lines: list[str] = []
    lines.append("# LB-47 家計の電気（power.household_curve）の測り直しの集計")
    lines.append("")
    lines.append(
        "場面は quick と同じ 6 場面 × 種 1〜3、300 ターン、切/入。合格線は README.md"
        "（結果を見る前に固定）。判定は会議。"
    )
    lines.append("")

    # 線 1: 取引の止まり
    lines.append("## 線 1: 取引の止まり（gate の no_trade の数）")
    lines.append("")
    lines.append("| 場面 | 切（種 1・2・3） | 切の幅 | 入（種 1・2・3） | 入の合計 / 切の合計 |")
    lines.append("|---|---|---|---|---|")
    total_off = total_on = 0
    total_known = True
    for scenario in SCENARIOS:
        offs = [no_trade((scenario, s, False)) for s in (1, 2, 3)]
        ons = [no_trade((scenario, s, True)) for s in (1, 2, 3)]
        if any(v is None for v in offs + ons):
            total_known = False
        total_off += sum(v or 0 for v in offs)
        total_on += sum(v or 0 for v in ons)
        span = (
            f"{min(v for v in offs if v is not None)}〜{max(v for v in offs if v is not None)}"
            if any(v is not None for v in offs)
            else "—"
        )
        lines.append(
            f"| {scenario} | {'・'.join(_fmt(v, 0) for v in offs)} | {span} "
            f"| {'・'.join(_fmt(v, 0) for v in ons)} "
            f"| {sum(v or 0 for v in ons)} / {sum(v or 0 for v in offs)} |"
        )
    ratio = (total_on / total_off) if total_off else None
    lines.append("")
    lines.append(
        f"全体の合計: 切 {total_off}、入 {total_on}"
        + (f"（入/切 = {ratio:.2f} 倍）" if ratio is not None else "（切が 0 なので倍率は出ない）")
        + ("。gate の無い本がある" if not total_known else "")
    )
    lines.append(
        "読み: 入の合計が切の合計以下なら「増えていない」。増えた場合は、場面ごとに"
        "入の種の値が切の種の幅（最小〜最大）の内にあるかで、ゆらぎの内かを読む。"
        "11-13 の基準点は「約 2 倍」。"
    )
    lines.append("")

    # 線 2: 貿易の途絶え
    lines.append("## 線 2: 貿易の途絶え（trade_stopped の出た島の数）")
    lines.append("")
    lines.append("| 場面 | 切（種 1・2・3） | 入（種 1・2・3） |")
    lines.append("|---|---|---|")
    stopped_on_total = 0
    for scenario in SCENARIOS:
        offs = [trade_stopped((scenario, s, False)) for s in (1, 2, 3)]
        ons = [trade_stopped((scenario, s, True)) for s in (1, 2, 3)]
        stopped_on_total += sum(v or 0 for v in ons)
        lines.append(
            f"| {scenario} | {'・'.join(_fmt(v, 0) for v in offs)} "
            f"| {'・'.join(_fmt(v, 0) for v in ons)} |"
        )
    lines.append("")
    lines.append(
        f"入の合計: {stopped_on_total}。"
        + ("線 2 は満たす（入で途絶えは出ていない）。" if stopped_on_total == 0 else "線 2 は外れる（入で途絶えが出た）。")
    )
    lines.append("")

    # 線 3: 失業率
    lines.append("## 線 3: 失業率（島ターンの平均）")
    lines.append("")
    lines.append(
        "| 場面 | 窓 | 切の中央値 | 切の種の幅 | 入の中央値 | 差（入−切） | 切の幅の広さ |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for scenario in SCENARIOS:
        for label, field in (("全ターン", "unemployment"), ("終盤 251〜300", "unemployment_late")):
            offs = [runs.get((scenario, s, False), {}).get(field) for s in (1, 2, 3)]
            ons = [runs.get((scenario, s, True), {}).get(field) for s in (1, 2, 3)]
            offs_v = [v for v in offs if v is not None]
            ons_v = [v for v in ons if v is not None]
            med_off = statistics.median(offs_v) if offs_v else None
            med_on = statistics.median(ons_v) if ons_v else None
            span = (max(offs_v) - min(offs_v)) if len(offs_v) >= 2 else None
            diff = (
                med_on - med_off if med_on is not None and med_off is not None else None
            )
            lines.append(
                f"| {scenario} | {label} | {_fmt(med_off)} "
                f"| {_fmt(min(offs_v) if offs_v else None)}〜{_fmt(max(offs_v) if offs_v else None)} "
                f"| {_fmt(med_on)} | {_fmt(diff)} | {_fmt(span)} |"
            )
    lines.append("")
    lines.append(
        "読み: 差が切の種の幅の広さの内なら「ゆらぎの内」。11-13 の基準点は「失業 +13%」"
        "（相対）。場面ごとの相対の差は次項。"
    )
    lines.append("")

    # 参考: 電気まわりと失業の相対差
    lines.append("## 参考: 電気の不足・家計の買えなかった量・失業の相対差（種の中央値）")
    lines.append("")
    lines.append(
        "| 場面 | 電気の不足率 切→入 | 家計の買えなかった割合 切→入 "
        "| 本当の電力不足 切→入 | 失業率の相対差（全ターン） |"
    )
    lines.append("|---|---|---|---|---|")
    for scenario in SCENARIOS:
        def med(field: str, on: bool) -> float | None:
            vals = [
                runs.get((scenario, s, on), {}).get(field)
                for s in (1, 2, 3)
            ]
            vals = [v for v in vals if v is not None]
            return statistics.median(vals) if vals else None

        rel = None
        off_u, on_u = med("unemployment", False), med("unemployment", True)
        if off_u and on_u is not None:
            rel = on_u / off_u - 1
        lines.append(
            f"| {scenario} | {_fmt(med('power_shortage', False))}→{_fmt(med('power_shortage', True))} "
            f"| {_fmt(med('power_unaffordable', False))}→{_fmt(med('power_unaffordable', True))} "
            f"| {_fmt(med('power_short_supply', False))}→{_fmt(med('power_short_supply', True))} "
            f"| {_fmt_pct(rel)} |"
        )
    lines.append("")

    # 崩れの全体
    kinds_all = sorted(
        {k for g in gates.values() if g for k in g.get("counts", {})}
    )
    if kinds_all:
        lines.append("## 崩れの合計（参考）")
        lines.append("")
        lines.append("| 種類 | 切（18 本） | 入（18 本） |")
        lines.append("|---|---|---|")
        for kind in kinds_all:
            off_sum = sum(
                (gates.get((sc, s, False)) or {}).get("counts", {}).get(kind, 0)
                for sc in SCENARIOS
                for s in (1, 2, 3)
            )
            on_sum = sum(
                (gates.get((sc, s, True)) or {}).get("counts", {}).get(kind, 0)
                for sc in SCENARIOS
                for s in (1, 2, 3)
            )
            lines.append(f"| {kind} | {off_sum} | {on_sum} |")
        lines.append("")

    missing = [
        f"{'on' if on else 'off'}-{sc}-s{seed}"
        for sc in SCENARIOS
        for seed in (1, 2, 3)
        for on in (False, True)
        if (sc, seed, on) not in runs
    ]
    if missing:
        lines.append(f"欠けている本: {', '.join(missing)}")
        lines.append("")

    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--scenario", choices=SCENARIOS, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--turns", type=int, default=300)
    p.add_argument("--out", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--off", action="store_true")
    group.add_argument("--on", action="store_true")
    p.set_defaults(func=probe)
    r = sub.add_parser("report")
    r.add_argument("files", nargs="+")
    r.add_argument("--out")
    r.set_defaults(func=report)
    args = parser.parse_args(argv)
    if args.cmd == "probe":
        args.on = bool(args.on)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
