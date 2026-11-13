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
- 輸入した物: 帳簿の値（手元の荷の平均の仕入れ値）と仕入れ直しの値（買う圏で前のターンに約定した
  値段 × 今のレート ＋ 関税・運賃・港の使用料）の安い方を下限に売る（低価法。FX-63）。手元の荷が
  「狙う量 × market.inventory_turns × market.clearance_ratio」を超えたまま trade.clearance_turns
  続いたら、超えた分だけをアウトレット（市場の段の2回目の板寄せ。MK-41）に出し、売れ残るターンごとに値幅ずつ
  下げる。止まりは下限の market.clearance_floor 倍と輸出し直して引き合う値の高い方で、そこでも
  trade.clearance_turns の 2 倍のターン売れ残れば、その分を輸出の荷に替えて圏で売る（FX-64）。
- 輸出の荷が、指値を外しても圏で trade.clearance_turns の 2 倍のターン売れ残ったら、島の市場に戻して
  指値なしで売る。

貿易圏の市場（貿易の段。hakoniwa_core/modules/trade）:
- 輸出の荷を、仕入れ値に取り分と積む港の使用料を足した値段（決済通貨に直す）を指値に売る。
  trade.clearance_turns ターン売れ残ったら、指値を外す。
- 輸入: 島の市場の前の値段から、取り分・船の運賃（借りた船の運賃の直近の実績を含む。FX-63）・
  下ろす港の使用料を引き、関税（フェーズ3-3。圏内の関税をなくした圏ではかからない）の分を割り引いて
  決済通貨に直した値段を指値に買う。
  量は「狙う量 ＋（持つ量 − 手元と運ぶ途中の輸入の荷）× market.inventory_adjust」（FX-66）で、空いて
  いる船、持っている決済通貨、商社の扱える重さ（trade.trader_capacity）で頭打ちになる。決済通貨と
  扱える重さが足りなければ、財の名前の順でなく、1 単位のお金あたりの儲けの大きい財から配る
  （FX-67）。運賃の見込みは、圏の加盟国のうち近い半分までの平均の距離で見る。

島の市場で前のターンに買い手のいなかった物は輸入せず、島の売り手が売りに出していなかった物は輸出
しません。狙う量は島の市場の買いたい量（輸入）と、島の売り手の売りたい量（輸出）までです。島の売り手
は施設と国の倉庫で、海外の商人と商社の売り（輸入した物と、圏で売れ残って島に戻した輸出の荷）は数え
ません（home_supply。島で作っておらず在庫もない物を、海外の商人の売れ残りを見て輸出しようとして
いた。試し遊び1回目）。

狙う量（財ごと。1ターンに流れる量）は毎ターン、売れた量と足りない量の分け前に trade.target_speed の
割合だけ近づけます（荷が島と圏の間で行ったり来たりしないように）。輸出の足りない量は圏の市場の買え
なかった量を、輸入の足りない量は島の市場で約定の値段以上を付けたのに買えなかった量（本当の品不足。
FX-65）を、同じ財を扱う商社の数で分けたものです。輸入の荷が持ちすぎの線（FX-64）を超えている間は、
輸入の狙う量を据え置きます。値段の差があって、手元の荷を売り切ったときは、全力の 1 割ずつ多めに
狙います（始めのうちは売れた量も足りない量もないため。売れずに手元の荷がないだけのときは増やさ
ない）。島の値段が輸入の原価を PUSH_MARGIN より上回るときは、島の市場の買いたい量より多めに狙い
ます（売りを増やして値段を原価のほうへ下げる）。

輸入の荷は、手元と運ぶ途中を合わせて「狙う量 ×（注文してから島で売れるまでのターン数 ＋ 在庫の
目安）」まで持ちます（持つ量。FX-66）。かかるターン数は自社の荷の（着いたターン − 積んだターン
＋ 1）の直近 trade.entry_turns 回の平均で、実績がないうちは 2 × 一番近い加盟国までの片道 ＋ 1 です。

計画経済の島（フェーズ3-4。公定価格のある島）の商社は、値段の差ではなく、島の市場で買えなかった
量（行列）のある財を、圏の値段で一番安い圏から輸入し、島の売り手の売れ残った財を輸出します（貿易の
独占）。輸出する量は、市場経済の島の商社と同じく、圏で売れた量と圏の買えなかった量の分け前（売り
切ったら多めに）を狙い、島の売れ残りを輸出する商社で分けた分までです（売れ残りをぜんぶ狙うと、
圏に買い手のない物の島での買いに扱える重さと預金を使い切り、圏に買い手のいる物を輸出できなかった）。
島では
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

import math
from dataclasses import dataclass, field
from typing import Any

