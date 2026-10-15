"""記録の段: このターンの統計を残し、経済の履歴を進め、最後に帳簿を確かめる。

統計の名前は「人口 population」「在庫 stock.<財>」「値段 price.<財>」のように付け、サーバーが
統計の表に保存します。生産量と電力は生産の段が書きます。お金の統計（GDP・物価・財政・家計の層）は
このターンの仕訳と、段が残したお金と物の流れ（hakoniwa_core/economy/books.py）から作ります。

最後に帳簿を確かめ（hakoniwa_core/ledger.py の check）、崩れていればそのターンを失敗にします
（例外を出し、サーバーは何も保存しない）。
"""

from __future__ import annotations

import math

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    GENERAL,
    OWNERS,
    banks,
    books,
    funds,
    household,
    is_transport,
    trucks,
)
from hakoniwa_core.economy.debt import annual_gdp, total_debt
from hakoniwa_core.economy.households import close_income
from hakoniwa_core.economy.stats import inflation, update_cpi
from hakoniwa_core.modules.labor import engineer_unemployment, has_own_wage
from hakoniwa_core.modules.population import subsistence_persons
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.trade import balance
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, from_milli, to_basis

SHORTAGE = "shortage"  # economy.ext: 品不足の続いている財 → ターン数（フェーズ3-2）


class LedgerCheckFailed(Exception):
    """帳簿の確かめが通らなかった（そのターンは保存しない）。"""


