"""教育の段（フェーズ2-3）: 教育水準と、大学の技術者の育成・引退。

- 教育水準 E（economy.education、1000 分の 1）は、毎ターン目標 E_goal との差の 1/education.speed
  だけ動きます（式 education_level）。E_goal = min(1, Σ(学校の定員 × 稼働率) ÷ 子どもの数)、
  子どもの数は住民の education.children です。教育費を削っても E はすぐには下がらず、数年かけて
  効いてきます。E は施設の生産性（economy/productivity.py）と幸福度に効きます。
- 大学は1ターンに「育てる数 × 稼働率」を一般の労働者から技術者にし、技術者は1ターンに
  education.retire_rate が引退して一般の層に戻ります（端数は次のターンに持ち越す）。育った人と
  引退した人の預金は、層を移る人と一緒に動きます（economy/households.py の set_workers）。

学校と大学の稼働率は、教員の人手と教材（投入の消費財）の足りている割合の小さい方で、生産の段で
決まります。教員の賃金と教材の代金は教育費（予算の項目 education）から払います。
"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy.households import set_workers
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import IslandState
from hakoniwa_core.turn.context import StageContext

FULL = 1000


def education_level(island: IslandState, goal: int, ctx: StageContext) -> int:
    """次のターンの教育水準: E + (E_goal − E) ÷ education.speed（名前付きの式 education_level）。"""
    now = island.economy.education
    step = (goal - now) / ctx.config.get("education.speed")
    return max(0, min(FULL, now + round(step)))


def school_seats(island: IslandState, ctx: StageContext) -> tuple[float, float]:
    """このターンに動いた学校の定員と、大学が育てる技術者の数（稼働率を掛けた分）。"""
    seats = trained = 0.0
    for f in island.facilities:
        spec = ctx.defs.facilities[f.kind].get("education")
        if not spec or not f.built:
            continue
        rate = f.operating_rate / FULL
        seats += spec.get("capacity", 0) * rate
        trained += spec.get("engineers", 0) * rate
    return seats, trained


class Education(IslandStage):
    name = "education"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        defs, settings = ctx.defs, ctx.config.get("education")
        economy = island.economy
        population = sum(t.population for t in island.tiles)
        children = population * settings["children"]
        seats, trained = school_seats(island, ctx)
        goal = economy.education
        if children > 0:
            goal = min(FULL, round(seats * FULL / children))
        economy.education = ctx.formula("education_level")(island, goal, ctx)
        island.stats["education.level"] = economy.education / 10
        island.stats["education.goal"] = goal / 10
        island.stats["education.seats"] = round(seats)
        # 技術者の育成と引退（端数は持ち越す）
        carry = economy.ext.setdefault("engineer_carry", [0.0, 0.0])
        pool = economy.workers.get(fac.ENGINEER, 0)
        grown = trained + carry[0]
        leaving = pool * settings["retire_rate"] + carry[1]
        grown_now = int(grown)
        general = round(population * fac.labor_share(defs, island))
        grown_now = min(grown_now, general)  # 一般の労働者より多くは育てられない
        retired_now = min(int(leaving), pool)
        carry[0], carry[1] = grown - int(grown), leaving - int(leaving)
        if grown_now or retired_now:
            set_workers(island, defs, fac.ENGINEER, pool + grown_now - retired_now)
        island.stats["engineers.trained"] = grown_now
        island.stats["engineers.retired"] = retired_now


def register(registry: Registry) -> None:
    registry.add_stage(Education())
    registry.add_formula("education_level", education_level)
