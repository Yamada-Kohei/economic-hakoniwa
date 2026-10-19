"""商社（フェーズ3-1）。

商社はオフィス街のマスに建つ施設で、島にいくつも置けます。財ごとに、輸出（島の市場で買い、貿易圏の
市場で売る）か輸入（圏で買い、届いた物を島の市場で売る）のどちらかをします。帳簿・税・配当はほかの
独立採算の施設と同じで、外貨は外貨の持ち高（money.py）に持ちます。荷は港の倉庫に置き、島の市場で
買う・売るときの島の中の運賃は港のマスから数えます（``economy["site"]`` と
``economy["freight_goods"]``。hakoniwa_core/economy/freight.py）。

島の市場（市場の段。前のターンの圏の値段とレートを見る）:
- 輸出: 圏の前の値段を為替で自国通貨に直し、取り分（trade.trader_cut）と港の使用料を引いて、輸出補助
  （フェーズ3-3）を足した値段まで買う（運賃は買い手の指値の中で払う）。
  量は「狙う量 − 手元の輸出の荷」。
- 輸入した物: 仕入れ値（圏の代金・船の運賃・港の使用料）を下限に売る。trade.clearance_turns ターン
  続けて1つも売れなかったら、値幅の下端まで下げて売る（仕入れ値の CLEARANCE_FLOOR の割合まで）。
- 輸出の荷が、指値を外しても圏で trade.clearance_turns の 2 倍のターン売れ残ったら、島の市場に戻して
  指値なしで売る。

貿易圏の市場（貿易の段。hakoniwa_core/modules/trade）:
- 輸出の荷を、仕入れ値に取り分と積む港の使用料を足した値段（決済通貨に直す）を指値に売る。
  trade.clearance_turns ターン売れ残ったら、指値を外す。
- 輸入: 島の市場の前の値段から、取り分・船の運賃・下ろす港の使用料を引き、関税（フェーズ3-3。圏内の
  関税をなくした圏ではかからない）の分を割り引いて決済通貨に直した値段を指値に買う。
  量は「狙う量 − 手元と運ぶ途中の輸入の荷」で、空いている船、持っている決済通貨、
  商社の扱える重さ（trade.trader_capacity）で頭打ちになる。運賃の見込みは、圏の加盟国のうち近い
  半分までの平均の距離で見る。

島の市場で前のターンに買い手のいなかった物は輸入せず、売りに出ていなかった物は輸出しません。狙う量は
島の市場の買いたい量（輸入）と売りたい量（輸出）までです。

狙う量（財ごと）は毎ターン、売れた量と足りない量の分け前に trade.target_speed の割合だけ近づけます
（荷が島と圏の間で行ったり来たりしないように）。輸出の足りない量は圏の市場の買えなかった量を、
輸入の足りない量は島の市場の買えなかった量を、同じ財を扱う商社の数で分けたものです。値段の差が
あって、手元の荷を売り切ったときは、全力の 1 割ずつ多めに狙います（始めのうちは売れた量も足りない
量もないため。売れずに手元の荷がないだけのときは増やさない）。島の値段が輸入の原価を
PUSH_MARGIN より上回るときは、島の市場の買いたい量より多めに狙います（売りを増やして値段を
原価のほうへ下げる）。

計画経済の島（フェーズ3-4。公定価格のある島）の商社は、値段の差ではなく、島の市場で買えなかった
量（行列）のある財を、圏の値段で一番安い圏から輸入し、売れ残った財を輸出します（貿易の独占）。島では
公定価格で売り買いし（輸入した物は仕入れ値によらず売り、輸出する物は公定価格で買う）、圏では輸出の
荷を指値なしで売り、輸入は圏の値幅の上端まで買います。輸入に使えるのは商社が輸出で得て持っている
外貨だけで（為替で外貨を買わない）、余った外貨は売りません（modules/trade の為替）。

島で買い手がいるのに売りに出ていない物は、島で作る施設の変動費と同じように、輸入が引き合う値段
（Prospect.import_floor。前のターンに圏の市場に売りがあった財だけ）まで島の値段を1ターンに値幅
ずつ上げます。反対に、売りに出ているのに買い手の
いない物は、輸出が引き合う値段（Prospect.export_limit）が売りの一番安い指値以上なら、島の値段がそれを
下回るまで1ターンに値幅ずつ下げます（どちらも市場の段の値段の記録。hakoniwa_core/modules/market）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.market import auction
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import (
    island_state,
    policy,
    port_of,
    ships,
    tradable_goods,
    traders_of,
    unit_weight,
)
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, part, round_money, to_basis

EXPORT = "x"
IMPORT = "m"
PROBE = 0.1  # 値段の差があって売り切ったときに、多めに狙う量（全力に対する割合）
FX_MARGIN = 1.5  # 持っておく外貨は、直近の輸入に払った額の平均のこの倍まで
FX_PROBE = 0.05  # 輸入を始めるときに外貨に換える、手元の自国通貨の割合（1ターン分）
# 島の値段が輸入の原価（取り分を含む）をこの割合より上回るときは、買い手の量より多めに入れる
PUSH_MARGIN = 0.2
# 売れ残った輸入の荷を値幅の下端まで下げて売るときも、仕入れ値のこの割合より安くは売らない
# （買い手の指値が島の値段と一緒に下がると、下端も毎ターン下がり、値段が 0 近くまで崩れるため）
CLEARANCE_FLOOR = 0.5


def is_planned(island: IslandState) -> bool:
    """計画経済の島か（公定価格がある。フェーズ3-4。hakoniwa_core/economy/plan.py）。"""
    return bool(island.ext.get("plan", {}).get("prices"))


ANY_RATE = 10**15  # 計画経済の輸入: どのレートでも引き合う（外貨は持っている分だけ使い、買わない）


@dataclass
class Prospect:
    """財ごとの、輸出と輸入の見込み（前のターンの値段とレートで）。"""

    good: str
    export_bloc: int | None = None
    export_currency: int | None = None
    export_limit: int = 0  # 島の市場で払える一番高い値段（自国通貨。運賃を含む）
    export_rate: int = 0  # 輸出が引き合う一番低いレート（今の島の値段で。× BASIS）
    import_bloc: int | None = None
    import_currency: int | None = None
    import_price: int = 0  # 圏の値段（決済通貨）
    import_limit: int = 0  # 圏で払える一番高い値段（決済通貨）
    import_rate: int = 0  # 輸入が引き合う一番高いレート（× BASIS）
    import_floor: int = 0  # 輸入が引き合う一番安い島の値段（自国通貨。0 は圏に売りがない）


def state_of(f: Facility) -> dict[str, Any]:
    """商社の状態（財ごとの狙う量・向き・仕入れ値など）。"""
    return f.economy.setdefault("trade", {"goods": {}})


def goods_of(f: Facility) -> dict[str, dict[str, Any]]:
    return state_of(f).setdefault("goods", {})


def entry(f: Facility, good: str) -> dict[str, Any]:
    """財の状態: dir（向き）、x・m（輸出と輸入の狙う量）、held（[持っている量（運ぶ途中を含む）,
    その仕入れ値]）、transit（運ぶ途中の量）、age（売れ残ったターン数）、bx・bm（使う圏）。"""
    item = goods_of(f).setdefault(good, {})
    item.setdefault("x", 0)
    item.setdefault("m", 0)
    item.setdefault("held", [0, 0])
    item.setdefault("transit", 0)
    item.setdefault("age", 0)
    return item


def views(island: IslandState) -> dict[str, dict[str, Any]]:
    """島が入っている貿易圏の、前のターンの様子（貿易の段が書く。圏の番号の文字列 → 様子）。"""
    return island_state(island).get("blocs", {})


def capacity_units(defs: Defs, config: GameConfig, good: str) -> int:
    """商社1社が1ターンに扱える量（財 good の 1000 分の 1 単位）。"""
    unit = unit_weight(defs, good)
    cap = config.get("trade.trader_capacity") * QUANTITY_SCALE
    if unit <= 0:
        return cap
    return cap * QUANTITY_SCALE // unit


def prospects(island: IslandState, defs: Defs, config: GameConfig) -> dict[str, Prospect]:
    """財ごとの輸出と輸入の見込み。港がないか、圏に入っていなければ空。"""
    if port_of(island, config) is None or not views(island):
        return {}
    cut = to_basis(config.get("trade.trader_cut"))
    fee = ships.port_fee(island, config)
    freight = ships.freight_rate(island, config)
    prices = island.economy.prices
    planned = is_planned(island)
    bloc_band = to_basis(config.get("trade.bloc_band"))
    out: dict[str, Prospect] = {}
    for good in tradable_goods(defs):
        p = Prospect(good)
        unit = unit_weight(defs, good)
        fee_unit = fee * unit // QUANTITY_SCALE
        home = prices.get(good, 0)
        subsidy = policy.export_subsidy(island, good)  # 輸出補助（フェーズ3-3）
        tariff = policy.tariff_rate(island, good)  # 関税（フェーズ3-3）
        best: tuple[bool, float] | None = None  # 輸入の圏の選び方（売りがあったか, 指値との開き）
        for key, view in sorted(views(island).items(), key=lambda kv: int(kv[0])):
            price = view.get("prices", {}).get(good)
            if not price or view.get("members", 0) < 2:
                continue
            currency = view["currency"]
            e = fxrates.rate(island, currency)
            value = price * e // BASIS
            limit = value - part(value, cut) - fee_unit + subsidy
            if limit > p.export_limit:
                p.export_bloc, p.export_currency, p.export_limit = int(key), currency, limit
                p.export_rate = -(
                    -max(0, home + fee_unit - subsidy)
                    * BASIS
                    * BASIS
                    // max(1, price * (BASIS - cut))
                )
            freight_unit = freight * unit * view.get("cells", 1) // QUANTITY_SCALE
            rate = 0 if view.get("no_tariff") else tariff
            duty = policy.with_tariff(value, rate)  # 圏の値段に関税を足した値
            landed = (duty + freight_unit + fee_unit) * BASIS // max(1, BASIS - cut) + 1
            # 輸入が引き合う値段は、前のターンに圏の市場に売りがあった財だけ（フェーズ3-2。誰も
            # 作っていない自動車で、島の値段と圏の買いの指値が互いを追いかけて上がり続けたため）
            on_sale = good in view.get("supply", ())
            if on_sale and (not p.import_floor or landed < p.import_floor):
                p.import_floor = landed
            if planned:  # 計画経済: 売りのある圏のうち、値段の一番安い圏から輸入する
                if on_sale and (p.import_bloc is None or price < p.import_price):
                    p.import_bloc, p.import_currency = int(key), currency
                    p.import_price = price
                    p.import_limit = auction.band(price, bloc_band)[1]
                    p.import_rate = ANY_RATE
                continue
            own = home - part(home, cut) - freight_unit - fee_unit
            bid = own * BASIS // max(1, policy.with_tariff(e, rate))
            # 前のターンに売りのあった圏を先に選ぶ（商社は取引の成り立つ市場に注文を出す。2 つの圏に
            # 入った国どうしで、売りと買いが別の圏に分かれ、どちらも約定しないままになっていた。試し
            # 遊び1回目）。どの圏にも売りがなければ、開きの一番大きい圏で買い注文を出して売りを待つ
            rank = (on_sale, (bid - price) / price)
            if bid > 0 and (best is None or rank > best):
                best = rank
                p.import_bloc, p.import_currency = int(key), currency
                p.import_price, p.import_limit = price, bid
                p.import_rate = own * BASIS // max(1, policy.with_tariff(price, rate))
        out[good] = p
    return out


def exportable(p: Prospect | None, home: int) -> bool:
    return p is not None and p.export_bloc is not None and p.export_limit > home


def importable(p: Prospect | None) -> bool:
    return p is not None and p.import_bloc is not None and p.import_limit > p.import_price


# --- 島の市場 -------------------------------------------------------------------------


@dataclass
class HomeOrder:
    """商社が島の市場に出す注文。limit は買いなら運賃を含めて払える額、売りなら手元に残したい額
    （None は指値なし）。"""

    facility: Facility
    good: str
    side: str
    qty: int
    limit: int | None


def home_orders(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> list[HomeOrder]:
    """島の市場の段で、商社が出す注文（輸出する物を買う・輸入した物を売る）。"""
    traders = traders_of(island, config)
    if not traders:
        return []
    found = prospects(island, defs, config)
    width = to_basis(config.get("market.band"))
    prices = island.economy.prices
    clearance = config.get("trade.clearance_turns")
    planned = is_planned(island)
    out: list[HomeOrder] = []
    for f in traders:
        budget = config.get("trade.trader_capacity") * QUANTITY_SCALE  # 扱える重さの残り
        for good, item in sorted(goods_of(f).items()):
            held = stock.total(f.stock, good)
            low, high = auction.band(prices.get(good, 1), width)
            if item.get("dir") == IMPORT:
                if held <= 0:
                    continue
                expiring = stock.expiring(f.stock, good, turn, defs.goods)
                if expiring:
                    out.append(HomeOrder(f, good, "sell", expiring, None))
                if held - expiring > 0:
                    cost = unit_cost(f, good)
                    if planned:  # 公定価格で売る（仕入れ値によらず）
                        out.append(HomeOrder(f, good, "sell", held - expiring, 0))
                        continue
                    if item["age"] >= clearance:
                        limit = max(low, round_money(cost * CLEARANCE_FLOOR))
                    else:
                        limit = min(high, max(cost, low))
                    out.append(HomeOrder(f, good, "sell", held - expiring, limit))
                continue
            if item.get("dir") == EXPORT and held > 0 and item["age"] >= 2 * clearance:
                # 圏で長く売れ残った輸出の荷は、島の市場に戻して売る（お金を寝かせないように）
                out.append(HomeOrder(f, good, "sell", held, None))
                continue
            p = found.get(good)
            if planned:  # 売れ残った物を公定価格で買って輸出する（値段の差によらず）
                if item.get("dir") != EXPORT or p is None or p.export_bloc is None:
                    continue
            elif item.get("dir") != EXPORT or not exportable(p, low):
                continue
            assert p is not None
            unit = unit_weight(defs, good)
            want = min(item["x"], capacity_units(defs, config, good)) - item["held"][0]
            if unit > 0:
                want = min(want, budget * QUANTITY_SCALE // unit)
            if want <= 0:
                continue
            # 計画経済は値幅の上端（公定価格）に運賃の上限を足した額まで払う
            limit = (
                high + part(high, to_basis(config.get("freight.max_rate")))
                if planned
                else p.export_limit
            )
            out.append(HomeOrder(f, good, "buy", want, limit))
            budget -= want * unit // QUANTITY_SCALE
    return out


def bought_home(f: Facility, good: str, qty: int, cost: int) -> None:
    """島の市場で輸出する物を買った（仕入れ値に足す）。"""
    item = entry(f, good)
    item["held"][0] += qty
    item["held"][1] += cost
    record = state_of(f).setdefault("turn", {})
    record["spent"] = record.get("spent", 0) + cost


def sold_home(f: Facility, good: str, qty: int) -> int:
    """島の市場で輸入した物を売った。売った分の仕入れ値を返す。"""
    item = entry(f, good)
    cost = take_cost(item, qty)
    sold = state_of(f).setdefault("turn", {}).setdefault("sold", {})
    sold[good] = sold.get(good, 0) + qty
    return cost


def take_cost(item: dict[str, Any], qty: int) -> int:
    """持っている量から qty を減らし、その分の仕入れ値（平均で）を返す。"""
    held = item["held"]
    if held[0] <= 0:
        return 0
    take = min(qty, held[0])
    cost = held[1] * take // held[0]
    held[0] -= take
    held[1] = held[1] - cost if held[0] > 0 else 0
    return cost


def add_cost(f: Facility, good: str, amount: int) -> None:
    """持っている荷の仕入れ値に、運賃や使用料を足す。"""
    if amount > 0:
        entry(f, good)["held"][1] += amount


def unit_cost(f: Facility, good: str) -> int:
    """持っている荷の 1 単位あたりの仕入れ値。"""
    qty, value = entry(f, good)["held"]
    return value * QUANTITY_SCALE // qty if qty > 0 else 0


# --- 狙う量と向き ------------------------------------------------------------------


def update_targets(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    bloc_unmet: dict[tuple[int, str], int],
    bloc_sellers: dict[tuple[int, str], int],
) -> None:
    """貿易の段の終わりに、商社ごと・財ごとの向きと狙う量を見直す。

    bloc_unmet は (圏, 財) ごとの圏の買えなかった量、bloc_sellers は (圏, 財) ごとの売りに出した商社
    の数。このターンに圏で売った量（輸出）と島の市場で売った量（輸入）は、商社の
    state_of(f)["turn"]。
    """
    traders = traders_of(island, config)
    if not traders:
        return
    found = prospects(island, defs, config)
    speed = config.get("trade.target_speed")
    records = {m.good: m for m in island.markets}
    importers: dict[str, int] = {}
    for f in traders:
        for good, item in goods_of(f).items():
            if item.get("dir") == IMPORT:
                importers[good] = importers.get(good, 0) + 1
    prices = island.economy.prices
    planned = is_planned(island)
    exporters: dict[str, int] = {}
    for f in traders:
        for good, item in goods_of(f).items():
            if item.get("dir") == EXPORT:
                exporters[good] = exporters.get(good, 0) + 1
    for f in traders:
        turn = state_of(f).pop("turn", {})
        sold_bloc = turn.get("exported", {})
        sold_home_ = turn.get("sold", {})
        _keep_history(config, f, turn)
        _forget_lost(f)
        for good in tradable_goods(defs):
            p = found.get(good)
            home = prices.get(good, 0)
            record = records.get(good)
            # 計画経済の島は、売れ残った物を値段によらず輸出する。公定価格は圏の値段より高いことが
            # 多く、値段で見ると初めて扱う物を輸出できず、国内に買い手のない物（ボーキサイトと
            # アルミ）を作る施設が動かなかった（フェーズ3-4）
            unsold = (
                planned
                and p is not None
                and p.export_bloc is not None
                and record is not None
                and record.supply > record.volume
            )
            if (
                good not in goods_of(f)
                and not unsold
                and not exportable(p, home)
                and not importable(p)
            ):
                continue
            item = entry(f, good)
            cap = capacity_units(defs, config, good)
            probe = round_money(cap * PROBE)
            held = item["held"][0]
            # 島で買い手のいない物は輸入せず、島で売りに出ていない物は輸出しない
            can_export = exportable(p, home) and record is not None and record.supply > 0
            can_import = importable(p) and record is not None and record.demand > 0
            if planned:  # 計画経済: 行列のある物を輸入し、売れ残った物を輸出する
                can_export = unsold
                can_import = (
                    p is not None
                    and p.import_bloc is not None
                    and record is not None
                    and record.demand > record.volume
                )
            direction = item.get("dir")
            if held <= 0:  # 荷が残っている間は向きを変えない（売り切ってから）
                if can_import and can_export:
                    assert p is not None
                    better = planned or _import_margin(p) > _export_margin(p, home)
                    direction = IMPORT if better else EXPORT
                elif can_import:
                    direction = IMPORT
                elif can_export:
                    direction = EXPORT
                else:
                    direction = None
            item["dir"] = direction
            if p is not None:
                item["bx"], item["bm"] = p.export_bloc, p.import_bloc
            want_x = want_m = 0
            if direction == EXPORT and can_export:
                assert p is not None
                key = (p.export_bloc, good)
                share = bloc_unmet.get(key, 0) // max(1, bloc_sellers.get(key, 0))
                want_x = sold_bloc.get(good, 0) + share
                if _probing(item["x"], sold_bloc.get(good, 0), stock.total(f.stock, good)):
                    want_x = max(want_x, item["x"] + probe)
                assert record is not None
                want_x = min(want_x, record.supply)
                if planned:  # 売れ残った量を、輸出する商社で分ける
                    unsold = max(0, record.supply - record.volume)
                    want_x = sold_bloc.get(good, 0) + unsold // max(1, exporters.get(good, 1))
            if direction == IMPORT and can_import:
                assert record is not None
                unmet = max(0, record.demand - record.volume)
                share = unmet // max(1, importers.get(good, 1))
                want_m = sold_home_.get(good, 0) + share
                left = stock.total(f.stock, good) + item["transit"]
                if _probing(item["m"], sold_home_.get(good, 0), left):
                    want_m = max(want_m, item["m"] + probe)
                want_m = min(want_m, record.demand)
                if not planned and _import_margin(p) > PUSH_MARGIN and left < record.demand:
                    # 島の値段が輸入の原価よりずっと高い: 買い手の量より多めに入れて売り値を
                    # 下げる（その値段で買われている量だけを狙うと、売りが買いに届かず値段が
                    # 下がらないため）
                    want_m = max(want_m, record.demand + probe)
            item["x"] = _approach(item["x"], min(cap, want_x), speed, cap)
            item["m"] = _approach(item["m"], min(cap, want_m), speed, cap)
            # 売れ残ったターン数: 荷があるのにこのターンに1つも売れなかったら数え、売れたら 0 に戻す
            # （荷が続けて入っても、売れている間は値を選ばずに売り急がない）
            sold_now = (sold_bloc if direction == EXPORT else sold_home_).get(good, 0)
            if stock.total(f.stock, good) > 0 and sold_now <= 0:
                item["age"] += 1
            else:
                item["age"] = 0
            if not item["x"] and not item["m"] and held <= 0 and item["transit"] <= 0:
                goods_of(f).pop(good, None)


def _forget_lost(f: Facility) -> None:
    """持っているつもりの量（held。運ぶ途中を含む）が、手元と運ぶ途中の荷より多ければ、無くなった
    分を仕入れ値ごと外す（回4。傷んだ荷を外していなかった前の版の世界の分）。"""
    for good, item in goods_of(f).items():
        actual = stock.total(f.stock, good) + item.get("transit", 0)
        if item["held"][0] > actual:
            take_cost(item, item["held"][0] - actual)


def _probing(target: int, sold: int, left: int) -> bool:
    """多めに狙うか: 始めたばかり（狙う量が 0）か、このターンに売れて手元の荷がなくなったとき。"""
    return left <= 0 and (target <= 0 or sold > 0)


def _import_margin(p: Prospect) -> float:
    return (p.import_limit - p.import_price) / max(1, p.import_price)


def _export_margin(p: Prospect, home: int) -> float:
    return (p.export_limit - home) / max(1, home)


def _approach(value: int, goal: int, speed: float, cap: int) -> int:
    """value を goal へ speed の割合だけ近づける（少なくとも 1 動かす。0 へは全力の 1% を
    切ったら）。"""
    if goal == value:
        return value
    step = round_money(abs(goal - value) * speed) or 1
    new = value + step if goal > value else value - step
    new = max(0, min(cap, new))
    if goal == 0 and new < cap // 100:
        return 0
    return new


def _keep_history(config: GameConfig, f: Facility, turn: dict[str, Any]) -> None:
    """直近 trade.entry_turns ターンの扱った重さ・島の市場で使った額を残す（雇う人数・開業・
    手元に残すお金の見込み）。"""
    keep = config.get("trade.entry_turns")
    state = state_of(f)
    state["volume"] = [*state.get("volume", []), turn.get("volume", 0)][-keep:]
    state["spent"] = [*state.get("spent", []), turn.get("spent", 0)][-keep:]
    abroad = state.setdefault("abroad", {})
    spent = turn.get("abroad", {})
    for key in sorted(set(abroad) | set(spent)):
        abroad[key] = [*abroad.get(key, []), spent.get(key, 0)][-keep:]
        if not any(abroad[key]):
            del abroad[key]


def spent_abroad(f: Facility, currency: int, amount: int) -> None:
    """このターンに圏の市場で輸入に払った額（決済通貨）を足す（持っておく外貨の見込みに使う）。"""
    record = state_of(f).setdefault("turn", {}).setdefault("abroad", {})
    record[str(currency)] = record.get(str(currency), 0) + amount


def abroad_average(f: Facility, currency: int) -> int:
    """直近 trade.entry_turns ターンに圏の市場で輸入に払った額の平均（決済通貨）。"""
    history = state_of(f).get("abroad", {}).get(str(currency), [])
    return sum(history) // len(history) if history else 0


def add_volume(f: Facility, load: int) -> None:
    """このターンに扱った重さ（1000 分の 1 トン）を足す。"""
    record = state_of(f).setdefault("turn", {})
    record["volume"] = record.get("volume", 0) + load


def exported(f: Facility, good: str, qty: int) -> None:
    """このターンに圏で売った量を足す（狙う量の見直しに使う）。"""
    record = state_of(f).setdefault("turn", {}).setdefault("exported", {})
    record[good] = record.get(good, 0) + qty


def volume_share(config: GameConfig, f: Facility) -> float:
    """直近の扱った重さの平均の、扱える重さに対する割合。"""
    history = state_of(f).get("volume", [])
    if not history:
        return 0.0
    return sum(history) / len(history) / (config.get("trade.trader_capacity") * QUANTITY_SCALE)


def spend_estimate(f: Facility) -> int:
    """次のターンに島の市場で輸出する物に使う見込み（直近の平均）。"""
    history = state_of(f).get("spent", [])
    return sum(history) // len(history) if history else 0


def workers_wanted(config: GameConfig, defs: Defs, f: Facility) -> int:
    """商社が雇いたい人数: 定義の人数 × 直近の扱った重さの割合（少なくとも 1 割）。"""
    full = defs.facilities[f.kind]["workers"]
    share = max(0.1, min(1.0, volume_share(config, f)))
    return round_money(full * share + 0.5)


def import_needs(
    f: Facility,
    views_: dict[str, dict[str, Any]],
    found: dict[str, Prospect],
    turns: int,
    probe: dict[int, int] | None = None,
) -> dict[int, list[tuple[int, int]]]:
    """次の turns ターンの輸入に要る決済通貨（通貨ごとに [(額, 引き合う一番高いレート)]。圏の前の
    値段で）。今のレートで輸入の見込みがある財だけを数え、初めのターンは手元と運ぶ途中の荷の分を
    引く（売れ残っている間は買わないので）。

    通貨ごとの合計は、turns ×「直近に輸入に払った額の平均の FX_MARGIN 倍か、probe（通貨ごと。
    手元の自国通貨の一部を直したもの）の大きいほう」までに縮める（輸入できていないのに外貨を
    寝かせて、賃金や仕入れのお金が尽きないように）。"""
    out: dict[int, list[tuple[int, int]]] = {}
    for good, item in sorted(goods_of(f).items()):
        if item.get("dir") != IMPORT or not item["m"] or item.get("bm") is None:
            continue
        p = found.get(good)
        if p is None or not importable(p) or p.import_bloc != item["bm"]:
            continue
        view = views_.get(str(item["bm"]))
        if view is None:
            continue
        price = view.get("prices", {}).get(good, 0)
        qty = max(0, item["m"] - item["held"][0]) + item["m"] * max(0, turns - 1)
        amount = price * qty // QUANTITY_SCALE
        if amount > 0:
            out.setdefault(view["currency"], []).append((amount, p.import_rate))
    for currency, items in out.items():
        total = sum(a for a, _ in items)
        cap = turns * max(
            round_money(abroad_average(f, currency) * FX_MARGIN), (probe or {}).get(currency, 0)
        )
        if 0 < cap < total:
            out[currency] = [(a * cap // total, r) for a, r in items]
    return out


def export_floors(f: Facility, found: dict[str, Prospect]) -> dict[int, int]:
    """輸出している財のうち一番引き合うものの、輸出が引き合う一番低いレート（通貨ごと）。稼いだ
    決済通貨は、これより安くは売らない（売るより持っておき、輸出を控える）。"""
    out: dict[int, int] = {}
    for good, item in goods_of(f).items():
        p = found.get(good)
        if item.get("dir") != EXPORT or p is None or p.export_currency is None:
            continue
        if p.export_rate > 0:
            c = p.export_currency
            out[c] = min(out.get(c, p.export_rate), p.export_rate)
    return out


def imported(island: IslandState, config: GameConfig) -> set[str]:
    """商社が輸入している財（狙う量があるか、手元か運ぶ途中に荷がある）。"""
    out = set()
    for f in traders_of(island, config):
        for good, item in goods_of(f).items():
            if item.get("dir") == IMPORT and (item["m"] > 0 or item["held"][0] > 0):
                out.add(good)
    return out