from hakoniwa_core import ledger, stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import Payer, books, facility_entity, funds, owner_entity
from hakoniwa_core.market import auction
from hakoniwa_core.state import Facility, IslandState, MarketRecord
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
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.trade.merchant import BLOCKADE as BLOCKADE_KEY
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, part, round_money, to_basis

EXPORT = "x"
IMPORT = "m"
PROBE = 0.1  # 値段の差があって売り切ったときに、多めに狙う量（全力に対する割合）
# 商社の状態: 次のターンの1ターン分の輸入の見込み（決済通貨の番号の文字列 → 額。FX-61。貿易の段の
# 記録で書き、銀行の段の次の支払いの見込みが読む）
IMPORT_NEED = "import_need"
# 島の値段が輸入の原価（取り分を含む）をこの割合より上回るときは、買い手の量より多めに入れる
PUSH_MARGIN = 0.2
# island.scratch: 財 → 島の市場で約定の値段以上を付けたのに買えなかった量（本当の品不足。市場の段が
# 書く。FX-65）
TRUE_SHORT = "trade_true_short"
# 財の状態: 持ちすぎの分の値下げ（FX-64）。{"turns": 線を超えたターン数, "line": 線, "steps":
# 値下げの段, "start": 値下げを始めた指値, "stop": 止まりの値, "stuck": 止まりで売れ残ったターン数}
OUTLET = "outlet"
# 財の状態: 輸出の荷に替えて圏で売る量（FX-64。持ちすぎの分が止まりの値でも売れなかった）と、
# 売る圏、圏で売れ残ったターン数
REEXPORT = "reexport"
REEXPORT_BLOC = "reexport_bloc"
REEXPORT_AGE = "reexport_age"
# 島の貿易の状態: (圏, 財) ごとの借りた船の運賃と買った量の直近 trade.cost_turns ターン分（FX-63）
CHARTER = "charter_seen"


def is_planned(island: IslandState) -> bool:
    """計画経済の島か（公定価格がある。フェーズ3-4。hakoniwa_core/economy/plan.py）。"""
    return bool(island.ext.get("plan", {}).get("prices"))


GOV_TRADE = "trade"  # 政府が貿易に払う項目（どの項目にも取り置いていない国庫から）
# 売りの二国間契約で国の倉庫に足りない分を政府が島の市場で買う項目（FX-24・FS-09。国債の利払い
# の次に取り置く。報告 #9: 取り置きの外の国庫から払うと、予算を大きく決めた国では残りがなく
# 買えなかった）
CONTRACT_SUPPLY = "contract_supply"


def contract_supply_plan(island: IslandState, config: GameConfig) -> dict[str, tuple[int, int]]:
    """売りの二国間契約のこのターンの量のうち、国の倉庫に足りない分（財 → (量, 指値の上限)）。

    上限は島の値段の値幅の上端（契約は外国との約束なので、国内の値段が契約の値段より高くても、
    損を出して買う。2026-11-12 に戻した。11-10 から 11-12 は契約の値段までで、国内が少しでも
    高いと売りが余っていても買えず、契約が届かなかった。掲示板 #31）。市場の段の買いと、予算の段の
    取り置きの見積もりの両方で使う。"""
    needs: dict[str, int] = {}
    for c in island.ext.get("trade", {}).get("contracts", []):
        if c.get("side") == "sell" and c.get("active"):
            needs[c["good"]] = needs.get(c["good"], 0) + c["qty"]
    width = to_basis(config.get("market.band"))
    prices = island.economy.prices
    plan = {}
    for good, qty in sorted(needs.items()):
        missing = qty - stock.total(island.warehouse, good)
        if missing > 0:
            plan[good] = (missing, auction.band(prices.get(good, 1), width)[1])
    return plan


PRICE_GAPS = "trade.price_gap_subsidies"  # 統計: 政府が国営の商社に払った価格差の補給金（1ターン）


def cover_price_gap(
    island: IslandState, f: Facility, entity: str, cost: int, received: int
) -> None:
    """計画経済の島の国営の商社が、仕入れ値 cost の物を received（自国の通貨）で売った損を、政府が
    貿易の項目から価格差の補給金として埋める（払える分だけ）。輸入した物を公定価格で島に売った分
    （市場の段）と、公定価格で買った余りを圏の値段で輸出した分（貿易の段）の両方。国の貿易の独占で
    は、国内の値段と外国の値段の差は国庫が持つ（ソ連の対外貿易の価格差補填）。前は差を銀行の運転
    資金で借り続け、利子が積み上がって銀行が中央銀行から借り続けた（W-158。2026-11-01）。
    国庫直轄の商社（entity が政府）は国庫の中なので払わない。"""
    gap = cost - received
    if gap <= 0 or entity == ledger.GOV or not is_planned(island):
        return
    if owner_entity(f) != ledger.GOV:
        return
    payer = Payer(ledger.GOV, GOV_TRADE)
    paid = min(gap, funds.available(island, payer))
    if paid <= 0:
        return
    funds.pay(island, payer, entity, paid, "price_gap_subsidy")
    books.add(island, f.id, books.SUBSIDY, paid)
    island.stats[PRICE_GAPS] = island.stats.get(PRICE_GAPS, 0) + paid


