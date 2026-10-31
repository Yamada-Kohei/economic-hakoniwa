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
from hakoniwa_core.military import forces, occupation
from hakoniwa_core.military.forces import BUDGET_PERSONNEL, BUDGET_PROCUREMENT
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, money_of, part, to_basis, to_milli

FOOD = "food"
AMMUNITION = "ammunition"


def wage_bill(island: IslandState) -> int:
    """このターンの兵の賃金の合計（志願と徴兵で倍率が違う）。"""
    state = forces.state_of(island)
    average = island.economy.average_wage
    return part(average * state["volunteers"], state.get("volunteer_wage", BASIS)) + part(
        average * state["conscripts"], state.get("conscript_wage", BASIS)
    )


def food_need(island: IslandState, config) -> int:
    """国の倉庫から食べる分の、1 ターンの食べ物（1000 分の 1 トン）。兵（外国の島にいる部隊を除く。
    その分は部隊が自分の補給で食べる。MI-64）と、捕らえている捕虜の分。"""
    per_thousand = to_milli(config.get("military.food_per_thousand"))
    state = island.ext.get(forces.KEY, {})
    home = max(0, forces.soldiers(island) - state.get("abroad", 0))
    prisoners = sum(n for _, _, n in state.get("prisoners", []))
    return (home + prisoners) * per_thousand // 1000


def starve_prisoners(island: IslandState, config, lack: int) -> int:
    """食べ物の足りなかった割合 lack（1 万分の 1）に応じて、捕虜が病気と飢えで亡くなる（兵と同じ
    military.starve。W-113）。捕らえた組ごとに人数に比例して減らし、亡くなった数を返す。"""
    batches = island.ext.get(forces.KEY, {}).get("prisoners", [])
    rate = part(to_basis(config.get("military.starve")), lack)
    dead = 0
    for batch in batches:
        n = part(batch[2], rate)
        batch[2] -= n
        dead += n
    if batches:
        island.ext[forces.KEY]["prisoners"] = [b for b in batches if b[2] > 0]
    if dead > 0:
        island.log.append(LogEntry("prisoners_starved", {"count": dead}))
    return dead


def planned(island: IslandState, config) -> dict[str, int]:
    """国庫の取り置きの見積もりの下限。前のターンの支出が 0 でも、兵の賃金と食べ物の分は取り置く
    （開戦や徴兵の直後に取り置きが付かず、賃金が遅れるのを避ける。MI-13）。"""
    if not forces.soldiers(island) and not occupation.held(island):
        return {}
    price = island.economy.prices.get(FOOD, 0)
    food = food_need(island, config) * price // QUANTITY_SCALE
    return {BUDGET_PERSONNEL: wage_bill(island) + food * config.get("military.food_stock_turns")}


