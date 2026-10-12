"""段に渡すもの。"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.registry import Registry
from hakoniwa_core.state import IslandState, WorldState
from hakoniwa_core.turn.rng import make_rng


@dataclass
class StageContext:
    defs: Defs
    config: GameConfig
    registry: Registry
    formulas: dict[str, Callable[..., Any]]
    turn: int  # いま処理しているターン（処理が終わると世界のターン番号がこれになる）
    seed: int
    stage: str
    before: WorldState | None  # 段が始まる前の世界（他の島を読む段だけ）

    def rng(self, island_id: int | None = None) -> random.Random:
        """この段・この島の乱数。島ごとの段では自分の島の番号を渡す。"""
        return make_rng(self.seed, self.turn, self.stage, island_id)

    def other_island(self, island_id: int) -> IslandState:
        """他の島の、段が始まる前の写し。読むだけにすること。"""
        if self.before is None:
            raise RuntimeError(
                f"段 {self.stage} が他の島を読むには reads_other_islands = True にしてください"
            )
        return self.before.islands[island_id]

    def formula(self, name: str) -> Callable[..., Any]:
        return self.formulas[name]