ANY_RATE = 10**15  # 計画経済の輸入: どのレートでも引き合う（外貨は持っている分だけ使い、買わない）
# island.scratch: 財 → [島の外から来た売り（海外の商人と商社）の量, そのうち売れた量]
# （市場の段が書く）
FOREIGN_SUPPLY = "foreign_supply"


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
    # 圏 → 1 単位を仕入れ直す値（自国通貨。前のターンに約定した値段 × 今のレート ＋ 関税・運賃・
    # 港の使用料。取り分は入れない。前のターンに約定した圏だけ。封鎖・禁輸の圏は除く。FX-63）
    restock: dict[int, int] = field(default_factory=dict)
    no_restock: set[int] = field(default_factory=set)  # 封鎖・禁輸で仕入れ直せない圏
    # 圏 → 輸入した荷をその圏で輸出し直して手元に残る 1 単位の値（自国通貨。圏の値段 × 今のレート −
    # 取り分 − 港の使用料。輸出補助は入れない。FX-64 の値下げの止まり）
    resale: dict[int, int] = field(default_factory=dict)


def state_of(f: Facility) -> dict[str, Any]:
    """商社の状態（財ごとの狙う量・向き・仕入れ値など）。"""
    return f.economy.setdefault("trade", {"goods": {}})


def goods_of(f: Facility) -> dict[str, dict[str, Any]]:
    return state_of(f).setdefault("goods", {})


def entry(f: Facility, good: str) -> dict[str, Any]:
    """財の状態: dir（向き）、x・m（輸出と輸入の狙う量）、held（[持っている量（運ぶ途中を含む）,
    その仕入れ値]）、transit（運ぶ途中の量）、age（売れ残ったターン数）、bx・bm（使う圏）、hold（輸入の
    荷の持つ量。FX-66）、leads（注文から売れるまでのターン数の実績）、restock（最後の仕入れ直しの値。
    FX-63）、outlet・reexport（持ちすぎの分の値下げと輸出し直し。FX-64）。"""
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
    listed = _listed_exports(island, config)
    # 港が封鎖されている島は、どの財も仕入れ直せない（FX-63。帳簿の値で売る）
    blockaded = bool(island_state(island).get(BLOCKADE_KEY))
    out: dict[str, Prospect] = {}
    for good in tradable_goods(defs):
        p = Prospect(good)
        export_rank: tuple[bool, int] | None = None  # 輸出の圏の選び方（荷を出した圏か, 指値）
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
            # 輸出の荷が売れ残っている間は、その荷を売りに出した圏で売り続ける（買い手が荷のある圏
            # へ来る。輸入は売りのあった圏を選ぶので、輸出も前のターンの相手の圏へ移ると、両方が
            # 毎ターン入れ違い続ける。試し遊び1回目の見直し）。荷がなければ一番高く売れる圏
            rank = (listed.get(good) == int(key), limit)
            if limit > 0 and (export_rank is None or rank > export_rank):
                export_rank = rank
                p.export_bloc, p.export_currency, p.export_limit = int(key), currency, limit
                p.export_rate = -(
                    -max(0, home + fee_unit - subsidy)
                    * BASIS
                    * BASIS
                    // max(1, price * (BASIS - cut))
                )
            # 運賃は自国の船の見込みに、借りた船の運賃（戦争の割増を含む）の直近の実績の 1 単位
            # あたりを足す（FX-63。CIF 建てで比べる。前は借りた船の運賃を数えずに買い、届いてから
            # 原価が島の値段を超えた荷を抱えた）
            # 自国の船の運賃は、自国の船で運んだ割合の分だけ（借りた船で運んだ分に重ねない）
            freight_unit = freight * unit * view.get("cells", 1) // QUANTITY_SCALE
            share = view.get("charter_share", {}).get(good, 0)
            freight_unit = freight_unit * (BASIS - share) // BASIS
            freight_unit += view.get("charter", {}).get(good, 0) * e // BASIS
            p.resale[int(key)] = max(0, value - part(value, cut) - fee_unit)
            rate = 0 if view.get("no_tariff") else tariff
            duty = policy.with_tariff(value, rate)  # 圏の値段に関税を足した値
            restock = duty + freight_unit + fee_unit  # 1 単位を仕入れ直す値（取り分なし）
            landed = restock * BASIS // max(1, BASIS - cut) + 1
            if blockaded or good in view.get("embargoed", ()):
                p.no_restock.add(int(key))
            elif good in view.get("traded", ()):
                p.restock[int(key)] = restock
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