class Records(IslandStage):
    name = "records"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        self._people(island, ctx)
        if island.has_economy:
            self._economy(island, ctx)
            self._check(island, ctx)

    # --- 人と物 -------------------------------------------------------------------

    @staticmethod
    def _people(island: IslandState, ctx: StageContext) -> None:
        defs, stats = ctx.defs, island.stats
        population = labor = employed = farming = 0
        share = fac.labor_share(defs, island)
        for tile in island.tiles:
            if tile.population <= 0:
                continue
            population += tile.population
            force = fac.labor_force(defs, island, tile)
            labor += force
            employed += tile.ext.get("employed", 0)
            farming += min(
                force - tile.ext.get("employed", 0),
                int(subsistence_persons(defs, island, tile) * share),
            )
        stats["population"] = population
        stats["labor"] = labor
        stats["employed"] = employed
        stats["subsistence"] = farming
        stats["unemployment"] = (labor - employed - farming) / labor if labor else 0.0
        layers = island.scratch.get("layer_happiness", {})
        persons = {n: h["persons"] for n, h in island.economy.households.items()}
        weight = sum(persons.get(n, 0) for n in layers)
        if weight:
            stats["happiness"] = sum(v * persons.get(n, 0) for n, v in layers.items()) / weight
        else:
            mood = sum(t.ext.get("happiness", 0) * t.population for t in island.tiles)
            stats["happiness"] = mood / population if population else 0.0
        food = island.scratch.get("food")
        if food is not None:
            stats["food.ratio"] = food["ratio"] / fac.FULL
            stats["food.need"] = from_milli(food["need"])
            stats["food.supplied"] = from_milli(food["supplied"])
        demand = stats.get("power.demand", 0)
        if demand:  # 電力の不足率（買いたかった量のうち届かなかった割合）
            stats["power.shortage"] = max(0.0, 1 - stats.get("power.used", 0) / demand)
        stats.update(_holdings(island))
        # 次のターンの初めに使える行政力（官庁の人手はこのターンに雇えた人数で決まる）
        stats["admin_power"] = ctx.formula("admin_power")(island, ctx)

    # --- お金 ---------------------------------------------------------------------

    def _economy(self, island: IslandState, ctx: StageContext) -> None:
        defs, config, stats = ctx.defs, ctx.config, island.stats
        economy = island.economy
        prices = economy.prices
        bought = island.scratch.get("household_bought", {})
        cpi = update_cpi(economy, defs, config, prices, bought)
        stats["cpi"] = cpi
        stats["inflation"] = inflation(economy)
        for good, price in sorted(prices.items()):
            stats[f"price.{good}"] = price
        for record in island.markets:
            stats[f"volume.{record.good}"] = from_milli(record.volume)
        _shortages(island, config)
        # GDP（生産の側と支出の側。同じ流れから作るので一致する）
        inventory = island.scratch.get("inventory_changes")
        if inventory is None:
            inventory = books.inventory_changes(island, prices)
        production, spending = books.gdp(island, inventory)
        stats["gdp"] = production
        stats["gdp.spending"] = spending
        floor = economy.average_wage or config.get("labor.initial_wage")  # 1人の1ターンの賃金
        stats["gdp.gap"] = books.gdp_gap(production, spending, floor)
        keep = config.get("records.history_turns")
        economy.gdp_history = [*economy.gdp_history, production][-keep:]
        # 賃金
        wages = [
            f.economy["wage"] for f in island.facilities if has_own_wage(defs, f) and f.workers
        ]
        stats["wage.average"] = economy.average_wage
        if wages:
            stats["wage.min"] = min(wages)
            stats["wage.max"] = max(wages)
        stats["wage.real"] = economy.average_wage * 100 / cpi if cpi else 0.0
        stats.update(_engineers(island, defs, config))
        # お金の量と財政
        money = ledger.aggregates(island.accounts)
        stats["money.deposits"] = money["deposits"]
        stats["money.central_bank"] = money["cb_money"]
        stats["money.nonresident"] = money["nonresident"]  # 海外が持つ自国通貨（フェーズ3-1）
        trade = books.sector(island, books.TRADE)  # 貿易収支（商社・契約・海外の商人）
        stats["trade.exports"] = trade.get("exports", 0)
        stats["trade.imports"] = trade.get("imports", 0)
        stats["trade.balance"] = trade.get("exports", 0) - trade.get("imports", 0)
        balance.write_stats(island)  # 経常収支と金融収支（フェーズ3-3）
        stats.update(_finances(island))
        stats.update(_banking(island, ctx))
        economy.finance["unemployment"] = stats["unemployment"]
        year = annual_gdp(economy)
        stats["debt_ratio"] = stats["debt"] / year if year else 0.0
        for kind, amount in sorted(self._fiscal(island).items()):
            stats[f"fiscal.{kind}"] = amount
        # 予算の項目ごとにこのターンに使った額（公共事業費・教育費など）
        for item, amount in sorted(funds.spent(island).items()):
            stats[f"spent.{item}"] = amount
        # 家計の層
        income = island.scratch.get("income", {})
        per_capita = {}
        for name, h in sorted(economy.households.items()):
            persons = h["persons"]
            stats[f"layer.{name}.persons"] = persons
            stats[f"layer.{name}.income"] = income.get(name, 0)
            stats[f"layer.{name}.deposits"] = ledger.money(island, household(name))
            mood = island.scratch.get("layer_happiness", {}).get(name)
            if mood is not None:
                stats[f"layer.{name}.happiness"] = mood
            if persons:
                per_capita[name] = income.get(name, 0) / persons
        if per_capita.get(GENERAL) and OWNERS in per_capita:
            stats["inequality"] = per_capita[OWNERS] / per_capita[GENERAL]
        # 施設の利益
        stats["profit"] = sum(b.items.get(books.PROFIT, 0) for b in island.books)
        stats["freight.surcharge"] = economy.freight.get("surcharge", BASIS) / BASIS
        for f in island.facilities:  # 運送業のトラック（フェーズ3-2）
            if is_transport(config, f) and trucks.owned(f):
                stats["freight.trucks"] = trucks.owned(f) / QUANTITY_SCALE
                stats["freight.trucks_working"] = f.economy.get(trucks.WORKING, 0) / QUANTITY_SCALE
                stats["freight.drivers_replaced"] = f.economy.get("drivers_replaced", 0)
        # 次のターンのための履歴
        close_income(island, config.get("household.income_turns"))
        economy.spending = funds.spent(island)
        paid = economy.freight.pop("paid", 0)
        surcharge = economy.freight.get("surcharge", BASIS) or BASIS
        economy.freight["revenue"] = paid * BASIS // surcharge

    @staticmethod
    def _fiscal(island: IslandState) -> dict[str, int]:
        """国庫の出入り（仕訳の種類ごと）。in.<種類> が収入、out.<種類> が支出。"""
        out: dict[str, int] = {}
        for e in island.journal:
            if e.debit == ledger.TREASURY_ACCOUNT:
                key = f"in.{e.kind}"
            elif e.credit == ledger.TREASURY_ACCOUNT:
                key = f"out.{e.kind}"
            else:
                continue
            out[key] = out.get(key, 0) + e.amount
        return out

    # --- 帳簿の確かめ ----------------------------------------------------------------

    @staticmethod
    def _check(island: IslandState, ctx: StageContext) -> None:
        opening = island.scratch.get("opening_accounts")
        if opening is None:
            return
        problems = ledger.check(island, opening, ctx.defs.journal_kinds())
        if problems:
            shown = " / ".join(problems[:5])
            more = f"（ほかに {len(problems) - 5} 件）" if len(problems) > 5 else ""
            raise LedgerCheckFailed(f"島 {island.id} の帳簿が合いません: {shown}{more}")


