"""移し替えの段（upgrade）: 前のフェーズから続く島を、今のフェーズの形にする（1度だけ）。

ターンの初めの段で、フェーズ2-2 までの島に、フェーズ2-3 の生産の基盤（インフラ・教育・技術者の
初めの値。economy/groundwork.py）を置きます。幸福度の重みは、population.blend_turns ターンかけて
フェーズ2-2 までの重み（game.yaml の happiness_phase2）から今の重みに移します。

移し替えをターンの中で行うのは、サーバーを通さずにコアだけで世界を回すとき（一括シミュレーションや
テスト）にも同じに動くようにするためです。
"""

from __future__ import annotations

from hakoniwa_core.economy import groundwork
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import IslandState
from hakoniwa_core.turn.context import StageContext


class Upgrade(IslandStage):
    name = "upgrade"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        economy = island.economy
        if not island.has_economy or economy.ext.get("version", 0) >= groundwork.VERSION:
            return
        groundwork.prepare(island, ctx.defs, ctx.config)
        if economy.happiness_blend <= 0:
            economy.happiness_blend = ctx.config.get("population.blend_turns")
            economy.ext["happiness_from"] = "happiness_phase2"


def register(registry: Registry) -> None:
    registry.add_stage(Upgrade())