def _listed_exports(island: IslandState, config: GameConfig) -> dict[str, int]:
    """財 → 島の商社が輸出の荷を売りに出している圏（売れ残った荷がある間）。"""
    out: dict[str, int] = {}
    for f in traders_of(island, config):
        for good, item in goods_of(f).items():
            held = item.get("held", [0, 0])[0]
            if item.get("dir") == EXPORT and held > 0 and item.get("bx") is not None:
                out.setdefault(good, item["bx"])
    return out


def exportable(p: Prospect | None, home: int) -> bool:
    return p is not None and p.export_bloc is not None and p.export_limit > home


def importable(p: Prospect | None) -> bool:
    return p is not None and p.import_bloc is not None and p.import_limit > p.import_price


# --- 島の市場 -------------------------------------------------------------------------


@dataclass
class HomeOrder:
    """商社が島の市場に出す注文。limit は買いなら運賃を含めて払える額、売りなら手元に残したい額
    （None は指値なし）。clearance は持ちすぎの分の値下げの売り（アウトレットに出す。FX-64）。"""

    facility: Facility
    good: str
    side: str
    qty: int
    limit: int | None
    clearance: bool = False


def home_orders(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> list[HomeOrder]:
    """島の市場の段で、商社が出す注文（輸出する物を買う・輸入した物を売る）。止まっている商社は
    出さない（W-159）。"""
    traders = traders_of(island, config, turn)
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
                # 輸出の荷に替えた分（FX-64）は港に置いて圏で売る
                held -= min(held, item.get(REEXPORT, 0))
                if held <= 0:
                    continue
                expiring = stock.expiring(f.stock, good, turn, defs.goods)
                expiring = min(expiring, held)
                if expiring:
                    out.append(HomeOrder(f, good, "sell", expiring, None))
                if held - expiring > 0:
                    if planned:  # 公定価格で売る（仕入れ値によらず）
                        out.append(HomeOrder(f, good, "sell", held - expiring, 0))
                        continue
                    floor = sell_floor(f, good, item, found.get(good))
                    limit = min(high, max(floor, low))
                    cut = _clearance_order(
                        config, item, found.get(good), floor, limit, held - expiring
                    )
                    if cut is not None:
                        out.append(HomeOrder(f, good, "sell", cut[0], cut[1], clearance=True))
                    # 荷はみな、ふだんの指値でふだんの板寄せ（1回目）に出す。値下げの売りは、1回目で
                    # 売れ残った分のうち超えた分だけを2回目に出し直すもの（MK-41 と同じ）
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


def sell_floor(f: Facility, good: str, item: dict[str, Any], p: Prospect | None) -> int:
    """輸入した荷を島で売る指値の下限（FX-63。低価法）: 帳簿の値（手元と運ぶ途中の荷の平均の仕入れ
    値）と、仕入れ直しの値の安い方。仕入れ直しの値は、買う圏（item["bm"]）で前のターンに約定して
    いればその値を荷ごとに残し、約定がない（買う圏がなくなったときも）ければ残した最後の値を使う。
    一度も値がなければ帳簿の値。
    封鎖・禁輸で仕入れ直せない財と、港か圏がない島は帳簿の値（残した値も使わない）。"""
    book = unit_cost(f, good)
    bloc = item.get("bm")
    if p is None or (bloc is not None and not _can_restock(p, bloc)):  # 港も圏もない・封鎖・禁輸
        item.pop("restock", None)
    elif bloc is not None and bloc in p.restock:
        item["restock"] = p.restock[bloc]
    restock = item.get("restock")
    if restock is None or restock <= 0:
        return book
    return min(book, restock) if book > 0 else restock


def _can_restock(p: Prospect, bloc: int) -> bool:
    """封鎖・禁輸で止まっていないか（圏で約定がなかっただけなら残した値を使う）。"""
    return bloc not in p.no_restock


def _clearance_order(
    config: GameConfig,
    item: dict[str, Any],
    p: Prospect | None,
    floor: int,
    limit: int,
    offer: int,
) -> tuple[int, int] | None:
    """持ちすぎの分の値下げの売り（FX-64）: (量, 指値)。出さなければ None。

    手元の荷が持ちすぎの線を超えたまま trade.clearance_turns 続いた（record_home_sales が数える）
    ら、線を超えた分を出す。指値はふだんの指値 limit（下限 floor。値幅の下端の方が高ければ下端）
    から始め、売れ残るターンごとに market.band ずつ下げ、「floor × market.clearance_floor」と輸出し
    直して手元に残る値（輸出し直す圏の Prospect.resale。輸出補助なし）の高い方で止める。"""
    state = item.get(OUTLET)
    if not state or state["turns"] < config.get("trade.clearance_turns"):
        return None
    qty = offer - state["line"]
    if qty <= 0:
        return None
    if "start" not in state:
        state["start"] = limit
        state["steps"] = 0
    stop = round_money(floor * config.get("market.clearance_floor"))
    bloc = resale_bloc(item)
    if p is not None and bloc is not None:
        stop = max(stop, p.resale.get(bloc, 0))
    price = round_money(state["start"] * (1 - config.get("market.band")) ** state["steps"])
    state["stop"] = stop
    return qty, max(1, stop, min(limit, price))


def record_home_sales(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    f: Facility,
    good: str,
    sales: tuple[int, int, int, int],
) -> None:
    """市場の段の後に、輸入した荷の売れ残りを数える（FX-64）。sales は (ふだんの指値で出した量,
    売れた量, 値下げで出した量, 売れた量)。傷みかけの指値なしの売りと、市場の段の後に着く荷は
    数えない。

    売れ残り（ふだんの指値で出して、どちらの板寄せでも売れなかった量）が持ちすぎの線（狙う量 ×
    market.inventory_turns × market.clearance_ratio。値下げを始めた後は始めたときの線）を超えて
    いれば数え、超えていなければ値下げをやめる。値下げで出した分が売れ残れば値下げの段を1つ進め、止まりの
    値で売れ残ったターンが trade.clearance_turns の 2 倍になれば、超えた分を輸出の荷に替える。
    clearance_ratio が 0 か計画経済の島なら値下げしない。"""
    item = goods_of(f).get(good)
    if item is None or item.get("dir") != IMPORT:
        return
    offered, sold, cut_offered, cut_sold = sales
    ratio = config.get("market.clearance_ratio")
    state = item.get(OUTLET)
    if not ratio or is_planned(island):
        item.pop(OUTLET, None)
        return
    if state is None:
        turns = stock.target_turns(config.get("market.inventory_turns"), defs.goods, good)
        line = round_money(item["m"] * turns * ratio)
    else:
        line = state["line"]
    left = offered - sold - cut_sold
    if left <= line:
        item.pop(OUTLET, None)
        return
    if state is None:
        state = item[OUTLET] = {"turns": 0, "line": line}
    state["turns"] += 1
    if cut_offered > 0 and cut_sold < cut_offered and "start" in state:
        price = round_money(state["start"] * (1 - config.get("market.band")) ** state["steps"])
        if price <= state.get("stop", 0):  # 止まりの値で売れ残った
            state["stuck"] = state.get("stuck", 0) + 1
        else:
            state["steps"] += 1
    if state.get("stuck", 0) >= 2 * config.get("trade.clearance_turns"):
        bloc = resale_bloc(item)
        if bloc is None:  # 輸出し直す圏がない: 止まりの値で売りに出し続ける
            return
        item[REEXPORT] = item.get(REEXPORT, 0) + left - line
        item[REEXPORT_BLOC] = bloc
        item[REEXPORT_AGE] = 0
        item.pop(OUTLET, None)


def resale_bloc(item: dict[str, Any]) -> int | None:
    """持ちすぎの分を輸出し直す圏: 輸出し直している圏、なければ荷を買う圏、なければ輸出の見込みの
    一番よい圏（FX-64）。"""
    for key in (REEXPORT_BLOC, "bm", "bx"):
        if item.get(key) is not None:
            return item[key]
    return None


def keep_reexport(config: GameConfig, item: dict[str, Any], sold: int, held: int) -> None:
    """輸出し直す荷の見直し（貿易の段の終わり）: 荷がなくなれば外す。圏で trade.clearance_turns の
    2 倍のターン 1 つも売れなければ、島の市場に戻す（輸出の荷が島に戻る決まりと同じ。圏を抜けた・
    圏に買い手がいないときに、荷が島でも圏でも売れないまま残らないように）。"""
    if not item.get(REEXPORT):
        return
    if held <= 0:
        for key in (REEXPORT, REEXPORT_BLOC, REEXPORT_AGE):
            item.pop(key, None)
        return
    item[REEXPORT] = min(item[REEXPORT], held)
    item[REEXPORT_AGE] = 0 if sold > 0 else item.get(REEXPORT_AGE, 0) + 1
    if item[REEXPORT_AGE] >= 2 * config.get("trade.clearance_turns"):
        for key in (REEXPORT, REEXPORT_BLOC, REEXPORT_AGE):
            item.pop(key, None)


def true_shortage(island: IslandState, record: MarketRecord | None) -> int:
    """島の市場の本当の品不足（FX-65）: 約定の値段以上を付けたのに買えなかった量（市場の段の
    TRUE_SHORT）。市場の段の記録がなければ、買いたい量 − 約定した量。"""
    if record is None:
        return 0
    found = island.scratch.get(TRUE_SHORT)
    if found is None:
        return max(0, record.demand - record.volume)
    return max(0, found.get(record.good, 0))


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
    turn: int | None = None,
) -> None:
    """貿易の段の終わりに、商社ごと・財ごとの向きと狙う量を見直す。

    bloc_unmet は (圏, 財) ごとの圏の買えなかった量、bloc_sellers は (圏, 財) ごとの売りに出した商社
    の数。このターンに圏で売った量（輸出）と島の市場で売った量（輸入）は、商社の
    state_of(f)["turn"]。止まっている商社（turn を渡したとき）は、扱った量の記録だけ残して狙う量は
    見直さない（注文を出していないので、売れなかった分で狙う量を下げない。W-159）。
    """
    traders = traders_of(island, config)
    working = {f.id for f in traders_of(island, config, turn)}
    if not traders:
        return
    found = prospects(island, defs, config)
    speed = config.get("trade.target_speed")
    records = {m.good: m for m in island.markets}
    importers: dict[str, int] = {}
    for f in traders:  # 足りない分は動いている商社で分ける（荷が余っている商社は除く。FX-65）
        if f.id not in working:
            continue
        for good, item in goods_of(f).items():
            if item.get("dir") == IMPORT and not surplus(item):
                importers[good] = importers.get(good, 0) + 1
    prices = island.economy.prices
    planned = is_planned(island)
    exporters: dict[str, int] = {}
    for f in traders:
        if f.id not in working:
            continue
        for good, item in goods_of(f).items():
            if item.get("dir") == EXPORT:
                exporters[good] = exporters.get(good, 0) + 1
    for f in traders:
        turn = state_of(f).pop("turn", {})
        sold_bloc = turn.get("exported", {})
        sold_home_ = turn.get("sold", {})
        _keep_history(config, f, turn)
        _forget_lost(f)
        if f.id not in working:
            continue
        for good in tradable_goods(defs):
            p = found.get(good)
            home = prices.get(good, 0)
            record = records.get(good)
            supply, left_over = home_supply(island, record)
            # 計画経済の島は、売れ残った物を値段によらず輸出する。公定価格は圏の値段より高いことが
            # 多く、値段で見ると初めて扱う物を輸出できず、国内に買い手のない物（ボーキサイトと
            # アルミ）を作る施設が動かなかった（フェーズ3-4）
            unsold = planned and p is not None and p.export_bloc is not None and left_over > 0
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
            # 島で買い手のいない物は輸入せず、島の売り手が売りに出していない物は輸出しない（海外の
            # 商人の売れ残りや、商社が輸入した物を輸出し直さない）
            can_export = exportable(p, home) and supply > 0
            can_import = importable(p) and record is not None and record.demand > 0
            if planned:  # 計画経済: 行列のある物を輸入し、島の売り手の売れ残った物を輸出する
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
                want_x = min(want_x, supply)
                if planned:  # 圏で売れる量まで。売れ残りを輸出する商社で分けた分を超えない
                    plan_x = sold_bloc.get(good, 0) + left_over // max(1, exporters.get(good, 1))
                    want_x = min(want_x, plan_x)
            frozen = False
            if direction == IMPORT and can_import and not planned and surplus(item):
                # 荷が持ちすぎの線を超えている間は、狙う量を据え置く（下げも積み増しもしない。
                # FX-65。売れ残りを品不足と読んで借りてまで積み増さず、売り切ってから 1 割ずつ
                # 立ち上げ直して振れもしない）
                frozen = True
            elif direction == IMPORT and can_import:
                assert record is not None
                # 足りない量は、約定の値段以上を付けたのに買えなかった量（本当の品不足。FX-65。
                # 自分の売りの指値が高くて買われなかった分を品不足に数えない）
                unmet = true_shortage(island, record)
                share = unmet // max(1, importers.get(good, 1))
                want_m = sold_home_.get(good, 0) + share
                left = stock.total(f.stock, good) + item["transit"]
                if _probing(item["m"], sold_home_.get(good, 0), left):
                    want_m = max(want_m, item["m"] + probe)
                want_m = min(want_m, record.demand)
                pipeline = round_money(
                    record.demand * holding_turns(island, defs, config, item, good)
                )
                # 多めに入れるのは、島の売り手が売りに出した分がみな売れたときだけ（島で作った
                # 物が売れ残っていれば、値段は売りが余って下がる。島の製油所の燃料が売れ残って
                # いるのに、島の値段が輸入の原価より高いのを見て燃料を買いたい量の 8 倍まで
                # 積み、燃料の値段が崩れて製油所が原油を買えず取引が止まった。2026-11-13 の
                # full と調べ 4）
                if (
                    not planned
                    and left_over <= 0
                    and _import_margin(p) > PUSH_MARGIN
                    and left < pipeline
                ):
                    # 島の値段が輸入の原価よりずっと高い: 買い手の量より多めに入れて売り値を
                    # 下げる（その値段で買われている量だけを狙うと、売りが買いに届かず値段が
                    # 下がらないため）。手元と運ぶ途中の荷は、買いたい量の持つ量と比べる（船に
                    # 積んだ分で止めない。FX-66）
                    want_m = max(want_m, record.demand + probe)
            item["x"] = _approach(item["x"], min(cap, want_x), speed, cap)
            if not frozen:
                item["m"] = _approach(item["m"], min(cap, want_m), speed, cap)
            # 輸入の荷の持つ量（FX-66）: 狙う量 ×（注文してから島で売れるまで ＋ 在庫の目安）。
            # 引き合わなくなったらすぐ 0
            if direction == IMPORT and (can_import or frozen):
                turns = holding_turns(island, defs, config, item, good)
                # 流れる分は、狙う量と島の買いたい量の少ない方（多めに入れる分は何ターン分も
                # 持たない。持つ量が島の買いたい量の 8 倍を超え、荷がまとめて着いて値段が崩れた）
                flow = item["m"] if record is None else min(item["m"], record.demand)
                item["hold"] = round_money(flow * turns)
            elif direction == IMPORT:
                item["hold"] = 0
            else:
                item.pop("hold", None)
            keep_reexport(config, item, sold_bloc.get(good, 0), stock.total(f.stock, good))
            # 売れ残ったターン数: 荷があるのにこのターンに1つも売れなかったら数え、売れたら 0 に戻す
            # （荷が続けて入っても、売れている間は値を選ばずに売り急がない）
            sold_now = (sold_bloc if direction == EXPORT else sold_home_).get(good, 0)
            if stock.total(f.stock, good) > 0 and sold_now <= 0:
                item["age"] += 1
            else:
                item["age"] = 0
            if not item["x"] and not item["m"] and held <= 0 and item["transit"] <= 0:
                goods_of(f).pop(good, None)


