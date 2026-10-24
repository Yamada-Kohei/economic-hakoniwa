"""災害の段。控えめにし、施設が何ターンか止まる程度にとどめます（マスごと消える災害はない）。

game.yaml の disasters.rate が1つの島に1ターンで災害が起きる確率です。0 にすると起きません。

年の終わり（12月）には、島ごとに次の年の雨のぶれを引きます（nature.draw_rain。水力発電所の出力が
渇水の年に落ちる。W-148）。水力発電所のある島には、雨の少ない年（平年の 8 割未満）に知らせます。
"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core import nature
from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.turn.rng import make_rng

DRY_YEAR = 0.8  # 雨のぶれがこれを下回る年は、水力発電所のある島に知らせる


class Events(IslandStage):
    name = "events"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if ctx.turn % TURNS_PER_YEAR == 0:
            self._draw_rain(island, ctx)
        settings = ctx.config.get("disasters")
        rng = ctx.rng(island.id)
        if settings["rate"] <= 0 or rng.random() >= settings["rate"]:
            return
        targets = [f for f in island.facilities if f.built and not fac.is_housing(ctx.config, f)]
        if not targets:
            return
        f = rng.choice(targets)
        kind = rng.choice(sorted(settings["kinds"]))
        low, high = settings["kinds"][kind]["halt_turns"]
        turns = rng.randint(low, high)
        # 止まっている施設は、止まる期間を短くしない（暴動でもっと長く止まっている施設が、災害で
        # 早く動き出していた。回6 の揺さぶりの試験で見つけた）
        if ctx.turn + turns >= f.ext.get("halted_until", 0):
            f.ext["halted_until"] = ctx.turn + turns
            f.ext.pop("halt_reason", None)  # 暴動で止まっていた（フェーズ3-4）のを災害に変える
        if fac.on_map(f):
            args = {"facility": f.kind, "x": f.x, "y": f.y, "turns": turns}
            island.log.append(LogEntry(f"disaster_{kind}", args))
        else:  # 運送業のようなマスを持たない施設は、島全体の施設として知らせる
            args = {"facility": f.kind, "turns": turns}
            island.log.append(LogEntry(f"disaster_{kind}_island", args))

    @staticmethod
    def _draw_rain(island: IslandState, ctx: StageContext) -> None:
        # 災害の乱数とは別に引く（災害の起き方を変えない）
        rng = make_rng(ctx.seed, ctx.turn, "rain", island.id)
        value = nature.draw_rain(island, ctx.config, rng, ctx.turn)
        hydro = any(
            nature.water_spec(ctx.defs.facilities[f.kind]["recipes"][f.recipe]) is not None
            for f in island.facilities
        )
        if hydro and value < DRY_YEAR:
            island.log.append(LogEntry("rain_dry", {"percent": round(value * 100)}))


def register(registry: Registry) -> None:
    registry.add_stage(Events())
