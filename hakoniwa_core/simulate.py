"""画面も DB もなしで、同じ段を回す一括シミュレーション。

    python -m hakoniwa_core.simulate --islands 50 --turns 300 --seed 1 --autoplan --gate

更新にかかる時間の測定や、自動テストでの確認に使います。--autoplan を付けると、
どの島にも標準の計画（hakoniwa_core/autoplan.py）で命令を並べます。--gate を付けると、
フェーズ2の関門の目安（hakoniwa_core/gate.py）から外れたところを最後に並べます。

バランス調整の回2 から、--scenario で場面（標準の計画・命令なし・中央銀行を独立させた国・少ない島の
世界。hakoniwa_core/measure.py）を選べます。--out で結果のまとめを JSON に（measure compare で
比べる）、--series で島×ターンの時系列（失業の内わけなど）を JSON Lines に書きます。

崩れを調べるため（やり直しの報告の I5）、--save で最後のターンの世界を写しのファイルに書き、
--load で写しから続けられます。同じ種・同じ版なら同じ世界になるので、崩れの初めの数ターン前まで
回して写します（測りと夜の点検が、崩れごとにその命令を出します）。

    python -m hakoniwa_core.simulate --scenario plan --seed 1 --turns 137 --save s1-t137.json
    python -m hakoniwa_core.simulate --scenario plan --load s1-t137.json --turns 10 --gate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from contextlib import ExitStack
from dataclasses import asdict

from hakoniwa_core import measure
from hakoniwa_core.game import load_game
from hakoniwa_core.gate import START, Recorder
from hakoniwa_core.state import MarketRecord, WorldState
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.state.serialize import world_from_dict, world_to_dict


def world_digest(world: WorldState) -> str:
    """世界の状態の指紋。同じ種で同じ結果になるかを比べるのに使う。"""
    data = json.dumps(world_to_dict(world), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def save_snapshot(world: WorldState, path: str) -> None:
    """世界の写しを書く。標準の計画が読む前のターンの統計と市場の記録も一緒に書く（サーバーでは
    これらは状態の写しでなく表にある）。"""
    data = {
        "world": world_to_dict(world),
        "stats": {str(i.id): i.stats for i in world.islands.values()},
        "markets": {str(i.id): [asdict(m) for m in i.markets] for i in world.islands.values()},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def load_snapshot(path: str) -> WorldState:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    world = world_from_dict(data["world"])
    for iid, island in world.islands.items():
        island.stats = dict(data["stats"].get(str(iid), {}))
        island.markets = [MarketRecord(**m) for m in data["markets"].get(str(iid), [])]
    return world


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--islands", type=int, default=None, help="島の数（既定は場面の数、50）")
    parser.add_argument("--turns", type=int, default=10)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--config", default=None, help="game.yaml の場所")
    parser.add_argument("--autoplan", action="store_true", help="標準の計画で命令を並べる")
    parser.add_argument(
        "--scenario",
        choices=sorted(measure.SCENARIOS),
        default=None,
        help="場面（既定は --autoplan なら plan、なければ none）",
    )
    parser.add_argument("--gate", action="store_true", help="関門の目安から外れたところを並べる")
    parser.add_argument("--out", default=None, help="結果のまとめを書く JSON のファイル")
    parser.add_argument(
        "--series", default=None, help="島×ターンの時系列を書く JSON Lines のファイル"
    )
    parser.add_argument(
        "--zones-every", type=int, default=12, help="時系列に町と村ごとの内わけを書く間隔（ターン）"
    )
    parser.add_argument("--save", default=None, help="最後のターンの世界の写しを書くファイル")
    parser.add_argument("--load", default=None, help="この写しから続ける（--seed は使わない）")
    args = parser.parse_args(argv)

    scenario = measure.SCENARIOS[args.scenario or ("plan" if args.autoplan else "none")]
    islands = args.islands or scenario.islands
    game = load_game(args.config)
    started = time.perf_counter()
    if args.load:
        world = load_snapshot(args.load)
        islands = len(world.islands)
        print(f"写しを読みました: ターン {world.turn}、{islands} 島")
    else:
        world = generate_world(args.seed, islands, game.config, game.defs)
        print(f"世界を作りました: {islands} 島（{time.perf_counter() - started:.2f} 秒）")
    print(f"場面: {scenario.title}")

    recorder = Recorder(world, game.defs, game.config) if args.gate or args.out else None
    with ExitStack() as stack:
        series = None
        if args.series:
            series = stack.enter_context(open(args.series, "w", encoding="utf-8"))
        meter = None
        if args.out or series is not None:
            meter = measure.Measure(
                world, game.defs, game.config, series, args.zones_every, scenario.war
            )
        per_turn = []
        for _ in range(args.turns):
            measure.queue_scenario_orders(world, scenario, game.defs, game.config)
            report = game.engine.run_turn(world)
            per_turn.append(report.total_seconds)
            if recorder is not None:
                recorder.record(world)
            if meter is not None:
                meter.record(world)
    seconds = time.perf_counter() - started
    print(
        f"{args.turns} ターン: 1ターンの平均 {statistics.mean(per_turn) * 1000:.1f} ms、"
        f"最大 {max(per_turn) * 1000:.1f} ms"
    )
    population = sum(t.population for i in world.islands.values() for t in i.tiles)
    facilities = sum(len(i.facilities) for i in world.islands.values())
    print(f"人口の合計 {population}、施設の数 {facilities}")
    slowest = max(report.seconds, key=report.seconds.get) if report.seconds else "-"
    print(f"最後のターンで一番時間のかかった段: {slowest}")
    digest = world_digest(world)
    print(f"ターン {world.turn} の状態の指紋: {digest}")
    if args.save:
        save_snapshot(world, args.save)
        print(f"写しを書きました: {args.save}")
    if args.out and recorder is not None and meter is not None:
        data = measure.result(
            scenario, args.seed, args.turns, islands, recorder, meter, seconds, digest, world
        )
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    if args.gate and recorder is not None:
        summary = recorder.summary()
        print("関門の目安で見る値: " + "、".join(f"{k} {v:.3f}" for k, v in summary.items()))
        print("値段 ÷ 初めの値段 ÷ 物価指数の伸び（最小・中央・最大）:")
        for good, (low, mid, high) in recorder.prices().items():
            print(f"  {good}: {low:.2f}・{mid:.2f}・{high:.2f}")
        print("財ごとの、作った島と売り買いのあった島の数（1ターンあたりの平均）:")
        for good, (made, traded) in recorder.goods().items():
            print(f"  {good}: {made:.1f}・{traded:.1f}")
        collapses = recorder.collapses()
        print(f"崩れ（1ターン目から）: {len(collapses)} 件")
        for c in collapses:
            print("  " + c["line"])
            if not args.load:
                print("    写し: " + measure.snapshot_command(scenario.name, args.seed, c["first"]))
        failures = recorder.failures(START, kinds=scenario.kinds)
        counted = "全部" if scenario.kinds is None else "、".join(scenario.kinds)
        print(f"目安から外れたところ（この場面で数える目安: {counted}）: {len(failures)} 件")
        for line in failures:
            print("  " + line)
        others = len(recorder.failures(START)) - len(failures)
        if others:
            print(f"（この場面で数えない目安から外れたところ: {others} 件）")
        return 1 if failures or collapses else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