def surplus(item: dict[str, Any]) -> bool:
    """輸入の荷が持ちすぎの線を超えている（値下げを数えている）か、輸出し直す荷がある（FX-64・
    FX-65）。"""
    return bool(item.get(OUTLET)) or item.get(REEXPORT, 0) > 0


def lead_turns(
    island: IslandState, config: GameConfig, item: dict[str, Any], weightless: bool = False
) -> float:
    """輸入の注文から島で売れるまでのターン数の見込み（FX-66）: 自社の荷の（着いたターン − 積んだ
    ターン ＋ 1）の直近の平均（item["leads"]。貿易の段の荷下ろしが書く）。実績がないうちは、使う圏
    の一番近いほかの加盟国まで、船は往復（2 × 片道 ＋ 1）、重さのない財は片道 ＋ 1。"""
    leads = item.get("leads")
    if leads:
        return sum(leads) / len(leads)
    view = views(island).get(str(item.get("bm")), {})
    oneway = max(1, math.ceil(view.get("near", 1) / config.get("trade.ship_speed") - 1e-9))
    return float(oneway + 1 if weightless else 2 * oneway + 1)


def holding_turns(
    island: IslandState, defs: Defs, config: GameConfig, item: dict[str, Any], good: str
) -> float:
    """輸入の荷の持つ量が、1ターンに流れる量の何ターン分か（FX-66）: 注文から島で売れるまでの
    ターン数 ＋ 在庫の目安（施設と同じ market.inventory_turns。傷む物は短い）。"""
    weightless = unit_weight(defs, good) <= 0
    shelf = stock.target_turns(config.get("market.inventory_turns"), defs.goods, good)
    return lead_turns(island, config, item, weightless) + shelf


