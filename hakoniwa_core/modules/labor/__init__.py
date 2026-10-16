"""賃金の段（payroll）: 施設ごとの賃金を払い、所得税を差し引き、失業手当を払い、賃金を見直す。

賃金は雇用の段で雇った人数分を払います（稼働率にかかわらず）。払うのは施設の会計の形で決まり
（economy/__init__.py の payer_of）、工事とインフラの作業員と公務員は国庫が払います。所得税は
賃金から差し引いて政府に払います。失業手当は「失業者1人に平均賃金の決めた割合」を国庫から払います。

技術者（フェーズ2-3）は技術者の層に払います。技術者の席の人には施設の技術者の賃金を、一般の席に
就いた人には一般の賃金を払います。失業した技術者にも失業手当を払います。

最後に、施設ごとに次のターンの賃金を見直します（式 wage_update。技術者の賃金は
engineer_wage_update）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    ENGINEERS,
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
from hakoniwa_core.economy.households import add_income, jobless_layer, set_jobless
from hakoniwa_core.economy.prices import variable_profit
from hakoniwa_core.economy.stats import inflation
from hakoniwa_core.modules import infrastructure
from hakoniwa_core.modules.employment import unemployment_rate
from hakoniwa_core.modules.population import jobless_persons, tile_jobless
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
        workers -= fac.engineers_needed(ctx.defs, f.kind)  # 技術者の賃金は利益の側で引く
        if workers > 0 and fac.recipe_of(ctx.defs, f)["outputs"]:
            cap = variable_profit(ctx.defs, ctx.config, f, prices, 0, rate) // workers
            new = min(new, max(wage, cap))
    return max(new, island.policy.minimum_wage, 1)


def engineer_unemployment(island: IslandState) -> float:
    """技術者の失業率（雇用の段で席に就けなかった技術者の割合）。"""
    pool = island.economy.workers.get(fac.ENGINEER, 0)
    if pool <= 0:
        return 0.0
    return island.scratch.get("engineers_left", 0) / pool


def engineer_wage_update(island: IslandState, f: Facility, ctx: StageContext) -> int:
    """次のターンの技術者の賃金: 一般の賃金（式 wage_update）と同じ式で、人手の足りなさは技術者の
    席の埋まらなかった割合、失業率は島の技術者の失業率で見る。技術者は一般の席にも就けるので、
    施設の一般の賃金より下げない。"""
    settings = ctx.config.get("labor")
    wage = fac.engineer_wage_of(ctx.config, f, f.economy["wage"])
    seats = f.economy.get("engineers_wanted", 0)
    hired = f.economy.get("engineers", 0)
    if seats > hired:
        gap = (seats - hired) / seats
    else:
        natural = settings["natural_unemployment"]
        gap = min(0.0, (natural - engineer_unemployment(island)) / natural)
    speed = settings["wage_speed"] if gap > 0 else settings["slack_speed"]
    change = settings["indexation"] * max(0.0, inflation(island.economy)) / 12 + speed * gap
    change = max(-settings["max_cut"], min(settings["max_raise"], change))
    return max(scale_price(wage, 1 + change), f.economy["wage"])


def has_own_wage(defs: Defs, f: Facility) -> bool:
    """賃金を自分で見直す施設か（工事中の施設と公務員の施設は島の平均賃金で雇う）。"""
    return f.built and not is_public_service(defs, f) and "wage" in f.economy


class Payroll(IslandStage):
    name = "payroll"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        defs, config = ctx.defs, ctx.config
        # 雇用の段の結果で、失業している家計の人数を数え直す（仕事を失った人は預金を持って移る）
        set_jobless(island, jobless_persons(defs, island))
        worker = household(GENERAL)
        paid = workers = 0  # 一般の賃金（島の平均賃金）
        skilled_paid = skilled_workers = 0  # 技術者の席の賃金
        for f in sorted(island.facilities, key=lambda f: f.id):
            if f.workers <= 0 or "wage" not in f.economy:
                continue
            payer = payer_of(defs, config, f)
            wage = f.economy["wage"]
            skilled = f.economy.get("engineers", 0)
            spared = f.economy.get("engineers_general", 0)
            # 労働の動員で割り当てた人（フェーズ3-4）は、動員の賃金で払う
            drafted = f.economy.get("mobilized", 0)
            drafted_pay = drafted * f.economy.get("mobilized_wage", 0)
            amount = wage * (f.workers - skilled - spared - drafted) + drafted_pay
            funds.pay(island, payer, worker, amount, "wage", committed=True)
            self._withhold(island, amount)
            paid += wage * (f.workers - skilled - drafted) + drafted_pay
            workers += f.workers - skilled
            if skilled or spared:  # 技術者の層に払う
                premium = fac.engineer_wage_of(config, f, wage) * skilled
                pay = premium + wage * spared
                funds.pay(island, payer, household(ENGINEERS), pay, "wage", committed=True)
                self._withhold(island, pay, ENGINEERS)
                amount += pay
                skilled_paid += premium
                skilled_workers += skilled
            if not f.built:
                books.add_sector(island, books.CONSTRUCTION, "wages", amount)
                f.economy["capital"] = f.economy.get("capital", 0) + amount
            else:
                if is_public_service(defs, f):
                    books.add_sector(island, books.GOVERNMENT, "wages", amount)
                books.add(island, f.id, books.WAGES, amount)
        if skilled_workers:
            island.scratch["engineer_wage"] = skilled_paid // skilled_workers
        hired, wage = infrastructure.payroll(island)  # インフラの作業員
        if hired > 0:
            amount = hired * wage
            funds.pay(island, infrastructure.PAYER, worker, amount, "wage", committed=True)
            self._withhold(island, amount)
            books.add_sector(island, books.CONSTRUCTION, "wages", amount)
            paid += amount
            workers += hired
        self._benefits(island, ctx)
        for f in island.facilities:
            if has_own_wage(defs, f):
                f.economy["wage"] = ctx.formula("wage_update")(island, f, ctx)
                if "engineer_wage" in f.economy:
                    f.economy["engineer_wage"] = ctx.formula("engineer_wage_update")(island, f, ctx)
        if workers:
            island.economy.average_wage = paid // workers
        keep = config.get("records.history_turns")
        history = [*island.economy.wage_history, island.economy.average_wage]
        island.economy.wage_history = history[-keep:]

    @staticmethod
    def _withhold(island: IslandState, amount: int, layer: str = GENERAL) -> None:
        """所得税を差し引いて政府に払い、残りを層 layer の可処分所得にする。"""
        tax = part(amount, island.policy.income_tax)
        ledger.pay(island, household(layer), ledger.GOV, tax, "tax_income")
        add_income(island, layer, amount - tax)

    @staticmethod
    def _benefits(island: IslandState, ctx: StageContext) -> None:
        """失業手当: 失業者（自給の農業をしている村の人を除く）1人に平均賃金の決めた割合。
        失業している家計の層（回3）に払う。"""
        rate = island.policy.unemployment_benefit
        if rate <= 0:
            return
        defs = ctx.defs
        unemployed = sum(tile_jobless(defs, island, t) for t in island.tiles if t.population > 0)
        payer = Payer(ledger.GOV, UNEMPLOYMENT)
        benefit = part(island.economy.average_wage, rate)
        idle = island.scratch.get("engineers_left", 0)  # 席に就けなかった技術者
        for layer, count in ((jobless_layer(island), unemployed), (ENGINEERS, idle)):
            if layer not in island.economy.households:
                continue
            amount = min(count * benefit, funds.available(island, payer))
            if amount > 0:
                funds.pay(island, payer, household(layer), amount, "unemployment_benefit")
                add_income(island, layer, amount)


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
    registry.add_formula("engineer_wage_update", engineer_wage_update)
    registry.add_order(
        OrderHandler("set_labor_policy", execute=set_labor_policy, check=check_labor_policy)
    )
