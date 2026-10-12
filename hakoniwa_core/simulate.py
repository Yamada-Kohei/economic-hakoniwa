"""画面も DB もなしで、同じ段を回す一括シミュレーション。

    python -m hakoniwa_core.simulate --islands 50 --turns 300 --seed 1

更新にかかる時間の測定や、自動テストでの確認に使います。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time

from hakoniwa_core.game import load_game
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
    args = parser.parse_args(argv)

    game = load_game(args.config)
    started = time.perf_counter()
    world = generate_world(args.seed, args.islands, game.config)
    print(f"世界を作りました: {args.islands} 島（{time.perf_counter() - started:.2f} 秒）")

    per_turn = []
    for _ in range(args.turns):
        report = game.engine.run_turn(world)
        per_turn.append(report.total_seconds)
    print(
        f"{args.turns} ターン: 1ターンの平均 {statistics.mean(per_turn) * 1000:.1f} ms、"
        f"最大 {max(per_turn) * 1000:.1f} ms"
    )
    print(f"ターン {world.turn} の状態の指紋: {world_digest(world)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