def import_want(config: GameConfig, item: dict[str, Any], planned: bool = False) -> int:
    """輸入の注文の量（FX-66）: 狙う量 ＋（持つ量 − 手元と運ぶ途中の荷）× market.inventory_adjust
    （施設の操業目標と同じ形。足りない分を一度に埋めない）。0 より少なくはしない。手元の荷が持ちすぎ
    の線を超えている間（surplus）は注文しない（売れない物を追加で仕入れない。FX-64・FX-65）。
    計画経済の島（planned）の商社は前のまま「狙う量 − 手元と運ぶ途中の荷」。"""
    m = item.get("m", 0)
    if planned:
        return max(0, m - item["held"][0])
    if m <= 0 or surplus(item):
        return 0
    hold = item.get("hold", m)
    gap = hold - item["held"][0]
    return max(0, m + round_money(gap * config.get("market.inventory_adjust")))


def share_by_rank(
    flows: list[int], fills: list[int], units: list[int], budget: int, ranks: list[float]
) -> list[int]:
    """足りない budget を、儲けの大きい注文から配る（FX-67）: まず 1 ターンに流れる分 flows を
    ranks（1 単位のお金あたりの儲け）の大きい順に、余りを足りない分を埋める注文 fills に同じ順に。
    同じ値の注文は並びの順。足りていればそのまま。割り振った量を返す。"""
    budget = max(0, budget)
    got = [0] * len(flows)
    order = sorted(range(len(flows)), key=lambda k: -ranks[k])
    for qty in (flows, fills):
        for k in order:
            if qty[k] <= 0 or budget <= 0:
                continue
            need = qty[k] * units[k] // QUANTITY_SCALE
            if need <= budget:
                got[k] += qty[k]
                budget -= need
            else:
                part_qty = budget * QUANTITY_SCALE // max(1, units[k])
                got[k] += part_qty
                budget = 0
    return got


