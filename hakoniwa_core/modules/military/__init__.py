"""軍の維持の段（military。フェーズ4-2 の②。島の段）と、兵の命令。

置く場所は、国庫の取り置きの後・雇用の前です。兵の賃金を国防費（`defense_personnel`）から払い、払えない
分は未払い賃金に残します。食べ物は国の倉庫から食べ、足りなければ兵が飢え、次のターンの分は国として
市場で買う注文を出します（市場の段が約定させる）。士気が低い部隊からは兵が逃げて家計に戻ります。
中身は hakoniwa_core/military/forces.py、設計は docs/design/15-military.md の MI-07〜MI-14。

命令: 国防費（set_defense）、志願（recruit）、徴兵（conscript）、除隊（discharge）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import ledger, stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import GENERAL, Payer, arrears, books, funds, household
from hakoniwa_core.economy.households import add_income
from hakoniwa_core.military import forces
from hakoniwa_core.military.forces import BUDGET_PERSONNEL, BUDGET_PROCUREMENT
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, part, to_basis, to_milli

FOOD = "food"


def wage_bill(island: IslandState) -> int:
    """このターンの兵の賃金の合計（志願と徴兵で倍率が違う）。"""
    state = forces.state_of(island)
    average = island.economy.average_wage
    return part(average * state["volunteers"], state.get("volunteer_wage", BASIS)) + part(
        average * state["conscripts"], state.get("conscript_wage", BASIS)
    )


def food_need(island: IslandState, config) -> int:
    """兵が 1 ターンに食べる食べ物（1000 分の 1 トン）。"""
    per_thousand = to_milli(config.get("military.food_per_thousand"))
    return forces.soldiers(island) * per_thousand // 1000


def planned(island: IslandState, config) -> dict[str, int]:
    """国庫の取り置きの見積もりの下限。前のターンの支出が 0 でも、兵の賃金と食べ物の分は取り置く
    （開戦や徴兵の直後に取り置きが付かず、賃金が遅れるのを避ける。MI-13）。"""
    if not forces.soldiers(island):
        return {}
    price = island.economy.prices.get(FOOD, 0)
    food = food_need(island, config) * price // QUANTITY_SCALE
    return {BUDGET_PERSONNEL: wage_bill(island) + food * config.get("military.food_stock_turns")}


class Military(IslandStage):
    name = "military"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy or not forces.soldiers(island):
            return
        config = ctx.config
        state = forces.state_of(island, config)
        stats = island.stats
        unpaid = self._pay(island, state)
        self._morale(island, state, config, unpaid > 0)
        whole = forces.soldiers(island)
        new = min(state["new"], whole)  # 入ったターンは家で食べる
        state["new"] = 0
        need = food_need(island, config) * (whole - new) // whole
        eaten = stock.take(island.warehouse, FOOD, need)
        state["hungry"] = eaten < need
        if state["hungry"]:
            self._hunger(island, state, config, need, eaten)
        rate = to_basis(config.get("military.training.rate"))
        cap = to_basis(config.get("military.training.basic"))
        state["training"] = max(state["training"], min(cap, state["training"] + rate))
        self._desert(island, state, config)
        self._order_food(island, config)
        stats["military.soldiers"] = forces.soldiers(island)
        stats["military.morale"] = state["morale"] / BASIS
        stats["military.training"] = state["training"] / BASIS
        stats["military.wages_unpaid"] = unpaid

    @staticmethod
    def _pay(island: IslandState, state: dict[str, Any]) -> int:
        """兵の賃金を払う。払えない分は未払い賃金に残し、払えなかった額を返す。"""
        due = wage_bill(island)
        payer = Payer(ledger.GOV, BUDGET_PERSONNEL)
        pay = min(due, funds.available(island, payer))
        if pay > 0:
            funds.pay(island, payer, household(GENERAL), pay, "wage")
            tax = part(pay, island.policy.income_tax)
            ledger.pay(island, household(GENERAL), ledger.GOV, tax, "tax_income")
            add_income(island, GENERAL, pay - tax)
            books.add_sector(island, books.GOVERNMENT, "wages", pay)
        arrears.add_wages(island, ledger.GOV, household(GENERAL), due - pay)
        island.stats["military.wages"] = pay
        return due - pay

    @staticmethod
    def _morale(island: IslandState, state: dict[str, Any], config, late: bool) -> None:
        morale = config.get("military.morale")
        if late:
            state["late"] += 1
            state["morale"] -= to_basis(morale["late"])
            if state["late"] == 1:
                island.log.append(LogEntry("military_wages_late", {}))
        else:
            state["late"] = 0
            start = to_basis(morale["start"])
            state["morale"] = min(start, state["morale"] + to_basis(morale["recover"]))
        state["morale"] = max(0, state["morale"])

    @staticmethod
    def _hunger(island: IslandState, state: dict[str, Any], config, need: int, eaten: int) -> None:
        """食べ物が尽きると士気と練度が落ち、病気と飢えで兵が亡くなる（MI-14）。"""
        morale = config.get("military.morale")
        lack = BASIS - eaten * BASIS // need if need else 0
        state["morale"] = max(0, state["morale"] - part(to_basis(morale["hungry"]), lack))
        state["training"] = max(
            0, state["training"] - part(to_basis(config.get("military.training.hungry")), lack)
        )
        dead = part(forces.soldiers(island), part(to_basis(config.get("military.starve")), lack))
        if dead > 0:
            forces.lose(island, dead)
            island.stats["military.starved"] = dead
            island.log.append(LogEntry("military_starved", {"count": dead}))

    @staticmethod
    def _desert(island: IslandState, state: dict[str, Any], config) -> None:
        """士気が低い部隊からは兵が逃げて家計に戻る（MI-14）。"""
        below = to_basis(config.get("military.morale.desert_below"))
        if state["morale"] >= below:
            return
        if (
            not forces.soldiers(island) or forces.population_of(island) <= 0
        ):  # 戻す先のマスがなければ逃げられない
            return
        rate = part(below - state["morale"], to_basis(config.get("military.morale.desert_rate")))
        gone = max(1, part(forces.soldiers(island), rate))
        gone = forces.lose(island, gone)
        forces.return_people(island, gone)
        island.stats["military.deserted"] = gone
        island.log.append(LogEntry("military_deserted", {"count": gone}))

    @staticmethod
    def _order_food(island: IslandState, config) -> None:
        """次のターンに食べる分まで、国の倉庫に食べ物が足りなければ、国として市場で買う注文を出す。"""
        price = island.economy.prices.get(FOOD, 0)
        want = food_need(island, config) * config.get("military.food_stock_turns")
        buy = want - stock.total(island.warehouse, FOOD)
        if price <= 0 or buy <= 0:
            return
        limit = round(price * config.get("military.food_limit"))
        island.economy.ext.setdefault("warehouse_orders", []).append(
            {
                "good": FOOD,
                "side": "buy",
                "quantity": buy,
                "limit": limit,
                "budget": BUDGET_PERSONNEL,
                "quiet": True,
            }
        )


# --- 命令 -------------------------------------------------------------------------------


def check_set_defense(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not island.has_economy:
        raise OrderFailed("reason_no_economy")


def set_defense(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """国防費の年の額と分け方（兵の賃金と食べ物 : 装備の買い入れと編成）と、志願の賃金を決める。"""
    state = forces.state_of(island, ctx.config)
    budget = island.policy.budget
    now = budget.get(BUDGET_PERSONNEL, 0) + budget.get(BUDGET_PROCUREMENT, 0)
    annual = payload.get("annual", now)
    if "annual" in payload or "personnel_share" in payload:
        # 額だけ・分け方だけを替えるときは、決めていないほうを今のままにする
        share = payload.get("personnel_share")
        if share is None:
            share = budget.get(BUDGET_PERSONNEL, 0) * 100 // now if now else 100
        budget[BUDGET_PERSONNEL] = annual * share // 100
        budget[BUDGET_PROCUREMENT] = annual - annual * share // 100
    if "volunteer_wage" in payload:
        state["volunteer_wage"] = to_basis(payload["volunteer_wage"] / 100)
    island.log.append(
        LogEntry(
            "defense_set",
            {
                "personnel": island.policy.budget.get(BUDGET_PERSONNEL, 0),
                "procurement": island.policy.budget.get(BUDGET_PROCUREMENT, 0),
                "wage": state["volunteer_wage"] / 100,
            },
        )
    )


def _unemployed(island: IslandState) -> int:
    """前のターンの失業者の数（働きたい人 × 失業率）。"""
    stats = island.stats
    return int(stats.get("labor", 0) * stats.get("unemployment", 0))


def check_recruit(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not island.has_economy:
        raise OrderFailed("reason_no_economy")


def recruit(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    got = forces.recruit(island, ctx.config, payload["count"], _unemployed(island))
    if got <= 0:
        island.log.append(LogEntry("recruit_none", {"count": payload["count"]}))
        return
    island.log.append(LogEntry("recruited", {"count": got, "wanted": payload["count"]}))


def conscript(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    got = forces.conscript(island, ctx.config, payload["count"])
    if got <= 0:
        island.log.append(LogEntry("recruit_none", {"count": payload["count"]}))
        return
    island.log.append(LogEntry("conscripted", {"count": got, "wanted": payload["count"]}))


def check_discharge(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not forces.soldiers(island):
        raise OrderFailed("reason_no_soldiers")


def discharge(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    gone = forces.discharge(island, payload["count"])
    island.log.append(LogEntry("discharged", {"count": gone}))


def register(registry: Registry) -> None:
    registry.add_stage(Military())
    registry.add_order(OrderHandler("set_defense", execute=set_defense, check=check_set_defense))
    registry.add_order(OrderHandler("recruit", execute=recruit, check=check_recruit))
    registry.add_order(OrderHandler("conscript", execute=conscript, check=check_recruit))
    registry.add_order(OrderHandler("discharge", execute=discharge, check=check_discharge))
