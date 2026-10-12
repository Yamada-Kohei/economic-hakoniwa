"""賃金の段（payroll）: 施設ごとの賃金を払い、所得税を差し引き、失業手当を払い、賃金を見直す。

賃金は雇用の段で雇った人数分を払います（稼働率にかかわらず）。払うのは施設の会計の形で決まり
（economy/__init__.py の payer_of）、工事の作業員と公務員は国庫が払います。所得税は賃金から
差し引いて政府に払います。失業手当は「失業者1人に平均賃金の決めた割合」を国庫から払います。

最後に、施設ごとに次のターンの賃金を見直します（式 wage_update）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    GENERAL,
    UNEMPLOYMENT,
    Payer,
    books,
    freight,
    funds,
    household,
    is_public_service,
    is_self_supporting,
    is_transport,
    payer_of,
)
from hakoniwa_core.economy.households import add_income
from hakoniwa_core.economy.prices import variable_profit
from hakoniwa_core.economy.stats import inflation
from hakoniwa_core.modules.employment import unemployment_rate
from hakoniwa_core.modules.population import subsistence_persons
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import Facility, IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import part, scale_price, to_basis


def wage_update(island: IslandState, f: Facility, ctx: StageContext) -> int:
    """次のターンの賃金: W × (1 + a × max(0, π) / 12 + k_w × g)。

    物価が上がった分の a（labor.indexation）を追いかけるが、物価が下がっても、それだけでは賃金を
    下げない（賃金は下がりにくい）。a が 1 より小さいので、一度の値上がりが賃金と物価の上がり続けに
    ならない。

    g は人手の足りなさ。雇いたい人数に届かなければ「足りない人数 ÷ 雇いたい人数」（k_w は
    labor.wage_speed）、全員を雇えたら「(u* − u) ÷ u*」を 0 以下に切った値（u は通勤圏の失業率、
    u* はふつうの失業率。k_w は labor.slack_speed）。
    1ターンの変化は labor.max_raise と labor.max_cut まで、最低賃金より下がらない。
    独立採算の施設は、今の値段で変動費の利益が 0 になる賃金より上げない。
    """
    settings = ctx.config.get("labor")
    wage = f.economy["wage"]
    wanted = f.economy.get("wanted", 0)
    if wanted > f.workers:
        gap = (wanted - f.workers) / wanted
    else:
        natural = settings["natural_unemployment"]
        gap = min(0.0, (natural - unemployment_rate(island, ctx, f)) / natural)
    speed = settings["wage_speed"] if gap > 0 else settings["slack_speed"]
    change = settings["indexation"] * max(0.0, inflation(island.economy)) / 12 + speed * gap
    change = max(-settings["max_cut"], min(settings["max_raise"], change))
    new = scale_price(wage, 1 + change)
    if new > wage and is_self_supporting(f) and not is_transport(ctx.config, f):
        workers = fac.workers_needed(ctx.defs, f.kind)
        rate = freight.facility_rates(island, ctx.defs, ctx.config).get(f.id, 0)
        prices = island.economy.prices
        if workers > 0 and fac.recipe_of(ctx.defs, f)["outputs"]:
            cap = variable_profit(ctx.defs, ctx.config, f, prices, 0, rate) // workers
            new = min(new, max(wage, cap))
    return max(new, island.policy.minimum_wage, 1)


def has_own_wage(defs: Defs, f: Facility) -> bool:
    """賃金を自分で見直す施設か（工事中の施設と公務員の施設は島の平均賃金で雇う）。"""
    return f.built and not is_public_service(defs, f) and "wage" in f.economy


class Payroll(IslandStage):
    name = "payroll"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        defs, config = ctx.defs, ctx.config
        worker = household(GENERAL)
        paid = workers = 0
        for f in sorted(island.facilities, key=lambda f: f.id):
            if f.workers <= 0 or "wage" not in f.economy:
                continue
            amount = f.economy["wage"] * f.workers
            funds.pay(island, payer_of(defs, config, f), worker, amount, "wage", committed=True)
            self._withhold(island, amount)
            if not f.built:
                books.add_sector(island, books.CONSTRUCTION, "wages", amount)
                f.economy["capital"] = f.economy.get("capital", 0) + amount
            else:
                if is_public_service(defs, f):
                    books.add_sector(island, books.GOVERNMENT, "wages", amount)
                books.add(island, f.id, books.WAGES, amount)
            paid += amount
            workers += f.workers
        self._benefits(island, ctx)
        for f in island.facilities:
            if has_own_wage(defs, f):
                f.economy["wage"] = ctx.formula("wage_update")(island, f, ctx)
        if workers:
            island.economy.average_wage = paid // workers
        keep = config.get("records.history_turns")
        history = [*island.economy.wage_history, island.economy.average_wage]
        island.economy.wage_history = history[-keep:]

    @staticmethod
    def _withhold(island: IslandState, amount: int) -> None:
        """所得税を差し引いて政府に払い、残りを一般の層の可処分所得にする。"""
        tax = part(amount, island.policy.income_tax)
        ledger.pay(island, household(GENERAL), ledger.GOV, tax, "tax_income")
        add_income(island, GENERAL, amount - tax)

    @staticmethod
    def _benefits(island: IslandState, ctx: StageContext) -> None:
        """失業手当: 失業者（自給の農業をしている村の人を除く）1人に平均賃金の決めた割合。"""
        rate = island.policy.unemployment_benefit
        if rate <= 0:
            return
        defs = ctx.defs
        share = fac.labor_share(defs, island)
        unemployed = 0
        for tile in island.tiles:
            if tile.population <= 0:
                continue
            idle = max(0, fac.labor_force(defs, island, tile) - tile.ext.get("employed", 0))
            farming = int(subsistence_persons(defs, island, tile) * share)
            unemployed += max(0, idle - farming)
        payer = Payer(ledger.GOV, UNEMPLOYMENT)
        amount = unemployed * part(island.economy.average_wage, rate)
        amount = min(amount, funds.available(island, payer))
        if amount > 0:
            funds.pay(island, payer, household(GENERAL), amount, "unemployment_benefit")
            add_income(island, GENERAL, amount)


# --- 命令 -------------------------------------------------------------------------


def check_labor_policy(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not payload:
        raise OrderFailed("reason_invalid_payload")


def set_labor_policy(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """最低賃金（0 で決めない）と失業手当（平均賃金に対する % ）を決める。"""
    policy = island.policy
    if "minimum_wage" in payload:
        policy.minimum_wage = payload["minimum_wage"]
    if "unemployment_benefit" in payload:
        policy.unemployment_benefit = to_basis(payload["unemployment_benefit"] / 100)
    island.log.append(
        LogEntry(
            "labor_policy_set",
            {
                "minimum_wage": policy.minimum_wage,
                "unemployment_benefit": policy.unemployment_benefit / 100,
            },
        )
    )


def register(registry: Registry) -> None:
    registry.add_stage(Payroll())
    registry.add_formula("wage_update", wage_update)
    registry.add_order(
        OrderHandler("set_labor_policy", execute=set_labor_policy, check=check_labor_policy)
    )