def keep_lead(config: GameConfig, item: dict[str, Any], turns: int) -> None:
    """届いた輸入の荷の、注文から島で売れるまでのターン数を残す（直近 trade.entry_turns 回）。"""
    keep = config.get("trade.entry_turns")
    item["leads"] = [*item.get("leads", []), max(1, turns)][-keep:]


def home_supply(island: IslandState, record: MarketRecord | None) -> tuple[int, int]:
    """このターンの島の市場で、島の売り手（施設と国の倉庫）が売りに出した量と、その売れ残り。海外の
    商人の売りと商社の売り（FOREIGN_SUPPLY）は、島で作った物でも島にある物でもないので除く（輸出の
    向きと狙う量は、これで決める）。"""
    if record is None:
        return 0, 0
    offered, sold = island.scratch.get(FOREIGN_SUPPLY, {}).get(record.good, (0, 0))
    supply = max(0, record.supply - offered)
    unsold = max(0, record.supply - record.volume - (offered - sold))
    return supply, unsold


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
    # 取り逃した輸入の儲けと仕入れ（FX-62。貿易の段の _keep_missed）
    state["missed"] = [*state.get("missed", []), turn.get("missed", [0, 0])][-keep:]
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


def missed_average(f: Facility) -> tuple[int, int]:
    """直近 trade.entry_turns ターンに取り逃した輸入の儲けと仕入れの平均（自国通貨。FX-62）。"""
    history = state_of(f).get("missed", [])
    if not history:
        return 0, 0
    return (
        sum(p for p, _ in history) // len(history),
        sum(c for _, c in history) // len(history),
    )


