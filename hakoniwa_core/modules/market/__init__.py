"""市場の段: 財ごとに1回の板寄せで売り買いを決め、代金・運賃・消費税を払う（電力は生産の段）。

注文を出す者:
- 施設（独立採算と国庫直轄）: 産出物の在庫を希望価格で売る（持ち越せる期間が今のターンで切れる物は
  指値なし）。次のターンの操業目標に要る投入財のうち手元にない分を、希望価格で買う（変動費の利益が
  0になる値段まで。品切れが続いても、払えない値段まで買い上がらない）。
- 国の倉庫: 命令 warehouse_trade で決めた量を、指値で売る・買う。
- 工事中の施設（国の工事）: 次の1ターン分の資材のうち、国の倉庫にもない分を、いま売りに
  出ている量まで買う（指値は値幅の上端）。
- 家計の層: 食料、消費財、町の資材（economy/households.py と、この段の _household_orders）。
  食料は、いま売りに出ている量を見て財ごとに割り振る。

注文は出すときに「量 × 指値」のお金（運賃と消費税を含む）を取り置き、取り置けない分は出せません。
買い注文の指値は運賃と消費税の分だけ、売り注文の指値は運賃の分だけ割って板寄せに出すので、
運賃と税を払っても指値より不利にはなりません。財ごとの板寄せは互いに独立で、順番で結果は
変わりません。

市場の後に、持ち越せる期間を過ぎた在庫を捨て、希望価格（式 price_update）と操業目標
（式 operating_target）を見直します。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    CONSTRUCTION,
    GENERAL,
    WAREHOUSE,
    Payer,
    books,
    entity_of,
    facility_entity,
    funds,
    household,
    is_self_supporting,
    is_transport,
    payer_of,
)
from hakoniwa_core.economy import freight as freight_rates
from hakoniwa_core.economy.households import consumption
from hakoniwa_core.economy.prices import input_limit, unit_floor, variable_profit
from hakoniwa_core.economy.stats import inflation
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules.population import layer_food_needs
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
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
    scale_price,
    to_basis,
    to_milli,
)

# 注文を出す者の種類
FACILITY = "facility"
SITE = "site"
HOUSEHOLD = "household"
STORE = "warehouse"


@dataclass
class Party:
    """注文を出した者。"""

    kind: str
    name: str  # 約定の記録に残す名前（fac:12、hh:general、gov）
    payer: Payer  # 買うときに払う側、売るときに代金を受け取る側
    freight: int = 0  # 運賃の率（1万分の1）
    tax: int = 0  # 消費税の率（家計が税のかかる財を買うとき）
    facility: Facility | None = None
    layer: str | None = None
    reserved: int = 0  # 取り置いたお金（買い注文）


@dataclass
class Book:
    """財ごとの注文。"""

    buys: list[tuple[Order, Party]] = field(default_factory=list)
    sells: list[tuple[Order, Party]] = field(default_factory=list)


def gross_cost(net: int, qty: int, freight: int, tax: int) -> int:
    """値段 net で qty 買ったときに払う額の合計（代金 + 運賃 + 消費税）。"""
    amount = money_of(net, qty)
    return amount + part(amount, freight) + part(amount, tax)


def net_limit(gross: int, freight: int, tax: int) -> int:
    """運賃と税を含めて gross まで払えるときの、板寄せに出す指値。"""
    return gross * BASIS // (BASIS + freight + tax)


def seller_limit(ask: int, freight: int, high: int) -> int:
    """運賃を払った後に ask が手元に残る指値（切り上げ）。ただし値幅の上端 high まで。

    上端より高い指値は売れないので、上端で売りに出す（費用より安くても、売れないまま腐らせる
    よりよい。値段は1ターンに値幅ずつ費用まで上がっていく）。
    """
    return min(high, -(-ask * BASIS // max(1, BASIS - freight)))


def usual_price(island: IslandState, good: str, turns: int) -> int:
    """家計が見る「いつもの値段」: 直近 turns ターンの値段の平均（記録がなければ今の値段）。"""
    history = island.economy.price_history.get(good, [])[-turns:]
    if not history:
        return island.economy.prices.get(good, 1)
    return max(1, round_money(sum(history) / len(history)))


def consumer_tiers(
    budget: int, price: int, tiers: list[float], freight: int, tax: int
) -> list[tuple[int, int]]:
    """消費財の注文（量, 指値）の段。

    指値はいつもの値段 price の tiers 倍で、運賃と税は量を決めるときに足す。

    指値の高い段から順に「その値段で budget を使い切る量」になるまで足していくので、値段が
    p の段まで約定すると budget ÷ p だけ買う（使う額は値段によらずほぼ budget。
    高いほど少なく買う）。
    """
    out = []
    bought = 0
    for tier in sorted(tiers, reverse=True):
        limit = scale_price(price, tier)
        total = qty_for(budget, limit + part(limit, freight) + part(limit, tax))
        if total > bought:
            out.append((total - bought, limit))
            bought = total
    return out


def food_split(
    need: int,
    values: dict[str, float],
    nutritions: dict[str, int],
    caps: dict[str, int],
    elasticity: float,
) -> dict[str, int]:
    """食料の要る量 need（栄養）を財ごとの注文の量に分ける。

    values は質を足した栄養あたりの値段、nutritions は1単位の栄養（1000 分の 1）、caps は
    いま売りに出ている量（書いていない財は 0）。売りに出ている栄養の量に
    「(値段 ÷ 一番安い値段) の −elasticity 乗」を掛けた重みで分け（少し安いだけの財に全部が
    移らないように）、上限で余った分は安い順に詰め、それでも足りない分は一番安い財に足す。
    """
    if need <= 0 or not values:
        return {}

    def nutrition_of(good: str, qty: int) -> int:
        return qty * nutritions[good] // QUANTITY_SCALE

    order = sorted(values, key=lambda g: (values[g], g))
    room = {g: nutrition_of(g, caps.get(g, 0)) for g in order}
    open_goods = [g for g in order if room[g] > 0]
    got = dict.fromkeys(order, 0)
    if open_goods:
        cheapest = values[open_goods[0]]
        weights = {g: room[g] * (values[g] / cheapest) ** -elasticity for g in open_goods}
        total = sum(weights.values())
        for g in open_goods:
            got[g] = min(room[g], int(need * weights[g] / total))
        for g in open_goods:  # 上限で余った分は安い順に
            got[g] += min(room[g] - got[g], need - sum(got.values()))
    left = need - sum(got.values())
    if left > 0:  # 足りない分は一番安い財に足して、買えるだけ買う
        got[(open_goods or order)[0]] += left
    return {g: -(-n * QUANTITY_SCALE // nutritions[g]) for g, n in got.items() if n > 0}


def food_by_quality(
    need: int,
    qualities: dict[str, float],
    values: dict[str, float],
    nutritions: dict[str, int],
    caps: dict[str, int],
) -> dict[str, int]:
    """食料の要る量 need を、質の高い財から、いま売りに出ている量（caps。書いていない財は 0）まで
    詰めた注文の量。

    詰めきれない分は、質を足した栄養あたりの値段（values）が安い順に、それでも足りなければ
    一番安い財に足す。
    """
    if need <= 0 or not qualities:
        return {}
    got = dict.fromkeys(qualities, 0)
    left = need
    for order in (
        sorted(qualities, key=lambda g: (-qualities[g], values[g], g)),
        sorted(qualities, key=lambda g: (values[g], g)),
    ):
        for g in order:
            room = caps.get(g, 0) * nutritions[g] // QUANTITY_SCALE - got[g]
            take = max(0, min(room, left))
            got[g] += take
            left -= take
    if left > 0:
        got[min(qualities, key=lambda g: (values[g], g))] += left
    return {g: -(-n * QUANTITY_SCALE // nutritions[g]) for g, n in got.items() if n > 0}


def upgrade_food(
    cheap: dict[str, int], best: dict[str, int], unit: dict[str, int], cap: int
) -> dict[str, int]:
    """安い組み合わせ cheap から、予算 cap に収まる分だけ質の高い組み合わせ best へ寄せる。

    unit は1単位の見込みの値段（運賃と税を含む）。cap が cheap の費用以下なら cheap のまま、
    best の費用以上（または best のほうが安い）なら best、その間は費用の割合で両方を混ぜる。
    """

    def cost(basket: dict[str, int]) -> int:
        return sum(money_of(unit[g], q) for g, q in basket.items())

    low, high = cost(cheap), cost(best)
    if high <= low:  # 質の高い組み合わせのほうが安い
        return best
    if cap <= low:
        return cheap
    mix = min(1.0, (cap - low) / (high - low))
    goods = sorted(set(cheap) | set(best))
    out = {
        g: round_money(cheap.get(g, 0) + mix * (best.get(g, 0) - cheap.get(g, 0))) for g in goods
    }
    return {g: q for g, q in out.items() if q > 0}


# --- 名前付きの式 -----------------------------------------------------------------


def price_update(price: int, offered: int, sold: int, shortage: float, ctx) -> int:
    """売り手の希望価格の見直し: P × (1 + k (D − S) / max(S, D))、1ターンに値幅まで。

    S は売りに出した量、D は売れた量。全部売れたら、市場全体の買えなかった量の割合 shortage を
    D に足す（値を上げる）。
    """
    if offered <= 0:
        return price
    k = ctx.config.get("market.price_speed")
    width = ctx.config.get("market.band")
    s, d = float(offered), float(sold)
    if sold >= offered:
        d = s * (1 + shortage)
    change = k * (d - s) / max(s, d)
    return scale_price(price, 1 + max(-width, min(width, change)))


def inventory_turns(ctx, good: str) -> float:
    """産出物 good の在庫の目安（売れ行きの見込みの何ターン分か）。

    持ち越せる期間の短い物は、その半分（(期間 − 1) ÷ 2 ターン）までにする（売れ残りを捨てない）。
    """
    turns = ctx.config.get("market.inventory_turns")
    life = ctx.defs.goods[good].get("shelf_life")
    return turns if life is None else min(turns, (life - 1) / 2)


def operating_target(island: IslandState, f: Facility, ctx: StageContext) -> int:
    """操業目標（1000 分の 1）の見直し: 見込みの売れ行きと、在庫を目安に戻す分だけ作る。

    産出物ごとに、売れ行きの見込み E を前のターンに売れた量へ market.sales_smoothing の割合だけ
    近づけ（売り切れて市場に買えなかった人が残っていれば、全力の target_step 分まで多めに見る）、
    「E ＋（E × market.inventory_turns − 在庫）× market.inventory_adjust」を作る量にする（持ち越せる
    期間が短い物は、在庫の目安を「(期間 − 1) ÷ 2」ターン分までにする）。
    売る物がなかった施設は、市場に買えなかった人がいればその量（全力まで）を、そうでなくても
    変動費の利益が出る値段なら（国庫直轄はいつも）全力の target_step 分を、売れる見込みにして
    作り始める（災害で漁港が止まったときなどに、止めていた農場がすぐ動き出すように）。
    売り切れて、値段が1単位あたりの変動費の (1 + market.expand_margin) 倍を超えていれば、
    見込みに全力の target_step 分を足して増産する（儲かる物は多く作られ、値段が費用に近づく）。
    希望価格が約定価格より高くて売れなかった分は、市場に買えなかった人がいれば売れたと見る（値上げ
    して売れなかったターンに減産すると、品不足なのに作る量が上下して、値段が上がり続けたため）。
    投入財か電力が足りずに止まった施設は、動けた割合に target_step を足した分までにする（動けない
    のに人を雇い続けない）。1ターンに変えるのは全力の market.max_target_change まで（人の出入りと
    仕入れを急に変えない）。
    発電所は在庫を持たないので、生産の段で、送電網の買いたい量に合わせて決める（power_target）。
    """
    settings = ctx.config.get("market")
    step = round_money(settings["target_step"] * fac.FULL + 0.5)
    target = f.economy.get("target", fac.FULL)
    power = ctx.config.get("power.good")
    outputs = {g: q for g, q in fac.recipe_of(ctx.defs, f)["outputs"].items() if g != power and q}
    records = {m.good: m for m in island.markets}
    expected = f.economy.setdefault("expected", {})
    prices = island.economy.prices
    rate = freight_rates.facility_rates(island, ctx.defs, ctx.config).get(f.id, 0)
    wage = f.economy.get("wage", 0)
    profitable = True
    if is_self_supporting(f):
        profitable = variable_profit(ctx.defs, ctx.config, f, prices, wage, rate) > 0
    plan = 0
    for good, full in sorted(outputs.items()):
        offered, sold, priced_out = f.economy.get("sales", {}).get(good, [0, 0, 0])
        record = records.get(good)
        unmet = record.demand - record.volume if record is not None else 0
        before = expected.get(good, fac.scale(full, target))
        held = stock.total(f.stock, good)
        floor = unit_floor(ctx.defs, ctx.config, f, good, prices, wage, rate)
        if priced_out and unmet > 0 and prices.get(good, 0) >= floor:
            # 希望価格が高すぎて売れなかった分は、市場に買えなかった人がいれば売れたと見る
            # （次のターンに希望価格を下げて売れる。値上げで売れなかったターンに減産しない）
            sold += min(priced_out, unmet)
        if offered == 0 and unmet > 0:  # 売る物がなく、市場に買えなかった人がいれば作り始める
            sold = max(fac.scale(full, step), min(unmet, full))
        elif offered == 0 and profitable:  # 売る物がなくても、利益の出る値段なら少し作ってみる
            sold = fac.scale(full, step)
        elif sold >= offered > 0:  # 売り切れた
            sold += min(unmet, fac.scale(full, step))
            if prices.get(good, 0) > floor * (1 + settings["expand_margin"]):
                sold = max(sold, before + fac.scale(full, step))
        estimate = before + round_money((sold - before) * settings["sales_smoothing"])
        expected[good] = estimate
        gap = round_money(estimate * inventory_turns(ctx, good)) - held
        amount = estimate + round_money(gap * settings["inventory_adjust"])
        plan = max(plan, fac.ratio(max(0, amount), full))
    if f.status.startswith("input:") or f.status == "power":  # 仕入れか電力が足りずに止まっている
        plan = min(plan, f.operating_rate + step)
    limit = round_money(settings["max_target_change"] * fac.FULL + 0.5)
    return max(target - limit, min(target + limit, plan))


# --- 段 -------------------------------------------------------------------------


class Market(IslandStage):
    name = "market"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        defs, config = ctx.defs, ctx.config
        rates = freight_rates.facility_rates(island, defs, config)
        hh_rate = island.scratch["freight_household"]
        self._supply_sites_from_warehouse(island, ctx)
        books_by_good: dict[str, Book] = {}
        self._warehouse_orders(island, ctx, books_by_good)
        self._facility_orders(island, ctx, books_by_good, rates)
        offered = {  # 買い手は、いま売りに出ている量を見て注文する（ない物にお金を取り置かない）
            good: sum(order.quantity for order, _ in book.sells)
            for good, book in books_by_good.items()
        }
        self._site_orders(island, ctx, books_by_good, rates, offered)
        self._household_orders(island, ctx, books_by_good, hh_rate, offered)
        transport = self._transport_entity(island, ctx)
        bought: dict[str, int] = island.scratch.setdefault("household_bought", {})
        results: dict[str, auction.Result] = {}
        width = to_basis(config.get("market.band"))
        for good in sorted(books_by_good):
            book = books_by_good[good]
            reference = island.economy.prices.get(good, 1)
            result = auction.clear(
                [o for o, _ in book.buys], [o for o, _ in book.sells], reference, width
            )
            results[good] = result
            self._settle(island, ctx, good, book, result, transport, bought)
        for _, book in sorted(books_by_good.items()):
            for _, party in book.buys:
                funds.release(island, party.payer, party.reserved)
                party.reserved = 0
        self._spoil(island, ctx)
        self._record(island, ctx, books_by_good, results, bought)
        self._update_prices(island, ctx, books_by_good, results)
        power = config.get("power.good")
        for f in island.facilities:
            outputs = fac.recipe_of(defs, f)["outputs"]
            if f.built and "target" in f.economy and any(g != power for g in outputs):
                f.economy["target"] = ctx.formula("operating_target")(island, f, ctx)

    # --- 注文 ---------------------------------------------------------------------

    @staticmethod
    def _add(books_by_good: dict[str, Book], good: str, side: str, order: Order, party: Party):
        book = books_by_good.setdefault(good, Book())
        (book.buys if side == "buy" else book.sells).append((order, party))

    def _supply_sites_from_warehouse(self, island: IslandState, ctx: StageContext) -> None:
        """国の工事は、国の倉庫にある資材を先に使う（次の1ターン分まで）。"""
        for f in sorted(island.facilities, key=lambda f: f.id):
            if f.built:
                continue
            for good, qty in sorted(fac.step_materials(ctx.defs, f).items()):
                want = qty - stock.total(f.stock, good)
                if want <= 0:
                    continue
                batches = stock.take_batches(island.warehouse, good, want)
                stock.put_batches(f.stock, good, batches)
                taken = sum(q for _, q in batches)
                if taken:
                    value = money_of(island.economy.prices.get(good, 0), taken)
                    f.economy["capital"] = f.economy.get("capital", 0) + value

    def _facility_orders(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        rates: dict[int, int],
    ) -> None:
        defs, config = ctx.defs, ctx.config
        power = config.get("power.good")
        prices = island.economy.prices
        width = to_basis(config.get("market.band"))
        for f in sorted(island.facilities, key=lambda f: f.id):
            if not f.built or fac.is_housing(config, f) or not fac.on_map(f):
                continue
            recipe = fac.recipe_of(defs, f)
            if not recipe["outputs"] and not recipe["inputs"]:
                continue
            rate = rates.get(f.id, 0)
            payer = payer_of(defs, config, f)
            name = facility_entity(f.id)
            seller = Party(FACILITY, name, Payer(entity_of(config, f), payer.item), rate, 0, f)
            for good in sorted(recipe["outputs"]):
                if good == power:
                    continue
                total = stock.total(f.stock, good)
                if total <= 0:
                    continue
                ask = f.economy.get("ask", {}).get(good, prices.get(good, 0))
                floor = unit_floor(defs, config, f, good, prices, f.economy.get("wage", 0), rate)
                ask = max(ask, floor)
                expiring = stock.expiring(f.stock, good, ctx.turn, defs.goods)
                if expiring:
                    self._add(books_by_good, good, "sell", Order(expiring), seller)
                if total - expiring > 0:
                    _, high = auction.band(prices.get(good, 1), width)
                    order = Order(total - expiring, seller_limit(ask, rate, high))
                    self._add(books_by_good, good, "sell", order, seller)
            needs = self._input_needs(ctx, f, power)
            if not needs:
                continue
            plan = {
                g: (q, net_limit(self._bid(ctx, f, g, prices, rate), rate, 0)) for g, q in needs
            }
            plan = self._afford(island, payer, plan, rate, 0)
            priority = fac.priority_of(defs, config, f)
            for good, (qty, limit) in sorted(plan.items()):
                party = Party(FACILITY, name, payer, rate, 0, f)
                party.reserved = gross_cost(limit, qty, rate, 0)
                funds.commit(island, payer, party.reserved)
                self._add(books_by_good, good, "buy", Order(qty, limit, priority), party)

    @staticmethod
    def _bid(ctx: StageContext, f: Facility, good: str, prices: dict[str, int], rate: int) -> int:
        """投入財の買いの指値（運賃込み）: 値幅の上端。ただし変動費の利益が0になる値段まで
        （国庫直轄は、今の値段まではいつも買う）。

        売りが余っていれば約定価格は売り手の指値で決まるので、高く出しても高く買うことには
        ならない。品切れのときだけ、払える値段まで1ターンに値幅ずつ買い上がる。
        """
        price = prices.get(good, 0)
        _, high = auction.band(price, to_basis(ctx.config.get("market.band")))
        wage = f.economy.get("wage", 0)
        limit = input_limit(ctx.defs, ctx.config, f, good, prices, wage, rate)
        if not is_self_supporting(f):
            limit = max(limit, price + part(price, rate))
        return min(high + part(high, rate), limit)

    @staticmethod
    def _input_needs(ctx: StageContext, f: Facility, power: str) -> list[tuple[str, int]]:
        """次のターンに要る投入財のうち手元にない分。人手の足りない施設は、今の人手より少し多い分。"""
        target = f.economy.get("target", fac.FULL)
        if fac.is_halted(f, ctx.turn + 1):
            expected = 0
        elif f.status == fac.STATUS_STARTING:
            expected = target
        else:
            headroom = round_money(ctx.config.get("market.target_step") * fac.FULL + 0.5)
            expected = min(target, fac.labor_ratio(ctx.defs, f) + headroom)
        needs = []
        for good, qty in sorted(fac.recipe_of(ctx.defs, f)["inputs"].items()):
            if good == power:
                continue
            want = fac.scale(qty, expected) - stock.total(f.stock, good)
            if want > 0:
                needs.append((good, want))
        return needs

    @staticmethod
    def _afford(
        island: IslandState,
        payer: Payer,
        plan: dict[str, tuple[int, int]],
        freight: int,
        tax: int,
    ) -> dict[str, tuple[int, int]]:
        """取り置けるお金に収まるように、どの財の量も同じ割合で減らす。"""
        cost = sum(gross_cost(limit, qty, freight, tax) for qty, limit in plan.values())
        money = funds.available(island, payer)
        if cost <= money:
            return {g: v for g, v in plan.items() if v[0] > 0}
        out = {}
        left = money
        for good, (qty, limit) in plan.items():
            scaled = qty * money // cost if cost > 0 else 0
            # 切り捨ての誤差で取り置けるお金を超えないように、残りのお金で買える量までにする
            scaled = min(scaled, qty_for(left * BASIS // (BASIS + freight + tax), limit))
            if scaled > 0:
                out[good] = (scaled, limit)
                left -= gross_cost(limit, scaled, freight, tax)
        return out

    def _site_orders(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        rates: dict[int, int],
        offered: dict[str, int],
    ) -> None:
        width = to_basis(ctx.config.get("market.band"))
        wanted = island.scratch.setdefault("wanted", {})  # 売りに出ていなくて買えなかった量
        for f in sorted(island.facilities, key=lambda f: f.id):
            if f.built:
                continue
            rate = rates.get(f.id, 0)
            payer = payer_of(ctx.defs, ctx.config, f)
            plan = {}
            for good, qty in sorted(fac.step_materials(ctx.defs, f).items()):
                want = qty - stock.total(f.stock, good)
                if want > 0 and offered.get(good, 0) <= 0:
                    wanted[good] = wanted.get(good, 0) + want
                want = min(want, offered.get(good, 0))
                if want > 0:
                    _, high = auction.band(island.economy.prices.get(good, 1), width)
                    plan[good] = (want, high)
            priority = fac.priority_of(ctx.defs, ctx.config, f)
            for good, (qty, limit) in sorted(self._afford(island, payer, plan, rate, 0).items()):
                party = Party(SITE, facility_entity(f.id), payer, rate, 0, f)
                party.reserved = gross_cost(limit, qty, rate, 0)
                funds.commit(island, payer, party.reserved)
                self._add(books_by_good, good, "buy", Order(qty, limit, priority), party)

    def _household_orders(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        rate: int,
        offered: dict[str, int],
    ) -> None:
        """家計の層ごとに、使う額の中で食料・消費財・町の資材を注文する。"""
        defs, config = ctx.defs, ctx.config
        settings = config.get("household")
        width = to_basis(config.get("market.band"))
        tax = island.policy.consumption_tax
        prices = island.economy.prices
        priority = config.get("market.household_priority")
        pi = inflation(island.economy)
        power_paid = island.scratch.get("household_power", {})
        needs = layer_food_needs(island, ctx)
        persons = {n: h["persons"] for n, h in island.economy.households.items()}
        total_persons = sum(persons.values()) or 1
        foods = sorted(g for g, d in defs.goods.items() if "food" in d)
        nutritions = {g: to_milli(defs.goods[g]["food"]["nutrition"]) for g in foods}
        qualities = {g: defs.goods[g]["food"].get("quality", 0) for g in foods}
        values = {  # 質を足した栄養あたりの値段
            g: prices.get(g, 1) / (defs.goods[g]["food"]["nutrition"] * (1 + qualities[g]))
            for g in foods
        }
        for layer in sorted(island.economy.households):
            payer = Payer(household(layer))
            budget = ctx.formula("consumption")(island, layer, config, pi)
            budget -= power_paid.get(layer, 0)
            budget = max(0, min(budget, funds.available(island, payer)))
            island.scratch.setdefault("household_budget", {})[layer] = budget
            share = persons[layer] / total_persons
            # 1. 食料: 売りに出ている量と値段で財ごとに割り振り、予算に余裕があれば質の高い物に
            #    替える。値幅の上端を指値にする
            caps = {g: int(offered.get(g, 0) * share) for g in foods}
            cheap = food_split(
                needs.get(layer, 0), values, nutritions, caps, settings["food_elasticity"]
            )
            best = food_by_quality(needs.get(layer, 0), qualities, values, nutritions, caps)
            unit = {g: prices.get(g, 1) * (BASIS + rate + tax) // BASIS for g in foods}
            food_budget = round_money(budget * settings["food_share"])
            amounts = upgrade_food(cheap, best, unit, food_budget)
            # 売りに出ていない食料も、あれば買いたい量を記録する（作る施設があれば、採算の合う
            # 値段まで値段が上がって作り始める。_record）
            plenty = dict.fromkeys(foods, needs.get(layer, 0) * QUANTITY_SCALE)
            ideal = upgrade_food(
                food_split(
                    needs.get(layer, 0), values, nutritions, plenty, settings["food_elasticity"]
                ),
                food_by_quality(needs.get(layer, 0), qualities, values, nutritions, plenty),
                unit,
                food_budget,
            )
            wanted = island.scratch.setdefault("wanted", {})
            for good, qty in sorted(ideal.items()):
                if offered.get(good, 0) <= 0:
                    wanted[good] = wanted.get(good, 0) + qty
            plan: dict[str, tuple[int, int]] = {}
            for good, qty in sorted(amounts.items()):
                _, high = auction.band(prices.get(good, 1), width)
                plan[good] = (qty, high)
            food_cost = sum(gross_cost(lim, q, rate, tax) for q, lim in plan.values())
            if food_cost > budget and food_cost > 0:
                plan = {g: (q * budget // food_cost, lim) for g, (q, lim) in plan.items()}
                food_cost = sum(gross_cost(lim, q, rate, tax) for q, lim in plan.values())
            for good, (qty, limit) in sorted(plan.items()):
                self._household_buy(
                    island, books_by_good, good, qty, limit, rate, tax, layer, priority
                )
            # 2. 消費財: 残りの決めた割合を、いつもの値段の倍率の段に分けて注文する
            #    （それより高ければ買わずに預金に残す。品切れで値段が跳ね上がり続けない）
            rest = max(0, budget - food_cost)
            spend = round_money(rest * settings["consumer_goods_share"])
            goods = [
                g
                for g, d in sorted(defs.goods.items())
                if d.get("consumer") and g != config.get("power.good") and "food" not in d
            ]
            tiers = settings["consumer_goods_tiers"]
            for good in goods:
                price = usual_price(island, good, settings["usual_price_turns"])
                for qty, limit in consumer_tiers(spend // len(goods), price, tiers, rate, tax):
                    self._household_buy(
                        island, books_by_good, good, qty, limit, rate, tax, layer, priority
                    )
        self._housing_orders(island, ctx, books_by_good, rate)

    def _household_buy(
        self,
        island: IslandState,
        books_by_good: dict[str, Book],
        good: str,
        qty: int,
        limit: int,
        rate: int,
        tax: int,
        layer: str,
        priority: int,
    ) -> None:
        if qty <= 0:
            return
        payer = Payer(household(layer))
        cost = gross_cost(limit, qty, rate, tax)
        money = funds.available(island, payer)
        if cost > money:
            qty = qty * money // cost
            cost = gross_cost(limit, qty, rate, tax)
        if qty <= 0:
            return
        party = Party(HOUSEHOLD, household(layer), payer, rate, tax, layer=layer)
        party.reserved = cost
        funds.commit(island, payer, cost)
        self._add(books_by_good, good, "buy", Order(qty, limit, priority), party)

    def _housing_orders(
        self, island: IslandState, ctx: StageContext, books_by_good: dict[str, Book], rate: int
    ) -> None:
        """町が育つのに要る資材（都市の発展の段が決めたもの）を、一般の層が預金の決めた割合まで買う。"""
        want = island.economy.housing.get("want", {})
        if not want:
            return
        payer = Payer(household(GENERAL))
        width = to_basis(ctx.config.get("market.band"))
        held = island.economy.housing.setdefault("stock", {})
        plan = {}
        for good, qty in sorted(want.items()):
            missing = qty - stock.total(held, good)
            if missing > 0:
                _, high = auction.band(island.economy.prices.get(good, 1), width)
                plan[good] = (missing, high)
        cap = part(
            ledger.money(island, household(GENERAL)),
            to_basis(ctx.config.get("household.housing_share")),
        )
        cost = sum(gross_cost(lim, q, rate, 0) for q, lim in plan.values())
        cap = min(cap, funds.available(island, payer))
        if cost > cap and cost > 0:
            plan = {g: (q * cap // cost, lim) for g, (q, lim) in plan.items()}
        priority = ctx.config.get("market.household_priority")
        for good, (qty, limit) in sorted(plan.items()):
            if qty <= 0:
                continue
            party = Party(HOUSEHOLD, household(GENERAL), payer, rate, 0, layer="housing")
            party.reserved = gross_cost(limit, qty, rate, 0)
            funds.commit(island, payer, party.reserved)
            self._add(books_by_good, good, "buy", Order(qty, limit, priority), party)

    def _warehouse_orders(
        self, island: IslandState, ctx: StageContext, books_by_good: dict[str, Book]
    ) -> None:
        """命令 warehouse_trade で並べた国の倉庫の売り買い（このターンだけ）。"""
        orders = island.economy.ext.pop("warehouse_orders", [])
        payer = Payer(ledger.GOV, WAREHOUSE)
        for item in orders:
            good, qty, limit = item["good"], item["quantity"], item["limit"]
            if item["side"] == "sell":
                qty = min(qty, stock.total(island.warehouse, good))
                if qty > 0:
                    party = Party(STORE, ledger.GOV, Payer(ledger.GOV, WAREHOUSE))
                    self._add(books_by_good, good, "sell", Order(qty, limit or None), party)
                continue
            plan = self._afford(island, payer, {good: (qty, limit)}, 0, 0)
            for g, (q, lim) in plan.items():
                party = Party(STORE, ledger.GOV, payer)
                party.reserved = gross_cost(lim, q, 0, 0)
                funds.commit(island, payer, party.reserved)
                self._add(books_by_good, g, "buy", Order(q, lim), party)

    # --- 約定 ---------------------------------------------------------------------

    @staticmethod
    def _transport_entity(island: IslandState, ctx: StageContext) -> tuple[str, int | None]:
        for f in island.facilities:
            if is_transport(ctx.config, f):
                return entity_of(ctx.config, f), f.id
        return ledger.GOV, None

    def _settle(
        self,
        island: IslandState,
        ctx: StageContext,
        good: str,
        book: Book,
        result: auction.Result,
        transport: tuple[str, int | None],
        bought: dict[str, int],
    ) -> None:
        price = result.price
        carrier, carrier_id = transport
        for b, s, qty in auction.match(result.bought, result.sold):
            _, buyer = book.buys[b]
            _, seller = book.sells[s]
            amount = money_of(price, qty)
            buyer_freight = part(amount, buyer.freight)
            seller_freight = part(amount, seller.freight)
            tax = part(amount, buyer.tax)
            paid = amount + buyer_freight + tax
            funds.pay(island, buyer.payer, seller.payer.entity, amount, "sale", committed=True)
            funds.pay(island, buyer.payer, carrier, buyer_freight, "freight", committed=True)
            funds.pay(island, buyer.payer, ledger.GOV, tax, "tax_consumption", committed=True)
            buyer.reserved -= paid
            # 売り手の運賃は、いま受け取った代金から払う
            ledger.pay(island, seller.payer.entity, carrier, seller_freight, "freight")
            if carrier_id is not None:
                books.add(island, carrier_id, books.SALES, buyer_freight + seller_freight)
            island.economy.freight["paid"] = (
                island.economy.freight.get("paid", 0) + buyer_freight + seller_freight
            )
            batches = self._take_from_seller(island, good, seller, qty)
            self._give_to_buyer(island, good, buyer, batches, amount, buyer_freight, tax, bought)
            if seller.facility is not None:
                books.add(island, seller.facility.id, books.SALES, amount)
                books.add(island, seller.facility.id, books.FREIGHT, seller_freight)
            else:
                books.add_sector(island, books.WAREHOUSE, "sales", amount)
            island.trades.append(TradeRecord(good, buyer.name, seller.name, qty, amount))

    @staticmethod
    def _take_from_seller(island: IslandState, good: str, seller: Party, qty: int) -> list:
        if seller.facility is not None:
            batches = stock.take_batches(seller.facility.stock, good, qty)
            books.stock_moved(island, seller.facility.id, good, -qty)
            return batches
        return stock.take_batches(island.warehouse, good, qty)

    @staticmethod
    def _give_to_buyer(
        island: IslandState,
        good: str,
        buyer: Party,
        batches: list,
        amount: int,
        freight: int,
        tax: int,
        bought: dict[str, int],
    ) -> None:
        qty = sum(q for _, q in batches)
        if buyer.kind == FACILITY:
            assert buyer.facility is not None
            stock.put_batches(buyer.facility.stock, good, batches)
            books.stock_moved(island, buyer.facility.id, good, qty)
            books.add(island, buyer.facility.id, books.PURCHASES, amount)
            books.add(island, buyer.facility.id, books.FREIGHT, freight)
        elif buyer.kind == SITE:
            assert buyer.facility is not None
            stock.put_batches(buyer.facility.stock, good, batches)
            books.add_sector(island, CONSTRUCTION, "materials", amount)
            books.add_sector(island, CONSTRUCTION, "freight", freight)
            f = buyer.facility
            f.economy["capital"] = f.economy.get("capital", 0) + amount + freight
        elif buyer.kind == HOUSEHOLD and buyer.layer == "housing":
            stock.put_batches(island.economy.housing.setdefault("stock", {}), good, batches)
            books.add_sector(island, books.HOUSEHOLDS, "housing", amount)
            books.add_sector(island, books.HOUSEHOLDS, "freight", freight)
        elif buyer.kind == HOUSEHOLD:
            eaten = island.scratch.setdefault("household_goods", {})
            layer_goods = eaten.setdefault(buyer.layer, {})
            layer_goods[good] = layer_goods.get(good, 0) + qty
            bought[good] = bought.get(good, 0) + qty
            books.add_sector(island, books.HOUSEHOLDS, "goods", amount)
            books.add_sector(island, books.HOUSEHOLDS, "freight", freight)
            books.add_sector(island, books.HOUSEHOLDS, "tax", tax)
        else:
            stock.put_batches(island.warehouse, good, batches)
            books.add_sector(island, books.WAREHOUSE, "purchases", amount)

    # --- 市場の後 -------------------------------------------------------------------

    @staticmethod
    def _spoil(island: IslandState, ctx: StageContext) -> None:
        """持ち越せる期間を過ぎた在庫を、施設・国の倉庫から捨てる。"""
        spoiled: dict[str, int] = {}
        for f in sorted(island.facilities, key=lambda f: f.id):
            for good, qty in stock.spoil(f.stock, ctx.turn, ctx.defs.goods).items():
                spoiled[good] = spoiled.get(good, 0) + qty
                if f.built:
                    books.stock_moved(island, f.id, good, -qty)
        for good, qty in stock.spoil(island.warehouse, ctx.turn, ctx.defs.goods).items():
            spoiled[good] = spoiled.get(good, 0) + qty
        for good, qty in sorted(spoiled.items()):
            island.log.append(LogEntry("goods_spoiled", {"good": good, "amount": from_milli(qty)}))

    @staticmethod
    def _record(
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        results: dict[str, auction.Result],
        bought: dict[str, int],
    ) -> None:
        supply = {}
        prices = island.economy.prices
        wanted = dict(island.scratch.get("wanted", {}))  # 売りに出ていなくて注文しなかった量
        for good in sorted(set(results) | set(wanted)):
            result = results.get(good)
            if result is None:  # 売りも買いの注文もなかったが、あれば買いたい人がいた
                island.markets.append(MarketRecord(good, prices.get(good, 1), 0, wanted[good], 0))
                continue
            demand = result.demand + (wanted.get(good, 0) if result.supply <= 0 else 0)
            island.markets.append(
                MarketRecord(good, result.price, result.volume, demand, result.supply)
            )
            supply[good] = result.supply
            if result.demand > 0 and result.supply > 0:  # 約定しなくても気配で値段が動く
                prices[good] = result.price
            elif result.demand > 0:
                wanted[good] = wanted.get(good, 0) + result.demand
        # 買いたい人がいるのに売りに出なかった財は、島の作る施設の1単位の変動費が今の値段以上なら、
        # 値幅の上端まで上げる（作って採算が合う値段まで1ターンに値幅ずつ上がり、作り始める）
        width = to_basis(ctx.config.get("market.band"))
        rates = freight_rates.facility_rates(island, ctx.defs, ctx.config)
        for good in sorted(wanted):
            if supply.get(good, 0) > 0:
                continue
            floors = [
                unit_floor(
                    ctx.defs,
                    ctx.config,
                    f,
                    good,
                    prices,
                    f.economy.get("wage", 0),
                    rates.get(f.id, 0),
                )
                for f in island.facilities
                if f.built and fac.recipe_of(ctx.defs, f)["outputs"].get(good)
            ]
            price = prices.get(good, 1)
            if floors and min(floors) >= price:
                prices[good] = auction.band(price, width)[1]

    def _update_prices(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        results: dict[str, auction.Result],
    ) -> None:
        """施設の売りの希望価格を見直す（買いの指値は注文のときに値幅と採算で決める。_bid）。"""
        update = ctx.formula("price_update")
        width = to_basis(ctx.config.get("market.band"))
        rates = freight_rates.facility_rates(island, ctx.defs, ctx.config)
        for good in sorted(results):
            result = results[good]
            book = books_by_good[good]
            shortage = result.unfilled_demand / result.demand if result.demand else 0.0
            low, high = auction.band(island.economy.prices.get(good, 1), width)
            # 売りに出した量、売れた量、希望価格が約定価格より高くて売れなかった量。持ち越せずに
            # 指値なしで出した分は、どれにも数えない（捨て値で売れるのを当てにして作り続けず、傷み
            # かけの物を捨て値で出したことで希望価格も下げない。傷みかけの物しかなかった施設は、
            # 売る物がなかったのと同じに扱う）
            sold: dict[int, list[int]] = {}
            for i, (order, party) in enumerate(book.sells):
                if party.facility is not None and order.limit is not None:
                    entry = sold.setdefault(party.facility.id, [0, 0, 0])
                    entry[0] += order.quantity
                    entry[1] += result.sold[i]
                    if order.limit > result.price:
                        entry[2] += order.quantity - result.sold[i]
            for fid, entry in sorted(sold.items()):
                offered, done, _ = entry
                f = island.facility(fid)
                assert f is not None
                f.economy.setdefault("sales", {})[good] = entry
                asks = f.economy.setdefault("ask", {})
                before = asks.get(good, result.price)
                ask = update(before, offered, done, shortage, ctx)
                ask = max(low, min(high, ask))
                rate = rates.get(f.id, 0)
                wage = f.economy.get("wage", 0)
                floor = unit_floor(ctx.defs, ctx.config, f, good, island.economy.prices, wage, rate)
                asks[good] = max(ask, floor)


# --- 命令 -------------------------------------------------------------------------


def check_set_priority(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    f = island.facility_at(payload["x"], payload["y"])
    if f is None or defs.facilities[f.kind].get("buildable") is False:
        raise OrderFailed("reason_no_facility")


def set_priority(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    f = island.facility_at(payload["x"], payload["y"])
    assert f is not None
    f.priority = payload["priority"]
    island.log.append(
        LogEntry(
            "priority_changed",
            {"facility": f.kind, "x": f.x, "y": f.y, "priority": payload["priority"]},
        )
    )


def check_warehouse_trade(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if payload["good"] not in defs.goods or defs.goods[payload["good"]].get("shelf_life") == 0:
        raise OrderFailed("reason_unknown_good")
    if payload["side"] == "sell" and stock.total(island.warehouse, payload["good"]) <= 0:
        raise OrderFailed("reason_not_in_warehouse", good=payload["good"])
    if payload["side"] == "buy" and payload.get("limit", 0) <= 0:
        raise OrderFailed("reason_limit_required")


def warehouse_trade(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """国の倉庫の財を、このターンの市場で売る・買う（買うのは国の倉庫の買い入れの予算から）。"""
    orders = island.economy.ext.setdefault("warehouse_orders", [])
    orders.append(
        {
            "good": payload["good"],
            "side": payload["side"],
            "quantity": to_milli(payload["quantity"]),
            "limit": payload.get("limit", 0),
        }
    )
    island.log.append(
        LogEntry(
            f"warehouse_{payload['side']}_ordered",
            {"good": payload["good"], "amount": payload["quantity"]},
        )
    )


def check_price_cap(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if payload["good"] not in defs.goods:
        raise OrderFailed("reason_unknown_good")


def set_price_cap(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """公共料金（電気料金）の上限。0 で上限をなくす。"""
    good, price = payload["good"], payload["price"]
    if price > 0:
        island.policy.price_cap[good] = price
        island.log.append(LogEntry("price_cap_set", {"good": good, "price": price}))
    else:
        island.policy.price_cap.pop(good, None)
        island.log.append(LogEntry("price_cap_cleared", {"good": good}))


def register(registry: Registry) -> None:
    registry.add_stage(Market())
    registry.add_formula("price_update", price_update)
    registry.add_formula("operating_target", operating_target)
    registry.add_formula("consumption", consumption)
    registry.add_order(OrderHandler("set_priority", execute=set_priority, check=check_set_priority))
    registry.add_order(
        OrderHandler("warehouse_trade", execute=warehouse_trade, check=check_warehouse_trade)
    )
    registry.add_order(OrderHandler("set_price_cap", execute=set_price_cap, check=check_price_cap))
