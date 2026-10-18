"""生産と電力の段。

稼働率 u = min(人手の充足率, 投入財の充足率, 電力の充足率, 技術者で決まる上限)。
産出量 = 基本の産出量 × u × 生産性です（生産性はインフラ・教育・汚染の係数。フェーズ2-3 から。
economy/productivity.py。式は output_rate で差し替えられます）。技術者で決まる上限は
facilities.engineer_cap です。産出物は施設の在庫に入り、市場の段で売られます。

作り方を替えた次のターン（フェーズ3-2。ext["recipe_changed"]）は、稼働率が半分になります。作り方は
国営なら命令「作り方の変更」（change_recipe）で、民営は 12 ターンごとの見直し（modules/firms）で
決め、市場の段で替えます（modules/market の _switch_recipes）。

産業のある施設の産出の倍率（稼働率 × 生産性）は、島×産業の経験の累積に足します（フェーズ3-3。
economy/experience.py）。年の終わり（12 ターンごと）に、その年にあまり動かさなかった産業の累積を
減らし、経験の目立つ産業が薄れたらログで知らせます。

寒冷の島の家計は、電力を climate.yaml の household_power 倍使います。マスの汚染で肥沃度が下がる
のもこの段です（hakoniwa_core/nature.py）。

電力はためておけないので、送電網ごとにその場で板寄せします（hakoniwa_core/market/auction.py）。
施設や住宅のあるマスと、送電網の整備度が定義の grid（既定 30%）以上のマスが送電網で、上下左右に
つながったまとまりが1つの送電網です。
売りは発電所（動ける分、指値は希望価格）、買いは施設と家計（指値は払える電気料金まで。優先順位は
施設の優先順位と、家計は power.household_priority）で、基準の値段は島の前のターンの電気料金です。
値幅の上端でも足りなければ、優先順位の低い所から停電します。
公共料金の上限（命令 set_price_cap）があれば、値幅の上端はそこで止まります。計画経済の島
（フェーズ3-4）は公定価格で値幅 0 にし、発電所は費用に届かなくても売り、足りなければ割当の順位
（重点産業 → ほかの国営施設 → 家計の配給）で分けます（hakoniwa_core/economy/plan.py）。
電気代はその場で払い、家計は消費税も払います。
発電所の操業目標（雇う人数と買う燃料）は、送電網の買いたい量で決めます（式 power_target）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, ledger, nature, stock
from hakoniwa_core.dates import is_year_end
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    Payer,
    banks,
    books,
    entity_of,
    experience,
    freight,
    funds,
    household,
    is_bank,
    is_public_service,
    is_self_supporting,
    is_stopped,
    payer_of,
    productivity,
)
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.economy.prices import input_limit, unit_floor
from hakoniwa_core.economy.stats import inflation
from hakoniwa_core.geo import components, distance, within
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import (
    OWNER_STATE,
    Facility,
    IslandState,
    LogEntry,
    MarketRecord,
    TradeRecord,
)
from hakoniwa_core.trade import staff
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
STATUS_CARGO = "cargo"  # 港・商社が扱う荷に合わせて少なく雇っている（フェーズ3-1）
STATUS_BUSINESS = "business"  # 小さい銀行が、預金と融資の大きさに合わせて少なく雇っている
PUBLIC_PRICE = "public_price"  # 学校と大学の教材が高くて買えない


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
    """産出の倍率（1000 分の 1）: 稼働率 × 生産性（インフラ・教育・汚染の係数。
    この段の初めに economy/productivity.py で計算し直したもの）。"""
    return fac.scale(rate, fac.productivity_of(f))


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
            f.economy.pop("late_sales", None)
            fac.step_fields(defs, f, ctx.turn)  # 畑の作付けを進める（回3）
            productivity.update(island, defs, config, f)
        runs = [
            self._prepare(island, f, ctx, power)
            for f in island.facilities
            if self._operates(f, ctx)
        ]
        unsold: dict[int, str] = {}  # 買い手がいたのに売れなかった発電所の番号 → 理由
        served = self._power_market(island, runs, ctx, power, unsold)
        unplugged: set[int] | None = None
        produced: dict[str, int] = {}
        # 施設が使った投入財（電力を除く。産業の鎖の画面。フェーズ3-2）
        used_up: dict[str, int] = {}
        for run in runs:
            f, rate, cause = run.f, run.rate, run.cause
            if f.id in served and served[f.id] < rate:
                rate = served[f.id]
                if run.recipe["inputs"].get(power):
                    if unplugged is None:
                        unplugged = self._unplugged(island, ctx, power)
                    cause = "no_grid" if f.id in unplugged else "power"
                elif not fac.short_of_inputs(cause):
                    # 発電所は使われる分だけ動くのがふつうなので、止まったときだけ理由にする。
                    # 燃料が足りずに動ける分が少なかったなら、燃料の不足のまま（試し遊びで、石炭の
                    # 切れた火力発電所が「電力需要なし」と出ていた）。電力の買い手がいたのに売れな
                    # かったのは、電気料金が発電所の費用に届かなかったため
                    cause = "" if rate > 0 else unsold.get(f.id, "no_demand")
            public = is_public_service(defs, f)  # 学校の教材は、買ったときに政府の消費に数える
            for good, need in sorted(run.recipe["inputs"].items()):
                if good != power:
                    used = stock.take(f.stock, good, fac.scale(need, rate))
                    used_up[good] = used_up.get(good, 0) + used
                    if not public:
                        books.stock_moved(island, f.id, good, -used)
            out_rate = ctx.formula("output_rate")(island, f, rate, ctx)
            industry = run.recipe.get("industry")
            if industry:  # 計画経済の重点産業は経験の伸びが大きい（フェーズ3-4）
                factor = planning.experience_factor(island, defs, industry)
                experience.add(island, industry, round(out_rate * factor))
            for good, qty in sorted(run.recipe["outputs"].items()):
                amount = fac.scale(qty, out_rate)
                produced[good] = produced.get(good, 0) + amount
                if good != power:
                    stock.add(f.stock, good, amount, ctx.turn, defs.goods)
                    books.stock_moved(island, f.id, good, amount)
            self._update_status(island, f, rate, cause)
        for good, amount in sorted(produced.items()):
            island.stats[f"production.{good}"] = from_milli(amount)
        for good, amount in sorted(used_up.items()):
            if amount > 0:
                island.stats[f"use.{good}"] = from_milli(amount)
        self._spread_pollution(island, ctx)
        nature.wear_fertility(island, config)
        self._experience(island, ctx)

    @staticmethod
    def _experience(island: IslandState, ctx: StageContext) -> None:
        """経験の統計（産業ごとの累積。標準の施設が全力で動いたターン数）と、年の終わりの見直し。"""
        if is_year_end(ctx.turn):
            notable = ctx.config.get("experience.base_turns") * experience.FULL
            before = {k: v[0] for k, v in experience.table(island).items()}
            for industry in experience.close_year(island, ctx.config):
                if before.get(industry, 0) >= notable:
                    island.log.append(LogEntry("experience_faded", {"industry": industry}))
        for industry, (amount, _) in sorted(experience.table(island).items()):
            island.stats[f"experience.{industry}"] = from_milli(amount)

    @staticmethod
    def _operates(f: Facility, ctx: StageContext) -> bool:
        return f.built and not fac.is_housing(ctx.config, f) and fac.on_map(f)

    @staticmethod
    def _prepare(island: IslandState, f: Facility, ctx: StageContext, power: str) -> _Run:
        recipe = fac.recipe_of(ctx.defs, f)
        if fac.is_halted(f, ctx.turn):  # 災害か暴動（フェーズ3-4）で止まっている
            return _Run(f, recipe, 0, "riot" if f.ext.get("halt_reason") == "riot" else "halted")
        # 雇いたい分を雇えなかった: 賃金を払うお金が足りなかったか、人がいなかったか
        rate, cause = (
            fac.labor_ratio(ctx.defs, f),
            "wages" if f.ext.get("short_wages") else "workers",
        )
        have, short = fac.input_ratio(ctx.defs, f, power)
        cargo, few = staff.wanted(ctx.config, ctx.defs, f), STATUS_CARGO
        if cargo is None and is_bank(ctx.config, f):
            cargo, few = banks.staff_wanted(island, ctx.config, ctx.defs, f), STATUS_BUSINESS
        if cargo is not None and f.workers >= cargo:
            # 港・商社は扱う荷に、小さい銀行は預金と融資に合わせて少なく雇う（定義の人数に足りなく
            # ても労働者不足ではない）
            cause = few
        elif f.workers >= fac.labor_demand(ctx.defs, f, ctx.turn, power):  # 雇いたい分は雇えた
            # 原料のある分しか雇わなかったなら原料、そうでなければ売れ行きに合わせた。前のターン
            # に買うつもりだった原料（economy["input_plan"] の稼働率の分）はそろっているなら、
            # 原料が少ないのは売れ行きに合わせて少なく買ったため
            target = f.economy.get("target", fac.FULL)
            planned = have >= f.economy.get("input_plan", fac.FULL + 1)
            cause = (
                "target"
                if short is None or have >= target or planned
                else Production._short(f, short, ctx.defs)
            )
        if short is not None and have < rate:
            rate, cause = have, Production._short(f, short, ctx.defs)
        cap = fac.engineer_cap(ctx.config, f)  # 技術者が足りなければ、稼働率の上限が下がる
        if cap < rate:
            rate, cause = cap, "engineers"
        if f.ext.get(fac.RECIPE_CHANGED) == ctx.turn:  # 作り方を替えたターンは半分（フェーズ3-2）
            half = fac.scale(rate, fac.FULL // 2)
            if half < rate:
                rate, cause = half, "recipe_change"
        return _Run(f, recipe, rate, cause if rate < fac.FULL else "")

    @staticmethod
    def _short(f: Facility, good: str, defs: Defs) -> str:
        """原料 good が足りない理由（施設の status）。前のターンの市場で買えなかったわけ
        （economy["short"]。hakoniwa_core/modules/market の _record_shortages）で決める。
        学校と大学には採算がないので、教材が高くて買えなかったときは public_price にする
        （「採算が合わない」と出ていた）。"""
        reason = f.economy.get("short", {}).get(good, "supply")
        if reason == "price" and is_public_service(defs, f):
            return f"{PUBLIC_PRICE}:{good}"
        return f"{SHORT_STATUS.get(reason, 'input')}:{good}"

    # --- 電力 ----------------------------------------------------------------

    def _power_market(
        self,
        island: IslandState,
        runs: list[_Run],
        ctx: StageContext,
        power: str,
        unsold: dict[int, str],
    ) -> dict[int, int]:
        """送電網ごとに電力を板寄せし、電力にかかわる施設の稼働率（施設の番号ごと）を返す。

        住宅のマスには、住民が受け取れた電力の割合を ext["power"] に書く。送電網に買えなかった
        買い手がいるのに1つも売れなかった発電所は、unsold に理由 power_price（電気料金が発電の
        費用に届かない）を書く。
        """
        defs, config = ctx.defs, ctx.config
        grid_of = self._grids(island, defs)
        household_use = config.get("power.household_use")
        climate_power = nature.household_power(defs, island)  # 寒冷の島は多く要る（フェーズ3-2）
        housing = fac.housing_kind(config)
        economy = island.economy
        has_money = island.has_economy
        reference = economy.prices.get(power, 1)
        width = to_basis(config.get("market.band"))
        cap = island.policy.price_cap.get(power) if has_money else None
        # 計画経済は公定価格で値幅 0 にし、足りなければ割当の順位と配給で分ける（フェーズ3-4）
        fixed = planning.official(island).get(power) if has_money else None
        if fixed and not planning.is_planned(island, defs):
            fixed = None
        if fixed:
            reference, cap = fixed, None
        bounds = (fixed, fixed) if fixed else None
        _, high = bounds or auction.band(reference, width, cap)
        rates_of = freight.facility_rates(island, defs, config) if has_money else {}

        plants: dict[int, list[tuple[_Run, int]]] = {}
        users: dict[int, list[_Run]] = {}
        for run in runs:
            grid = grid_of[(run.f.x, run.f.y)]
            if run.recipe["outputs"].get(power, 0):  # 発電所の能力は生産性を掛けた量
                out = fac.scale(run.recipe["outputs"][power], fac.productivity_of(run.f))
                plants.setdefault(grid, []).append((run, out))
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
            home_need = to_milli(home_people * household_use * climate_power)
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
                if fixed:  # 公定価格で売る（費用に届かなくても）
                    sell_orders.append(Order(capacity))
                    continue
                ask = run.f.economy.get("ask", {}).get(power, reference)
                if has_money:  # 1単位の変動費（燃料の運賃を含む）より安くは売らない（国庫直轄も）
                    wage = run.f.economy.get("wage", 0)
                    rate = rates_of.get(run.f.id, 0)
                    floor = unit_floor(defs, config, run.f, power, economy.prices, wage, rate)
                    ask = max(ask, floor)
                sell_orders.append(Order(capacity, min(ask, high)))  # 上端より高くては売れない
            buy_orders = []
            tiers: list[int] = []
            for b in buyers:
                qty, limit = b.need, None
                if fixed:
                    limit = fixed
                    if b.layer:
                        tiers.append(planning.HOUSEHOLDS)
                    else:
                        f = b.run.f  # type: ignore[union-attr]
                        prior = planning.is_priority(island, defs, f)
                        tiers.append(planning.PRIORITY if prior else planning.STATE)
                elif has_money:
                    limit = min(high, self._power_limit(island, ctx, b, reference, power))
                if has_money:
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
            allocate = None
            if fixed:
                layers = [b.layer for b in buyers]
                allocate = planning.allocator(tiers, layers, persons)
            result = auction.clear(
                buy_orders,
                sell_orders,
                reference,
                width,
                cap,
                bounds=bounds,
                allocate_buys=allocate,
            )
            if has_money:
                self._settle(island, ctx, result, buyers, sellers, power)
            for b, got in zip(buyers, result.bought, strict=True):
                if b.run is not None:
                    rates[b.run.f.id] = fac.scale(b.run.rate, fac.ratio(got, b.need))
            for (run, out), sold in zip(sellers, result.sold, strict=True):
                rates[run.f.id] = fac.ratio(sold, out)
                if sold <= 0 < fac.scale(out, run.rate) and result.unfilled_demand > 0:
                    unsold[run.f.id] = "power_price"
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
                    if "target" in run.f.economy and not is_stopped(run.f):
                        run.f.economy["target"] = target
            totals["capacity"] += result.supply
            totals["demand"] += sum(b.need for b in buyers)
            totals["used"] += result.volume
            volume += result.volume
            value += money_of(result.price, result.volume)
        for key, amount in totals.items():
            island.stats[f"power.{key}"] = from_milli(amount)
        if has_money:
            price = fixed or (unit_price(value, volume) if volume else reference)
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
            planning.note_sale(island, config, power, plant, qty, result.price)
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
        grid_of = self._grids(island, ctx.defs)
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
    def _grids(island: IslandState, defs: Defs) -> dict[tuple[int, int], int]:
        """送電網: 施設や住宅のマスと、電力を通す送電網のマスの、上下左右につながったまとまり。"""
        nodes = {(f.x, f.y) for f in island.facilities if fac.on_map(f)}
        nodes |= {(t.x, t.y) for t in island.tiles if t.infra and infra.carries_power(defs, t)}
        grid_of = {}
        for i, group in enumerate(components(island, nodes)):
            for pos in group:
                grid_of[pos] = i
        return grid_of

    # --- 状態とログ ------------------------------------------------------------

    @staticmethod
    def _update_status(island: IslandState, f: Facility, rate: int, cause: str) -> None:
        """稼働率と理由を書き、止まったとき・動き出したときにログを出す。

        止まっている間に理由が変わったとき（災害から売れ行きへ、など）も、止まっている間に1度
        だけその理由を知らせる（知らせた理由は ext["stop_causes"]。動き出したら消す）。
        """
        before_rate, before_status = f.operating_rate, f.status
        f.operating_rate = rate
        f.status = cause if rate < fac.FULL else ""
        where = {"facility": f.kind, "x": f.x, "y": f.y}
        stopped = rate == 0 and (before_rate > 0 or before_status == fac.STATUS_STARTING)
        if stopped:
            f.ext["stop_causes"] = []
        told = f.ext.get("stop_causes")
        if rate == 0 and (stopped or (cause != before_status and cause not in (told or []))):
            kind, _, good = cause.partition(":")
            args = {**where, "good": good} if good else where
            island.log.append(LogEntry(f"facility_stopped_{kind}", args))
            f.ext["stop_causes"] = [*(told or []), cause]
        elif rate > 0 and before_rate == 0:
            f.ext.pop("stop_causes", None)
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


# --- 命令 -------------------------------------------------------------------------


def check_change_recipe(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    """作り方の変更（フェーズ3-2）は国営の施設だけ。民営は 12 ターンごとに自分で見直す。"""
    f = island.facility_at(payload["x"], payload["y"])
    if f is None or defs.facilities[f.kind].get("buildable") is False:
        raise OrderFailed("reason_no_facility")
    if f.owner != OWNER_STATE:
        raise OrderFailed("reason_not_state_owned", facility=f.kind)
    recipe = payload["recipe"]
    if recipe not in defs.facilities[f.kind]["recipes"]:
        raise OrderFailed("reason_unknown_recipe", facility=f.kind)
    if recipe == f.ext.get(fac.RECIPE_NEXT, f.recipe):
        raise OrderFailed("reason_recipe_unchanged", facility=f.kind)
    problem = fac.switch_problem(defs, island, f, recipe)
    if problem is not None:
        raise OrderFailed(problem[0], **problem[1])


def change_recipe(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """このターンの市場の段で作り方を替える（その市場で新しい原料を買い、前の作り方の物を売る）。
    次のターンは稼働率が半分。"""
    f = island.facility_at(payload["x"], payload["y"])
    assert f is not None
    if payload["recipe"] == f.recipe:  # 替える前に取り消した
        f.ext.pop(fac.RECIPE_NEXT, None)
    else:
        f.ext[fac.RECIPE_NEXT] = payload["recipe"]


def register(registry: Registry) -> None:
    registry.add_stage(Production())
    registry.add_order(
        OrderHandler("change_recipe", execute=change_recipe, check=check_change_recipe)
    )
    registry.add_formula("output_rate", output_rate)
    registry.add_formula("power_target", power_target)
