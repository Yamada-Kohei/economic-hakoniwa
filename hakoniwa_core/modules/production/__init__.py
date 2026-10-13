"""生産と電力の段。

稼働率 u = min(人手の充足率, 投入財の充足率, 電力の充足率)。産出量 = 基本の産出量 × u です
（インフラと教育の係数はフェーズ2-3 から。式は output_rate で差し替えられます）。産出物は
施設の在庫に入り、市場の段で売られます。

電力はためておけないので、送電網ごとにその場で板寄せします（hakoniwa_core/market/auction.py）。
施設や住宅のあるマスと送電線のマスが送電網で、上下左右につながったまとまりが1つの送電網です。
売りは発電所（動ける分、指値は希望価格）、買いは施設と家計（指値は払える電気料金まで。優先順位は
施設の優先順位と、家計は power.household_priority）で、基準の値段は島の前のターンの電気料金です。
値幅の上端でも足りなければ、優先順位の低い所から停電します。
公共料金の上限（命令 set_price_cap）があれば、値幅の上端はそこで止まります。
電気代はその場で払い、家計は消費税も払います。
発電所の操業目標（雇う人数と買う燃料）は、送電網の買いたい量で決めます（式 power_target）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import (
    Payer,
    books,
    entity_of,
    freight,
    funds,
    household,
    is_self_supporting,
    payer_of,
)
from hakoniwa_core.economy.prices import input_limit, unit_floor
from hakoniwa_core.economy.stats import inflation
from hakoniwa_core.geo import components, distance, within
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.registry import IslandStage, Registry
from hakoniwa_core.state import Facility, IslandState, LogEntry, MarketRecord, TradeRecord
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import (
    BASIS,
    QUANTITY_SCALE,
    from_milli,
    money_of,
    part,
    qty_for,
    round_money,
    to_basis,
    to_milli,
    unit_price,
)

# 原料を買えなかったわけ（economy["short"]）ごとの施設の status: 売りに出ている量が足りない、
# 指値（採算の合う値段）より高い、取り置けるお金が足りない
SHORT_STATUS = {"supply": "input", "price": "input_price", "funds": "funds"}


@dataclass
class _Run:
    """このターンの1つの施設の動き。"""

    f: Facility
    recipe: Any
    rate: int  # 電力を除いた稼働率
    cause: str  # rate を下げている理由


@dataclass
class _Buyer:
    """電力の買い手（施設か、送電網の中の家計の層）。"""

    name: str
    payer: Payer
    need: int
    run: _Run | None = None
    layer: str | None = None
    tax: int = 0
    reserved: int = 0


def output_rate(island: IslandState, f: Facility, rate: int, ctx: StageContext) -> int:
    """産出の倍率（1000 分の 1）。フェーズ2-1 までは稼働率そのまま。

    フェーズ2-3 でインフラと教育の係数を掛ける。
    """
    return rate


def power_target(demand: int, capacity: int, ctx: StageContext) -> int:
    """発電所の操業目標（1000 分の 1）: 送電網の買いたい量 ÷ 送電網の発電所が全力で出せる量に、
    market.target_step の余裕を足したもの。電力は在庫を持てないので、需要に合わせて人を雇う。
    """
    step = round_money(ctx.config.get("market.target_step") * fac.FULL + 0.5)
    return min(fac.FULL, fac.ratio(demand, capacity) + step)


class Production(IslandStage):
    name = "production"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        defs, config = ctx.defs, ctx.config
        power = config.get("power.good")
        for f in island.facilities:  # このターンに売りに出した量と売れた量（電力とこの後の市場）
            f.economy.pop("sales", None)
        runs = [self._prepare(f, ctx, power) for f in island.facilities if self._operates(f, ctx)]
        served = self._power_market(island, runs, ctx, power)
        unplugged: set[int] | None = None
        produced: dict[str, int] = {}
        for run in runs:
            f, rate, cause = run.f, run.rate, run.cause
            if f.id in served and served[f.id] < rate:
                rate = served[f.id]
                if run.recipe["inputs"].get(power):
                    if unplugged is None:
                        unplugged = self._unplugged(island, ctx, power)
                    cause = "no_grid" if f.id in unplugged else "power"
                else:  # 発電所は使われる分だけ動くのがふつうなので、止まったときだけ理由にする
                    cause = "no_demand" if rate == 0 else ""
            for good, need in sorted(run.recipe["inputs"].items()):
                if good != power:
                    used = stock.take(f.stock, good, fac.scale(need, rate))
                    books.stock_moved(island, f.id, good, -used)
            out_rate = ctx.formula("output_rate")(island, f, rate, ctx)
            for good, qty in sorted(run.recipe["outputs"].items()):
                amount = fac.scale(qty, out_rate)
                produced[good] = produced.get(good, 0) + amount
                if good != power:
                    stock.add(f.stock, good, amount, ctx.turn, defs.goods)
                    books.stock_moved(island, f.id, good, amount)
            self._update_status(island, f, rate, cause)
        for good, amount in sorted(produced.items()):
            island.stats[f"production.{good}"] = from_milli(amount)
        self._spread_pollution(island, ctx)

    @staticmethod
    def _operates(f: Facility, ctx: StageContext) -> bool:
        return f.built and not fac.is_housing(ctx.config, f) and fac.on_map(f)

    @staticmethod
    def _prepare(f: Facility, ctx: StageContext, power: str) -> _Run:
        recipe = fac.recipe_of(ctx.defs, f)
        if fac.is_halted(f, ctx.turn):
            return _Run(f, recipe, 0, "halted")
        rate, cause = fac.labor_ratio(ctx.defs, f), "workers"
        have, short = fac.input_ratio(ctx.defs, f, power)
        if f.workers >= fac.labor_demand(ctx.defs, f, ctx.turn, power):  # 雇いたい分は雇えた
            # 原料のある分しか雇わなかったなら原料、そうでなければ売れ行きに合わせた
            target = f.economy.get("target", fac.FULL)
            cause = "target" if short is None or have >= target else Production._short(f, short)
        if short is not None and have < rate:
            rate, cause = have, Production._short(f, short)
        return _Run(f, recipe, rate, cause if rate < fac.FULL else "")

    @staticmethod
    def _short(f: Facility, good: str) -> str:
        """原料 good が足りない理由（施設の status）。前のターンの市場で買えなかったわけ
        （economy["short"]。hakoniwa_core/modules/market の _record_shortages）で決める。"""
        reason = f.economy.get("short", {}).get(good, "supply")
        return f"{SHORT_STATUS.get(reason, 'input')}:{good}"

    # --- 電力 ----------------------------------------------------------------

    def _power_market(
        self, island: IslandState, runs: list[_Run], ctx: StageContext, power: str
    ) -> dict[int, int]:
        """送電網ごとに電力を板寄せし、電力にかかわる施設の稼働率（施設の番号ごと）を返す。

        住宅のマスには、住民が受け取れた電力の割合を ext["power"] に書く。
        """
        defs, config = ctx.defs, ctx.config
        grid_of = self._grids(island)
        household_use = config.get("power.household_use")
        housing = fac.housing_kind(config)
        economy = island.economy
        has_money = island.has_economy
        reference = economy.prices.get(power, 1)
        width = to_basis(config.get("market.band"))
        cap = island.policy.price_cap.get(power) if has_money else None
        _, high = auction.band(reference, width, cap)

        plants: dict[int, list[tuple[_Run, int]]] = {}
        users: dict[int, list[_Run]] = {}
        for run in runs:
            grid = grid_of[(run.f.x, run.f.y)]
            if run.recipe["outputs"].get(power, 0):
                plants.setdefault(grid, []).append((run, run.recipe["outputs"][power]))
            elif run.recipe["inputs"].get(power, 0):
                users.setdefault(grid, []).append(run)
        homes: dict[int, list[tuple[int, int]]] = {}
        for f in island.facilities:
            if f.kind == housing and fac.on_map(f):
                homes.setdefault(grid_of[(f.x, f.y)], []).append((f.x, f.y))
        persons = {n: h["persons"] for n, h in economy.households.items()}
        population = sum(persons.values())
        rates: dict[int, int] = {}
        totals = {"capacity": 0, "demand": 0, "used": 0}
        volume = value = 0
        for grid in sorted(set(plants) | set(users) | set(homes)):
            buyers: list[_Buyer] = []
            for run in users.get(grid, []):
                need = fac.scale(run.recipe["inputs"][power], run.rate)
                payer = payer_of(defs, config, run.f)
                buyers.append(_Buyer(f"fac:{run.f.id}", payer, need, run=run))
            home_people = sum(island.tile(x, y).population for x, y in homes.get(grid, ()))
            home_need = to_milli(home_people * household_use)
            if home_need and population:
                for layer in sorted(persons):
                    need = home_need * persons[layer] // population
                    payer = Payer(household(layer))
                    tax = island.policy.consumption_tax
                    buyers.append(_Buyer(household(layer), payer, need, layer=layer, tax=tax))
            sellers = plants.get(grid, [])
            sell_orders = []
            for run, out in sellers:
                capacity = fac.scale(out, run.rate)
                ask = run.f.economy.get("ask", {}).get(power, reference)
                if has_money:  # 1単位の変動費より安くは売らない（国庫直轄も）
                    wage = run.f.economy.get("wage", 0)
                    ask = max(ask, unit_floor(defs, config, run.f, power, economy.prices, wage, 0))
                sell_orders.append(Order(capacity, min(ask, high)))  # 上端より高くては売れない
            buy_orders = []
            for b in buyers:
                qty, limit = b.need, None
                if has_money:
                    limit = min(high, self._power_limit(island, ctx, b, reference, power))
                    per_unit = limit + part(limit, b.tax)
                    qty = min(qty, qty_for(funds.available(island, b.payer), per_unit))
                    b.reserved = money_of(limit, qty) + part(money_of(limit, qty), b.tax)
                    funds.commit(island, b.payer, b.reserved)
                priority = (
                    config.get("power.household_priority")
                    if b.layer
                    else fac.priority_of(defs, config, b.run.f)  # type: ignore[union-attr]
                )
                buy_orders.append(Order(qty, limit, priority))
            result = auction.clear(buy_orders, sell_orders, reference, width, cap)
            if has_money:
                self._settle(island, ctx, result, buyers, sellers, power)
            for b, got in zip(buyers, result.bought, strict=True):
                if b.run is not None:
                    rates[b.run.f.id] = fac.scale(b.run.rate, fac.ratio(got, b.need))
            for (run, out), sold in zip(sellers, result.sold, strict=True):
                rates[run.f.id] = fac.ratio(sold, out)
            home_got = sum(got for b, got in zip(buyers, result.bought, strict=True) if b.layer)
            home_rate = fac.ratio(home_got, home_need) if home_need else 0
            for x, y in homes.get(grid, ()):
                tile = island.tile(x, y)
                if tile.population and home_rate:
                    tile.ext["power"] = home_rate
                else:
                    tile.ext.pop("power", None)
            if has_money:
                self._update_asks(island, ctx, sellers, result, power)
                demand = sum(b.need for b in buyers)
                # 燃料が手に入らずに止まっている発電所は数えない（ほかの発電所が多めに動く）
                able = sum(o for run, o in sellers if not fac.short_of_inputs(run.cause))
                target = ctx.formula("power_target")(demand, able, ctx)
                for run, _ in sellers:
                    if "target" in run.f.economy:
                        run.f.economy["target"] = target
            totals["capacity"] += result.supply
            totals["demand"] += sum(b.need for b in buyers)
            totals["used"] += result.volume
            volume += result.volume
            value += money_of(result.price, result.volume)
        for key, amount in totals.items():
            island.stats[f"power.{key}"] = from_milli(amount)
        if has_money:
            price = unit_price(value, volume) if volume else reference
            island.markets.append(
                MarketRecord(power, price, volume, totals["demand"], totals["capacity"])
            )
            if volume:
                economy.prices[power] = price
        return rates

    @staticmethod
    def _power_limit(
        island: IslandState, ctx: StageContext, b: _Buyer, reference: int, power: str
    ) -> int:
        """電力の買い注文の指値（税抜き）。

        施設は、ほかの値段のままで変動費の利益が0になる電気料金まで（国庫直轄はいつも今の電気料金
        までは買う）。家計の層は、使う額（式 consumption）の power.household_share までで
        買える値段。
        品切れが続いても、払えない値段まで電気料金が上がり続けないようにする。
        """
        config = ctx.config
        if b.run is not None:
            f = b.run.f
            rate = freight.facility_rates(island, ctx.defs, config).get(f.id, 0)
            wage = f.economy.get("wage", island.economy.average_wage)
            limit = input_limit(ctx.defs, config, f, power, island.economy.prices, wage, rate)
            return limit if is_self_supporting(f) else max(limit, reference)
        assert b.layer is not None
        budget = ctx.formula("consumption")(island, b.layer, config, inflation(island.economy))
        share = round_money(budget * config.get("power.household_share"))
        return share * QUANTITY_SCALE // max(1, b.need) * BASIS // (BASIS + b.tax)

    def _settle(
        self,
        island: IslandState,
        ctx: StageContext,
        result: auction.Result,
        buyers: list[_Buyer],
        sellers: list[tuple[_Run, int]],
        power: str,
    ) -> None:
        """電気代を払う（買い手 → 発電所。家計は消費税も）。"""
        config = ctx.config
        paid_by_layer = island.scratch.setdefault("household_power", {})
        bought = island.scratch.setdefault("household_bought", {})
        for b_index, s_index, qty in auction.match(result.bought, result.sold):
            buyer = buyers[b_index]
            plant = sellers[s_index][0].f
            amount = money_of(result.price, qty)
            tax = part(amount, buyer.tax)
            seller = entity_of(config, plant)
            funds.pay(island, buyer.payer, seller, amount, "electricity", committed=True)
            funds.pay(island, buyer.payer, ledger.GOV, tax, "tax_consumption", committed=True)
            buyer.reserved -= amount + tax
            books.add(island, plant.id, books.SALES, amount)
            if buyer.run is not None:
                books.add(island, buyer.run.f.id, books.ELECTRICITY, amount)
            else:
                assert buyer.layer is not None
                paid_by_layer[buyer.layer] = paid_by_layer.get(buyer.layer, 0) + amount + tax
                bought[power] = bought.get(power, 0) + qty
                books.add_sector(island, books.HOUSEHOLDS, "electricity", amount)
                books.add_sector(island, books.HOUSEHOLDS, "tax", tax)
            island.trades.append(TradeRecord(power, buyer.name, f"fac:{plant.id}", qty, amount))
        for buyer in buyers:
            funds.release(island, buyer.payer, buyer.reserved)
            buyer.reserved = 0

    @staticmethod
    def _update_asks(
        island: IslandState,
        ctx: StageContext,
        sellers: list[tuple[_Run, int]],
        result: auction.Result,
        power: str,
    ) -> None:
        """発電所の希望価格を見直す（売りに出した量と売れた量で）。"""
        update = ctx.formula("price_update")
        shortage = result.unfilled_demand / result.demand if result.demand else 0.0
        for (run, out), sold in zip(sellers, result.sold, strict=True):
            offered = fac.scale(out, run.rate)
            run.f.economy.setdefault("sales", {})[power] = [offered, sold, 0]
            asks = run.f.economy.setdefault("ask", {})
            ask = asks.get(power, result.price)
            asks[power] = max(
                result.low, min(result.high, update(ask, offered, sold, shortage, ctx))
            )

    def _unplugged(self, island: IslandState, ctx: StageContext, power: str) -> set[int]:
        """電力を使う施設のうち、発電所（止まっているものも数える）のない送電網にある施設の番号。

        状態を「電力不足」ではなく「送電網につながっていない」にして、送電線を引けば動くと分かるように
        する。
        """
        grid_of = self._grids(island)
        powered = set()
        users = []
        for f in island.facilities:
            if not f.built or not fac.on_map(f):
                continue
            recipe = fac.recipe_of(ctx.defs, f)
            if recipe["outputs"].get(power):
                powered.add(grid_of[(f.x, f.y)])
            elif recipe["inputs"].get(power):
                users.append(f)
        return {f.id for f in users if grid_of[(f.x, f.y)] not in powered}

    @staticmethod
    def _grids(island: IslandState) -> dict[tuple[int, int], int]:
        """送電網: 施設や住宅のあるマスと送電線のマスの、上下左右につながったまとまり。"""
        nodes = {(f.x, f.y) for f in island.facilities if fac.on_map(f)}
        nodes |= {(t.x, t.y) for t in island.tiles if t.power_line}
        grid_of = {}
        for i, group in enumerate(components(island, nodes)):
            for pos in group:
                grid_of[pos] = i
        return grid_of

    # --- 状態とログ ------------------------------------------------------------

    @staticmethod
    def _update_status(island: IslandState, f: Facility, rate: int, cause: str) -> None:
        before_rate, before_status = f.operating_rate, f.status
        f.operating_rate = rate
        f.status = cause if rate < fac.FULL else ""
        where = {"facility": f.kind, "x": f.x, "y": f.y}
        if rate == 0 and (before_rate > 0 or before_status == fac.STATUS_STARTING):
            kind, _, good = cause.partition(":")
            args = {**where, "good": good} if good else where
            island.log.append(LogEntry(f"facility_stopped_{kind}", args))
        elif rate > 0 and before_rate == 0:
            started = before_status == fac.STATUS_STARTING
            kind = "facility_started" if started else "facility_restarted"
            island.log.append(LogEntry(kind, where))

    @staticmethod
    def _spread_pollution(island: IslandState, ctx: StageContext) -> None:
        """汚染: 施設のマスで一番濃く、radius マス先に向かって薄くなる。"""
        totals: dict[tuple[int, int], int] = {}
        for f in island.facilities:
            pollution = ctx.defs.facilities[f.kind].get("pollution")
            if not pollution or not f.built or f.operating_rate <= 0 or not fac.on_map(f):
                continue
            amount = fac.scale(to_milli(pollution["amount"]), f.operating_rate)
            radius = pollution["radius"]
            for pos in within(island, (f.x, f.y), radius):
                share = amount * (radius + 1 - distance(pos, (f.x, f.y))) // (radius + 1)
                totals[pos] = totals.get(pos, 0) + share
        for tile in island.tiles:
            tile.pollution = round(from_milli(totals.get((tile.x, tile.y), 0)))


def register(registry: Registry) -> None:
    registry.add_stage(Production())
    registry.add_formula("output_rate", output_rate)
    registry.add_formula("power_target", power_target)
