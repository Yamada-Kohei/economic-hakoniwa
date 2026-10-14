"""市場の段: 財ごとに1回の板寄せで売り買いを決め、代金・運賃・消費税を払う（電力は生産の段）。

注文を出す者:
- 施設（独立採算と国庫直轄）: 産出物の在庫を希望価格で売る（持ち越せる期間が今のターンで切れる物は
  指値なし）。次のターンの操業目標に要る投入財のうち手元にない分を、希望価格で買う（変動費の利益が
  0になる値段まで。品切れが続いても、払えない値段まで買い上がらない）。
- 国の倉庫: 命令 warehouse_trade で決めた量を、指値で売る・買う。
- 工事中の施設（国の工事）: 次の1ターン分の資材のうち、国の倉庫にもない分を、いま売りに
  出ている量まで買う（指値は値幅の上端）。
- インフラの公共事業（フェーズ2-3。modules/infrastructure）: 次のターンの維持と工事に要る資材の
  うち、国の倉庫にもない分を同じように買う（運賃の率は家計と同じ）。維持の分は施設の工事より先に、
  新しい工事の分は後に注文する。
- 家計の層: 食料、消費財、町の資材（economy/households.py と、この段の _household_orders）。
  食料は、いま売りに出ている量を見て財ごとに割り振る。

持ち越せる期間が今のターンで切れる物（指値なしの売り）は、その場で食べる家計に先に回します。
在庫に置く買い手（施設・工事・倉庫）が買うと、使う前に捨てられるためです。家計の買った量を
超えて売れた分は、同じ値段で売れ残っていたほかの売りに回します（_late_to_eaters）。それもなく
工場が買った分は、買えなかった量に数えて止まった理由に出します（_record_shortages）。

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
from hakoniwa_core.allocation import proportional
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
    is_public_service,
    is_self_supporting,
    is_stopped,
    is_transport,
    payer_of,
)
from hakoniwa_core.economy import freight as freight_rates
from hakoniwa_core.economy.households import consumption
from hakoniwa_core.economy.prices import input_limit, unit_floor, variable_profit
from hakoniwa_core.economy.stats import inflation
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules import infrastructure
from hakoniwa_core.modules.population import layer_food_needs
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import (
    OWNER_PRIVATE,
    Facility,
    IslandState,
    LogEntry,
    MarketRecord,
    TradeRecord,
)
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
WORKS = "works"  # インフラの公共事業
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
    public: bool = False  # 公務の施設（学校と大学）の買い注文（政府の消費に数える）


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
    budget: int,
    price: int,
    tiers: list[float],
    freight: int,
    tax: int,
    current: int = 0,
    low_tiers: list[float] | None = None,
) -> list[tuple[int, int]]:
    """消費財の注文（量, 指値）の段。

    指値はいつもの値段 price の tiers 倍で、運賃と税は量を決めるときに足す。今の値段 current が
    いつもの値段の一番下の段より安いときは、今の値段の low_tiers 倍の段も足す（値下がりした分だけ
    多く買う。フェーズ2-2 の需要の見直し）。

    指値の高い段から順に「その値段で budget を使い切る量」になるまで足していくので、値段が
    p の段まで約定すると budget ÷ p だけ買う（使う額は値段によらずほぼ budget。
    高いほど少なく買い、安いほど多く買う）。
    """
    limits = {scale_price(price, tier) for tier in tiers}
    if current > 0 and low_tiers and current < min(limits):
        limits |= {scale_price(current, tier) for tier in low_tiers}
    out = []
    bought = 0
    for limit in sorted(limits, reverse=True):
        if limit <= 0:
            continue
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


def late_weight(price: int, floor: int, band: float) -> float:
    """持ち越せずに指値なしで出して売れた分を、操業目標の見込みに数える割合。

    値段が1単位あたりの変動費 floor 以上なら全部（1）、floor の (1 − band) 倍以下なら数えず（0）、
    その間は値段に比例させる。
    """
    if floor <= 0 or price >= floor:
        return 1.0
    return max(0.0, 1 - (floor - price) / (floor * band))


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
    前のターンの売れ残りを持ち越せずに指値なしで出して売れた分も、値段が1単位あたりの変動費を
    下回っていなければ、売れた量に数える（数えないと、売れ残りが翌ターンに捨て値で売れて新しく作った
    分が売れないように見え、魚の余った島でどの漁港も同時に減産し、売れ残りが尽きたところで食料が
    足りなくなった。変動費を大きく下回っても数えると、買い手の少ない魚を作り続けて投げ売りし、値段が
    初めの 1% まで下がった）。変動費を下回ったら、数える割合を、値幅（market.band）の分だけ
    下回るまでに比例して減らす（late_weight。少し下回っただけで全く数えなくなると、同じように漁港が
    同時に減産して飢饉になった）。
    投入財が手に入らずに（品不足・割高・資金不足）か、電力が足りずに止まった施設は、動けた割合に
    target_step を足した分までにする（動けないのに仕入れを増やし続けない）。1ターンに変えるのは
    全力の market.max_target_change まで（人の出入りと仕入れを急に変えない）。
    発電所は在庫を持たないので、生産の段で、送電網の買いたい量に合わせて決める（power_target）。
    """
    settings = ctx.config.get("market")
    step = round_money(settings["target_step"] * fac.FULL + 0.5)
    target = f.economy.get("target", fac.FULL)
    power = ctx.config.get("power.good")
    outputs = {g: q for g, q in fac.outputs_of(ctx.defs, f).items() if g != power and q}
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
        # 売れ残りを変動費より安く投げ売りした分は、下回った幅に応じて少なく数える
        weight = late_weight(prices.get(good, 0), floor, settings["band"])
        late_offered, late_sold = f.economy.get("late_sales", {}).get(good, [0, 0])
        offered += round_money(late_offered * weight)
        sold += round_money(late_sold * weight)
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
    if fac.short_of_inputs(f.status) or f.status in ("power", "no_grid"):  # 仕入れか電力が足りない
        plan = min(plan, f.operating_rate + step)
    limit = round_money(settings["max_target_change"] * fac.FULL + 0.5)
    return max(target - limit, min(target + limit, plan))


