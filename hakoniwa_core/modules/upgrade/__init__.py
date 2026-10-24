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

フェーズ3-4 で足したものは、ここではほとんど置きません。政治の状態のない島は、政情の段の最初の
ターンに政情を目標から始めます（いきなり暴動にしない）。計画経済の島は、市場の段の初めに原価を
解きます（economy/plan.py の prepare）。ただし、フェーズ3-4 より前に建てた見本の島のように政治の
体制（政治体制と国家元首）が決まっていない島には、軸ごとに定義の初めの選択（民主主義・君主制）を
置き、政情を目標から始め直します（体制がないと政情の目標が低いまま暴動が続くため）。経済体制の軸は
置きません（空のままでも経済体制の既定で動いていて、置くと施設の持ち主や会計の扱いが変わるため）。

バランス調整の回3 からは、失業している家計の層（economy/households.py）のない島に層を置きます
（人数は同じターンの賃金の段で、雇用の結果から数えます）。
回4 からは、お金の経済のある島にまだ商業・サービス業がなければ置き、サービスの値段を今の平均賃金から
決めます（economy/services.py の prepare。建国したばかりの島も、最初の更新でここを通る）。
国債の期間の割合に小数が入った島は、割合を整数に直します（modules/bonds の whole_terms。夜の点検
2026-10-17 で、小数の割合から国庫や工事に小数が広がってターンが止まった）。

回7 後半からは、定義から消えた作り方を指す施設（自動車工場の「アルミ」）を既定の作り方に移します
（fix_recipes。サーバーの世界は移行ファイル server/world/migrations/0006 で同じことをする）。

フェーズ4-2 からは、銅の鉱脈を一部の島に 1 度だけ置きます（veins.py。経済のない島にも）。

移し替えをターンの中で行うのは、サーバーを通さずにコアだけで世界を回すとき（一括シミュレーションや
テスト）にも同じに動くようにするためです。
"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core import nature, politics, veins
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import groundwork, services
from hakoniwa_core.economy.households import ensure_jobless
from hakoniwa_core.economy.prices import add_missing_prices
from hakoniwa_core.modules.bonds import whole_terms
from hakoniwa_core.modules.central_bank import end_for_planned
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.trade import setup as trade_setup
from hakoniwa_core.trade import started as trade_started
from hakoniwa_core.turn.context import StageContext


class Upgrade(IslandStage):
    name = "upgrade"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        economy = island.economy
        for good in veins.place(island, ctx.config, ctx.seed):  # 経済のない島にも置く（MI-60）
            island.log.append(LogEntry("vein_found", {"good": good}))
        if not island.has_economy:
            return
        if economy.ext.get("version", 0) < groundwork.VERSION:
            groundwork.prepare(island, ctx.defs, ctx.config, ctx.turn)
            if economy.happiness_blend <= 0:
                economy.happiness_blend = ctx.config.get("population.blend_turns")
                economy.ext["happiness_from"] = "happiness_phase2"
        add_missing_prices(island, ctx.defs, ctx.config)
        ensure_jobless(island, ctx.config)
        services.prepare(island, ctx.defs, ctx.config)
        whole_terms(island)
        if not trade_started(island):
            trade_setup.prepare(island, ctx.defs, ctx.config, ctx.turn)
        if economy.ext.get("nature", 0) < nature.VERSION:
            nature.migrate(island, ctx.config)
            economy.happiness_blend = ctx.config.get("population.blend_turns")
            economy.ext["happiness_from"] = "happiness_phase3_1"
        elif island.ext.get(nature.BLEND):
            nature.step_blend(island)
        fill_regime(island, ctx.defs)
        fix_recipes(island, ctx.defs)
        end_for_planned(island, ctx)  # 計画経済の国は中央銀行を独立させられない（BK-43）


def fix_recipes(island: IslandState, defs: Defs) -> None:
    """定義から消えた作り方を指す施設を、既定の作り方（default）に移す（回7 後半）。"""
    for f in island.facilities:
        recipes = defs.facilities[f.kind]["recipes"]
        if f.recipe not in recipes and "default" in recipes:
            f.recipe = "default"
        if f.ext.get(fac.RECIPE_NEXT, f.recipe) not in recipes:
            f.ext.pop(fac.RECIPE_NEXT)


KEEP_AXES = ("economy",)  # 欠けていても置かない軸


def fill_regime(island: IslandState, defs: Defs) -> None:
    """政治の体制の軸が欠けた島に、軸ごとに定義の初めの選択を置き、政情を目標から始め直す。"""
    missing = {
        axis: choices[0]
        for axis, choices in defs.regime_axes().items()
        if axis not in KEEP_AXES and choices and island.regime.get(axis) not in defs.regimes
    }
    if not missing:
        return
    island.regime.update(missing)
    politics.state_of(island).pop("stability", None)
    shown = {
        axis: island.regime[axis] for axis in ("politics", "head_of_state") if axis in island.regime
    }
    island.log.append(LogEntry("regime_filled", shown))


def register(registry: Registry) -> None:
    registry.add_stage(Upgrade())
