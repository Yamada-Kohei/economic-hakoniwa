"""画面も DB もなしで、同じ段を回す一括シミュレーション。

    python -m hakoniwa_core.simulate --islands 50 --turns 300 --seed 1 --autoplan --gate

更新にかかる時間の測定や、自動テストでの確認に使います。--autoplan を付けると、
どの島にも標準の計画（hakoniwa_core/autoplan.py）で命令を並べます。--gate を付けると、
フェーズ2の関門の目安（hakoniwa_core/gate.py）から外れたところを最後に並べます。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time

from hakoniwa_core.autoplan import plan_trade, plan_turn, queue_orders
from hakoniwa_core.game import load_game
from hakoniwa_core.gate import Recorder
from hakoniwa_core.state import WorldState
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.state.serialize import world_to_dict


def world_digest(world: WorldState) -> str:
    """世界の状態の指紋。同じ種で同じ結果になるかを比べるのに使う。"""
    data = json.dumps(world_to_dict(world), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--islands", type=int, default=50)
    parser.add_argument("--turns", type=int, default=10)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--config", default=None, help="game.yaml の場所")
    parser.add_argument("--autoplan", action="store_true", help="標準の計画で命令を並べる")
    parser.add_argument("--gate", action="store_true", help="関門の目安から外れたところを並べる")
    args = parser.parse_args(argv)

    game = load_game(args.config)
    started = time.perf_counter()
    world = generate_world(args.seed, args.islands, game.config, game.defs)
    print(f"世界を作りました: {args.islands} 島（{time.perf_counter() - started:.2f} 秒）")

    recorder = Recorder(world, game.defs, game.config) if args.gate else None
    per_turn = []
    for _ in range(args.turns):
        if args.autoplan:
            for island in world.islands.values():
                orders = plan_turn(island, game.defs, game.config, world.turn + 1)
                queue_orders(island, orders)
            for island_id, orders in plan_trade(world, game.defs, game.config).items():
                queue_orders(world.islands[island_id], orders)
        report = game.engine.run_turn(world)
        per_turn.append(report.total_seconds)
        if recorder is not None:
            recorder.record(world)
    print(
        f"{args.turns} ターン: 1ターンの平均 {statistics.mean(per_turn) * 1000:.1f} ms、"
        f"最大 {max(per_turn) * 1000:.1f} ms"
    )
    population = sum(t.population for i in world.islands.values() for t in i.tiles)
    facilities = sum(len(i.facilities) for i in world.islands.values())
    print(f"人口の合計 {population}、施設の数 {facilities}")
    slowest = max(report.seconds, key=report.seconds.get) if report.seconds else "-"
    print(f"最後のターンで一番時間のかかった段: {slowest}")
    print(f"ターン {world.turn} の状態の指紋: {world_digest(world)}")
    if recorder is not None:
        summary = recorder.summary()
        print("関門の目安で見る値: " + "、".join(f"{k} {v:.3f}" for k, v in summary.items()))
        print("値段 ÷ 初めの値段（最小・中央・最大）:")
        for good, (low, mid, high) in recorder.prices().items():
            print(f"  {good}: {low:.2f}・{mid:.2f}・{high:.2f}")
        print("財ごとの、作った島と売り買いのあった島の数（1ターンあたりの平均）:")
        for good, (made, traded) in recorder.goods().items():
            print(f"  {good}: {made:.1f}・{traded:.1f}")
        failures = recorder.failures()
        print(f"目安から外れたところ: {len(failures)} 件")
        for line in failures:
            print("  " + line)
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