# --- 工事の資材の代金の取り置き ------------------------------------------------------

SITE_HOLDS = "site_holds"  # island.scratch: 雇用の段で取り置いた資材の代金の (支払い手, 額)


class SiteFunds:
    """工事が市場で資材に払う額の見込み。雇用の段で、優先度の高い工事から順に作業員の賃金と
    一緒に取り置く（優先度の低い工事が賃金で公共事業費を使い切り、優先度の高い工事が資材を
    買えなくならないように）。取り置いた分は、市場の段で工事の注文を出す前に戻す（_site_orders）。

    市場の段では、工事は国の倉庫の資材を先に使い、足りない分を値幅の上端の指値で買う。雇用の段
    ではまだ売りの注文がないので、手元の資材と雇う人手で進んだ後に要る次の1ターン分を、国の倉庫の
    在庫と前のターンに売りに出ていた量（どちらも見積もった工事の順に割り当てる）までで見積もる。
    """

    def __init__(self, island: IslandState, ctx: StageContext) -> None:
        self.island, self.ctx = island, ctx
        self.warehouse = stock.totals(island.warehouse)
        self.supply: dict[str, int] = {}  # 前のターンに施設が売りに出した量（生産の段で消す前）
        for f in island.facilities:
            for good, entry in f.economy.get("sales", {}).items():
                self.supply[good] = self.supply.get(good, 0) + entry[0]
        self.rates: dict[int, int] | None = None

    def cost(self, f: Facility, workers: int) -> int:
        """工事 f が workers 人で進んだ後、市場で資材に払う額の見込み（運賃を含む）。"""
        defs, config = self.ctx.defs, self.ctx.config
        cdef = defs.facilities[f.kind]["construction"]
        total = cdef["turns"] * fac.FULL
        rate = min(fac.ratio(workers, cdef.get("labor", 0)), fac.material_ratio(defs, f))
        after = min(f.progress + rate, total)
        width = to_basis(config.get("market.band"))
        amount = 0
        for good, need in sorted(fac.step_materials(defs, f, after).items()):
            qty = cdef["materials"][good]
            used = qty * after // total - qty * f.progress // total
            want = need - (stock.total(f.stock, good) - used)
            free = max(0, min(want, self.warehouse.get(good, 0)))
            if free:
                self.warehouse[good] -= free
            buy = min(want - free, self.supply.get(good, 0))
            if buy <= 0:
                continue
            self.supply[good] -= buy
            if self.rates is None:
                sites = [s for s in self.island.facilities if not s.built]
                self.rates = freight_rates.rates_of(self.island, defs, config, sites)
            _, high = auction.band(self.island.economy.prices.get(good, 1), width)
            amount += gross_cost(high, buy, self.rates.get(f.id, 0), 0)
        return amount


