"""教育の段（フェーズ2-3。回3 で人の蓄えにした）: 教育水準と、大学の技術者の育成・引退。

- 教育水準 E（economy.education、1000 分の 1）は「働き手のうち学校で学んだ人の割合」という人の
  蓄えです（回3）。毎ターン、働き手に加わる若い人と、引退や死亡で抜ける人の分だけ入れ替わり、
  入れ替わりの速さは働く期間（education.working_years 年）から出します（式 education_level）。
  働き手に加わる若い人の学んだ割合は、子どもたちが学校に通えた割合で、これも通う年数
  （education.school_years 年）をかけて入れ替わります（economy.ext["schooled"]）。今のターンに
  通えている割合 = min(1, Σ(学校の定員 × 稼働率) ÷ 子どもの数)、子どもの数は住民の
  education.children です。学校が止まっても、すでに学んだ人の分は残り、学校を建てても、学んだ
  人が働き手に入るまで時間がかかります（現実の韓国は教育の水準を大きく上げるのに30年ほど）。
  E は施設の生産性（economy/productivity.py）と幸福度に効きます。
- 大学は1ターンに「育てる数 × 稼働率 × 入学の割合」を一般の労働者から技術者にし、技術者は1ターンに
  education.retire_rate が引退して一般の層に戻ります（端数は次のターンに持ち越す）。育った人と
  引退した人の預金は、層を移る人と一緒に動きます（economy/households.py の set_workers）。
  入学の割合は 1 − 技術者の失業率 ÷ education.intake_unemployment（0 未満は 0）です。席に就けない
  技術者が多いと、技術者を目指す人が減ります（育ちすぎて失業する技術者が増え続けないように）。

学校と大学の稼働率は、教員の人手と教材（投入の消費財）の足りている割合の小さい方で、生産の段で
決まります。教員の賃金と教材の代金は教育費（予算の項目 education）から払います。
"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.economy.households import recount_engineers as _recount
from hakoniwa_core.economy.households import set_workers
from hakoniwa_core.modules.labor import engineer_unemployment
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import IslandState
from hakoniwa_core.turn.context import StageContext

FULL = 1000
STOCK = "education_stock"  # 働き手の学んだ割合（0〜1 の小数。economy.education はこれを丸めた値）
SCHOOLED = "schooled"  # 働き手に加わる若い人の学んだ割合（0〜1）


def education_level(island: IslandState, attending: float, ctx: StageContext) -> float:
    """次のターンの教育水準（0〜1。名前付きの式 education_level）。

    子どもたちの学んだ割合 C は、今通えている割合 attending へ 1 ÷ (通う年数 × 12) ずつ
    入れ替わり、働き手の学んだ割合 E は C へ 1 ÷ (働く年数 × 12) ずつ入れ替わる（1ターンに。
    働き手に加わる若い人と、引退して抜ける人の入れ替わり）。端数を丸めないよう、蓄えは
    economy.ext に小数で持つ。
    """
    settings = ctx.config.get("education")
    ext = island.economy.ext
    now = ext.get(STOCK, 0.0)
    if round(now * FULL) != island.economy.education:  # 建国や移し替えで水準が置き直された
        now = island.economy.education / FULL
    schooled = ext.get(SCHOOLED, now)
    schooled += (attending - schooled) / (settings["school_years"] * YEAR)
    now += (schooled - now) / (settings["working_years"] * YEAR)
    ext[SCHOOLED] = round(schooled, 6)
    return max(0.0, min(1.0, now))


def school_seats(island: IslandState, ctx: StageContext) -> tuple[float, float]:
    """このターンに動いた学校の定員と、大学が育てる技術者の数（稼働率を掛けた分）。"""
    seats = trained = 0.0
    for f in island.facilities:
        spec = ctx.defs.facilities[f.kind].get("education")
        if not spec or not f.built:
            continue
        # 教材に電子機器を使う作り方は、教員 1 人あたりの育ちが上がる（MI-43）
        rate = f.operating_rate / FULL * fac.recipe_of(ctx.defs, f).get("education_scale", 1.0)
        rate *= fac.size_of(f)  # 建て増した学校は規模の分だけ多く教える（段6 A3）
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
        if settings.get("intake_unemployment"):
            trained *= max(0.0, 1 - engineer_unemployment(island) / settings["intake_unemployment"])
        attending = economy.ext.get(SCHOOLED, economy.education / FULL)
        if children > 0:
            attending = min(1.0, seats / children)
        level = ctx.formula("education_level")(island, attending, ctx)
        economy.ext[STOCK] = round(level, 6)
        economy.education = round(level * FULL)
        island.stats["education.level"] = economy.education / 10
        island.stats["education.goal"] = round(attending * 100, 1)
        island.stats["education.schooled"] = round(economy.ext[SCHOOLED] * 100, 1)
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
            _recount(island, pool, grown_now, retired_now)
        island.stats["engineers.trained"] = grown_now
        island.stats["engineers.retired"] = retired_now


def register(registry: Registry) -> None:
    registry.add_stage(Education())
    registry.add_formula("education_level", education_level)