def opening_stats(island: IslandState, defs: Defs) -> dict[str, float]:
    """建国したときの統計。最初の更新の前から、島の画面に人口・国の倉庫・国庫を出すためのもの。

    就業者・幸福度・値段などは最初の更新で決まるので入れない。
    """
    stats: dict[str, float] = {
        "population": sum(t.population for t in island.tiles if t.population > 0),
        "labor": sum(fac.labor_force(defs, island, t) for t in island.tiles if t.population > 0),
    }
    stats.update(_holdings(island))
    if island.has_economy:
        stats.update(_finances(island))
    return stats


def _holdings(island: IslandState) -> dict[str, float]:
    """国の倉庫の在庫と、施設の数。"""
    stats: dict[str, float] = {
        f"stock.{good}": from_milli(qty) for good, qty in stock.totals(island.warehouse).items()
    }
    stats["facilities"] = sum(1 for f in island.facilities if f.built)
    stats["construction"] = sum(1 for f in island.facilities if not f.built)
    return stats


def _finances(island: IslandState) -> dict[str, float]:
    """国庫・国債の残高・政策金利。"""
    return {
        "treasury": ledger.money(island, ledger.GOV),
        "debt": total_debt(island),
        "policy_rate": island.policy.policy_rate * 100 / BASIS,
    }


def _engineers(island: IslandState, defs: Defs, config) -> dict[str, float]:
    """技術者の数・働いている数（一般の仕事に就いた分を含む）・技術者の席に就いた数・失業率・
    技術者の賃金の平均（席に就いた人数で重み付け）。フェーズ2-3。"""
    if fac.ENGINEER not in defs.workers:
        return {}
    pool = island.economy.workers.get(fac.ENGINEER, 0)
    seated = paid = 0
    for f in island.facilities:
        hired = f.economy.get("engineers", 0)
        if hired and has_own_wage(defs, f):
            seated += hired
            paid += hired * fac.engineer_wage_of(config, f, f.economy["wage"])
    stats: dict[str, float] = {
        "engineers": pool,
        "engineers.employed": island.scratch.get("engineers_employed", seated),
        "engineers.seated": seated,
        "engineers.unemployment": engineer_unemployment(island),
    }
    if seated:
        stats["engineers.wage"] = paid / seated
    return stats


def _banking(island: IslandState, ctx: StageContext) -> dict[str, float]:
    """銀行ごとの預金・融資・自己資本比率と、融資の残高・遅れ・預金金利（フェーズ2-2）。"""
    config, turn = ctx.config, ctx.turn
    stats: dict[str, float] = {"deposit_rate": island.policy.deposit_rate * 100 / BASIS}
    for bank in banks.bank_list(island):
        number = banks.bank_number(bank)
        stats[f"bank.{number}.deposits"] = banks.deposits_of(island, bank)
        stats[f"bank.{number}.loans"] = banks.loans_of(island, bank)
        stats[f"bank.{number}.net_worth"] = banks.net_worth(island, config, bank, turn)
        ratio = banks.capital_ratio(island, config, bank, turn)
        if math.isfinite(ratio):
            stats[f"bank.{number}.capital_ratio"] = ratio
    by_purpose: dict[str, int] = {}
    late = 0
    for loan in island.loans:
        left = banks.outstanding(island, loan)
        by_purpose[loan.purpose] = by_purpose.get(loan.purpose, 0) + left
        late += left if loan.arrears else 0
    stats["loans"] = sum(by_purpose.values())
    for purpose, amount in sorted(by_purpose.items()):
        stats[f"loans.{purpose}"] = amount
    stats["loans.arrears"] = late
    written = sum(b.items.get(books.WRITE_OFF, 0) for b in island.books)
    stats["loans.write_off"] = written
    return stats


def register(registry: Registry) -> None:
    registry.add_stage(Records())


def _shortages(island: IslandState, config) -> None:
    """市場で買いたい量の records.shortage.share 以上を買えなかった財を数え、records.shortage.turns
    ターン続いたらターンログで知らせる（続いている間は1度だけ。産業の鎖の画面。フェーズ3-2）。"""
    settings = config.get("records.shortage")
    share = to_basis(settings["share"])
    before = island.economy.ext.get(SHORTAGE, {})
    now = {}
    for record in sorted(island.markets, key=lambda r: r.good):
        unmet = record.demand - record.volume
        if record.demand <= 0 or unmet * BASIS < record.demand * share:
            continue
        turns = before.get(record.good, 0) + 1
        now[record.good] = turns
        if turns == settings["turns"]:
            got = record.volume * 100 // record.demand
            args = {"good": record.good, "turns": turns, "share": got}
            island.log.append(LogEntry("shortage_continued", args))
    if now:
        island.economy.ext[SHORTAGE] = now
    else:
        island.economy.ext.pop(SHORTAGE, None)
