"""ターン処理の進行役。段を game.yaml の stages: の順に呼ぶだけです。"""

from __future__ import annotations

import copy
import importlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.registry import IslandStage, Registry, Stage, WorldStage
from hakoniwa_core.state import WorldState
from hakoniwa_core.turn.context import StageContext


class EngineError(Exception):
    pass


@dataclass
class TurnReport:
    turn: int
    seconds: dict[str, float] = field(default_factory=dict)  # 段ごとにかかった時間

    @property
    def total_seconds(self) -> float:
        return sum(self.seconds.values())


class Engine:
    def __init__(self, defs: Defs, config: GameConfig, registry: Registry):
        self.defs = defs
        self.config = config
        self.registry = registry
        self.stages: list[Stage] = []
        for name in config.stages:
            if name not in registry.stages:
                raise EngineError(
                    f"game.yaml の stages にある段 {name} はどのモジュールにもありません"
                )
            self.stages.append(registry.stages[name])
        self.formulas = _resolve_formulas(registry, config)
        missing = sorted(set(defs.orders) - set(registry.orders))
        if missing:
            raise EngineError(f"処理する関数がない命令があります: {', '.join(missing)}")

    def run_turn(self, world: WorldState) -> TurnReport:
        """世界を1ターン進める。world をその場で書き換える。

        途中で例外が出たときの world は中途半端なので使わないこと
        （サーバーは保存せず、ターンも進めない）。
        """
        turn = world.turn + 1
        report = TurnReport(turn=turn)
        for island in world.islands.values():
            island.log.clear()
        for stage in self.stages:
            started = time.perf_counter()
            if isinstance(stage, IslandStage):
                self._run_island_stage(stage, world, turn)
            else:
                self._run_world_stage(stage, world, turn)
            report.seconds[stage.name] = time.perf_counter() - started
        world.turn = turn
        return report

    def _context(self, stage: Stage, world: WorldState, turn: int, before: Any) -> StageContext:
        return StageContext(
            defs=self.defs,
            config=self.config,
            registry=self.registry,
            formulas=self.formulas,
            turn=turn,
            seed=world.seed,
            stage=stage.name,
            before=before,
        )

    def _run_island_stage(self, stage: IslandStage, world: WorldState, turn: int) -> None:
        before = copy.deepcopy(world) if stage.reads_other_islands else None
        ctx = self._context(stage, world, turn, before)
        for island_id in world.island_ids():
            stage.run_island(world.islands[island_id], ctx)

    def _run_world_stage(self, stage: WorldStage, world: WorldState, turn: int) -> None:
        ctx = self._context(stage, world, turn, world)
        ids = world.island_ids()
        requests = {i: stage.collect(world.islands[i], ctx) for i in ids}
        results = stage.resolve(requests, ctx)
        ctx.before = None
        for i in ids:
            stage.apply(world.islands[i], results.get(i), ctx)


def _resolve_formulas(registry: Registry, config: GameConfig) -> dict[str, Callable[..., Any]]:
    formulas = dict(registry.formulas)
    for name, target in config.formulas.items():
        if name not in formulas:
            raise EngineError(
                f"game.yaml の formulas にある式 {name} はどのモジュールにもありません"
            )
        module_name, _, attr = target.partition(":")
        try:
            formulas[name] = getattr(importlib.import_module(module_name), attr)
        except (ImportError, AttributeError) as e:
            raise EngineError(f"式 {name} の差し替え先 {target} を読み込めません: {e}") from e
    return formulas