# --- 段 -------------------------------------------------------------------------


class Market(IslandStage):
    name = "market"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        defs, config = ctx.defs, ctx.config
        rates = freight_rates.facility_rates(island, defs, config)
        hh_rate = island.scratch["freight_household"]
        island.economy.freight["household"] = hh_rate  # 次のターンの公共事業の資材の見積もりに使う
        self._supply_sites_from_warehouse(island, ctx)
        books_by_good: dict[str, Book] = {}
        self._warehouse_orders(island, ctx, books_by_good)
        self._facility_orders(island, ctx, books_by_good, rates)
        offered = {  # 買い手は、いま売りに出ている量を見て注文する（ない物にお金を取り置かない）
            good: sum(order.quantity for order, _ in book.sells)
            for good, book in books_by_good.items()
        }
        self._site_orders(island, ctx, books_by_good, rates, offered, hh_rate)
        self._household_orders(island, ctx, books_by_good, hh_rate, offered)
        transport = self._transport_entity(island, ctx)
        routes = freight_rates.Routes(island, defs)
        bought: dict[str, int] = island.scratch.setdefault("household_bought", {})
        results: dict[str, auction.Result] = {}
        stale: dict[str, dict[int, int]] = {}  # 財 → 買い注文の番号 → 今夜捨てられる分を買った量
        width = to_basis(config.get("market.band"))
        for good in sorted(books_by_good):
            book = books_by_good[good]
            reference = island.economy.prices.get(good, 1)
            result = auction.clear(
                [o for o, _ in book.buys], [o for o, _ in book.sells], reference, width
            )
            self._late_to_eaters(book, result)
            results[good] = result
            stale[good] = self._settle(island, ctx, good, book, result, transport, bought, routes)
        self._record_shortages(books_by_good, results, stale)
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
                if not is_stopped(f):
                    f.economy["target"] = ctx.formula("operating_target")(island, f, ctx)

    # --- 注文 ---------------------------------------------------------------------

    @staticmethod
    def _add(books_by_good: dict[str, Book], good: str, side: str, order: Order, party: Party):
        book = books_by_good.setdefault(good, Book())
        (book.buys if side == "buy" else book.sells).append((order, party))

    @staticmethod
    def _sites(island: IslandState, ctx: StageContext) -> list[Facility]:
        """工事中の施設。資材とお金は優先度の高い工事から回す（同じ優先度は番号の順）。"""
        sites = [f for f in island.facilities if not f.built]
        sites.sort(key=lambda f: (-fac.priority_of(ctx.defs, ctx.config, f), f.id))
        return sites

    def _supply_sites_from_warehouse(self, island: IslandState, ctx: StageContext) -> None:
        """国の工事は、国の倉庫にある資材を先に使う（次の1ターン分まで）。民営の工事は市場で買う
        （国の倉庫の物は、政府が売りに出したときだけ買える）。インフラの維持は施設の工事より先に、
        新しいインフラの工事は後に使う。"""
        infrastructure.supply_from_warehouse(island, ctx, upkeep=True)
        for f in self._sites(island, ctx):
            if f.owner == OWNER_PRIVATE:
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
        infrastructure.supply_from_warehouse(island, ctx, upkeep=False)

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
            f.economy.pop("short", None)
            if recipe["inputs"]:  # 次のターンにどれだけ動くつもりで原料を買ったか（生産の段で使う）
                f.economy["input_plan"] = self._input_plan(ctx, f)
            if not needs:
                continue
            plan = {
                g: (q, net_limit(self._bid(ctx, f, g, prices, rate), rate, 0)) for g, q in needs
            }
            plan = self._afford(island, payer, plan, rate, 0)
            short = {g: "funds" for g, q in needs if plan.get(g, (0, 0))[0] < q}
            if short:  # 取り置けるお金が足りず、要る分を注文できなかった
                f.economy["short"] = short
            priority = fac.priority_of(defs, config, f)
            for good, (qty, limit) in sorted(plan.items()):
                party = Party(FACILITY, name, payer, rate, 0, f)
                party.public = is_public_service(defs, f)
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
        if is_public_service(ctx.defs, f):  # 学校と大学は値幅の上端まで買う
            return high + part(high, rate)
        wage = f.economy.get("wage", 0)
        limit = input_limit(ctx.defs, ctx.config, f, good, prices, wage, rate)
        if not is_self_supporting(f):
            limit = max(limit, price + part(price, rate))
        return min(high + part(high, rate), limit)

    @staticmethod
    def _input_plan(ctx: StageContext, f: Facility) -> int:
        """次のターンに動くつもりの稼働率（1000 分の 1）。原料はこの分を買う（_input_needs）。"""
        settings = ctx.config.get("market")
        target = f.economy.get("target", fac.FULL)
        if fac.is_halted(f, ctx.turn + 1):
            return 0
        if f.status == fac.STATUS_STARTING:
            return target
        if f.workers < f.economy.get("wanted", 0):
            headroom = round_money(settings["target_step"] * fac.FULL + 0.5)
            return min(target, fac.labor_ratio(ctx.defs, f) + headroom)
        change = round_money(settings["max_target_change"] * fac.FULL + 0.5)
        return min(fac.FULL, target + change)

    @staticmethod
    def _input_needs(ctx: StageContext, f: Facility, power: str) -> list[tuple[str, int]]:
        """次のターンに要る投入財のうち手元にない分。

        操業目標はこの段の終わりに見直すので、上がる分（market.max_target_change まで）も先に
        買っておく（目標を上げた次のターンに原料が足りず、人を雇えないまま目標を下げ直して、
        のこぎりの歯のように増えたり減ったりしないように）。雇いたいだけ雇えなかった施設は、
        今の人手より少し多い分（market.target_step）まで。学校と大学は market.public_stock_turns
        ターン分。
        """
        settings = ctx.config.get("market")
        expected = Market._input_plan(ctx, f)
        if is_public_service(ctx.defs, f):
            # 学校と大学は教材を数ターン分持っておく（消費財が1、2ターン買えないだけで休校に
            # ならないように）
            expected *= settings["public_stock_turns"]
        needs = []
        for good, qty in sorted(fac.recipe_of(ctx.defs, f)["inputs"].items()):
            if good == power:
                continue
            # このターンの終わりに捨てられる分は、次のターンに使えないので手元に数えない
            have = stock.total(f.stock, good) - stock.expiring(
                f.stock, good, ctx.turn, ctx.defs.goods
            )
            want = fac.scale(qty, expected) - have
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
        hh_rate: int,
    ) -> None:
        """工事の資材の注文（指値は値幅の上端）。公共事業費は優先度の高い工事から取り置く。
        雇用の段で取り置いておいた資材の代金（SiteFunds とインフラの見込み）は、ここで戻してから
        注文する。インフラの維持の資材は施設の工事より先に、新しいインフラの工事の資材は後に注文する。

        お金が足りずに要る分を注文できなかった工事は、ext["short_funds"] に残す（施設の状態に、
        資材を待っているわけとして出す）。

        同じ払い手（公共事業費など）の注文は、合わせて売りに出ている量までにする（工事ごとに売りに
        出ている量まで注文すると、買えない分にまでお金を取り置き、後の工事が「お金が足りない」に
        なる）。そのために注文しなかった分は scratch["unplaced"] に残して、値段の見直しでは買えな
        かった量に数える。
        """
        defs, config = ctx.defs, ctx.config
        for payer, amount in island.scratch.pop(SITE_HOLDS, []):
            funds.release(island, payer, amount)
        for amount in infrastructure.holds(island):
            funds.release(island, infrastructure.PAYER, amount)
        width = to_basis(config.get("market.band"))
        wanted = island.scratch.setdefault("wanted", {})  # 売りに出ていなくて買えなかった量
        claimed: dict[tuple[Payer, str], int] = {}  # 払い手ごとの注文した量
        self._works_orders(island, ctx, books_by_good, offered, hh_rate, claimed, upkeep=True)
        for f in self._sites(island, ctx):
            f.ext.pop("short_funds", None)
            rate = rates.get(f.id, 0)
            payer = payer_of(defs, config, f)
            plan = {}
            for good, qty in sorted(fac.step_materials(ctx.defs, f).items()):
                want = qty - stock.total(f.stock, good)
                if want > 0 and offered.get(good, 0) <= 0:
                    wanted[good] = wanted.get(good, 0) + want
                want = self._room(island, offered, claimed, payer, good, want)
                if want > 0:
                    _, high = auction.band(island.economy.prices.get(good, 1), width)
                    plan[good] = (want, high)
            priority = fac.priority_of(defs, config, f)
            afforded = self._afford(island, payer, plan, rate, 0)
            if any(afforded.get(g, (0, 0))[0] < q for g, (q, _) in plan.items()):
                f.ext["short_funds"] = True
            for good, (qty, limit) in sorted(afforded.items()):
                party = Party(SITE, facility_entity(f.id), payer, rate, 0, f)
                party.reserved = gross_cost(limit, qty, rate, 0)
                funds.commit(island, payer, party.reserved)
                self._add(books_by_good, good, "buy", Order(qty, limit, priority), party)
                claimed[(payer, good)] = claimed.get((payer, good), 0) + qty
        self._works_orders(island, ctx, books_by_good, offered, hh_rate, claimed, upkeep=False)

    @staticmethod
    def _room(
        island: IslandState,
        offered: dict[str, int],
        claimed: dict[tuple[Payer, str], int],
        payer: Payer,
        good: str,
        want: int,
    ) -> int:
        """payer が good をあといくら注文するか: 売りに出ている量から、同じ払い手がもう注文した量を
        引いた分まで。売りに出ている量までなら注文していたのに、同じ払い手の注文で売りに出ている量
        に届いたために注文しなかった分は scratch["unplaced"] に足す（値段の見直しの合図を、払い手
        ごとに分けなかったときと同じにする。売りに出ていない分は呼ぶ側が wanted に足す）。"""
        on_sale = offered.get(good, 0)
        room = max(0, on_sale - claimed.get((payer, good), 0))
        extra = min(want, on_sale) - min(want, room)
        if extra > 0:
            unplaced = island.scratch.setdefault("unplaced", {})
            unplaced[good] = unplaced.get(good, 0) + extra
        return min(want, room)

    def _works_orders(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        offered: dict[str, int],
        rate: int,
        claimed: dict[tuple[Payer, str], int],
        upkeep: bool,
    ) -> None:
        """インフラの維持（upkeep）か新しい工事の、次のターンの資材の注文（指値は値幅の上端、
        運賃の率は家計と同じ）。公共事業費の注文は、施設の工事と合わせて売りに出ている量まで。"""
        want = infrastructure.wants(island, ctx, upkeep)
        if not want:
            return
        width = to_basis(ctx.config.get("market.band"))
        wanted = island.scratch.setdefault("wanted", {})
        payer = infrastructure.PAYER
        plan = {}
        for good, qty in sorted(want.items()):
            if offered.get(good, 0) <= 0:
                wanted[good] = wanted.get(good, 0) + qty
            qty = self._room(island, offered, claimed, payer, good, qty)
            if qty > 0:
                _, high = auction.band(island.economy.prices.get(good, 1), width)
                plan[good] = (qty, high)
        key = "infrastructure.upkeep_priority" if upkeep else "infrastructure.build_priority"
        priority = ctx.config.get(key)
        for good, (qty, limit) in sorted(self._afford(island, payer, plan, rate, 0).items()):
            party = Party(WORKS, ledger.GOV, payer, rate, 0)
            party.reserved = gross_cost(limit, qty, rate, 0)
            funds.commit(island, payer, party.reserved)
            self._add(books_by_good, good, "buy", Order(qty, limit, priority), party)
            claimed[(payer, good)] = claimed.get((payer, good), 0) + qty

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
            low = settings["consumer_goods_low_tiers"]
            for good in goods:
                price = usual_price(island, good, settings["usual_price_turns"])
                now = prices.get(good, 0)
                for qty, limit in consumer_tiers(
                    spend // len(goods), price, tiers, rate, tax, now, low
                ):
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

    @classmethod
    def _record_shortages(
        cls,
        books_by_good: dict[str, Book],
        results: dict[str, auction.Result],
        stale: dict[str, dict[int, int]],
    ) -> None:
        """施設が投入財を注文した分だけ買えなかったわけを economy["short"] に残す（次のターンの
        生産の段で、止まった理由として出す）。買えたうち今夜捨てられる分（stale）は、使えないので
        買えなかった量に数える。

        指値が約定価格（売り買いがなければ気配）より安かったか、次のターンまで持つ物が指値より
        高い希望価格で売れ残っていたのは price（高くて採算が合わない）、そうでなければ supply
        （売りに出ている量が足りない）。"""
        for good, book in sorted(books_by_good.items()):
            result = results[good]
            dear = [
                auction.sell_limit(o)
                for i, (o, p) in enumerate(book.sells)
                if not cls._late(o, p) and o.quantity > result.sold[i]
            ]
            cheapest = min(dear) if dear else None
            for i, (order, party) in enumerate(book.buys):
                f = party.facility
                usable = result.bought[i] - stale.get(good, {}).get(i, 0)
                if party.kind != FACILITY or f is None or usable >= order.quantity:
                    continue
                limit = auction.buy_limit(order, result.high)
                expensive = (result.supply > 0 and limit < result.price) or (
                    cheapest is not None and limit < cheapest
                )
                f.economy.setdefault("short", {})[good] = "price" if expensive else "supply"

    # --- 約定 ---------------------------------------------------------------------

    @staticmethod
    def _stores(party: Party) -> bool:
        """買った物を在庫に置く買い手か（施設・工事・倉庫・住宅の資材）。家計はその場で食べる。"""
        return party.kind != HOUSEHOLD or party.layer == "housing"

    @staticmethod
    def _late(order: Order, party: Party) -> bool:
        """持ち越せずに指値なしで出した売り（このターンの終わりに捨てられる分）か。"""
        return party.facility is not None and order.limit is None

    @classmethod
    def _late_to_eaters(cls, book: Book, result: auction.Result) -> None:
        """このターンの終わりに捨てられる分の売りが、その場で食べる家計の買った量より多く売れたら、
        多い分を、約定価格で売れ残っていたほかの売りに回す（約定の量と値段は変わらない）。

        板寄せでは指値なしの売りが先に約定するので、家計の買う量より多く売りに出ていると、
        残りを工場が買い、使う前に捨てられていた（同じ値段で次のターンまで持つ物が売れ残って
        いても）。ほかの売りもなければそのまま工場が買い、捨てられた分は買えなかった量に数える
        （_record_shortages）。
        """
        late = [i for i, (o, p) in enumerate(book.sells) if cls._late(o, p) and result.sold[i]]
        if not late:
            return
        eaten = sum(result.bought[i] for i, (_, p) in enumerate(book.buys) if not cls._stores(p))
        excess = sum(result.sold[i] for i in late) - eaten
        spare = [
            (i, o.quantity - result.sold[i])
            for i, (o, p) in enumerate(book.sells)
            if not cls._late(o, p)
            and auction.sell_limit(o) <= result.price
            and o.quantity > result.sold[i]
        ]
        if excess <= 0 or not spare:
            return
        moved = proportional(min(excess, sum(q for _, q in spare)), spare)
        for i, qty in moved.items():
            result.sold[i] += qty
        total = sum(result.sold[i] for i in late)
        left = proportional(total - sum(moved.values()), [(i, result.sold[i]) for i in late])
        for i in late:
            result.sold[i] = left[i]

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
        routes: freight_rates.Routes,
    ) -> dict[int, int]:
        """約定を買い手と売り手の組ごとに払い、物を移す。在庫に置く買い手が今夜捨てられる分を
        買った量を、買い注文の番号ごとに返す（ほかに売りがなく、それしか買えなかったとき）。"""
        price = result.price
        carrier, carrier_id = transport
        life = ctx.defs.goods[good].get("shelf_life")
        expiry = None if life is None else ctx.turn - life  # この組まで、このターンの終わりに捨てる
        stale: dict[int, int] = {}
        for b, s, qty in self._pairs(island, book, result, routes):
            _, buyer = book.buys[b]
            sell, seller = book.sells[s]
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
            # 施設の売りは、注文ごとの組を渡す: 指値なしの売りは今夜捨てられる組（古い順）、
            # 指値のある売りはそれ以外の組。国の倉庫の売りは、在庫に置く買い手（施設・工事・倉庫）
            # には今夜捨てられる組を最後に回して渡す（家計はその場で食べるので古い順のまま）
            if seller.facility is not None:
                last = None if self._late(sell, seller) else expiry
            else:
                last = expiry if self._stores(buyer) else None
            batches = self._take_from_seller(island, good, seller, qty, last)
            if expiry is not None and self._stores(buyer):
                spoiling = sum(q for made, q in batches if made <= expiry)
                if spoiling:
                    stale[b] = stale.get(b, 0) + spoiling
            self._give_to_buyer(island, good, buyer, batches, amount, buyer_freight, tax, bought)
            if seller.facility is not None:
                books.add(island, seller.facility.id, books.SALES, amount)
                books.add(island, seller.facility.id, books.FREIGHT, seller_freight)
            else:
                books.add_sector(island, books.WAREHOUSE, "sales", amount)
            island.trades.append(TradeRecord(good, buyer.name, seller.name, qty, amount))
        return stale

    @staticmethod
    def _pairs(
        island: IslandState, book: Book, result: auction.Result, routes: freight_rates.Routes
    ) -> list[tuple[int, int, int]]:
        """約定を、道のりの近い買い手と売り手の組から順に割り当てる（運賃の率は注文のときに
        決めた見込みのままなので、払う額は変わらない。どこからどこへ運んだかの記録が変わる）。
        国の倉庫は首都にあり、家計には一番近い住んでいるマスへ運ぶ。

        持ち越せずに指値なしで出した売り（このターンの終わりに捨てられる分）は、その場で食べる
        家計に先に回し、在庫に置く買い手（施設・工事・倉庫）には、ほかの売りが尽きてから回す。
        在庫に置く買い手が買うと、使う前に捨てられるため。"""
        far = 10**6
        tier = 10**7  # 道のりより大きい。組の順を先に分ける

        def where(party: Party) -> tuple[int, int] | str | None:
            if party.facility is not None:
                return (party.facility.x, party.facility.y)
            return "homes" if party.kind == HOUSEHOLD else island.capital

        buyers = [where(p) for _, p in book.buys]
        sellers = [where(p) for _, p in book.sells]

        stores = [Market._stores(p) for _, p in book.buys]
        late = [Market._late(o, p) for o, p in book.sells]

        def distance(b: int, s: int) -> int:
            origin, goal = sellers[s], buyers[b]
            if stores[b]:
                rank = 2 if late[s] else 0
            else:
                rank = 0 if late[s] else 1
            if not isinstance(origin, tuple) or goal is None:
                return rank * tier + far
            d = routes.to_homes(origin) if goal == "homes" else routes.between(origin, goal)
            return rank * tier + (far if d is None else d)

        return auction.match_nearest(result.bought, result.sold, distance)

    @staticmethod
    def _take_from_seller(
        island: IslandState, good: str, seller: Party, qty: int, last_until: int | None = None
    ) -> list:
        if seller.facility is not None:
            batches = stock.take_batches(seller.facility.stock, good, qty, last_until)
            books.stock_moved(island, seller.facility.id, good, -qty)
            return batches
        return stock.take_batches(island.warehouse, good, qty, last_until)

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
        if buyer.kind == FACILITY and buyer.public:
            # 公務の施設（学校と大学）の教材は政府の消費（GDP の支出の側）。施設の在庫に数えない
            assert buyer.facility is not None
            stock.put_batches(buyer.facility.stock, good, batches)
            books.add_sector(island, books.GOVERNMENT, "goods", amount)
            books.add_sector(island, books.GOVERNMENT, "freight", freight)
        elif buyer.kind == FACILITY:
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
        elif buyer.kind == WORKS:
            infrastructure.received(island, good, batches)
            books.add_sector(island, CONSTRUCTION, "materials", amount)
            books.add_sector(island, CONSTRUCTION, "freight", freight)
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
                if f.built and not is_public_service(ctx.defs, f):
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
            demand += island.scratch.get("unplaced", {}).get(good, 0)
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
        """施設の売りの希望価格を見直す（買いの指値は注文のときに値幅と採算で決める。_bid）。

        値上げは、買えなかった買い手の一番高い指値まで（それより高く出すと誰も買わず、そのターンは
        1つも売れないまま次のターンに値下げし直すことになる。家計は消費財をいつもの値段の
        1.05 倍までしか買わない）。
        """
        update = ctx.formula("price_update")
        width = to_basis(ctx.config.get("market.band"))
        rates = freight_rates.facility_rates(island, ctx.defs, ctx.config)
        unplaced = island.scratch.get("unplaced", {})  # 売りに出ている量を超えて注文しなかった量
        for good in sorted(results):
            result = results[good]
            book = books_by_good[good]
            extra = unplaced.get(good, 0)
            demand = result.demand + extra
            shortage = (result.unfilled_demand + extra) / demand if demand else 0.0
            unfilled = [
                auction.buy_limit(order, result.high)
                for i, (order, _) in enumerate(book.buys)
                if result.bought[i] < order.quantity
            ]
            ceiling = max(unfilled) if unfilled else None
            low, high = auction.band(island.economy.prices.get(good, 1), width)
            # 売りに出した量、売れた量、希望価格が約定価格より高くて売れなかった量。持ち越せずに
            # 指値なしで出した分は、どれにも数えない（傷みかけの物を捨て値で出したことで希望価格を
            # 下げない。傷みかけの物しかなかった施設は、売る物がなかったのと同じに扱う）。その分の
            # 出した量と売れた量は late_sales に別に残し、操業目標の見込みにだけ足す
            sold: dict[int, list[int]] = {}
            late: dict[int, list[int]] = {}
            for i, (order, party) in enumerate(book.sells):
                if party.facility is None:
                    continue
                if order.limit is None:
                    entry = late.setdefault(party.facility.id, [0, 0])
                    entry[0] += order.quantity
                    entry[1] += result.sold[i]
                    continue
                entry = sold.setdefault(party.facility.id, [0, 0, 0])
                entry[0] += order.quantity
                entry[1] += result.sold[i]
                if order.limit > result.price:
                    entry[2] += order.quantity - result.sold[i]
            for fid, entry in sorted(late.items()):
                f = island.facility(fid)
                assert f is not None
                f.economy.setdefault("late_sales", {})[good] = entry
            for fid, entry in sorted(sold.items()):
                offered, done, _ = entry
                f = island.facility(fid)
                assert f is not None
                f.economy.setdefault("sales", {})[good] = entry
                asks = f.economy.setdefault("ask", {})
                before = asks.get(good, result.price)
                ask = update(before, offered, done, shortage, ctx)
                rate = rates.get(f.id, 0)
                if ceiling is not None and ask > before:
                    # 板寄せに出す指値（運賃を足した値段）が、買えなかった買い手の指値を超えない
                    ask = min(ask, max(before, ceiling * (BASIS - rate) // BASIS))
                ask = max(low, min(high, ask))
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
