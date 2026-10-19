"""市場の段: 財ごとに1回の板寄せで売り買いを決め、代金・運賃・消費税を払う（電力は生産の段）。

注文を出す者:
- 施設（独立採算と国庫直轄）: 産出物の在庫を希望価格で売る（持ち越せる期間が今のターンで切れる物は
  指値なし）。次のターンの操業目標に要る投入財のうち手元にない分を、希望価格で買う（変動費の利益が
  0になる値段まで。品切れが続いても、払えない値段まで買い上がらない）。
- 国の倉庫: 命令 warehouse_trade で決めた量を、指値で売る・買う。船で国の倉庫に届いた腐る物（契約で
  買った食品など。trade.to_market）は、売り切れるか傷んで捨てられるまで、毎ターン指値なしで売る。
  契約で「市場に出す」と決めた腐らない物は、売り切れるまで毎ターン島の値段（基準の値段）を指値に売る。
- 工事中の施設（国の工事）: 次の1ターン分の資材のうち、国の倉庫にもない分を、いま売りに
  出ている量まで買う（指値は値幅の上端）。
- インフラの公共事業（フェーズ2-3。modules/infrastructure）: 次のターンの維持と工事に要る資材の
  うち、国の倉庫にもない分を同じように買う（運賃の率は家計と同じ）。維持の分は施設の工事より先に、
  新しい工事の分は後に注文する。
- 家計の層: 食料、消費財、サービス、町の資材（economy/households.py と、この段の
  _household_orders）。食料は、いま売りに出ている量を見て財ごとに割り振る。
- 商業・サービス業（バランス調整。economy/services.py）: 段の初めに、雇った人が出せるサービスを
  在庫に置き、希望価格を指値に売る（計画経済は公定価格）。売れ残りは段の終わりに消える（傷んだ物の
  知らせは出さない）。段の終わりに、売上から次のターンに雇う人数を決める。
- 貿易（フェーズ3-1。_trade_orders）: 商社が輸出する物を買い、輸入した物を売る
  （hakoniwa_core/trade/traders.py。島の中の運賃は港のマスから）。海運業が船の燃料と、船が足りない
  ときに輸送船を買う。二国間契約で売る国の政府が、国の倉庫に足りない分を優先して買う。海外の商人が
  決まった値段で売る（hakoniwa_core/trade/merchant.py。代金は島の外に出て消え、輸入に数える）。

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

計画経済の島（フェーズ3-4。hakoniwa_core/economy/plan.py）では、段の初めに公定価格を基準の値段にし、
財ごとの板寄せを公定価格で値幅 0 にします。施設は費用に届かなくても公定価格で売り、投入財はいつも
買いに出ます。買いが余れば、重点産業の施設 → ほかの国営施設と国の工事 → 家計（1人あたり同じ量まで
の配給）→ 輸出（商社と二国間契約）の順に配ります（_limits）。段の終わりに需要と行列と強制貯蓄を
残し（_plan_record）、国営施設の操業目標を生産目標から決めます。混合経済で値段の上限（price_cap）
か下限（price_floor）を決めた財は、値幅をそこで止めます。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, politics, stock, trade
from hakoniwa_core.allocation import proportional
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    CONSTRUCTION,
    GENERAL,
    WAREHOUSE,
    Payer,
    books,
    companies,
    entity_of,
    equipment,
    facility_entity,
    funds,
    household,
    is_public_service,
    is_self_supporting,
    is_stopped,
    is_transport,
    needs,
    payer_of,
    productivity,
    retail,
    services,
    trucks,
)
from hakoniwa_core.economy import freight as freight_rates
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.economy.households import SUPPORT, consumption
from hakoniwa_core.economy.prices import (
    input_limit,
    material_limit,
    unit_floor,
    usual_price,
    variable_profit,
)
from hakoniwa_core.economy.start import next_payments
from hakoniwa_core.economy.stats import inflation
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules import infrastructure
from hakoniwa_core.modules.population import FED, FOOD_ORDERS, HUNGER, layer_food_needs
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import (
    OWNER_PRIVATE,
    Facility,
    IslandState,
    LogEntry,
    MarketRecord,
    TradeRecord,
)
from hakoniwa_core.trade import balance, merchant, ships, traders
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

ANCHOR = "anchor"  # 家計の層の状態: 飢える前の食料の値段（値幅の上端。回3）

# 注文を出す者の種類
FACILITY = "facility"
SITE = "site"
WORKS = "works"  # インフラの公共事業
HOUSEHOLD = "household"
STORE = "warehouse"
REBUILD = "rebuild"  # 首都の建物の建て替えの資材（フェーズ3-4。modules/politics）
REPAIR = "repair"  # 傷んだ設備を直す資材（回6。economy/equipment.py）
MERCHANT = "merchant"  # 海外の商人（フェーズ3-1）
TRADER_PRIORITY = 0  # 商社の輸出の買い注文の優先順位（値幅の端で止まったときは島の買い手が先）
CONTRACT_PRIORITY = 9  # 契約で売る分を政府が買う注文の優先順位
GOV_TRADE = "trade"  # 政府が貿易に払う項目（どの項目にも取り置いていない国庫から）
MERCHANT_GAP = Payer(ledger.GOV, GOV_TRADE)  # 計画経済の島で、海外の商人の値段と公定価格の差を払う
CLEARING = "market_clearing"  # island.scratch: 板寄せの払いに使う（運送業, 道のり）
IMPORTED = "imported"  # 国の倉庫に届いた腐る物の売り（Party.layer）


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
    site: tuple[int, int] | None = None  # 物の出入りのマス（商社と海運業は港。運ぶ道のりに使う）
    gap: int = 0  # 海外の商人の売り: 計画経済の島で、政府が1単位ごとに払う公定価格との差
    hungry: bool = (
        False  # 前のターンに食べきれなかった家計の食料の買い（値幅の上を買い上がる。回3）
    )


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
    売りに出ている財のうち一番安い財に足す（売りに出ていない財の値段は前のままで、ほかの食料が
    値上がりすると一番安く見え、ない物を注文して食べられなくなる。回3）。
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
        open_goods = [g for g in qualities if caps.get(g, 0) > 0] or list(qualities)
        got[min(open_goods, key=lambda g: (values[g], g))] += left
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


@dataclass
class FoodMenu:
    """食料の注文を組むのに使う、財ごとの数（島で1ターンに1つ。家計の層によらない）。"""

    values: dict[str, float]  # 質を足した栄養あたりの値段
    nutritions: dict[str, int]  # 1単位の栄養（1000 分の 1）
    qualities: dict[str, float]
    unit: dict[str, int]  # 1単位の見込みの値段（運賃と税を含む）
    limits: dict[str, int]  # 指値（値幅の上端）
    rate: int  # 運賃の率
    tax: int  # 消費税の率
    elasticity: float


def food_orders(
    need: int, caps: dict[str, int], menu: FoodMenu, food_budget: int, budget: int
) -> dict[str, tuple[int, int]]:
    """食料の要る量 need（栄養）を、いま売りに出ている量 caps で財ごとに割り振った注文
    （量, 指値）。

    安い組み合わせ（food_split）から、必需品の上限 food_budget に収まる分だけ質の高い組み合わせ
    （food_by_quality）へ寄せ（upgrade_food）、使う額 budget を超える分は、どの財も同じ割合で
    減らす。
    """
    cheap = food_split(need, menu.values, menu.nutritions, caps, menu.elasticity)
    best = food_by_quality(need, menu.qualities, menu.values, menu.nutritions, caps)
    amounts = upgrade_food(cheap, best, menu.unit, food_budget)
    plan = {g: (q, menu.limits[g]) for g, q in sorted(amounts.items())}
    cost = sum(gross_cost(lim, q, menu.rate, menu.tax) for q, lim in plan.values())
    if cost > budget and cost > 0:
        plan = {g: (q * budget // cost, lim) for g, (q, lim) in plan.items()}
    return plan


def hungry_food_orders(
    need: int,
    shares: dict[str, int],
    offered: dict[str, int],
    menu: FoodMenu,
    anchors: dict[str, int],
    usual: int,
    budget: int,
    steps: list[float],
    elasticity: float,
) -> list[tuple[str, int, int]]:
    """飢えている家計の食料の注文（財, 量, 指値）の並び（回3）。急ぐ段から並べる。

    まず要る量 need を、ふだんと同じく人数の割合の分（shares）で一番安い組み合わせ（food_split）
    にし、足りない分は、売りに出ている量（offered）の残りの多い財から足す（ほかの層の分にも手を
    伸ばす。足す先を主な食料に絞り、売りに出た量の少ない財の値段だけが跳ね上がらないように）。
    それを要る量の累積の割合 steps の段に分け、段 j（要る量の F_j まで食べられる）の指値を、飢える
    前の値段（anchors。飢えていないターンの値幅の上端）の F_j ^ (−1/elasticity) 倍にする（食料の
    需要は値段にあまり反応しないので、少ししか食べられない段ほど高くても買う。基準を今の値段に
    すると、上がった値段が基準になって上がり続ける）。ふだんの食費 usual で今の値段の値幅の上端
    まで買える段は、飢えていないときと同じく上端より安くはしない。どの段も、前の段までの取り置きと
    合わせて食費の限り budget（預金を崩す分を含む）に収まる分までにする（お金のない家計から買えなく
    なり、値段は買える人の分まで上がって止まる）。
    """
    if need <= 0 or budget <= 0:
        return []

    def nutrition(basket: dict[str, int]) -> int:
        return sum(q * menu.nutritions[g] // QUANTITY_SCALE for g, q in basket.items())

    split = food_split(need, menu.values, menu.nutritions, shares, menu.elasticity)
    basket = {g: min(q, shares.get(g, 0)) for g, q in split.items()}
    short = need - nutrition(basket)
    room = {g: max(0, offered.get(g, 0) - basket.get(g, 0)) for g in menu.values}
    for good in sorted(room, key=lambda g: (-room[g] * menu.nutritions[g], g)):
        if short <= 0 or room[good] <= 0:
            break
        qty = min(room[good], -(-short * QUANTITY_SCALE // menu.nutritions[good]))
        basket[good] = basket.get(good, 0) + qty
        short -= qty * menu.nutritions[good] // QUANTITY_SCALE
    basket = {g: q for g, q in sorted(basket.items()) if q > 0}
    whole = sum(gross_cost(menu.limits[g], q, menu.rate, menu.tax) for g, q in basket.items())
    if nutrition(basket) <= 0 or whole <= 0:
        return []
    orders: list[tuple[str, int, int]] = []
    done, reserved = 0.0, 0
    for upto in steps:
        portion = upto - done
        done = upto
        if portion <= 0:
            continue
        scale = upto ** (-1 / elasticity)
        floor = upto * whole <= usual  # ふだんの食費で今の値幅の上端まで買える段
        limits = {}
        for g in basket:
            limit = scale_price(anchors.get(g, menu.limits[g]), scale)
            limits[g] = max(limit, menu.limits[g]) if floor else limit
        cost = sum(gross_cost(limits[g], q, menu.rate, menu.tax) for g, q in basket.items())
        fit = min(portion, (budget - reserved) / cost) if cost > 0 else 0.0
        if fit <= 0:
            break
        for good, qty in basket.items():
            amount = round_money(qty * fit)
            if amount > 0:
                orders.append((good, amount, limits[good]))
                reserved += gross_cost(limits[good], amount, menu.rate, menu.tax)
        if fit < portion:
            break
    return orders


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
        total = fac.total_progress(defs, f)
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
            limit = material_limit(self.island, config, good, width)
            amount += gross_cost(limit, buy, self.rates.get(f.id, 0), 0)
        return amount

    def repairs(self) -> int:
        """国営の施設の傷んだ設備を直す資材に、市場で払う額の見込み（回6。国の倉庫の分と、前の
        ターンに売りに出ていた量を超える分は除く。運賃は見込まない）。"""
        island, defs, config = self.island, self.ctx.defs, self.ctx.config
        width = to_basis(config.get("market.band"))
        amount = 0
        for f, _, _ in Market._repairs(island, self.ctx):
            if f.owner == OWNER_PRIVATE:
                continue
            for good, want in equipment.wants(defs, config, f).items():
                free = max(0, min(want, self.warehouse.get(good, 0)))
                if free:
                    self.warehouse[good] -= free
                buy = min(want - free, self.supply.get(good, 0))
                if buy <= 0:
                    continue
                self.supply[good] -= buy
                amount += gross_cost(material_limit(island, config, good, width), buy, 0, 0)
        return amount


# --- 段 -------------------------------------------------------------------------


class Market(IslandStage):
    name = "market"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        if not island.has_economy:
            return
        defs, config = ctx.defs, ctx.config
        # 計画経済の公定価格（フェーズ3-4）: 見直しのターンなら解き直し、基準の値段にする
        official = planning.prepare(island, defs, config, ctx.turn)
        retail.prepare(island, config)
        island.scratch[retail.UNRETAILED] = (services.good_of(config), retail.good_of(config))
        rates = freight_rates.facility_rates(island, defs, config)
        hh_rate = island.scratch["freight_household"]
        island.economy.freight["household"] = hh_rate  # 次のターンの公共事業の資材の見積もりに使う
        self._switch_recipes(island, ctx)
        self._supply_sites_from_warehouse(island, ctx)
        books_by_good: dict[str, Book] = {}
        self._warehouse_orders(island, ctx, books_by_good)
        self._facility_orders(island, ctx, books_by_good, rates)
        self._services_orders(island, ctx, books_by_good, official)
        self._trade_orders(island, ctx, books_by_good, rates, hh_rate)
        offered = {  # 買い手は、いま売りに出ている量を見て注文する（ない物にお金を取り置かない）
            good: sum(order.quantity for order, _ in book.sells)
            for good, book in books_by_good.items()
        }
        self._site_orders(island, ctx, books_by_good, rates, offered, hh_rate)
        self._transport_orders(island, ctx, books_by_good, hh_rate, offered)
        self._household_orders(island, ctx, books_by_good, hh_rate, offered)
        transport = self._transport_entity(island, ctx)
        routes = freight_rates.Routes(island, defs)
        island.scratch[CLEARING] = (transport, routes)
        bought: dict[str, int] = island.scratch.setdefault("household_bought", {})
        results: dict[str, auction.Result] = {}
        stale: dict[str, dict[int, int]] = {}  # 財 → 買い注文の番号 → 今夜捨てられる分を買った量
        persons = {n: h["persons"] for n, h in island.economy.households.items()}
        shop_good = retail.good_of(config)
        for good in sorted(books_by_good):
            if good == shop_good:  # 小売りは品物の市場の後に（回7）
                continue
            self._clear(island, ctx, good, books_by_good, official, persons, results, stale)
        self._retail(island, ctx, books_by_good, official, persons, results, stale)
        self._record_shortages(books_by_good, results, stale)
        for f, good, qty, cost in island.scratch.pop(REPAIR, []):  # 直す資材を設備に入れる（回6）
            equipment.install(defs, f, good, qty, cost)
        self._trade_settled(island, ctx, rates)
        for _, book in sorted(books_by_good.items()):
            for _, party in book.buys:
                funds.release(island, party.payer, party.reserved)
                party.reserved = 0
            for _, party in book.sells:  # 海外の商人の売れ残りの分の、政府が払う差
                funds.release(island, MERCHANT_GAP, party.reserved)
                party.reserved = 0
        needs.close_cars(island, config)
        sold = self._imports_sold(books_by_good, results)
        self._spoil(island, ctx)
        self._imports_left(island, sold)
        self._record(island, ctx, books_by_good, results, bought)
        self._update_prices(island, ctx, books_by_good, results)
        power = config.get("power.good")
        if official:
            self._plan_record(island, ctx, books_by_good, official, bought)
        for f in island.facilities:
            outputs = fac.recipe_of(defs, f)["outputs"]
            if f.built and "target" in f.economy and any(g != power for g in outputs):
                if not is_stopped(f):
                    # 計画経済の国営施設は、採算ではなく生産目標に合わせて動く（フェーズ3-4）
                    goal = planning.target(island, f, defs, config) if official else None
                    if goal is None:
                        goal = ctx.formula("operating_target")(island, f, ctx)
                    f.economy["target"] = goal
        services.plan(
            island,
            config,
            planning.is_planned(island, defs),
            self._services_unmet(island, config, books_by_good, results),
        )

    def _clear(
        self,
        island: IslandState,
        ctx: StageContext,
        good: str,
        books_by_good: dict[str, Book],
        official: dict[str, int],
        persons: dict[str, int],
        results: dict[str, auction.Result],
        stale: dict[str, dict[int, int]],
    ) -> None:
        """財 good の板寄せをして、約定を払い物を移す。"""
        book = books_by_good[good]
        reference = island.economy.prices.get(good, 1)
        width = to_basis(ctx.config.get("market.band"))
        bounds, allocate = self._limits(island, ctx, good, book, official, persons)
        result = auction.clear(
            [o for o, _ in book.buys],
            [o for o, _ in book.sells],
            reference,
            width,
            bounds=bounds,
            allocate_buys=allocate,
        )
        self._late_to_eaters(book, result)
        results[good] = result
        transport, routes = island.scratch[CLEARING]
        bought: dict[str, int] = island.scratch.setdefault("household_bought", {})
        stale[good] = self._settle(island, ctx, good, book, result, transport, bought, routes)

    # --- 小売り（回7。economy/retail.py） ------------------------------------------------------

    @staticmethod
    def _retail_keep(
        island: IslandState, ctx: StageContext, layer: str, planned: int, tax: int
    ) -> int:
        """家計の層の使う額 planned のうち、品物に付く小売りの代金に取り分ける額。前のターンまでに
        小売りに払った額の割合（層の状態 retail_share）で、なければ値段から見込む。"""
        config = ctx.config
        good = retail.good_of(config)
        if good not in ctx.defs.goods:
            return 0
        island.scratch.setdefault(retail.PLANNED, {})[layer] = planned
        share = island.economy.households[layer].get(retail.SHARE)
        if share is not None:
            return part(planned, share)
        price = usual_price(island, good, config.get("household.usual_price_turns"))
        rate = retail.margin(island, config, price or island.economy.prices.get(good, 0), tax)
        return planned - planned * BASIS // (BASIS + rate) if rate > 0 else 0

    def _retail(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        official: dict[str, int],
        persons: dict[str, int],
        results: dict[str, auction.Result],
        stale: dict[str, dict[int, int]],
    ) -> None:
        """品物の市場の後に、家計の層が買った品物の代金に見合う小売りを、商店と百貨店から買う。
        店に届くお客の割合（retail.reach）で、店ごとに売れる量に上限を付ける。店で買いきれなかった
        分は、商業・サービス業（小さな店と屋台）から小売りの値段の 1 + retail.direct_premium 倍で
        買う（直売・屋台）。"""
        defs, config = ctx.defs, ctx.config
        good = retail.good_of(config)
        if good not in defs.goods:
            return
        tax = island.policy.consumption_tax
        width = to_basis(config.get("market.band"))
        price = island.economy.prices.get(good, 1)
        _, high = auction.band(price, width)
        # 店には、直売・屋台より高くは払わない
        high = min(high, self._direct_unit(island, config))
        priority = config.get("market.household_priority")
        spent = island.scratch.pop(retail.SPENT, {})
        reserves = island.scratch.pop(retail.RESERVE, {})
        wanted: dict[str, int] = {}
        for layer in sorted(island.economy.households):
            funds.release(island, Payer(household(layer)), reserves.get(layer, 0))
            qty = retail.units_for(island, config, spent.get(layer, 0))
            if qty > 0:
                wanted[layer] = qty
        total = sum(wanted.values())
        island.economy.finance[retail.DEMAND] = total
        island.scratch[retail.NEED] = wanted
        book = books_by_good.get(good)
        if book is not None and book.sells:
            capped = []
            for order, party in book.sells:
                f = party.facility
                if f is not None and not services.is_services(config, f):
                    cap = int(total * retail.reach(island, defs, config, f))
                    order = Order(min(order.quantity, cap), order.limit, order.priority)
                capped.append((order, party))
            book.sells[:] = [(o, p) for o, p in capped if o.quantity > 0]
        if (book is None or not book.sells) and total > 0:
            # 売りに出ていなければ、買いたい量を記録する（店が開く合図。_record）
            record = island.scratch.setdefault("wanted", {})
            record[good] = record.get(good, 0) + total
        if book is not None and book.sells:
            for layer, qty in sorted(wanted.items()):
                self._household_buy(island, books_by_good, good, qty, high, 0, tax, layer, priority)
            if book.buys:
                self._clear(island, ctx, good, books_by_good, official, persons, results, stale)
            for _, party in book.buys:  # 買えなかった分の取り置きは、直売・屋台に回す
                funds.release(island, party.payer, party.reserved)
                party.reserved = 0
        got = island.scratch.get("household_goods", {})
        island.stats["retail.need"] = total / QUANTITY_SCALE
        sold = results[good].volume if good in results else 0
        island.stats["retail.sold"] = sold / QUANTITY_SCALE
        self._direct(island, ctx, wanted, got)
        # 小売りに払った額の、使う額に対する割合を、次のターンの取り分けに使う（半分ずつ近づける）
        paid = island.scratch.pop(retail.PAID, {})
        for layer, planned in island.scratch.pop(retail.PLANNED, {}).items():
            state = island.economy.households.get(layer)
            if state is None or planned <= 0:
                continue
            target = min(BASIS // 2, paid.get(layer, 0) * BASIS // planned)
            before = state.get(retail.SHARE, target)
            state[retail.SHARE] = before + (target - before) // 2

    @staticmethod
    def _direct_unit(island: IslandState, config: GameConfig) -> int:
        """直売・屋台の小売り1単位の値段: 小さな店と屋台の1人の1ターンの働き（サービスと同じ）の
        値段の 1 + retail.direct_premium 倍。"""
        price = island.economy.prices.get(services.good_of(config), 1)
        premium = to_basis(retail.settings(config)["direct_premium"])
        return max(1, price + part(price, premium))

    @staticmethod
    def _direct(
        island: IslandState,
        ctx: StageContext,
        wanted: dict[str, int],
        got: dict[str, dict[str, int]],
    ) -> None:
        """店で買いきれなかった小売りを、商業・サービス業から割高に買う（直売・屋台）。"""
        config = ctx.config
        good = retail.good_of(config)
        shop = services.facility_of(island, config)
        if shop is None or not shop.built or "foreclosed" in shop.economy:
            return
        unit = Market._direct_unit(island, config)
        tax = island.policy.consumption_tax
        seller = entity_of(config, shop)
        qty_total = paid_total = 0
        for layer, qty in sorted(wanted.items()):
            short = qty - got.get(layer, {}).get(good, 0)
            if short <= 0:
                continue
            payer = Payer(household(layer))
            have = funds.available(island, payer)
            amount = money_of(unit, short)
            if gross_cost(unit, short, 0, tax) > have:
                short = qty_for(have * BASIS // (BASIS + tax), unit)
                amount = money_of(unit, short)
            if short <= 0 or amount <= 0:
                continue
            levy = part(amount, tax)
            paid = island.scratch.setdefault(retail.PAID, {})
            paid[layer] = paid.get(layer, 0) + amount + levy
            funds.pay(island, payer, seller, amount, "sale")
            funds.pay(island, payer, ledger.GOV, levy, "tax_consumption")
            books.add(island, shop.id, books.SALES, amount)
            books.add_sector(island, books.HOUSEHOLDS, "goods", amount)
            books.add_sector(island, books.HOUSEHOLDS, "tax", levy)
            qty_total += short
            paid_total += amount
        island.scratch[retail.DIRECT] = (qty_total, paid_total)
        stats = island.stats
        stats["retail.direct"] = qty_total / QUANTITY_SCALE
        stats["retail.direct_sales"] = paid_total
        # 直売・屋台が売った小売りも作った量（実質の生産と、関門の「作った島」に数える）
        if qty_total > 0:
            key = f"production.{good}"
            stats[key] = stats.get(key, 0) + qty_total / QUANTITY_SCALE

    # --- 公定価格と値段の規制（フェーズ3-4） ------------------------------------------------

    @staticmethod
    def _limits(
        island: IslandState,
        ctx: StageContext,
        good: str,
        book: Book,
        official: dict[str, int],
        persons: dict[str, int],
    ) -> tuple[tuple[int, int] | None, auction.Allocate | None]:
        """板寄せの値幅と、買いの割当ルール。計画経済の財は公定価格で値幅 0 にし、割当の順位と
        配給で分ける。混合経済で上限（price_cap）か下限（price_floor）を決めた財は、値幅をそこで
        止める。前のターンに食べきれなかった家計が値幅の上端より高く買う食料は、上端をその指値まで
        広げる（回3。上限を決めた財は広げない）。ほかは今のまま（None, None）。"""
        price = official.get(good)
        if price:
            tiers: list[int] = []
            layers: list[str | None] = []
            traders_ = {f.id for f in trade.traders_of(island, ctx.config)}
            for order, party in book.buys:
                tier, layer = planning.STATE, None
                f = party.facility
                if party.kind == HOUSEHOLD and party.layer != "housing":
                    tier, layer = planning.HOUSEHOLDS, party.layer
                elif party.kind == STORE and order.priority == CONTRACT_PRIORITY:
                    tier = planning.EXPORTS  # 二国間契約で売る分
                elif f is not None and f.id in traders_:
                    tier = planning.EXPORTS  # 商社の輸出
                elif f is not None and planning.is_priority(island, ctx.defs, f):
                    tier = planning.PRIORITY
                tiers.append(tier)
                layers.append(layer)
            return (price, price), planning.allocator(tiers, layers, persons)
        policy = island.policy
        cap, floor = policy.price_cap.get(good), policy.price_floor.get(good)
        controlled = politics.trait(island, ctx.defs, "price_controls", False)
        width = to_basis(ctx.config.get("market.band"))
        low, high = auction.band(island.economy.prices.get(good, 1), width)
        # 前のターンに食べきれなかった家計の買いは、値幅の上端を超えて買い上がる（回3。上限を
        # 決めた財は上限で止まる）
        bids = [order.limit for order, party in book.buys if party.hungry and order.limit]
        top = max(bids) if bids else 0
        if not ((cap or floor) and controlled):
            return ((low, top), None) if top > high else (None, None)
        if cap:
            high = max(1, min(high, cap))
            low = min(low, high)
        else:
            high = max(high, top)
        if floor:
            low = max(low, floor)
            high = max(high, low)
        return (low, high), None

    @staticmethod
    def _plan_record(
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        official: dict[str, int],
        bought: dict[str, int],
    ) -> None:
        """公定価格を基準の値段に戻し、需要（買えなかった分も含む）・生産目標・行列・強制貯蓄を残す。"""
        planning.apply_prices(island)
        power = ctx.config.get("power.good")
        records = {m.good: m for m in island.markets}
        demand = {
            g: m.demand for g, m in records.items() if g in official and g != power and m.demand
        }
        unplaced = island.scratch.get("unplaced", {})
        desire = island.scratch.get(planning.DESIRE, {})
        household_: dict[str, tuple[int, int]] = {}
        for good in sorted(set(books_by_good) | set(desire)):
            if good not in official or good == power:
                continue
            book = books_by_good.get(good)
            ordered = 0
            if (
                book is not None
            ):  # 公定価格より安い指値の段（いつもの値段より安ければ多く買う分）は数えない
                ordered = sum(
                    o.quantity
                    for o, p in book.buys
                    if p.kind == HOUSEHOLD
                    and p.layer != "housing"
                    and auction.buy_limit(o, official[good]) >= official[good]
                )
            want = max(ordered + unplaced.get(good, 0), desire.get(good, 0))
            if want > 0:
                household_[good] = (want, bought.get(good, 0))
                # 家計が買いたかったのに売りに出ていなかった分も、需要に数える
                demand[good] = max(demand.get(good, 0), want)
        planning.record(island, ctx.config, demand, household_)
        planning.pay_subsidies(island, ctx.config)  # 据え置いた財の補助金（電力の分も）

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

    @staticmethod
    def _repairs(
        island: IslandState, ctx: StageContext
    ) -> list[tuple[Facility, int | None, dict[str, int]]]:
        """傷んだ設備を直す施設（回6）と、民営なら全力の変動費の利益と比べに使った値段。国営は採算を
        比べず、民営は直す採算が合うときだけ（economy/equipment.py の worthwhile）。差し押さえ中・
        閉鎖中は直さない。番号の順。"""
        from hakoniwa_core.modules.firms import average_prices  # firms は market を読むので、ここで

        defs, config = ctx.defs, ctx.config
        prices: dict[str, int] = {}
        rates = freight_rates.facility_rates(island, defs, config)
        found: list[tuple[Facility, int | None, dict[str, int]]] = []
        for f in sorted(island.facilities, key=lambda f: f.id):
            if not equipment.has_equipment(defs, config, f) or is_stopped(f):
                continue
            if equipment.condition(f) >= equipment.level(config, f):
                continue
            profit = None
            if f.owner == OWNER_PRIVATE:
                if not prices:  # 直近の値段の平均で比べる（一時の安値で直すのをやめない）
                    prices = average_prices(island, config.get("firms.price_turns"))
                wage = f.economy.get("wage", 0)
                profit = variable_profit(defs, config, f, prices, wage, rates.get(f.id, 0))
                if not equipment.worthwhile(defs, config, f, profit, prices):
                    continue
            found.append((f, profit, prices))
        return found

    @staticmethod
    def _repair_limit(
        island: IslandState,
        ctx: StageContext,
        f: Facility,
        goods: list[str],
        profit: int | None,
        prices: dict[str, int],
        width: int,
        books_by_good: dict[str, Book],
    ) -> dict[str, int]:
        """直す資材の指値（回6）。ふだんは工事の資材と同じ（material_limit。高ければ待つ）。島の
        売り手がいなく海外の商人しか売っていない資材は、待っても安くならない（去る前に値上げし、
        去れば買えなくなる）ので、値幅の上端まで払う（「いつもの値段」の指値のまま1年以上直せず、
        売り手も買い手もいるのに取引が止まった）。民営は、その値段でも直す採算が合うときだけ。"""
        config = ctx.config
        limits = {g: material_limit(island, config, g, width) for g in goods}
        imported = [
            g
            for g in goods
            if g in books_by_good
            and books_by_good[g].sells
            and all(party.kind == MERCHANT for _, party in books_by_good[g].sells)
        ]
        if not imported:
            return limits
        high = {g: auction.band(island.economy.prices.get(g, 1), width)[1] for g in imported}
        if profit is not None:
            dear = dict(prices) | high
            if not equipment.worthwhile(ctx.defs, config, f, profit, dear):
                return limits
        return limits | high

    def _repair_orders(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        rates: dict[int, int],
        offered: dict[str, int],
        claimed: dict[tuple[Payer, str], int],
    ) -> None:
        """傷んだ設備を直す資材の注文（回6。指値は _repair_limit）。国営の施設は公共事業費
        から（インフラの維持の後、新しい工事の前に取り置く）、民営は施設の預金から。"""
        defs, config = ctx.defs, ctx.config
        width = to_basis(config.get("market.band"))
        wanted = island.scratch.setdefault("wanted", {})
        for f, profit, prices in self._repairs(island, ctx):
            state = f.owner != OWNER_PRIVATE
            payer = Payer(ledger.GOV, CONSTRUCTION) if state else payer_of(defs, config, f)
            rate = rates.get(f.id, 0)
            full = equipment.wants(defs, config, f)
            for good, want in full.items():
                if offered.get(good, 0) <= 0:
                    wanted[good] = wanted.get(good, 0) + want
            # 一番足りない資材で届く割合までにする（ほかの資材だけ買っても直らない）
            room = {g: max(0, offered.get(g, 0) - claimed.get((payer, g), 0)) for g in full}
            plan = {}
            limits = self._repair_limit(
                island, ctx, f, sorted(full), profit, prices, width, books_by_good
            )
            for good, want in equipment.wants(defs, config, f, room).items():
                want = self._room(island, offered, claimed, payer, good, want)
                if want > 0:
                    plan[good] = (want, limits[good])
            if not state:
                # 民営は、次のターンの支払いの見込みを残したお金で直す（直して返済が遅れない）。
                # 企業の施設は、足りない分を企業の預金から回す（回7）
                cost = sum(gross_cost(lim, q, rate, 0) for q, lim in plan.values())
                need = cost + next_payments(island, defs, config, f)
                companies.draw(island, f, need - funds.available(island, payer))
                spare = funds.available(island, payer) - next_payments(island, defs, config, f)
                plan = self._afford_within(plan, max(0, spare), rate)
            if state:
                priority = config.get("equipment.state_priority")
            else:
                priority = fac.priority_of(defs, config, f)
            for good, (qty, limit) in sorted(self._afford(island, payer, plan, rate, 0).items()):
                party = Party(REPAIR, facility_entity(f.id), payer, rate, 0, f)
                party.reserved = gross_cost(limit, qty, rate, 0)
                funds.commit(island, payer, party.reserved)
                self._add(books_by_good, good, "buy", Order(qty, limit, priority), party)
                claimed[(payer, good)] = claimed.get((payer, good), 0) + qty

    @staticmethod
    def _afford_within(
        plan: dict[str, tuple[int, int]], money: int, freight: int
    ) -> dict[str, tuple[int, int]]:
        """plan の注文を、どの財も同じ割合で減らして money に収める。"""
        cost = sum(gross_cost(limit, qty, freight, 0) for qty, limit in plan.values())
        if cost <= money:
            return plan
        out = {}
        for good, (qty, limit) in plan.items():
            scaled = qty * money // cost if cost > 0 else 0
            if scaled > 0:
                out[good] = (scaled, limit)
        return out

    def _supply_sites_from_warehouse(self, island: IslandState, ctx: StageContext) -> None:
        """国の工事は、国の倉庫にある資材を先に使う（次の1ターン分まで）。民営の工事は市場で買う
        （国の倉庫の物は、政府が売りに出したときだけ買える）。インフラの維持は施設の工事より先に、
        新しいインフラの工事は後に使う。"""
        infrastructure.supply_from_warehouse(island, ctx, upkeep=True)
        prices = island.economy.prices
        for f, _, _ in self._repairs(island, ctx):  # 国営の施設の設備を直す資材（回6）
            if f.owner == OWNER_PRIVATE:
                continue
            for good, want in equipment.wants(ctx.defs, ctx.config, f).items():
                batches = stock.take_batches(island.warehouse, good, want)
                taken = sum(q for _, q in batches)
                cost = money_of(prices.get(good, 0), taken)
                equipment.install(ctx.defs, f, good, taken, cost)
                if taken:  # 倉庫の物は買ったときに支出（GDP）に数えてある
                    stats = island.stats
                    stats["equipment.repair"] = stats.get("equipment.repair", 0) + cost
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

    @staticmethod
    def _switch_recipes(island: IslandState, ctx: StageContext) -> None:
        """命令か民営の見直しで決めた作り方の変更（facilities.RECIPE_NEXT）を、注文の前に行う
        （新しい原料をこの市場で買い、前の作り方の物を売る。次のターンは稼働率が半分。フェーズ3-2）。"""
        defs = ctx.defs
        for f in sorted(island.facilities, key=lambda f: f.id):
            recipe = f.ext.pop(fac.RECIPE_NEXT, None)
            if recipe is None or recipe == f.recipe or not f.built:
                continue
            if fac.switch_problem(defs, island, f, recipe) is not None:
                continue
            fac.switch_recipe(defs, f, recipe, ctx.turn + 1)
            productivity.update(island, defs, ctx.config, f)
            # 民営の施設は自分の見直しで替える（命令で替えられるのは国営だけ）。命令していない
            # 切り替えだと分かるように、民営と書く
            kind = "recipe_changed_private" if f.owner == OWNER_PRIVATE else "recipe_changed"
            island.log.append(
                LogEntry(
                    kind,
                    {"facility": f.kind, "x": f.x, "y": f.y, "recipe": f"{f.kind}_{recipe}"},
                )
            )

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
        official = planning.official(island) if planning.is_planned(island, defs) else {}
        for f in sorted(island.facilities, key=lambda f: f.id):
            if not f.built or fac.is_housing(config, f) or not fac.located(f):
                continue
            recipe = fac.recipe_of(defs, f)
            # 作り方のない施設は飛ばす。ただし畑をみな蒔き直している途中の農場は、前の作物の在庫を
            # 売る（回3）。商社の輸出と輸入の荷は商社が自分で売り買いするので、ここでは売らない
            if not recipe["outputs"] and not recipe["inputs"] and fac.FIELDS not in f.economy:
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
                if good in official:  # 計画経済: 費用に届かなくても公定価格で売る（フェーズ3-4）
                    ask = 0
                expiring = stock.expiring(f.stock, good, ctx.turn, defs.goods)
                if expiring:
                    self._add(books_by_good, good, "sell", Order(expiring), seller)
                if total - expiring > 0:
                    _, high = auction.band(prices.get(good, 1), width)
                    order = Order(total - expiring, seller_limit(ask, rate, high))
                    self._add(books_by_good, good, "sell", order, seller)
            # 作り方を替えて使わなくなった物（フェーズ3-2）は、値幅の下端を指値に売る
            used = set(recipe["outputs"]) | set(recipe["inputs"])
            for good, total in sorted(stock.totals(f.stock).items()):
                if good in used or good == power or total <= 0:
                    continue
                low, high = auction.band(prices.get(good, 1), width)
                order = Order(total, seller_limit(low, rate, high))
                self._add(books_by_good, good, "sell", order, seller)
            needs = self._input_needs(ctx, f, power)
            f.economy.pop("short", None)
            if recipe["inputs"]:  # 次のターンにどれだけ動くつもりで原料を買ったか（生産の段で使う）
                f.economy["input_plan"] = self._input_plan(ctx, f)
            if not needs:
                continue
            plan = {
                g: (q, net_limit(self._bid(ctx, f, g, prices, rate, g in official), rate, 0))
                for g, q in needs
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
    def _bid(
        ctx: StageContext,
        f: Facility,
        good: str,
        prices: dict[str, int],
        rate: int,
        fixed: bool = False,
    ) -> int:
        """投入財の買いの指値（運賃込み）: 値幅の上端。ただし変動費の利益が0になる値段まで
        （国庫直轄は、今の値段まではいつも買う）。公定価格の財（fixed。フェーズ3-4）は、値幅の
        上端でいつも買う（約定は公定価格）。

        売りが余っていれば約定価格は売り手の指値で決まるので、高く出しても高く買うことには
        ならない。品切れのときだけ、払える値段まで1ターンに値幅ずつ買い上がる。
        """
        price = prices.get(good, 0)
        _, high = auction.band(price, to_basis(ctx.config.get("market.band")))
        if fixed or is_public_service(ctx.defs, f):  # 学校と大学は値幅の上端まで買う
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
        worn = fac.equipment_cap(f)  # 設備が傷んで作れない分の原料は買わない（回6）
        if f.status == fac.STATUS_STARTING:
            return min(target, worn)
        if f.workers < f.economy.get("wanted", 0):
            headroom = round_money(settings["target_step"] * fac.FULL + 0.5)
            return min(target, worn, fac.labor_ratio(ctx.defs, f) + headroom)
        change = round_money(settings["max_target_change"] * fac.FULL + 0.5)
        return min(fac.FULL, worn, target + change)

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

    def _rebuild_orders(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        offered: dict[str, int],
        claimed: dict[tuple[Payer, str], int],
    ) -> None:
        """体制を変えた後の首都の建物の建て替え（フェーズ3-4。modules/politics）の資材の注文。
        公共事業費で、ほかの工事より先に買う（指値は工事の資材と同じ）。買えた物は国の倉庫でなく
        建て替えの資材（politics.rebuild_stock）に入れる（国の工事に先に使われないように）。"""
        need = politics.state_of(island).get("rebuild") or {}
        if not need:
            return
        width = to_basis(ctx.config.get("market.band"))
        payer = Payer(ledger.GOV, CONSTRUCTION)
        bought = politics.rebuild_stock(island)
        plan = {}
        for good, qty in sorted(need.items()):
            want = qty - stock.total(bought, good)
            want = self._room(island, offered, claimed, payer, good, want)
            if want > 0:
                plan[good] = (want, material_limit(island, ctx.config, good, width))
        for good, (qty, limit) in sorted(self._afford(island, payer, plan, 0, 0).items()):
            party = Party(REBUILD, ledger.GOV, payer)
            party.reserved = gross_cost(limit, qty, 0, 0)
            funds.commit(island, payer, party.reserved)
            self._add(books_by_good, good, "buy", Order(qty, limit), party)
            claimed[(payer, good)] = claimed.get((payer, good), 0) + qty

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
        self._rebuild_orders(island, ctx, books_by_good, offered, claimed)
        self._works_orders(island, ctx, books_by_good, offered, hh_rate, claimed, upkeep=True)
        self._repair_orders(island, ctx, books_by_good, rates, offered, claimed)
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
                    plan[good] = (want, material_limit(island, config, good, width))
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
                plan[good] = (qty, material_limit(island, ctx.config, good, width))
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
        """家計の層ごとに、使う額の中で食料・衣料・自動車と燃料・ぜいたく品・町の資材を注文する
        （フェーズ3-2 の必需品・ぜいたく品・耐久消費財。economy/needs.py）。"""
        defs, config = ctx.defs, ctx.config
        settings = config.get("household")
        width = to_basis(config.get("market.band"))
        tax = island.policy.consumption_tax
        prices = island.economy.prices
        priority = config.get("market.household_priority")
        pi = inflation(island.economy)
        power_paid = island.scratch.get("household_power", {})
        needs_by_layer = layer_food_needs(island, ctx)
        persons = {n: h["persons"] for n, h in island.economy.households.items()}
        total_persons = sum(persons.values()) or 1
        foods = sorted(g for g, d in defs.goods.items() if "food" in d)
        nutritions = {g: to_milli(defs.goods[g]["food"]["nutrition"]) for g in foods}
        qualities = {g: defs.goods[g]["food"].get("quality", 0) for g in foods}
        values = {  # 質を足した栄養あたりの値段
            g: prices.get(g, 1) / (defs.goods[g]["food"]["nutrition"] * (1 + qualities[g]))
            for g in foods
        }
        power = config.get("power.good")
        clothing = [
            g
            for g in needs.goods_of(defs, needs.NECESSITY)
            if g not in foods and g != power and needs.target_of(config, g) > 0
        ]
        luxuries = needs.goods_of(defs, needs.LUXURY)
        wanted = island.scratch.setdefault("wanted", {})
        plans = island.scratch.setdefault(needs.PLAN, {})
        # 計画経済（フェーズ3-4）: 売りに出ていない物を、売りに出ていれば買った量（行列と生産
        # 目標に使う）。売りに出ている物は、出した注文で数える（_plan_record）
        desire: dict[str, int] | None = None
        if planning.is_planned(island, defs):
            desire = island.scratch.setdefault(planning.DESIRE, {})
        menu = FoodMenu(
            values=values,
            nutritions=nutritions,
            qualities=qualities,
            unit={g: prices.get(g, 1) * (BASIS + rate + tax) // BASIS for g in foods},
            limits={g: auction.band(prices.get(g, 1), width)[1] for g in foods},
            rate=rate,
            tax=tax,
            elasticity=settings["food_elasticity"],
        )
        planned_of = {
            layer: ctx.formula("consumption")(island, layer, config, pi)
            for layer in sorted(island.economy.households)
        }
        all_planned = sum(planned_of.values()) or 1
        hungry_settings = settings["hungry"]
        for layer in sorted(island.economy.households):
            payer = Payer(household(layer))
            # 品物に付く小売りの代金（回7）を先に取り分け、残りで品物とサービスを買う
            keep = self._retail_keep(island, ctx, layer, planned_of[layer], tax)
            planned = planned_of[layer] - keep
            paid = power_paid.get(layer, 0)
            available = funds.available(island, payer)
            budget = max(0, min(planned - paid, available))
            reserve = min(keep, max(0, available - budget))
            if reserve > 0:
                funds.commit(island, payer, reserve)
                island.scratch.setdefault(retail.RESERVE, {})[layer] = reserve
                available -= reserve
            # 飢えて預金を崩す前の使う額（サービスとぜいたく品はこの中で使う）
            usual_budget = budget
            # 家計の支え合いで受け取った額（回7。賃金の段）は食べ物だけに使う（サービスと
            # ぜいたく品の使う額には足さない）
            received = island.scratch.get(SUPPORT, {}).get(layer, 0)
            if received > 0:
                budget = min(available, budget + received)
            # 飢えている家計（回3。前のターンに食べきれなかったか、飢えの蓄えが残っている）: 預金を
            # 崩してでも食料を先に買い、ほかの層の分にも手を伸ばして、値幅の上端を超えて買い上がる
            # （お金のない家計から買えなくなる）
            state = island.economy.households[layer]
            hungry = state.get(FED, fac.FULL) < to_milli(hungry_settings["threshold"])
            hungry = hungry or state.get(HUNGER, 0) > 0
            if hungry:
                extra = part(
                    ledger.money(island, household(layer)), to_basis(hungry_settings["savings"])
                )
                budget = max(0, min(available, budget + extra))
            island.scratch.setdefault("household_budget", {})[layer] = budget
            share = persons[layer] / total_persons
            income = needs.real_income(island, config, planned, persons[layer])
            # 1. 衣料（必需品）: 1人あたりの目安の量を、いつもの値段の一番上の段（値幅の上端まで）を
            #    指値に。食料の後に注文するが、食料の質を上げる予算からは先に引いておく
            wear: dict[str, tuple[int, int]] = {}
            for good in clothing:
                qty = to_milli(persons[layer] * needs.target_of(config, good))
                if qty <= 0:
                    continue
                if offered.get(good, 0) <= 0:
                    wanted[good] = wanted.get(good, 0) + qty
                    if desire is not None:
                        desire[good] = desire.get(good, 0) + qty
                    continue
                usual = usual_price(island, good, settings["usual_price_turns"])
                top = scale_price(usual, max(settings["consumer_goods_tiers"]))
                wear[good] = (qty, min(top, auction.band(prices.get(good, 1), width)[1]))
            wear_cost = sum(gross_cost(lim, q, rate, tax) for q, lim in wear.values())
            # 2. 食料: 売りに出ている量と値段で財ごとに割り振り、必需品の上限に余裕があれば質の
            #    高い物に替える。値幅の上端を指値にする
            need = needs_by_layer.get(layer, 0)
            caps = {g: int(offered.get(g, 0) * share) for g in foods}
            cap = round_money(planned * needs.necessity_share(config, income))
            food_budget = max(0, cap - paid - wear_cost) + received
            if hungry:
                # 飢える前の値段（飢えていないターンに残した値幅の上端）を基準に買い上がる
                anchors = {**menu.limits, **state.get(ANCHOR, {})}
                tiers = hungry_food_orders(
                    need,
                    caps,
                    offered,
                    menu,
                    anchors,
                    food_budget,
                    budget,
                    hungry_settings["steps"],
                    hungry_settings["elasticity"],
                )
                food_budget = budget
                plan = {}
            else:
                tiers = []
                plan = food_orders(need, caps, menu, food_budget, budget)
                state[ANCHOR] = {g: menu.limits[g] for g in foods if offered.get(g, 0) > 0}
            # 売りに出ていない食料も、あれば買いたい量を記録する（作る施設があれば、採算の合う
            # 値段まで値段が上がって作り始める。_record）
            plenty = dict.fromkeys(foods, need * QUANTITY_SCALE)
            ideal = upgrade_food(
                food_split(need, values, nutritions, plenty, settings["food_elasticity"]),
                food_by_quality(need, qualities, values, nutritions, plenty),
                menu.unit,
                food_budget,
            )
            for good, qty in sorted(ideal.items()):
                if offered.get(good, 0) <= 0:
                    wanted[good] = wanted.get(good, 0) + qty
            if desire is not None and need > 0:
                # 計画経済: 売りに出ていない食料は、それだけで足りるほど売りに出ていたら（ほかは
                # 今のまま）買った量を数える。どれも売りに出ていたとした理想の献立で数えると、
                # 質の高い魚で足りていて買わない穀物まで行列と生産目標に入り、作った分が腐った
                for good in foods:
                    if offered.get(good, 0) > 0:
                        continue
                    enough = -(-need * QUANTITY_SCALE // nutritions[good])
                    maybe = food_orders(need, {**caps, good: enough}, menu, food_budget, budget)
                    qty = maybe.get(good, (0, 0))[0]
                    if qty > 0:
                        desire[good] = desire.get(good, 0) + qty
            food_cost = sum(gross_cost(lim, q, rate, tax) for q, lim in plan.values())
            if tiers:  # 飢えた家計は食費を先に取る（食料に使い切らなかった分は預金に残る）
                food_cost = min(budget, sum(gross_cost(lim, q, rate, tax) for _, q, lim in tiers))
                for good, qty, limit in tiers:
                    self._household_buy(
                        island, books_by_good, good, qty, limit, rate, tax, layer, priority, True
                    )
            left = budget - food_cost
            if wear_cost > left and wear_cost > 0:
                wear = {g: (q * max(0, left) // wear_cost, lim) for g, (q, lim) in wear.items()}
                wear_cost = sum(gross_cost(lim, q, rate, tax) for q, lim in wear.values())
            for good, (qty, limit) in sorted({**plan, **wear}.items()):
                self._household_buy(
                    island, books_by_good, good, qty, limit, rate, tax, layer, priority
                )
            # 3. 必需品の残りのうち、所得に応じた割合をサービスに使う（バランス調整）。食料が
            #    足りないとき（売りに出ている量で足りないか、前のターンに食べきれなかったとき）は、
            #    食料に使うつもりだった額のうち足りない割合の分を、ほかに使わず手元に残す（飢えて
            #    いる人は外食や娯楽より食べ物を探す）。飢えている層も、サービスとぜいたく品は
            #    預金を崩す前の使う額から、買いたい食料をふだんの指値（値幅の上端）で買った額を
            #    引いた残りで買う（買い上がる段の指値で取り置いた額を引くと、層の中の少しの人が
            #    飢えただけで、層の全員がそのターンにサービスもぜいたく品も買わなくなった）
            basket: dict[str, int] = {}
            for good, qty, _ in tiers:
                basket[good] = basket.get(good, 0) + qty
            basket = basket if tiers else {g: q for g, (q, _) in plan.items()}
            fed = sum(q * nutritions[g] // QUANTITY_SCALE for g, q in basket.items())
            # 飢えの知らせ（人口の段が「お金がないのか、物がないのか」を見分けるのに使う）:
            # 要る栄養、注文した栄養、売りに出ていた栄養（この層の取り分）、食費、使えるお金
            island.scratch.setdefault(FOOD_ORDERS, {})[layer] = {
                "need": need,
                "ordered": fed,
                "offered": sum(caps[g] * nutritions[g] // QUANTITY_SCALE for g in foods),
                "cost": food_cost,
                "budget": budget,
            }
            short = fac.FULL - min(fac.ratio(fed, need), state.get(FED, fac.FULL))
            food_usual = sum(gross_cost(menu.limits[g], q, rate, tax) for g, q in basket.items())
            usual_food_budget = max(0, cap - paid - wear_cost)
            hold = max(0, usual_food_budget - food_usual) * short // fac.FULL
            # 支え合いで受け取った額で買った分は、ふだんの使う額から引かない
            rest = max(0, usual_budget - max(0, food_usual - received) - wear_cost - hold)
            rest = min(rest, funds.available(island, payer))  # 食料の取り置きの残りまで
            rest -= self._service_buys(
                island,
                ctx,
                books_by_good,
                layer,
                rest,
                income,
                tax,
                offered,
            )
            # 4. 残りの決めた割合を、自動車の燃料、自動車、ぜいたく品の順に使う
            spend = round_money(rest * settings["consumer_goods_share"])
            spend -= self._car_orders(
                island,
                ctx,
                books_by_good,
                layer,
                persons[layer],
                income,
                spend,
                rate,
                tax,
                offered,
                plans,
                (planned, all_planned),
            )
            # 5. ぜいたく品: 売りに出ている物の間で重みで割り振り（needs.split）、いつもの値段の
            #    倍率の段に分けて注文する（それより高ければ買わずに預金に残す。品切れで値段が跳ね
            #    上がり続けない）。売りに出ていない物は、目安の量を買いたい量として記録する
            open_goods = [g for g in luxuries if offered.get(g, 0) > 0]
            for good in luxuries:
                if good not in open_goods:
                    qty = to_milli(persons[layer] * needs.target_of(config, good))
                    if qty > 0:
                        wanted[good] = wanted.get(good, 0) + qty
                        if desire is not None:
                            desire[good] = desire.get(good, 0) + qty
            # 売りに出ている量で買える額（使う額の割合の分）を超える分は、ほかの物に回す
            tiers = settings["consumer_goods_tiers"]
            low = settings["consumer_goods_low_tiers"]
            usual = {g: usual_price(island, g, settings["usual_price_turns"]) for g in open_goods}
            caps = {
                g: gross_cost(
                    scale_price(usual[g], max(tiers)),
                    offered[g] * planned // all_planned,
                    rate,
                    tax,
                )
                for g in open_goods
            }
            weights = needs.weights_of(config, open_goods)
            portions = needs.split(max(0, spend), weights, caps)
            # 上限に届いてほかの物に回した分は、買えなかった量として記録する（ほかのぜいたく品に
            # 回すと品切れの合図が消え、値段も上がらず、消費財の工場が増えなかった）
            total = sum(weights.values())
            unplaced = island.scratch.setdefault("unplaced", {})
            for good in open_goods:
                ideal = max(0, spend) * weights[good] / total if total > 0 else 0
                extra = int(ideal) - portions[good]
                if extra > 0:
                    top = scale_price(usual[good], max(tiers))
                    qty = qty_for(extra * BASIS // (BASIS + rate + tax), top)
                    unplaced[good] = unplaced.get(good, 0) + qty
            for good in open_goods:
                price = usual[good]
                now = prices.get(good, 0)
                portion = portions[good]
                for qty, limit in consumer_tiers(portion, price, tiers, rate, tax, now, low):
                    self._household_buy(
                        island, books_by_good, good, qty, limit, rate, tax, layer, priority
                    )
        self._housing_orders(island, ctx, books_by_good, rate)

    def _service_buys(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        layer: str,
        rest: int,
        income: float,
        tax: int,
        offered: dict[str, int],
    ) -> int:
        """家計の層 layer が、必需品の残り rest のうち所得に応じた割合（economy/services.py の
        budget_share）でサービスを注文し、注文に取り置いた額を返す。ぜいたく品と同じく、いつもの値段の
        倍率の段に分けて注文し、売りに出ている量を全部買える額を超える分は買えなかった量として記録する
        （運賃はかからない）。ぜいたく品と違って層ごとの取り分に分けない（ほかに回す物がないので、
        所得の高い層の分が取り分を超えて「買えなかった量」になり、売れ残りがあるのに品切れの合図が
        出て、人を雇いすぎた）。売りに出ていなければ、買いたい量だけ記録する。"""
        config = ctx.config
        good = services.good_of(config)
        settings = config.get("household")
        amount = round_money(rest * services.budget_share(config, income))
        if amount <= 0 or good not in ctx.defs.goods:
            return 0
        usual = usual_price(island, good, settings["usual_price_turns"])
        tiers = settings["consumer_goods_tiers"]
        top = scale_price(usual, max(tiers))
        if offered.get(good, 0) <= 0:
            qty = qty_for(amount * BASIS // (BASIS + tax), usual)
            wanted = island.scratch.setdefault("wanted", {})
            wanted[good] = wanted.get(good, 0) + qty
            if planning.is_planned(island, ctx.defs):
                desire = island.scratch.setdefault(planning.DESIRE, {})
                desire[good] = desire.get(good, 0) + qty
            return 0
        cap = gross_cost(top, offered[good], 0, tax)
        portion = min(amount, cap)
        if amount > portion:
            unplaced = island.scratch.setdefault("unplaced", {})
            qty = qty_for((amount - portion) * BASIS // (BASIS + tax), top)
            unplaced[good] = unplaced.get(good, 0) + qty
        now = island.economy.prices.get(good, 0)
        priority = config.get("market.household_priority")
        payer = Payer(household(layer))
        before = funds.available(island, payer)
        low = settings["consumer_goods_low_tiers"]
        for qty, limit in consumer_tiers(portion, usual, tiers, 0, tax, now, low):
            self._household_buy(island, books_by_good, good, qty, limit, 0, tax, layer, priority)
        return max(0, before - funds.available(island, payer))

    def _services_orders(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        official: dict[str, int],
    ) -> None:
        """商業・サービス業が、雇った人が出せるサービスを在庫に置き、希望価格（ほかの施設と同じく、
        売れ残れば下げ、売り切れて買えなかった人がいれば上げる。_update_prices）を指値に売る（計画経済の
        島は公定価格。運賃はかからない）。売れ残った分はそのターンのうちに消えるので、変動費の下限は
        付けない（雇った人の賃金はもう払ってある。人数は売上に合わせて services.plan で減らす）。"""
        defs, config = ctx.defs, ctx.config
        if not services.produce(island, config, defs, ctx.turn):
            return
        f = services.facility_of(island, config)
        assert f is not None
        good = services.good_of(config)
        payer = payer_of(defs, config, f)
        seller = Party(
            FACILITY, facility_entity(f.id), Payer(entity_of(config, f), payer.item), 0, 0, f
        )
        price = island.economy.prices.get(good, 1)
        low, high = auction.band(price, to_basis(config.get("market.band")))
        ask = max(low, min(high, f.economy.get("ask", {}).get(good, price)))
        limit = 0 if good in official else ask
        self._add(books_by_good, good, "sell", Order(stock.total(f.stock, good), limit), seller)

    @staticmethod
    def _services_unmet(
        island: IslandState,
        config: GameConfig,
        books_by_good: dict[str, Book],
        results: dict[str, auction.Result],
    ) -> int:
        """約定した値段でも買いたかったのに買えなかったサービスの量（その値段以上の指値の注文の
        買えなかった分と、売りに出ている量を超えて注文しなかった分。売りに出ていなければ買いたい量）。
        約定した値段より安い段の注文は数えない（その値段では買う気のなかった分で、数えると人を雇い
        すぎて値段が賃金まで下がった）。"""
        good = services.good_of(config)
        result = results.get(good)
        if result is None:  # 売りに出ていなかった（買いたい量だけ記録した）
            return island.scratch.get("wanted", {}).get(good, 0)
        book = books_by_good[good]
        unfilled = sum(
            order.quantity - result.bought[i]
            for i, (order, _) in enumerate(book.buys)
            if auction.buy_limit(order, result.high) >= result.price
        )
        return unfilled + island.scratch.get("unplaced", {}).get(good, 0)

    def _car_orders(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        layer: str,
        persons: int,
        income: float,
        spend: int,
        rate: int,
        tax: int,
        offered: dict[str, int],
        plans: dict[str, Any],
        portion: tuple[int, int],
    ) -> int:
        """層の自動車の燃料と自動車の注文（economy/needs.py）。取り置いた額（注文した額）を返す。

        燃料は持っている車の分を、いつもの値段の一番上の段（household.consumer_goods_tiers）と値幅の
        上端の安い方まで（値幅の上端で買い続けると、品薄の島で燃料の値段が際限なく上がった）、
        自動車は買いたい台数を、ぜいたく品の予算の household.cars.share までで、いつもの値段の
        倍率の段で買う。売りに出ていなければ、買いたい量だけ記録する。売りに出ている台数のうち、
        この層の分（使う額の割合 portion で分ける）を超える分は注文せず、買えなかった量として記録
        する（売りに出ていない車にお金を取り置くと、その分がぜいたく品に回らずに預金に残った）。"""
        config = ctx.config
        settings = config.get("household")
        prices = island.economy.prices
        width = to_basis(config.get("market.band"))
        priority = config.get("market.household_priority")
        wanted = island.scratch.setdefault("wanted", {})
        car = settings["cars"]["good"]
        owned = needs.cars_owned(island, layer)
        desired = needs.desired_cars(config, layer, persons, income)
        fuel = needs.car_fuel(config, owned)
        plans[layer] = {"desired": desired, "fuel": fuel}
        used = 0
        for good, qty in sorted(fuel.items()):
            if qty <= 0:
                continue
            if offered.get(good, 0) <= 0:
                wanted[good] = wanted.get(good, 0) + qty
                continue
            usual = usual_price(island, good, settings["usual_price_turns"])
            limit = min(
                auction.band(prices.get(good, 1), width)[1],
                scale_price(usual, max(settings["consumer_goods_tiers"])),
            )
            cost = gross_cost(limit, qty, rate, tax)
            if cost > spend - used:
                qty = qty * max(0, spend - used) // cost if cost > 0 else 0
                cost = gross_cost(limit, qty, rate, tax)
            if qty > 0:
                self._household_buy(
                    island, books_by_good, good, qty, limit, rate, tax, layer, priority
                )
                used += cost
        want = needs.cars_wanted(config, owned, desired)
        if want <= 0:
            return used
        if offered.get(car, 0) <= 0:
            wanted[car] = wanted.get(car, 0) + want
            return used
        part_of, whole = portion
        mine = offered[car] * part_of // max(1, whole)
        extra = min(want, offered[car]) - min(want, mine)
        if extra > 0:
            unplaced = island.scratch.setdefault("unplaced", {})
            unplaced[car] = unplaced.get(car, 0) + extra
        want = min(want, mine)
        if want <= 0:
            return used
        price = usual_price(island, car, settings["usual_price_turns"])
        room = min(round_money((spend - used) * settings["cars"]["share"]), spend - used)
        top = scale_price(price, max(settings["consumer_goods_tiers"]))
        room = min(room, gross_cost(top, want, rate, tax))
        bought = 0
        for qty, limit in consumer_tiers(
            room,
            price,
            settings["consumer_goods_tiers"],
            rate,
            tax,
            prices.get(car, 0),
            settings["consumer_goods_low_tiers"],
        ):
            qty = min(qty, want - bought)
            if qty <= 0:
                break
            self._household_buy(island, books_by_good, car, qty, limit, rate, tax, layer, priority)
            bought += qty
            used += gross_cost(limit, qty, rate, tax)
        return used

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
        hungry: bool = False,
    ) -> None:
        if qty <= 0:
            return
        payer = Payer(household(layer))
        cost = gross_cost(limit, qty, rate, tax)
        money = funds.available(island, payer)
        if cost > money:
            qty = qty * money // cost
            cost = gross_cost(limit, qty, rate, tax)
            if cost > money:
                # 切り捨ての誤差で取り置けるお金を超えたら、お金で買える量までにする（回4。
                # 失業者の層の買い物が、わずかに足りずにターンごと止まった）
                qty = min(qty, qty_for(money * BASIS // (BASIS + rate + tax), limit))
                cost = gross_cost(limit, qty, rate, tax)
        if qty <= 0:
            return
        party = Party(HOUSEHOLD, household(layer), payer, rate, tax, layer=layer, hungry=hungry)
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
        """命令 warehouse_trade で並べた国の倉庫の売り買い（このターンだけ）と、船で届いた腐る物と
        契約で「市場に出す」と決めた物の売り（売り切れるまで毎ターン）。売りは合わせて倉庫にある量
        まで。"""
        orders = island.economy.ext.pop("warehouse_orders", [])
        offered: dict[str, int] = {}

        def sell(good: str, qty: int, limit: int | None, layer: str | None = None) -> None:
            qty = min(qty, stock.total(island.warehouse, good) - offered.get(good, 0))
            if qty > 0:
                offered[good] = offered.get(good, 0) + qty
                party = Party(STORE, ledger.GOV, Payer(ledger.GOV, WAREHOUSE), layer=layer)
                self._add(books_by_good, good, "sell", Order(qty, limit), party)

        for good, qty in sorted(trade.to_market(island).items()):
            # 傷みやすい物は値段を問わず、契約で「市場に出す」と決めた物と閉じた施設から引き取った
            # 物は島の値段で売りに出す（値崩れさせずに、売れるだけ毎ターン出す）
            perishable = ctx.defs.goods[good].get("shelf_life")
            sell(good, qty, None if perishable else island.economy.prices.get(good), IMPORTED)
        for item in orders:
            good, qty, limit = item["good"], item["quantity"], item["limit"]
            if item["side"] == "sell":
                sell(good, qty, limit or None)
                continue
            buyer = Payer(ledger.GOV, WAREHOUSE)
            plan = self._afford(island, buyer, {good: (qty, limit)}, 0, 0)
            for g, (q, lim) in plan.items():
                party = Party(STORE, ledger.GOV, buyer)
                party.reserved = gross_cost(lim, q, 0, 0)
                funds.commit(island, buyer, party.reserved)
                self._add(books_by_good, g, "buy", Order(q, lim), party)

    # --- 貿易（フェーズ3-1） -----------------------------------------------------------

    def _trade_orders(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        rates: dict[int, int],
        hh_rate: int,
    ) -> None:
        """商社・海運業・契約で売る政府・海外の商人の注文。"""
        defs, config = ctx.defs, ctx.config
        if not trade.started(island):
            return
        width = to_basis(config.get("market.band"))
        prices = island.economy.prices
        site = trade.site_of(island, config)
        # 海外の商人（決まった値段の売り）。計画経済の島では、商人の値段が公定価格より高ければ、
        # 差を政府（貿易の項目）が払って公定価格で売る（国の貿易の独占。払える量まで）
        official = planning.official(island) if planning.is_planned(island, defs) else {}
        for good, qty, price in merchant.offers(island, defs, config, ctx.turn):
            party = Party(MERCHANT, MERCHANT, Payer(ledger.CB), site=site)
            fixed = official.get(good)
            if fixed and price > fixed:
                party.gap = price - fixed
                qty = min(qty, qty_for(funds.available(island, MERCHANT_GAP), party.gap))
                if qty <= 0:
                    continue
                party.reserved = money_of(party.gap, qty)
                funds.commit(island, MERCHANT_GAP, party.reserved)
                price = fixed
            self._add(books_by_good, good, "sell", Order(qty, price), party)
        # 商社
        for order in traders.home_orders(island, defs, config, ctx.turn):
            f = order.facility
            rate = rates.get(f.id, 0)
            payer = Payer(entity_of(config, f))
            party = Party(FACILITY, facility_entity(f.id), payer, rate, 0, f, site=site)
            _, high = auction.band(prices.get(order.good, 1), width)
            if order.side == "sell":
                limit = None if order.limit is None else seller_limit(order.limit, rate, high)
                self._add(books_by_good, order.good, "sell", Order(order.qty, limit), party)
                continue
            plan = self._afford(
                island,
                payer,
                {order.good: (order.qty, net_limit(order.limit or 0, rate, 0))},
                rate,
                0,
            )
            for good, (qty, limit) in plan.items():
                party.reserved = gross_cost(limit, qty, rate, 0)
                funds.commit(island, payer, party.reserved)
                self._add(books_by_good, good, "buy", Order(qty, limit, TRADER_PRIORITY), party)
        # 海運業の燃料と輸送船
        shipping = trade.shipping_of(island, config)
        if shipping is not None and shipping.built and not is_stopped(shipping):
            payer = payer_of(defs, config, shipping)
            fuel = {}
            for good, qty in sorted(ships.fuel_wanted(island, config, shipping).items()):
                fuel[good] = (qty, auction.band(prices.get(good, 1), width)[1])
            vessels = {}
            want = ships.ships_wanted(config, shipping, ctx.turn)
            # 買いたい船の代金を次の支払いに数え、足りなければ運転資金か持ち主の出資で賄う（回6）
            shipping.economy["ships_wanted"] = want
            if want > 0:
                good = config.get("trade.ship_good")
                vessels[good] = (want, auction.band(prices.get(good, 1), width)[1])
            name = facility_entity(shipping.id)
            # 燃料を先に取り置き、残りのお金で船を買う（船の買い替えで燃料が削られ、船が出せずに
            # 「船が足りない」になって、また船を買う回り方にならないように。回6）
            for plan in (fuel, vessels):
                for good, (qty, limit) in sorted(
                    self._afford(island, payer, plan, hh_rate, 0).items()
                ):
                    party = Party(FACILITY, name, payer, hh_rate, 0, shipping, site=site)
                    party.reserved = gross_cost(limit, qty, hh_rate, 0)
                    funds.commit(island, payer, party.reserved)
                    self._add(books_by_good, good, "buy", Order(qty, limit), party)
        # 二国間契約で売る分のうち、国の倉庫に足りない分を政府が買う
        needs: dict[str, int] = {}
        for c in trade.island_state(island).get("contracts", []):
            if c.get("side") == "sell" and c.get("active"):
                needs[c["good"]] = needs.get(c["good"], 0) + c["qty"]
        payer = Payer(ledger.GOV, GOV_TRADE)
        plan = {}
        for good, qty in sorted(needs.items()):
            missing = qty - stock.total(island.warehouse, good)
            if missing > 0:
                plan[good] = (missing, auction.band(prices.get(good, 1), width)[1])
        for good, (qty, limit) in sorted(self._afford(island, payer, plan, 0, 0).items()):
            party = Party(STORE, ledger.GOV, payer, site=site)
            party.reserved = gross_cost(limit, qty, 0, 0)
            funds.commit(island, payer, party.reserved)
            self._add(books_by_good, good, "buy", Order(qty, limit, CONTRACT_PRIORITY), party)

    def _transport_orders(
        self,
        island: IslandState,
        ctx: StageContext,
        books_by_good: dict[str, Book],
        rate: int,
        offered: dict[str, int],
    ) -> None:
        """運送業のトラックにする自動車と、トラックの燃料の注文（フェーズ3-2。economy/trucks.py）。
        値幅の上端を指値にし、売りに出ていなければ買いたい量だけ記録する。燃料の指値は、トラックが
        運転手より安く使える値段まで（trucks.fuel_limit）。"""
        defs, config = ctx.defs, ctx.config
        f = next((f for f in island.facilities if is_transport(config, f)), None)
        if f is None or not f.built or is_stopped(f):
            return
        width = to_basis(config.get("market.band"))
        prices = island.economy.prices
        wanted = island.scratch.setdefault("wanted", {})
        plan = {}
        for good, qty in sorted(trucks.orders(island, config, f).items()):
            if offered.get(good, 0) <= 0:
                wanted[good] = wanted.get(good, 0) + qty
                continue
            limit = auction.band(prices.get(good, 1), width)[1]
            if good != config.get("freight.trucks.good"):
                limit = min(limit, trucks.fuel_limit(island, config, f, good))
            if limit > 0:
                plan[good] = (qty, limit)
        payer = payer_of(defs, config, f)
        name = facility_entity(f.id)
        for good, (qty, limit) in sorted(self._afford(island, payer, plan, rate, 0).items()):
            party = Party(FACILITY, name, payer, rate, 0, f)
            party.reserved = gross_cost(limit, qty, rate, 0)
            funds.commit(island, payer, party.reserved)
            self._add(books_by_good, good, "buy", Order(qty, limit), party)

    @staticmethod
    def _trade_settled(island: IslandState, ctx: StageContext, rates: dict[int, int]) -> None:
        """商社が島の市場で買った・売った量を、仕入れ値の記録に足す（運賃を含めて）。"""
        found = {facility_entity(f.id): f for f in trade.traders_of(island, ctx.config)}
        if not found:
            return
        for record in island.trades:
            f = found.get(record.buyer)
            if f is not None:
                cost = record.amount + part(record.amount, rates.get(f.id, 0))
                traders.bought_home(f, record.good, record.quantity, cost)
            f = found.get(record.seller)
            if f is not None:
                traders.sold_home(f, record.good, record.quantity)

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
            kind = "merchant" if seller.kind == MERCHANT else "sale"
            funds.pay(island, buyer.payer, seller.payer.entity, amount, kind, committed=True)
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
            batches = self._take_from_seller(island, good, seller, qty, last, ctx)
            if expiry is not None and self._stores(buyer):
                spoiling = sum(q for made, q in batches if made <= expiry)
                if spoiling:
                    stale[b] = stale.get(b, 0) + spoiling
            self._give_to_buyer(island, good, buyer, batches, amount, buyer_freight, tax, bought)
            if seller.facility is not None:
                books.add(island, seller.facility.id, books.SALES, amount)
                books.add(island, seller.facility.id, books.FREIGHT, seller_freight)
                planning.note_sale(island, ctx.config, good, seller.facility, qty, price)
            elif seller.kind == MERCHANT:  # 海外の商人の代金は島の外に出る（輸入）
                # 計画経済の島で公定価格との差を政府が払った分は、商人に払った額と国際収支に
                # 入れる（GDP の輸入は、島の買い手が払った額のまま。生産の側と合わせる）
                gap = money_of(seller.gap, qty)
                funds.pay(island, MERCHANT_GAP, seller.payer.entity, gap, kind, committed=True)
                seller.reserved -= gap
                books.add_sector(island, books.TRADE, "imports", amount)
                paid_out = amount + gap
                island.stats["trade.merchant"] = island.stats.get("trade.merchant", 0) + paid_out
                if gap:
                    total = island.stats.get("trade.merchant_gap", 0) + gap
                    island.stats["trade.merchant_gap"] = total
                balance.merchant(island, paid_out)  # 国際収支（フェーズ3-3）
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
            if party.site is not None:
                return party.site
            if party.facility is not None and fac.located(party.facility):
                return fac.position(party.facility)
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
        island: IslandState,
        good: str,
        seller: Party,
        qty: int,
        last_until: int | None = None,
        ctx: StageContext | None = None,
    ) -> list:
        if seller.kind == MERCHANT:  # 海外の商人は、このターンに持ってきた物を渡す
            assert ctx is not None
            brought: dict[str, list[list[int]]] = {}
            stock.add(brought, good, qty, ctx.turn, ctx.defs.goods)
            return brought.get(good, [])
        if seller.facility is not None:
            batches = stock.take_batches(seller.facility.stock, good, qty, last_until)
            books.stock_moved(island, seller.facility.id, good, -qty)
            return batches
        return stock.take_batches(island.warehouse, good, qty, last_until)

    @staticmethod
    def _unretailed(island: IslandState) -> tuple[str, ...]:
        """小売りを通さない物（サービスと、最後に小売りそのもの）。"""
        return tuple(island.scratch.get(retail.UNRETAILED, ()))

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
        elif buyer.kind == REPAIR:
            # 傷んだ設備を直す資材（回6）: そのまま設備に入れ、設備の額に足す（GDP では工事）
            assert buyer.facility is not None
            bought_repairs = island.scratch.setdefault(REPAIR, [])
            bought_repairs.append((buyer.facility, good, qty, amount + freight))
            books.add_sector(island, CONSTRUCTION, "materials", amount)
            books.add_sector(island, CONSTRUCTION, "freight", freight)
            stats = island.stats
            stats["equipment.repair"] = stats.get("equipment.repair", 0) + amount + freight
        elif buyer.kind == WORKS:
            infrastructure.received(island, good, batches)
            books.add_sector(island, CONSTRUCTION, "materials", amount)
            books.add_sector(island, CONSTRUCTION, "freight", freight)
        elif buyer.kind == REBUILD:
            stock.put_batches(politics.rebuild_stock(island), good, batches)
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
            free = Market._unretailed(island)
            if good not in free:  # 小売りを通す品物の代金（回7）
                spent = island.scratch.setdefault(retail.SPENT, {})
                spent[buyer.layer] = spent.get(buyer.layer, 0) + amount
            elif free and good == free[-1]:  # 小売りに払った額
                paid = island.scratch.setdefault(retail.PAID, {})
                paid[buyer.layer] = paid.get(buyer.layer, 0) + amount + tax
            books.add_sector(island, books.HOUSEHOLDS, "goods", amount)
            books.add_sector(island, books.HOUSEHOLDS, "freight", freight)
            books.add_sector(island, books.HOUSEHOLDS, "tax", tax)
        else:
            stock.put_batches(island.warehouse, good, batches)
            books.add_sector(island, books.WAREHOUSE, "purchases", amount)

    # --- 市場の後 -------------------------------------------------------------------

    @staticmethod
    def _imports_sold(
        books_by_good: dict[str, Book], results: dict[str, auction.Result]
    ) -> dict[str, int]:
        """船で国の倉庫に届いた腐る物の売りが、このターンに売れた量（財 → 量）。"""
        sold: dict[str, int] = {}
        for good, book in books_by_good.items():
            for i, (_, party) in enumerate(book.sells):
                if party.kind == STORE and party.layer == IMPORTED:
                    sold[good] = sold.get(good, 0) + results[good].sold[i]
        return sold

    @staticmethod
    def _imports_left(island: IslandState, sold: dict[str, int]) -> None:
        """市場に出す残りから売れた量を引く。傷んで捨てられ倉庫に残っていない分も除く。"""
        left = trade.to_market(island)
        for good in sorted(left):
            qty = min(left[good] - sold.get(good, 0), stock.total(island.warehouse, good))
            if qty > 0:
                left[good] = qty
            else:
                del left[good]
        if not left:
            island.ext.get(trade.KEY, {}).pop(trade.TO_MARKET, None)

    @staticmethod
    def _spoil(island: IslandState, ctx: StageContext) -> None:
        """持ち越せる期間を過ぎた在庫を、施設・国の倉庫から捨てる。商社の荷は、持っている量と
        仕入れ値からも外す（回4。外さずにいて、傷んで無くなった食べ物を持っているつもりで輸入の
        注文を出さず、向きも替えられないまま飢饉になった島があった）。"""
        spoiled: dict[str, int] = {}
        for f in sorted(island.facilities, key=lambda f: f.id):
            for good, qty in stock.spoil(f.stock, ctx.turn, ctx.defs.goods).items():
                spoiled[good] = spoiled.get(good, 0) + qty
                if f.built and not is_public_service(ctx.defs, f):
                    books.stock_moved(island, f.id, good, -qty)
                if trade.is_trader(ctx.config, f) and good in traders.goods_of(f):
                    traders.take_cost(traders.goods_of(f)[good], qty)
        for good, qty in stock.spoil(island.warehouse, ctx.turn, ctx.defs.goods).items():
            spoiled[good] = spoiled.get(good, 0) + qty
        unused = services.good_of(ctx.config)  # 売れ残ったサービスは傷んだのでなく、使われなかった
        for good, qty in sorted(spoiled.items()):
            if good == unused:
                island.stats["services.unused"] = from_milli(qty)
                continue
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
        # 買いたい人がいるのに売りに出なかった財は、島で作り始められる一番安い値段
        # （_supply_floors）が今の値段以上なら、値幅の上端まで上げる（作って採算が合う値段まで
        # 1ターンに値幅ずつ上がり、作り始める）。商社がいれば、輸入が引き合う値段も作る施設の
        # 変動費と同じに見る（フェーズ3-1）
        width = to_basis(ctx.config.get("market.band"))
        rates = freight_rates.facility_rates(island, ctx.defs, ctx.config)
        # 売りに出ているのに買い手のいなかった財（指値のある売りの一番安い指値。今夜捨てられる分の
        # 指値なしの売りは、売り手が付けた値段ではないので見ない）
        idle = {}
        for good, result in results.items():
            if result.supply <= 0 or result.demand > 0 or good not in books_by_good:
                continue
            asks = [o.limit for o, _ in books_by_good[good].sells if o.limit is not None]
            if asks:
                idle[good] = min(asks)
        found: dict[str, traders.Prospect] = {}
        if (wanted or idle) and traders.traders_of(island, ctx.config):
            found = traders.prospects(island, ctx.defs, ctx.config)
        # 商社がいて、輸出が引き合う値段（Prospect.export_limit）が売りの一番安い指値以上なのに、
        # 島の値段がそれより高い財は、値幅の下端まで下げる（輸入の側と同じく1ターンに値幅ずつ。
        # 値段が輸出の値段まで下がると、商社が輸出する物を買い始める。フェーズ3-1。前は売りしか
        # ない財の値段が動かず、圏に買い手がいても輸出されなかった）
        for good, ask in sorted(idle.items()):
            p = found.get(good)
            price = prices.get(good, 1)
            if p is not None and p.export_bloc is not None and ask <= p.export_limit < price:
                prices[good] = auction.band(price, width)[0]
        for good in sorted(wanted):
            if supply.get(good, 0) > 0:
                continue
            floors = Market._supply_floors(island, ctx, good, prices, rates)
            if good in found and found[good].import_floor > 0:
                floors.append(found[good].import_floor)
            price = prices.get(good, 1)
            if floors and min(floors) >= price:
                prices[good] = auction.band(price, width)[1]

    @staticmethod
    def _supply_floors(
        island: IslandState,
        ctx: StageContext,
        good: str,
        prices: dict[str, int],
        rates: dict[int, int],
    ) -> list[int]:
        """島の施設が財 good を作り始める値段の候補（_record。買いたい人がいるのに売りに出なかった
        財）。

        今の作り方で good を作る施設は、1単位の変動費。作り方を替えれば作れる施設（綿を作っている
        農場の穀物など）は、その作り方の1単位の変動費。民営の施設は、さらに作り方の見直し
        （modules/firms）で替える値段（その作り方の変動費の利益が、今の作り方より
        firms.recipe_margin の割合だけ大きくなる値段）を下回らない（回4。前は今の作り方で作る
        施設だけを見たので、島で最後の穀物の農場が綿に替えると、穀物を作る施設がなくなって値段が
        上がらず、どの農場も穀物に戻らないまま島が飢えた）。
        """
        defs, config = ctx.defs, ctx.config
        margin = to_basis(config.get("firms.recipe_margin"))
        floors = []
        for f in island.facilities:
            if not f.built or not fac.on_map(f):
                continue
            wage = f.economy.get("wage", 0)
            rate = rates.get(f.id, 0)
            if fac.recipe_of(defs, f)["outputs"].get(good):
                floors.append(unit_floor(defs, config, f, good, prices, wage, rate))
                continue
            if "closing" in f.economy or "foreclosed" in f.economy:
                continue
            now: int | None = None
            for name, recipe in sorted(defs.facilities[f.kind]["recipes"].items()):
                if not recipe["outputs"].get(good) or fac.switch_problem(defs, island, f, name):
                    continue
                probe = productivity.probe(island, defs, config, f.kind, name, (f.x, f.y))
                floor = unit_floor(defs, config, probe, good, prices, wage, rate)
                if f.owner == OWNER_PRIVATE and is_self_supporting(f):
                    if now is None:
                        now = variable_profit(defs, config, f, prices, wage, rate)
                    goal = now + part(abs(now), margin)
                    price = Market._price_for(defs, config, probe, good, prices, wage, rate, goal)
                    floor = max(floor, price)
                floors.append(floor)
        return floors

    @staticmethod
    def _price_for(
        defs: Defs,
        config: GameConfig,
        probe: Facility,
        good: str,
        prices: dict[str, int],
        wage: int,
        rate: int,
        goal: int,
    ) -> int:
        """仮の施設 probe（作り方を替えた後）の変動費の利益が goal になる、財 good の値段（利益は
        good の値段について1次なので、今の値段と倍の値段の2点から解く）。"""
        price = max(1, prices.get(good, 1))
        low = variable_profit(defs, config, probe, prices, wage, rate)
        high = variable_profit(defs, config, probe, {**prices, good: price * 2}, wage, rate)
        if low >= goal or high <= low:
            return price
        return price + -(-(goal - low) * price // (high - low))

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
    # 電気料金（ためておけない財）のほかは、混合経済だけ（体制の特色 price_controls。フェーズ3-4）
    utility = defs.goods[payload["good"]].get("shelf_life") == 0
    if not utility and not politics.trait(island, defs, "price_controls", False):
        raise OrderFailed("reason_price_controls_forbidden")


def set_price_cap(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """値段の上限。電気料金はどの体制でも（公共料金）、ほかの財は混合経済だけ（フェーズ3-4。
    下限は modules/plan の set_price_floor）。0 で上限をなくす。"""
    good, price = payload["good"], payload["price"]
    # ログの文: 電気料金は「公共料金の上限」、ほかの財は「値段の上限」
    head = "price_cap" if ctx.defs.goods[good].get("shelf_life") == 0 else "goods_price_cap"
    if price > 0:
        island.policy.price_cap[good] = price
        island.log.append(LogEntry(f"{head}_set", {"good": good, "price": price}))
    else:
        island.policy.price_cap.pop(good, None)
        island.log.append(LogEntry(f"{head}_cleared", {"good": good}))


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
