"""移し替えの段（upgrade）: 前のフェーズから続く島を、今のフェーズの形にする（1度だけ）。

ターンの初めの段で、フェーズ2-2 までの島に、フェーズ2-3 の生産の基盤（インフラ・教育・技術者の
初めの値。economy/groundwork.py）を置きます。幸福度の重みは、population.blend_turns ターンかけて
フェーズ2-2 までの重み（game.yaml の happiness_phase2）から今の重みに移します。

フェーズ3-1 からは、お金の経済のある島にまだ貿易の仕組みがなければ、港・海運業・輸送船・商社を置き、
海外の商人を来させます（hakoniwa_core/trade/setup.py。建国したばかりの島も、最初の更新でここを通る）。

フェーズ3-2 からは、自然条件のない島に気候帯を置き（hakoniwa_core/nature.py の migrate）、気候の
倍率と幸福度の重み（happiness_phase3_1 から）を 12 ターンかけて移します。家計の自動車は 0 台から
始めます。値段のない財（新しい品目）には、初めの値段に今の物価指数の倍率を掛けた値段を置きます
（economy/prices.py の add_missing_prices。定義に財を足したときも同じ）。

フェーズ3-4 で足したものは、ここでは置きません。政治の状態のない島は、政情の段の最初のターンに
政情を目標から始めます（いきなり暴動にしない）。計画経済の島は、市場の段の初めに公定価格を今の平均
賃金で解きます（economy/plan.py の prepare）。

移し替えをターンの中で行うのは、サーバーを通さずにコアだけで世界を回すとき（一括シミュレーションや
テスト）にも同じに動くようにするためです。
"""

from __future__ import annotations

from hakoniwa_core import nature
from hakoniwa_core.economy import groundwork
from hakoniwa_core.economy.prices import add_missing_prices
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import IslandState
from hakoniwa_core.trade import setup as trade_setup
from hakoniwa_core.trade import started as trade_started
from hakoniwa_core.turn.context import StageContext


class Upgrade(IslandStage):
    name = "upgrade"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        economy = island.economy
        if not island.has_economy:
            return
        if economy.ext.get("version", 0) < groundwork.VERSION:
            groundwork.prepare(island, ctx.defs, ctx.config, ctx.turn)
            if economy.happiness_blend <= 0:
                economy.happiness_blend = ctx.config.get("population.blend_turns")
                economy.ext["happiness_from"] = "happiness_phase2"
        add_missing_prices(island, ctx.defs, ctx.config)
        if not trade_started(island):
            trade_setup.prepare(island, ctx.defs, ctx.config, ctx.turn)
        if economy.ext.get("nature", 0) < nature.VERSION:
            nature.migrate(island, ctx.config)
            economy.happiness_blend = ctx.config.get("population.blend_turns")
            economy.ext["happiness_from"] = "happiness_phase3_1"
        elif island.ext.get(nature.BLEND):
            nature.step_blend(island)


def register(registry: Registry) -> None:
    registry.add_stage(Upgrade())