class Military(IslandStage):
    name = "military"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        config = ctx.config
        island.ext.get(forces.KEY, {}).pop("purchases", None)  # 前のターンの買い入れの結果
        if not forces.soldiers(island):  # 兵がいなくても、編成中の部隊の装備は買い集める
            wanted = island.ext.get(forces.KEY, {}).get("wanted", {})
            for good, qty in sorted(wanted.items()):
                self._order(island, config, good, qty, BUDGET_PROCUREMENT)
            if occupation.held(island):  # 兵がいなくても捕虜には食べさせる（W-113）
                self._feed_prisoners(island, config)
            return
        state = forces.state_of(island, config)
        stats = island.stats
        unpaid = self._pay(island, state, self._food_hold(island, config))
        self._morale(island, state, config, unpaid > 0)
        whole = forces.soldiers(island)
        new = min(state["new"], whole)  # 入ったターンは家で食べる
        state["new"] = 0
        # 入ったばかりの兵の分だけ引く（捕虜はいつも国の倉庫から食べる。W-113）
        per_thousand = to_milli(config.get("military.food_per_thousand"))
        need = max(0, food_need(island, config) - new * per_thousand // 1000)
        eaten = stock.take(island.warehouse, FOOD, need)
        state["hungry"] = eaten < need
        if state["hungry"]:
            self._hunger(island, state, config, need, eaten)
            if occupation.held(island):  # 捕虜も同じだけ足りない
                starve_prisoners(island, config, BASIS - eaten * BASIS // need)
        ammo = self._train(island, state, config)
        self._desert(island, state, config)
        food = food_need(island, config) * config.get("military.food_stock_turns")
        self._order(island, config, FOOD, food, BUDGET_PERSONNEL)
        self._order(island, config, AMMUNITION, ammo * 2, BUDGET_PROCUREMENT)
        wanted = dict(state.get("wanted", {}))
        for good, qty in state.get("reserve", {}).items():  # 備蓄の目標（外国へ送る補給のもと）
            wanted[good] = max(wanted.get(good, 0), qty)
        for good, qty in sorted(wanted.items()):  # 部隊に集める装備と燃料、備蓄
            self._order(island, config, good, qty, BUDGET_PROCUREMENT)
        stats["military.soldiers"] = forces.soldiers(island)
        stats["military.morale"] = state["morale"] / BASIS
        stats["military.training"] = state["training"] / BASIS
        stats["military.wages_unpaid"] = unpaid

    def _feed_prisoners(self, island: IslandState, config) -> None:
        """兵のいない国が捕らえている捕虜に、国の倉庫の食べ物を食べさせ、次の分を国として買う
        （ジュネーブ条約の捕虜の給養。お金は兵の食べ物と同じ国防費の賃金と食べ物の枠）。足りなければ
        捕虜が病気と飢えで亡くなる。"""
        need = food_need(island, config)
        eaten = stock.take(island.warehouse, FOOD, need)
        if eaten < need:
            starve_prisoners(island, config, BASIS - eaten * BASIS // need)
        food = food_need(island, config) * config.get("military.food_stock_turns")
        self._order(island, config, FOOD, food, BUDGET_PERSONNEL)

    @staticmethod
    def _food_hold(island: IslandState, config) -> int:
        """このターンに兵の食べ物を買い足すのに要るお金（国防費の賃金と食べ物の月割りから、賃金より
        先に取り分けておく額。W-184: 補給は賃金より先に確保する。買い注文は指値の額を取り置くので、
        指値で数える）。国の倉庫の食べ物がこのターンに食べる分を引いても目標に届くなら 0。"""
        price = island.economy.prices.get(FOOD, 0)
        if price <= 0:
            return 0
        need = food_need(island, config)
        target = need * config.get("military.food_stock_turns")
        left = max(0, stock.total(island.warehouse, FOOD) - need)
        buy = target - left
        if buy <= 0:
            return 0
        return money_of(round(price * config.get("military.buy_limit")), buy)

    @staticmethod
    def _pay(island: IslandState, state: dict[str, Any], hold: int = 0) -> int:
        """兵の賃金を払う。食べ物を買う分（hold）は賃金より先に残し、払えない賃金は未払い賃金に
        残す。払えなかった額を返す。"""
        due = wage_bill(island)
        payer = Payer(ledger.GOV, BUDGET_PERSONNEL)
        pay = min(due, max(0, funds.available(island, payer) - hold))
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
        if not forces.soldiers(island) or not forces.can_return(island):  # 戻る先がない
            return
        rate = part(below - state["morale"], to_basis(config.get("military.morale.desert_rate")))
        gone = max(1, part(forces.soldiers(island), rate))
        gone = forces.lose(island, gone)
        forces.return_people(island, gone)
        island.stats["military.deserted"] = gone
        island.log.append(LogEntry("military_deserted", {"count": gone}))

    @staticmethod
    def _train(island: IslandState, state: dict[str, Any], config) -> int:
        """訓練で弾薬を使い（国の倉庫から。足りなければ使わない）、練度を基礎訓練まで上げる。
        1 ターンに使う弾薬の量（1000 分の 1 トン）を返す。"""
        use = forces.soldiers(island) * to_milli(config.get("military.ammo_per_thousand")) // 1000
        short = stock.total(island.warehouse, AMMUNITION) < use
        if not short:
            stock.take(island.warehouse, AMMUNITION, use)
        state["ammo_short"] = (
            short  # 基礎訓練までは弾薬なしで進む。その先の訓練は戦闘のある 4-3 で使う
        )
        rate = to_basis(config.get("military.training.rate"))
        cap = to_basis(config.get("military.training.basic"))
        state["training"] = max(state["training"], min(cap, state["training"] + rate))
        return use

    @staticmethod
    def _order(island: IslandState, config, good: str, want: int, budget: str) -> None:
        """good が国の倉庫に want まで足りなければ、国として市場で買う注文を出す。"""
        price = island.economy.prices.get(good, 0)
        buy = want - stock.total(island.warehouse, good)
        for item in island.economy.ext.get("warehouse_orders", []):  # 同じ財の注文を重ねない
            if item.get("military") and item["good"] == good:
                buy -= item["quantity"]
        if price <= 0 or buy <= 0:
            return
        limit = round(price * config.get("military.buy_limit"))
        island.economy.ext.setdefault("warehouse_orders", []).append(
            {
                "good": good,
                "side": "buy",
                "quantity": buy,
                "limit": limit,
                "budget": budget,
                "military": True,  # 結果は軍の状態（purchases）とログに出す（W-184）
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
    if "reserve" in payload:  # 備蓄の目標（トン）。外国へ送る補給や戦う弾薬のもと（4-3）
        state["reserve"] = {g: to_milli(q) for g, q in payload["reserve"].items()}
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


def _free(island: IslandState) -> int:
    """部隊に入っていない兵の数（除隊できるのはこの分だけ）。"""
    return forces.soldiers(island) - island.ext.get(forces.KEY, {}).get("assigned", 0)


def check_discharge(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not forces.soldiers(island):
        raise OrderFailed("reason_no_soldiers")
    if _free(island) <= 0:
        raise OrderFailed("reason_soldiers_in_units")


def discharge(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    gone = forces.discharge(island, min(payload["count"], _free(island)))
    island.log.append(LogEntry("discharged", {"count": gone}))


def register(registry: Registry) -> None:
    registry.add_stage(Military())
    registry.add_order(OrderHandler("set_defense", execute=set_defense, check=check_set_defense))
    registry.add_order(OrderHandler("recruit", execute=recruit, check=check_recruit))
    registry.add_order(OrderHandler("conscript", execute=conscript, check=check_recruit))
    registry.add_order(OrderHandler("discharge", execute=discharge, check=check_discharge))