def spend_estimate(f: Facility) -> int:
    """次のターンに島の市場で輸出する物に使う見込み（直近の平均）。"""
    history = state_of(f).get("spent", [])
    return sum(history) // len(history) if history else 0


def workers_wanted(config: GameConfig, defs: Defs, f: Facility) -> int:
    """商社が雇いたい人数: 定義の人数 × 直近の扱った重さの割合（少なくとも trade.staff_floor。荷が
    ないまま圏にも入れない商社が、定義の人数の 1 割の賃金を払い続けて赤字だった。W-157）。"""
    full = defs.facilities[f.kind]["workers"]
    share = max(config.get("trade.staff_floor"), min(1.0, volume_share(config, f)))
    return max(1, round_money(full * share + 0.5))


def import_need_own(island: IslandState, f: Facility) -> int:
    """次のターンの1ターン分の輸入の見込みのうち、持っている外貨で足りない分（自国通貨。今の
    レートで直す。FX-61・BK-51）。島の通貨の圏の分はそのまま足す。"""
    entity = facility_entity(f.id)
    total = 0
    for key, amount in sorted(state_of(f).get(IMPORT_NEED, {}).items()):
        currency = int(key)
        if currency == island.id:
            total += amount
            continue
        missing = amount - fxmoney.holding(island, entity, currency)
        if missing > 0:
            total += fxrates.to_own(island, currency, missing)
    return total


def trade_collateral(island: IslandState, f: Facility) -> tuple[int, int, int]:
    """商社の荷と外貨の値打ち（自国通貨。BK-51）: (手元の輸入の荷, 運ぶ途中の荷, 外貨の持ち高)。
    荷は仕入れ値と島の値段の低い方（足りない島の高い値段で担保を膨らませない）、外貨は今の
    レートと買ったときのレート（帳簿の値打ち）の低い方で見る。"""
    prices = island.economy.prices
    held = transit = 0
    for good, item in sorted(goods_of(f).items()):
        if item.get("dir") != IMPORT:
            continue
        cost = unit_cost(f, good)
        price = prices.get(good, 0)
        unit = min(cost, price) if cost > 0 else price
        held += unit * stock.total(f.stock, good) // QUANTITY_SCALE
        transit += unit * item.get("transit", 0) // QUANTITY_SCALE
    entity = facility_entity(f.id)
    fx_value = 0
    for key, (amount, book) in sorted(fxmoney.holdings(island).get(entity, {}).items()):
        if amount > 0:
            fx_value += min(fxrates.to_own(island, int(key), amount), max(0, book))
    return held, transit, fx_value


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
