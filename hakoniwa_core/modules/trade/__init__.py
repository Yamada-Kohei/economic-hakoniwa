"""貿易の段（フェーズ3-1。世界の段）と、貿易の確かめの段。

島と島をまたぐ処理は、市場の段の後の世界の段 trade で、島の番号・距離・圏と契約の番号の順に決めます
（島の順番は結果に効かない）。中身の部品は hakoniwa_core/trade/ にあります。

1. 届いた荷を買い手の港で下ろす（下ろす港の使用料。港の積み下ろしの余裕を超えた荷は次のターンへ）。
   国の倉庫に入る腐る物（契約で買った食品など）と、契約で「市場に出す」と決めた物は、次のターンからの
   市場の段が売りに出す
2. 海運業の船: 使えるターン数を過ぎた船を解体し、市場で買った船を船団に入れる
3. 圏の作成・加盟・設定、契約の申し込み・受け入れ・やめる（命令の段の申し送りを、島の番号の順に）
4. 為替の板寄せ（島の通貨と、その島が使う決済通貨の組ごと。hakoniwa_core/trade/fx.py）。外貨建ての
   契約で政府がこのターンに払う外貨は、板寄せの前に中央銀行が持っている外貨から先に政府へ回し、
   足りない分だけ板で買う。中央銀行の受け身の売りに足りない外貨は中央銀行どうしの融通で借り、
   全部の板の後に融通の利払いと返済をする（hakoniwa_core/trade/swap.py）
5. 二国間契約の履行（船と港の余裕は契約が先に使う）
6. 圏×財の板寄せ（値幅 trade.bloc_band）。約定は近い島の組から割り当て、代金はその場で決済通貨の
   島の帳簿の上で払う。荷は買い手の島の船で運び、往復の間（片道の 2 倍）船はほかに使えない
7. 抜けると決めた島を圏から外し、期間の終わった契約を終わりにする
8. 記録: 船の運賃と港の扱った量を見直し、商社の狙う量を見直し、次のターンの市場の段のために、圏の
   値段とレートを島に書く（``island.ext["trade"]["blocs"]``）

お金: 圏の市場の代金は仕訳の種類 trade、契約は contract、運賃は shipping_freight、港の使用料は
port_fee、為替は fx_exchange、融通は fx_swap と swap_interest（hakoniwa_core/trade/money.py）。

GDP: 輸出は売り手の島の商社の売上（契約は国の倉庫の売り渡し）、輸入は買い手の島の商社の仕入れ（契約
は国の倉庫の買い入れ）と同じ額を、貿易の部門の exports・imports に足します。運ぶ途中の荷は、買った
ときに買い手の商社の在庫に数えます（届いたときには数えない）。

貿易の確かめの段（trade_check。記録の段の後）は、外貨の持ち高と非居住者の預金の突き合わせと、運ぶ
途中の荷の数を確かめ、崩れていればそのターンを失敗にします。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import Any

from hakoniwa_core import ledger, stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.diplomacy import treaties as diplomacy_treaties
from hakoniwa_core.diplomacy import war
from hakoniwa_core.economy import SUBSIDIES, Payer, books, entity_of, funds
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.military import naval
from hakoniwa_core.modules.records import LedgerCheckFailed
from hakoniwa_core.orders import defer, fail_deferred, take_deferred
from hakoniwa_core.registry import OrderFailed, OrderHandler, Registry, WorldStage
from hakoniwa_core.state import Facility, IslandState, LogEntry, WorldState
from hakoniwa_core.trade import (
    abroad,
    balance,
    blocs,
    contracts,
    currency_label,
    fx_priority,
    is_trader,
    island_state,
    merchant,
    next_id,
    one_way_turns,
    policy,
    port_of,
    send_to_market,
    shipping_of,
    ships,
    started,
    swap,
    tradable,
    tradable_goods,
    traders,
    traders_of,
    unit_weight,
    weight,
    world_state,
)
from hakoniwa_core.trade import cells as route_cells
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.trade import orders as ship_orders
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import (
    BASIS,
    QUANTITY_SCALE,
    money_of,
    part,
    to_basis,
)

GOV_ITEM = (
    "trade"  # 政府が貿易（契約・運賃・使用料）に払うお金の項目（どの項目にも取り置いていない国庫）
)
REQUESTS = "trade_requests"  # island.scratch: 命令の段が積んだ申し送り

# 商社が輸入の狙う量を圏で買えなかったわけ（商社の財の状態の why。貿易の画面に出す）
WHY_NO_PRICE = "no_price"  # 圏にまだ値段がない
WHY_PRICE = "price"  # 運賃と港の使用料を引くと、島の値段では引き合わない
WHY_SHIPS = "ships"  # 空いている輸送船が足りない
WHY_CAPACITY = "capacity"  # 商社の1ターンに扱える重さの上限
WHY_MONEY = "money"  # 払う決済通貨（外貨）が足りない
WHY_SELLERS = "sellers"  # 圏の売りが足りないか、売りの指値が高い
WHY_BUYERS = "buyers"  # 圏の買いが足りないか、買いの指値が安い（輸出）
WHY_FUNDS = "funds"  # 払うお金（商社の預金）が足りない（決済通貨が自国の通貨のとき）
WHY_QUOTA = "quota"  # 輸入の割り当て（フェーズ3-3）の上限まで買った
WHY_BLOCKADE = "blockade"  # 封鎖で船出を断られた（MI-34。船が足りないのとは分ける。W-187）
WHY_PRIORITY = "fx_priority"  # 計画経済の商社が、外貨を使う順で先の財に外貨を取り置いた（FX-76）


def gov_payer() -> Payer:
    return Payer(ledger.GOV, GOV_ITEM)


def request(island: IslandState, kind: str, payload: dict[str, Any]) -> None:
    """命令の段から、貿易の段への申し送りを積む。"""
    defer(island, REQUESTS, kind, payload)


@dataclass
class _Bid:
    """圏の市場の注文1つ。"""

    island: int
    facility: Facility
    entity: str
    good: str


@dataclass
class _ImportTerms:
    """圏で1つの財を輸入するときの指値と原価（_import_terms）。"""

    limit: int  # 指値（決済通貨。圏の値幅の上端まで）
    freight_unit: int  # 1 単位の運賃（自国通貨）
    fee_unit: int  # 1 単位の港の使用料（自国通貨）
    cost: int  # 1 単位の輸入の原価（自国通貨。圏の値段・関税・運賃・使用料）
    profit: int  # 島の値段 − 原価（自国通貨。FX-62 の取り逃した儲け）
    # 1 単位の借りた船の運賃の見込み（決済通貨。決済通貨で払うので、外貨の見込みと割り振りに足す）
    charter: int = 0


@dataclass
class _Allocated:
    """商社の輸入の注文に割り振った量（FX-67。_allocate）。"""

    qty: int  # 割り振った量（船・割り当て・決済通貨・扱える重さで縮めた後）
    cut_capacity: int  # 1ターンに流れる分のうち、扱える重さで削った量（FX-62）
    cut_money: int  # 1ターンに流れる分のうち、決済通貨（自分のお金）で削った量（FX-62）
    why: str | None  # 縮めたわけ（扱える重さ・決済通貨）


@dataclass
class _IslandTurn:
    """このターンの島ごとの数（船・港・運賃の見直し）。"""

    port_used: int = 0
    handled: int = 0
    carried: int = 0  # 運んだ量（トン × 区画。1000 分の 1）
    fuel_value: int = 0
    ships_used: int = 0  # このターンに出した船の重さ（1000 分の 1 トン）
    ship_short: bool = False  # 船が足りずに輸入を減らした
    # 載せられなかった荷を運ぶのに要った船の重さ（1000 分の 1 トン。船の前注文の隻数。FX-60）
    ship_short_load: int = 0
    exports: int = 0
    imports: int = 0
    unpaid: int = 0  # お金が足りずに払えなかった運賃と使用料
    import_share: float = 1.0  # 輸入の注文に回せる船の割合
    trips: int = 2  # 輸入の往復の見込みのターン数（燃料の見積もり）
    shipments: list[int] = field(default_factory=list)
    turnover: dict[int, int] = field(default_factory=dict)  # 通貨 → 島の主体が払い・受け取った額
    # (圏, 財) → [借りた船の運賃（決済通貨）, 買った量, そのうち借りた船で運んだ量]（次のターン
    # からの輸入の原価。FX-63）
    charter: dict[tuple[int, str], list[int]] = field(default_factory=dict)

    def settled(self, currency: int, amount: int) -> None:
        self.turnover[currency] = self.turnover.get(currency, 0) + amount


@dataclass
class _ContractFx:
    """このターンに政府が外貨建ての契約の払いに用意した外貨（島と通貨ごと。どれもその通貨の額）。
    払えなかった契約のログと契約の記録に出す。"""

    need: int = 0  # このターンに契約で払う額の合計
    had: int = 0  # 政府がターンの初めに持っていた分
    reserve: int = 0  # 中央銀行の外貨から受け取った分
    market: int = 0  # 為替の市場で買えた分

    def short(self) -> int:
        return max(0, self.need - self.had - self.reserve - self.market)

    def values(self) -> dict[str, int]:
        return {
            "need": self.need,
            "had": self.had,
            "reserve": self.reserve,
            "market": self.market,
            "short": self.short(),
        }


class Trade(WorldStage):
    name = "trade"

    def collect(self, island: IslandState, ctx: StageContext) -> Any:
        return None

    def resolve(self, requests: dict[int, Any], ctx: StageContext) -> dict[int, Any]:
        assert ctx.before is not None
        _Round(ctx.before, ctx).run()
        return {}

    def apply(self, island: IslandState, result: Any, ctx: StageContext) -> None:
        return None


class _Round:
    """1ターン分の貿易の段の処理（世界全体を、決まった順で書き換える）。"""

    def __init__(self, world: WorldState, ctx: StageContext) -> None:
        self.world = world
        self.ctx = ctx
        self.defs: Defs = ctx.defs
        self.config: GameConfig = ctx.config
        self.turn = ctx.turn
        self.state = world_state(world)
        self.ids = [
            i
            for i in world.island_ids()
            if world.islands[i].has_economy and started(world.islands[i])
        ]
        self.islands = {i: _IslandTurn() for i in self.ids}
        self.quota_left: dict[tuple[int, str], int] = {}  # (島, 財) → 輸入の割り当ての残り
        self.cargo = self.state.setdefault("cargo", {"loaded": {}, "unloaded": {}})
        # (島, 通貨) → このターンに政府が外貨建ての契約の払いに用意した外貨
        self.contract_fx: dict[tuple[int, int], _ContractFx] = {}
        # 島 → 財ごとの輸出と輸入の見込み（為替の段で読み、記録の段で読み直す）
        self.found: dict[int, dict[str, traders.Prospect]] = {}
        # (島, 主体) → 輸入に使わずに残すお金（賃金と輸出の仕入れの見込み。FX-61。島の通貨の圏の
        # 輸入にも残す）
        self.reserve: dict[tuple[int, str], int] = {}
        # 島 → 外貨を自分の使えるお金の分しか買えなかった商社（FX-62 で取り逃しに数える）
        self.fx_limited: dict[int, set[str]] = {}
        # (島, 商社) → 財 → (取り逃した量, 1 単位の儲け, 1 単位の原価)（FX-62）
        self.missed: dict[tuple[int, int], dict[str, tuple[int, int, int]]] = {}
        # (島, 主体, 通貨) → 財 → 計画経済の国営の商社が、その財のために取り置いている外貨（FX-76）
        self.fx_queue: dict[tuple[int, str, int], dict[str, int]] = {}
        # (島, 商社, 財) → 輸入の注文に割り振った量（FX-67。圏の市場の前に _allocate が決める）
        self.alloc: dict[tuple[int, int, str], _Allocated] = {}

    def run(self) -> None:
        self._intercept()
        self._unload()
        for i in self.ids:
            island = self.world.islands[i]
            ships.scrap(island, self.config, self.turn)
            ships.commission(island, self.defs, self.config, self.turn)
        self._requests()
        self._fx()
        self._contracts()
        bloc_unmet, bloc_sellers = self._blocs()
        blocs.settle_leaves(self.world)
        contracts.close(self.world, self.turn)
        self._close(bloc_unmet, bloc_sellers)
        self._merchant_blockade()

    def _merchant_blockade(self) -> None:
        """港が封鎖された島に、次のターンに海外の商人が来なくなる割合（W-186 回4。封鎖の強さと
        同じ）。"""
        groups = naval.blockades(self.world, self.defs, self.config, self.turn)
        for i in self.ids:
            island = self.world.islands[i]
            ships_ = sum(groups.get((island.wx, island.wy), {}).values())
            state = island_state(island)
            if ships_ > 0:
                state[merchant.BLOCKADE] = to_basis(naval.blockade_share(self.config, ships_))
            else:
                state.pop(merchant.BLOCKADE, None)

    # --- 0. 海での襲撃と封鎖（フェーズ4-3 の②。MI-33・MI-34・MI-48・MI-56） ----------------

    def _intercept(self) -> None:
        """航海中の貿易の船が、見張りの区画や封鎖の区画で襲われるか。行きで沈めば船だけ、帰りで
        沈めば荷も失う。封鎖で追い返された中立の船は 1 ターン待つ。"""
        watchers = naval.hunters(self.world, self.defs, self.config)
        if not watchers and not naval.blockades(self.world, self.defs, self.config, self.turn):
            return
        gone: set[int] = set()
        for s in sorted(self.state["shipments"], key=lambda s: s["id"]):
            # 護衛が艦を沈めると封鎖が弱まることがあるので、船ごとに数え直す
            groups = naval.blockades(self.world, self.defs, self.config, self.turn)
            outcome = naval.shipment_outcome(
                self.world, self.defs, self.config, self.turn, s, watchers, groups
            )
            if outcome is None:
                continue
            island = self.world.islands.get(s["to"])
            where = naval.position(self.world, s, self.turn)
            if island is None or where is None:
                continue
            carrier = self._carrier_of(s)
            if outcome == naval.TURNED:
                s["arrive"] += 1
                s["delay"] = s.get("delay", 0) + 1
                args = {"good": s["good"]}
                if carrier is not None and carrier.id != island.id:
                    # 借りた中立国の船は、戦っている国の艦にも沈められず追い返される
                    args["country"] = carrier.name
                    island.log.append(LogEntry("chartered_turned_back", args))
                else:
                    island.log.append(LogEntry("cargo_turned_back", args))
            elif where[1]:
                self._sink_cargo(island, s)
                gone.add(s["id"])
            elif carrier is not None and shipping_of(carrier, self.config) is not None:
                self._sink_ship(island, s)
                s["arrive"] += s["oneway"]
                s["delay"] = s.get("delay", 0) + s["oneway"]
        if gone:
            self.state["shipments"] = [s for s in self.state["shipments"] if s["id"] not in gone]

    def _carrier_of(self, s: dict[str, Any]) -> IslandState | None:
        """S1: 荷を運んでいる船の島（ほかの国の船を借りた荷はその国。ほかは買い手の島）。"""
        return self.world.islands.get(s.get("carrier", s["to"]))

    def _lose_ships(self, island: IslandState, s: dict[str, Any], release: bool = True) -> int:
        """沈んだ船を船団から除き、傷みと同じ費用に数える（運賃に乗る）。除いた隻数（1000 分の 1）を
        返す。行きで沈んだときは release=False（代わりの船が同じ荷の分を使い続ける）。S1: 船は荷を
        運んでいた国の船団から除く。"""
        carrier = self._carrier_of(s)
        shipping = shipping_of(carrier, self.config) if carrier is not None else None
        if carrier is None or shipping is None:
            return 0
        if release:
            ships.release(shipping, s["id"])
        lost = max(1, ships.ships_of_load(self.config, s["load"]))
        return ships.lose_ships(carrier, self.config, shipping, lost)

    def _sink_ship(self, island: IslandState, s: dict[str, Any]) -> None:
        count = self._lose_ships(island, s, release=False)
        # 船を失ったのは運んでいた国（借りた船ならその国）
        owner = self._carrier_of(s) or island
        owner.stats["trade.ships_sunk"] = owner.stats.get("trade.ships_sunk", 0) + count
        ships_ = count / QUANTITY_SCALE
        if owner.id == island.id:
            owner.log.append(LogEntry("ship_sunk_out", {"ships": ships_}))
            return
        owner.log.append(LogEntry("lent_ship_sunk", {"ships": ships_, "country": island.name}))
        island.log.append(
            LogEntry("chartered_ship_sunk_out", {"ships": ships_, "country": owner.name})
        )

    def _sink_cargo(self, island: IslandState, s: dict[str, Any]) -> None:
        """帰りの航海で沈んだ船の荷（沈んで失った荷として突き合わせに入れる）。"""
        good = s["good"]
        qty = sum(q for _, q in s["batches"])
        f = island.facility(s["facility"]) if s.get("facility") is not None else None
        if f is not None and is_trader(self.config, f):
            item = traders.entry(f, good)
            item["transit"] = max(0, item["transit"] - qty)
            books.stock_moved(island, f.id, good, -qty)  # 買ったときに入れた在庫の増えを消す
        count = self._lose_ships(island, s)
        unloaded = self.cargo["unloaded"]
        unloaded[good] = unloaded.get(good, 0) + qty
        owner = self._carrier_of(s) or island  # 船は運んでいた国の数、荷は買い手の数
        owner.stats["trade.ships_sunk"] = owner.stats.get("trade.ships_sunk", 0) + count
        stats = island.stats
        stats["trade.cargo_sunk"] = stats.get("trade.cargo_sunk", 0) + qty
        args = {"ships": count / QUANTITY_SCALE, "good": good, "amount": qty / QUANTITY_SCALE}
        if owner.id == island.id:
            island.log.append(LogEntry("ship_sunk_cargo", args))
            return
        owner.log.append(
            LogEntry("lent_ship_sunk", {"ships": args["ships"], "country": island.name})
        )
        island.log.append(LogEntry("chartered_ship_sunk_cargo", {**args, "country": owner.name}))

    # --- 1. 荷下ろし -------------------------------------------------------------------

    def _unload(self) -> None:
        waiting = []
        due = sorted(
            (s for s in self.state["shipments"] if s["arrive"] <= self.turn),
            key=lambda s: (s["arrive"], s["id"]),
        )
        for s in self.state["shipments"]:
            if s["arrive"] > self.turn:
                waiting.append(s)
        for s in due:
            island = self.world.islands.get(s["to"])
            if island is None or s["to"] not in self.islands:
                self._lost(s)
                continue
            here = self.islands[s["to"]]
            port = port_of(island, self.config)
            if port is not None:  # 港がなくなった（戦争で失うなど）島には、混み具合を見ずに下ろす
                room = ships.port_capacity(self.config, port) * QUANTITY_SCALE - here.port_used
                if s["load"] > room and here.port_used > 0:
                    waiting.append(s)  # 港が混んでいる。次のターンに下ろす
                    continue
            self._deliver(island, s, port)
        self.state["shipments"] = sorted(waiting, key=lambda s: s["id"])

    def _deliver(self, island: IslandState, s: dict[str, Any], port: Facility | None) -> None:
        here = self.islands[island.id]
        good, batches = s["good"], s["batches"]
        qty = sum(q for _, q in batches)
        f = island.facility(s["facility"]) if s.get("facility") is not None else None
        if f is not None and not is_trader(self.config, f):
            f = None
        fee = ships.fee_for(island, self.config, s["load"]) if port is not None else 0
        if f is not None:
            stock.put_batches(f.stock, good, batches)
            item = traders.entry(f, good)
            item["transit"] = max(0, item["transit"] - qty)
            # 注文（積んだターン）から島で売れる（次のターンの市場の段）までのターン数（FX-66）
            traders.keep_lead(self.config, item, self.turn - s.get("loaded", self.turn) + 1)
            paid = self._pay_fee(island, Payer(entity_of(self.config, f)), port, fee, f)
            traders.add_cost(f, good, paid)
            traders.add_cost(f, good, self._tariff(island, s, f, qty))
        else:
            stock.put_batches(island.warehouse, good, batches)
            if s.get("facility") is not None:  # 買った商社がなくなった（倒産など）: 国の倉庫へ
                island.log.append(LogEntry("cargo_to_warehouse", {"good": good}))
            amount = qty / QUANTITY_SCALE
            if self.defs.goods[good].get("shelf_life"):  # 腐る物は、住民に回るよう市場に出す
                send_to_market(island, good, qty)
                island.log.append(LogEntry("cargo_to_market", {"good": good, "amount": amount}))
            elif s.get("kind") == "contract" and s.get("delivery", contracts.MARKET) == (
                contracts.MARKET
            ):  # 契約で「市場に出す」と決めた物は、島の市場の値段で売りに出す
                send_to_market(island, good, qty)
                args = {"good": good, "amount": amount}
                island.log.append(LogEntry("cargo_to_market_contract", args))
            self._pay_fee(island, gov_payer(), port, fee, None)
        here.port_used += s["load"]
        here.handled += s["load"]
        carrier = self._carrier_of(s)  # S1: 借りた船は、運んだ国の船団に戻す
        shipping = shipping_of(carrier, self.config) if carrier is not None else None
        if shipping is not None:
            ships.release(shipping, s["id"])
        unloaded = self.cargo["unloaded"]
        unloaded[good] = unloaded.get(good, 0) + qty
        island.log.append(LogEntry("cargo_arrived", {"good": good, "amount": qty / QUANTITY_SCALE}))

    def _tariff(self, island: IslandState, s: dict[str, Any], f: Facility, qty: int) -> int:
        """関税（フェーズ3-3）: 商社が圏の市場で買った荷を下ろすとき、約定の値段 × 為替 × 率を国庫に
        払う（払えるだけ）。圏の代表が圏内の関税をなくしている圏の荷にはかからない。払った額を返す
        （仕入れ値に足す）。"""
        rate = policy.tariff_rate(island, s["good"])
        if rate <= 0 or s.get("kind") != "bloc" or s.get("price") is None:
            return 0
        bloc = blocs.find(self.world, s.get("ref", -1))
        if bloc is not None and blocs.no_tariff(bloc):
            return 0
        value = money_of(s["price"], qty)
        if s["currency"] != island.id:
            value = fxrates.to_own(island, s["currency"], value)
        amount = part(value, rate)
        entity = entity_of(self.config, f)
        if entity != ledger.GOV:
            have = funds.available(island, Payer(entity))
            if have < amount:
                stats = island.stats
                stats["trade.tariff_unpaid"] = stats.get("trade.tariff_unpaid", 0) + amount - have
                amount = have
            funds.pay(island, Payer(entity), ledger.GOV, amount, "tariff")
        books.add(island, f.id, books.TARIFF, amount)
        island.stats["trade.tariffs"] = island.stats.get("trade.tariffs", 0) + amount
        return amount

    def _lost(self, s: dict[str, Any]) -> None:
        """届け先の島がなくなった荷（記録の上で、下ろしたことにして消す）。S1: 借りた船は、運んで
        いた国の船団に戻す（その国はまだある）。"""
        qty = sum(q for _, q in s["batches"])
        unloaded = self.cargo["unloaded"]
        unloaded[s["good"]] = unloaded.get(s["good"], 0) + qty
        carrier = self._carrier_of(s)
        shipping = shipping_of(carrier, self.config) if carrier is not None else None
        if shipping is not None:
            ships.release(shipping, s["id"])

    # --- 港の使用料と船の運賃 -----------------------------------------------------------

    def _pay_fee(
        self,
        island: IslandState,
        payer: Payer,
        port: Facility | None,
        amount: int,
        f: Facility | None,
    ) -> int:
        """港の使用料を払う（払えるだけ）。払った額を返す。政府が国庫直轄の港に払うときは、お金は
        動かず、帳簿（港の売上と政府の運賃）にだけ残す。"""
        if port is None or amount <= 0:
            return 0
        payee = entity_of(self.config, port)
        if payer.entity != payee:
            have = funds.available(island, payer)
            if have < amount:
                self.islands[island.id].unpaid += amount - have
                amount = have
            funds.pay(island, payer, payee, amount, "port_fee")
        self._book_service(island, port, f, amount)
        return amount

    def _pay_freight(
        self, island: IslandState, payer: Payer, amount: int, f: Facility | None
    ) -> int:
        """自国の海運業に船の運賃を払う（払えるだけ）。払った額を返す。"""
        shipping = shipping_of(island, self.config)
        if shipping is None or amount <= 0:
            return 0
        payee = entity_of(self.config, shipping)
        if payer.entity != payee:
            have = funds.available(island, payer)
            if have < amount:
                self.islands[island.id].unpaid += amount - have
                amount = have
            funds.pay(island, payer, payee, amount, "shipping_freight")
        self._book_service(island, shipping, f, amount)
        return amount

    @staticmethod
    def _book_service(
        island: IslandState, provider: Facility, f: Facility | None, amount: int
    ) -> None:
        """港と海運業の売上を、払った側の運賃（商社）か政府の運賃（契約）と同じ額で帳簿に残す。"""
        if amount <= 0:
            return
        books.add(island, provider.id, books.SALES, amount)
        if f is not None:
            books.add(island, f.id, books.FREIGHT, amount)
        else:
            books.add_sector(island, books.GOVERNMENT, "freight", amount)

    # --- 2. 申し送り ---------------------------------------------------------------------

    def _requests(self) -> None:
        cancel_wait = self.config.get("trade.contract_cancel_turns")
        for i in self.world.island_ids():
            island = self.world.islands[i]
            policy.write_notes(self.world, island)
            for req, order_id in take_deferred(island, REQUESTS):
                kind = req["kind"]
                reason = None
                if i not in self.islands:
                    reason = "reason_trade_not_ready"
                elif kind == "send_aid":
                    reason = self._aid(island, req)
                elif kind == "create_bloc":
                    blocs.create(self.world, island, req["name"], req.get("open", True), self.turn)
                elif kind in ("propose_contract",):
                    if not tradable(self.defs, req["good"]):
                        reason = "reason_not_tradable"
                    elif req["partner"] not in self.islands:
                        reason = "reason_contract_partner"
                    else:
                        reason = contracts.propose(self.world, island, req, self.turn)
                elif kind in ("accept_contract", "cancel_contract"):
                    c = contracts.find(self.world, req["contract"])
                    if c is None:
                        reason = "reason_contract_unknown"
                    elif kind == "accept_contract":
                        reason = contracts.accept(
                            self.world, island, c, self.turn, req.get("delivery")
                        )
                    else:
                        reason = contracts.cancel(self.world, island, c, self.turn, cancel_wait)
                else:
                    reason = self._bloc_request(island, kind, req)
                if reason is not None:
                    fail_deferred(island, order_id, reason)
                    island.log.append(
                        LogEntry("trade_request_failed", {"order": kind, "reason": reason})
                    )

    def _bloc_request(self, island: IslandState, kind: str, req: dict[str, Any]) -> str | None:
        bloc = blocs.find(self.world, req.get("bloc", -1))
        if bloc is None:
            return "reason_bloc_unknown"
        if kind == "join_bloc":
            return blocs.join(self.world, island, bloc)
        if kind == "leave_bloc":
            return blocs.leave(island, bloc)
        if kind == "take_over_bloc":
            return blocs.take_over(self.world, island, bloc)
        if bloc["rep"] != island.id:
            return "reason_bloc_not_rep"
        if kind == "bloc_settings":
            if req.get("approve") is not None:
                return blocs.approve(self.world, bloc, req["approve"])
            if req.get("no_tariff") is not None:
                blocs.set_no_tariff(self.world, bloc, req["no_tariff"])
            if req.get("fixed_good") is not None:
                good = req["fixed_good"]
                if not tradable(self.defs, good):
                    return "reason_not_tradable"
                blocs.set_fixed(self.world, bloc, good, req.get("fixed_price", 0))
            return blocs.settings(self.world, bloc, req.get("open"), req.get("currency"))
        if kind == "transfer_bloc":
            return blocs.transfer(self.world, bloc, req["to"])
        return "reason_invalid_payload"

    def _aid(self, island: IslandState, req: dict[str, Any]) -> str | None:
        """援助（フェーズ3-3）。お金の援助は政府から相手の政府へ、どちらかの国の通貨でその場で送る
        （仕訳の種類 aid。経常収支の移転）。物の援助は、値段 0 で相手の承諾の要らない片道の契約に
        して、次のターンから船で運ぶ（contracts.aid）。"""
        partner = req.get("partner")
        if partner not in self.islands or partner == island.id:
            return "reason_contract_partner"
        if policy.blocked(self.world, island.id, partner, req.get("good") or ""):
            return "reason_embargo"
        other = self.world.islands[partner]
        if req.get("good") is not None:
            if not tradable(self.defs, req["good"]):
                return "reason_not_tradable"
            contracts.aid(self.world, island, req, self.turn)
            return None
        currency = req.get("currency", island.id)
        if currency not in (island.id, partner):
            return "reason_contract_currency"
        amount = int(req["amount"])
        fxrates.ensure(self.world, other, currency)
        if currency == island.id:
            if funds.available(island, gov_payer()) < amount:
                return "reason_aid_money"
            value = amount
        else:
            if fxmoney.holding(island, ledger.GOV, currency) < amount:
                return "reason_aid_money"
            value = fxrates.to_own(island, currency, amount)
        received = amount if currency == partner else fxrates.to_own(other, currency, amount)
        fxmoney.transfer(
            self.world,
            currency,
            (island.id, ledger.GOV),
            (partner, ledger.GOV),
            amount,
            "aid",
            received,
            payer=gov_payer(),
            values={island.id: value, partner: received},
        )
        diplomacy_treaties.note_payment(self.world, island.id, partner, currency, amount, self.turn)
        args = {"amount": amount, "currency": currency_label(self.world, currency)}
        island.log.append(LogEntry("aid_sent", {**args, "country": other.name}))
        other.log.append(LogEntry("aid_received", {**args, "country": island.name}))
        island.stats["aid.sent"] = island.stats.get("aid.sent", 0) + value
        other.stats["aid.received"] = other.stats.get("aid.received", 0) + received
        return None

    # --- 3. 為替 --------------------------------------------------------------------------

    def _fx(self) -> None:
        """島ごと・決済通貨ごとに、商社と政府の為替の注文を板寄せする。商社は、次の fx.keep_turns
        ターンの輸入に要る分を、輸入が引き合う一番高いレートまで買い、余った分を、輸出が引き合う
        一番低いレートより安くは売らない（賃金を払う自国通貨が足りないときは、その分は値幅の下端まで
        売る）。政府は契約の支払いに要る分を、まず中央銀行が持っている外貨から受け取り（_from_reserve）、
        足りない分を値幅の上端まで買い（このターンに払う分は、段に分けずにほかの買いより先に）、
        受け取った分を下端まで売る。次のターンからの払いの分は、中央銀行に残っている外貨で足りない
        分だけを前もって買う。市場の厚みには、このターンに効く契約の代金を足す（fxrates.depth）。"""
        width = fxrates.band_width(self.config)
        keep = self.config.get("fx.keep_turns")
        active = contracts.active(self.world, self.turn)
        for i in self.ids:
            island = self.world.islands[i]
            # 主体 → 通貨 → [(要る額, 払ってよい一番高いレート。None は値幅の上端)]
            needs: dict[str, dict[int, list[tuple[int, int | None]]]] = {}
            floors: dict[str, dict[int, int]] = {}  # 主体 → 通貨 → 受け取ってよい一番低いレート
            short: dict[str, int] = {}  # 主体 → 賃金に足りない自国通貨
            keep_all: set[str] = set()  # 余った外貨を売らない主体（計画経済の商社。フェーズ3-4）
            payers: dict[str, Payer] = {}
            found = traders.prospects(island, self.defs, self.config)
            wage = island.economy.average_wage or self.config.get("labor.initial_wage")
            caps: dict[str, dict[int, int]] = {}  # 主体 → 通貨 → 輸入のために買ってよい外貨
            self.found[i] = found
            limited = self.fx_limited.setdefault(i, set())
            quota_used: dict[str, int] = {}
            for f in traders_of(island, self.config, self.turn):  # 止まった商社は両替しない
                entity = entity_of(self.config, f)
                payers[entity] = Payer(entity)
                cash = funds.available(island, payers[entity])
                floors[entity] = traders.export_floors(f, found)
                wages = traders.workers_wanted(self.config, self.defs, f) * wage * keep
                short[entity] = max(0, wages - cash)
                if traders.is_planned(island):  # 計画経済は外貨を売らず、買わない
                    keep_all.add(entity)
                    needs[entity] = {}
                    continue
                full, step = self._import_needs(island, f, quota_used)
                needs[entity] = full
                # 外貨を買うのは、賃金と輸出の仕入れの見込みを残したお金だけ（FX-61）。通貨ごとの
                # 見込みの比で分ける。このターンの賃金と仕入れは払った後で、次のターンの分は
                # 銀行の段が次の支払いの見込みで貸すので、残すのは fx.keep_turns ターン分の
                # 賃金か、そのうち次のターンより後の分の賃金と仕入れの大きいほう
                later = (wages // keep + traders.spend_estimate(f)) * (keep - 1)
                self.reserve[(i, entity)] = max(wages, later)
                spendable = max(0, cash - self.reserve[(i, entity)])
                caps[entity], short_of_money = self._fx_caps(island, entity, full, step, spendable)
                if short_of_money:
                    limited.add(entity)
            due: dict[int, int] = {}  # 通貨 → このターンに契約で払う額（政府）
            for c in active:
                if c["buyer"] == i and c["currency"] != i:
                    amount = money_of(c["price"], c["qty"])
                    due[c["currency"]] = due.get(c["currency"], 0) + amount
            gov_need: dict[int, list[tuple[int, int | None]]] = {}
            for currency, amount in sorted(due.items()):
                self.contract_fx[(i, currency)] = self._from_reserve(island, currency, amount)
                # 次のターンからの払いも中央銀行の外貨から先に受け取るので、前もって買うのは
                # 中央銀行に残っている外貨で足りない分だけ
                ahead = amount * (keep - 1) - fxmoney.holding(island, ledger.CB, currency)
                gov_need[currency] = [(ahead, None)] if ahead > 0 else []
            needs[ledger.GOV] = gov_need
            payers[ledger.GOV] = gov_payer()
            self._capital_needs(island, needs, floors, payers, width)
            currencies = set(blocs.currencies_of(self.world, i))
            for entity_needs in needs.values():
                currencies |= set(entity_needs)
            halted = {  # 止まっている商社は外貨を持ったまま、売りも買いもしない（W-159）
                entity_of(self.config, f)
                for f in traders_of(island, self.config)
                if f.ext.get("halted_until", 0) >= self.turn
            }
            for entity, held in fxmoney.holdings(island).items():
                currencies |= {int(k) for k, (amount, _) in held.items() if amount > 0}
                # 中央銀行の注文は fxrates.dealer_orders が出す
                if entity != ledger.CB and entity not in halted:
                    payers.setdefault(entity, Payer(entity))
            # 中央銀行の介入と固定相場の通貨（フェーズ3-3。注文は intervention_orders が出す）
            steered = {int(k) for k in island.policy.fx_intervention} | {
                int(k) for k in island.policy.fx_peg
            }
            currencies |= steered
            for currency in sorted(currencies - {i}):
                if currency not in self.world.islands:
                    continue
                fxrates.ensure(self.world, island, currency)
                scheduled = sum(  # このターンに効く契約で、この通貨で払い・受け取る額
                    money_of(c["price"], c["qty"])
                    for c in active
                    if c["currency"] == currency and i in (c["buyer"], c["seller"])
                )
                room = abroad.reserve_room(island, self.config)  # 受け身の注文の上限まで
                if room is not None:
                    room = fxrates.to_foreign(island, currency, room)
                book = fxrates.Book(
                    depth=fxrates.depth(island, currency, self.config, scheduled),
                    peg_band=fxrates.peg_band(self.config),
                    reserve_room=room,
                    # 受け身の売りに足りない分は、中央銀行どうしの融通で借りる（swap.py）
                    borrow_room=swap.available(self.world, self.config, i, currency),
                    borrow=partial(swap.draw, self.world, self.config, i, currency),
                )
                reference = fxrates.rate(island, currency)
                for entity in sorted(payers):
                    self._fx_orders(
                        island,
                        book,
                        entity,
                        payers[entity],
                        currency,
                        needs.get(entity, {}).get(currency, []),
                        floors.get(entity, {}).get(currency),
                        short.get(entity, 0),
                        reference,
                        width,
                        due.get(currency, 0) if entity == ledger.GOV else 0,
                        entity in keep_all,
                        caps.get(entity, {}).get(currency),
                    )
                    if short.get(entity, 0) > 0:  # 売る分は、次の通貨の分から引く
                        have = fxmoney.holding(island, entity, currency)
                        short[entity] = max(0, short[entity] - have * reference // BASIS)
                if not book.buys and not book.sells and currency not in steered:
                    continue
                before = fxmoney.holding(island, ledger.GOV, currency)
                price, volume, _, _ = fxrates.clear(self.world, island, currency, book, width)
                island.stats[f"fx.volume.{currency}"] = volume
                record = self.contract_fx.get((i, currency))
                if record is not None:  # このターンの契約の払いのために、政府が板で買えた分
                    bought = fxmoney.holding(island, ledger.GOV, currency) - before
                    record.market = max(0, min(bought, record.need - record.had - record.reserve))
        swap.service(self.world, self.config)  # 融通の利払いと返済

    def _import_plan(
        self,
        island: IslandState,
        f: Facility,
        quota_used: dict[str, int],
        ships_now: bool = True,
    ) -> list[tuple[int, int, int, int, int]]:
        """商社の輸入の見込み（FX-61）: 輸入している財ごとに (決済通貨, 1 ターン分の量,
        fx.keep_turns ターン分の量, 指値, 輸入が引き合う一番高いレート)。量は注文の量（FX-66）を
        狙う量までにしたものを、圏の市場の注文と同じ頭打ち（輸入の割り当ての残り・圏の船で運べる量・扱える
        重さの残り）で縮める。2 ターン目からは狙う量を、割り当てと扱える重さ（財の間で分ける）で
        縮めた分（空いた船の数は次のターンには変わるので見ない）。ships_now でなければ 1 ターン分も
        空いた船の数で縮めない（_import_room）。quota_used（財 → 島のほかの商社が見込んだ量）に、
        見込んだ量を足す。計画経済の商社は外貨を買わないので空。"""
        if traders.is_planned(island):
            return []
        keep = self.config.get("fx.keep_turns")
        found = self.found.get(island.id) or {}
        left = self.config.get("trade.trader_capacity") * QUANTITY_SCALE
        for good, item in traders.goods_of(f).items():  # 輸出の荷も扱える重さを使う
            if item.get("dir") == traders.EXPORT:
                left -= weight(self.defs, good, stock.total(f.stock, good))
        rows = []  # (財, 決済通貨, 1 ターン目の量, 2 ターン目からの量, 指値, 引き合うレート)
        for good, item in sorted(traders.goods_of(f).items()):
            if item.get("dir") != traders.IMPORT or not item["m"] or item.get("bm") is None:
                continue
            p = found.get(good)
            if p is None or not traders.importable(p) or p.import_bloc != item["bm"]:
                continue
            bloc = blocs.find(self.world, item["bm"])
            if bloc is None:
                continue
            terms, _ = self._import_terms(island, good, bloc)
            if terms is None:
                continue
            quota = policy.quota(island, good)
            if quota is not None:
                quota = max(0, quota - quota_used.get(good, 0))
            big = 10**18  # 扱える重さは下で財の間に割り振る（FX-67）
            # 外貨は 1 ターンに流れる分（狙う量）までしか前もって買わない。持つ量を埋める分
            # （FX-66）は、持っている外貨の余りで買う（埋める分まで外貨を買うと、外貨の足りない島で
            # 埋まらないまま毎ターン自国通貨を売り続け、通貨が前の 2〜9 倍の速さで下がり、食料も
            # 輸入できなくなった。2026-11-12 の full と調べ 2・3）
            want = min(traders.import_want(self.config, item), item["m"])
            first, _, _ = self._import_room(island, good, bloc, want, big, quota, False, ships_now)
            # 2 ターン目からは 1 ターンに流れる分（狙う量）で数える（FX-66）
            each, _, _ = self._import_room(
                island, good, bloc, item["m"], big, policy.quota(island, good), False, False
            )
            if first <= 0 and each <= 0:
                continue
            # 外貨の見込みは、品物の代金に決済通貨で払う借りた船の運賃を足す
            price = terms.limit + terms.charter
            rank = terms.profit / max(1, terms.cost)
            rows.append(
                (good, bloc["currency"], first, each, price, p.import_rate, item["m"], rank)
            )
        units = [unit_weight(self.defs, r[0]) for r in rows]
        ranks = [r[7] for r in rows]
        firsts = traders.share_by_rank(
            [min(r[2], r[6]) for r in rows],
            [max(0, r[2] - r[6]) for r in rows],
            units,
            left,
            ranks,
        )
        eaches = traders.share_by_rank([r[3] for r in rows], [0] * len(rows), units, left, ranks)
        out = []
        for (good, currency, _, _, limit, rate, _, _), first, each in zip(
            rows, firsts, eaches, strict=True
        ):
            if first <= 0 and each <= 0:
                continue
            quota_used[good] = quota_used.get(good, 0) + first
            out.append((currency, first, first + each * (keep - 1), limit, rate))
        return out

    def _import_needs(
        self,
        island: IslandState,
        f: Facility,
        quota_used: dict[str, int],
        ships_now: bool = True,
    ) -> tuple[dict[int, list[tuple[int, int | None]]], dict[int, int]]:
        """外貨の見込み（FX-61）: 通貨ごとの [(fx.keep_turns ターン分の額, 引き合う一番高いレート)]
        と、通貨ごとの1ターン分の額（決済通貨）。島の通貨の圏の分も入れる（使えるお金を分ける比に
        使う。為替では買わない）。"""
        full: dict[int, list[tuple[int, int | None]]] = {}
        step: dict[int, int] = {}
        for currency, first, total, limit, rate in self._import_plan(
            island, f, quota_used, ships_now
        ):
            full.setdefault(currency, []).append((money_of(limit, total), rate))
            step[currency] = step.get(currency, 0) + money_of(limit, first)
        return full, step

    @staticmethod
    def _fx_caps(
        island: IslandState,
        entity: str,
        full: dict[int, list[tuple[int, int | None]]],
        step: dict[int, int],
        spendable: int,
    ) -> tuple[dict[int, int], bool]:
        """通貨ごとに、このターンに輸入のために買ってよい外貨（FX-61）: 持ち高を fx.keep_turns
        ターン分の見込みへ 1 ターン分ずつ近づける分を、使えるお金 spendable（通貨ごとの見込みの比で
        分ける）までにする。使えるお金で頭打ちになった通貨があれば True も返す（FX-62）。"""
        wanted: dict[int, int] = {}
        for currency, items in full.items():
            total = sum(amount for amount, _ in items)
            have = fxmoney.holding(island, entity, currency) if currency != island.id else 0
            wanted[currency] = max(0, min(total - have, step.get(currency, 0)))
        values = {
            c: (amount if c == island.id else fxrates.to_own(island, c, amount))
            for c, amount in wanted.items()
        }
        total_value = sum(values.values())
        caps: dict[int, int] = {}
        limited = False
        for currency, amount in sorted(wanted.items()):
            if currency == island.id:
                continue
            share = spendable * values[currency] // total_value if total_value > 0 else 0
            cap = min(amount, fxrates.to_foreign(island, currency, share))
            limited = limited or cap < amount
            caps[currency] = cap
        return caps, limited

    def _note_missed(
        self,
        island: IslandState,
        f: Facility,
        entity: str,
        good: str,
        terms: _ImportTerms,
        cut_capacity: int,
        cut_money: int,
        currency: int,
    ) -> None:
        """取り逃した儲け（FX-62）: 引き合う輸入のうち、商社の扱える重さか自分のお金で買えなかった
        量を数える。お金の分は、前のターンの銀行の段で銀行の体力（自己資本比率・大口融資の規制）に
        断られた商社と、外貨の注文を出しきったのに為替で買えなかった分は数えない（新しい商社も同じ
        壁に当たるため）。船・割り当ても数えない。"""
        if traders.is_planned(island) or terms.profit <= 0:
            return
        missed = cut_capacity
        if cut_money > 0 and traders.state_of(f).get("bank_wall") != self.turn - 1:
            if currency == island.id or entity in self.fx_limited.get(island.id, set()):
                missed += cut_money
        if missed > 0:
            self.missed.setdefault((island.id, f.id), {})[good] = (
                missed,
                terms.profit,
                terms.cost,
            )

    def _keep_import_needs(self, island: IslandState) -> None:
        """次のターンの1ターン分の輸入の見込み（決済通貨ごと）を商社に書く（FX-61。銀行の段の
        次の支払いの見込みが読む。economy/start.py の next_payments）。記録の段で、狙う量と圏の
        値段を見直した後に出し直す。"""
        self.found[island.id] = traders.prospects(island, self.defs, self.config)
        quota_used: dict[str, int] = {}
        working = {f.id for f in traders_of(island, self.config, self.turn)}
        for f in traders_of(island, self.config):
            state = traders.state_of(f)
            state.pop(traders.IMPORT_NEED, None)
            if f.id not in working:
                continue
            _, step = self._import_needs(island, f, quota_used, ships_now=False)
            need = {str(c): amount for c, amount in sorted(step.items()) if amount > 0}
            if need:
                state[traders.IMPORT_NEED] = need

    def _keep_missed(self, island: IslandState) -> None:
        """このターンに取り逃した儲けと仕入れ（FX-62）を、財ごとに島の市場の本当の品不足（FX-65）までに
        縮めて商社に書く（商社ごとの多めの狙いで膨らまないように）。"""
        records = {m.good: m for m in island.markets}
        by_good: dict[str, int] = {}
        for (iid, _), goods in self.missed.items():
            if iid == island.id:
                for good, (qty, _, _) in goods.items():
                    by_good[good] = by_good.get(good, 0) + qty
        for f in traders_of(island, self.config):
            goods = self.missed.get((island.id, f.id), {})
            profit = cost = 0
            for good, (qty, unit_profit, unit_cost) in sorted(goods.items()):
                # 島の市場の本当の品不足（約定の値段以上で買えなかった量。FX-65）までに縮める
                unmet = traders.true_shortage(island, records.get(good))
                qty = qty * min(unmet, by_good[good]) // max(1, by_good[good])
                profit += money_of(unit_profit, qty)
                cost += money_of(unit_cost, qty)
            turn = traders.state_of(f).setdefault("turn", {})
            turn["missed"] = [profit, cost]

    def _from_reserve(self, island: IslandState, currency: int, due: int) -> _ContractFx:
        """国の契約のこのターンの払いに要る外貨 due のうち、政府の手持ちで足りない分を、中央銀行が
        持っている外貨（為替介入で買った分や、受け身の買いで貯まった分）から先に受け取る（2026-10-20
        セビーリャさんの決定「国の契約だけ」。商社の輸入には回さない）。今のレートで、政府が自国通貨を
        中央銀行に払う（為替の市場で中央銀行から買うときと同じ仕訳の種類 fx_exchange。中央銀行の
        売りとして fx.dealt にも数える）。足りない分は、これまでどおり為替の市場で買う
        （_fx_orders）。介入で外貨を買っている通貨では中央銀行の受け身の売りが出ないので、前は政府が
        板で買えず、外貨を貯めている国でも契約が届かなかった（試し遊び1回目）。"""
        record = _ContractFx(need=due, had=fxmoney.holding(island, ledger.GOV, currency))
        e = fxrates.rate(island, currency)
        take = min(
            due - record.had,
            fxmoney.holding(island, ledger.CB, currency),
            funds.available(island, gov_payer()) * BASIS // max(1, e),
        )
        own = fxrates.to_own(island, currency, take)
        if take <= 0 or own <= 0:
            return record
        funds.pay(island, gov_payer(), ledger.CB, own, "fx_exchange")
        fxmoney.transfer(
            self.world,
            currency,
            (island.id, ledger.CB),
            (island.id, ledger.GOV),
            take,
            "fx_exchange",
            own,
        )
        key = f"fx.dealt.{currency}"
        island.stats[key] = island.stats.get(key, 0) - take
        record.reserve = take
        return record

    def _capital_needs(
        self,
        island: IslandState,
        needs: dict[str, dict[int, list[tuple[int, int | None]]]],
        floors: dict[str, dict[int, int]],
        payers: dict[str, Payer],
        width: int,
    ) -> None:
        """外国の国債を買うための外貨（フェーズ3-3。hakoniwa_core/trade/abroad.py）。政府は命令
        set_foreign_bonds で毎ターン買う額面の分を値幅の上端まで買う。市中銀行は abroad.bank_needs
        の分を値幅の上側の半ばまでで買い、当てのない外貨は下側の半ばより安くは売らない。"""
        gov_need = needs.setdefault(ledger.GOV, {})
        for key, amount in sorted(island.policy.foreign_bonds.get(ledger.GOV, {}).items()):
            other = self.world.islands.get(int(key))
            if amount > 0 and other is not None:
                if abroad.may_buy(self.world, self.defs, island, ledger.GOV, other):
                    gov_need.setdefault(other.id, []).append((amount, None))
        wanted = abroad.bank_needs(self.world, self.defs, self.config, island, self.turn)
        for bank in sorted(set(wanted) | set(fxmoney.holdings(island))):
            if not ledger.is_bank(bank):
                continue
            payers[bank] = Payer(bank)
            mine = needs.setdefault(bank, {})
            for currency, amount in sorted(wanted.get(bank, {}).items()):
                e = fxrates.rate(island, currency)
                low, high = auction.band(e, width)
                mine.setdefault(currency, []).append((amount, e + (high - e) // 2))
            for key in fxmoney.holdings(island).get(bank, {}):
                e = fxrates.rate(island, int(key))
                low, _ = auction.band(e, width)
                floors.setdefault(bank, {})[int(key)] = e - (e - low) // 2

    @staticmethod
    def _fx_orders(
        island: IslandState,
        book: fxrates.Book,
        entity: str,
        payer: Payer,
        currency: int,
        needs: list[tuple[int, int | None]],
        floor: int | None,
        short: int,
        reference: int,
        width: int,
        due: int = 0,
        keep: bool = False,
        cap: int | None = None,
    ) -> None:
        """1つの主体の、1つの通貨の注文。持ち高をこのターンに払う代金 due（政府の契約）と引き合う
        輸入から順に当て、足りない分を買い、余った分を売る。due の足りない分は、値幅の上端の1つの
        指値で、ほかの買いより先に買う（fxrates.add_buy）。keep（計画経済の商社）なら、余った分を
        売らず、輸入のための外貨も買わない（輸入は持っている外貨の範囲。設計「計画経済（3-4）」）。
        cap は needs のために買ってよい量の上限（商社の1ターン分と使えるお金。FX-61）。"""
        have = fxmoney.holding(island, entity, currency)
        if short > 0 and have > 0:  # 賃金を払う自国通貨が足りない: 足りない分は値を選ばず売る
            urgent = min(have, -(-short * BASIS // max(1, reference)))
            fxrates.add_sell(book, entity, payer, urgent, reference, width)
            have -= urgent
            needs = []
        total = due + sum(amount for amount, _ in needs)
        if have > total:
            if not keep:
                fxrates.add_sell(book, entity, payer, have - total, reference, width, floor)
            return
        left = have - min(have, due)
        if due > have:
            fxrates.add_buy(island, book, entity, payer, due - have, reference, width, due=True)
        if keep:
            return
        for amount, limit in sorted(needs, key=lambda n: -(n[1] or 10**18)):
            cover = min(left, amount)
            left -= cover
            want = amount - cover if cap is None else min(amount - cover, cap)
            if want > 0 and (limit is None or limit > reference):
                fxrates.add_buy(island, book, entity, payer, want, reference, width, limit)
                if cap is not None:
                    cap -= want

    # --- 4. 契約 --------------------------------------------------------------------------

    def _contracts(self) -> None:
        for c in contracts.active(self.world, self.turn):
            seller = self.world.islands.get(c["seller"])
            buyer = self.world.islands.get(c["buyer"])
            if seller is None or buyer is None or c["seller"] not in self.islands:
                continue
            if c["buyer"] not in self.islands:
                continue
            c.pop("fx", None)  # 外貨の用意の記録は、外貨が足りなかったターンだけ残す
            if policy.blocked(self.world, seller.id, buyer.id, c["good"]):
                # 禁輸（フェーズ3-3）: その国との契約は届けない（止まったターンに1度だけ知らせる）
                c["short"] = c["qty"]
                c["short_why"], c["short_turn"] = "embargo", self.turn
                if not c.get("embargoed"):
                    c["embargoed"] = True
                    args = contracts.short_args(self.world, c, c["qty"], "embargo")
                    seller.log.append(LogEntry("contract_embargoed", args))
                    buyer.log.append(LogEntry("contract_embargoed", args))
                continue
            c.pop("embargoed", None)
            self._fulfil(c, seller, buyer)

    def _fulfil(self, c: dict[str, Any], seller: IslandState, buyer: IslandState) -> None:
        good, price, currency = c["good"], c["price"], c["currency"]
        want = c["qty"]
        limits = {"goods": stock.total(seller.warehouse, good)}
        if price > 0:
            own = funds.available(buyer, gov_payer())
            if buyer.id == currency:
                limits["money"] = own * QUANTITY_SCALE // price
            else:
                held = fxmoney.holding(buyer, ledger.GOV, currency) * QUANTITY_SCALE // price
                # 外貨が足りないとき、国庫の自国通貨なら足りるのは、外貨を買えなかったため（fx）
                spare = fxrates.to_foreign(buyer, currency, own) * QUANTITY_SCALE // price
                limits["fx" if held + spare >= want else "money"] = held
        # 封鎖に止められた船出は、船が足りないのと分けて数える（契約の荷も物の援助の荷も船で
        # 運ぶので、封鎖の島の出入りは止まる。MI-34・W-187）
        if unit_weight(self.defs, good) > 0 and naval.blocked(
            self.world, self.defs, self.config, self.turn, buyer.id, seller.id, good
        ):  # 重さのない財は船が自分で航海して届くので封鎖に止められない
            limits["blockade"] = 0
        limits["ships"] = self._ship_room(buyer, seller, good)
        limits["port"] = self._port_room(seller, good)
        qty = min(want, *limits.values())
        qty = max(0, qty)
        if qty < want:
            why = min(limits, key=lambda k: limits[k])
            if why == "ships" and port_of(buyer, self.config) is not None:
                # S2: 船が足りずに荷を残した（海運業が船を買う見込みに入れる。港のない島は
                # 船では直らないので数えない）
                self.islands[buyer.id].ship_short = True
                self._short_load(buyer, seller, good, want - qty)
            missing = want - qty
            record = self.contract_fx.get((buyer.id, currency)) if why == "fx" else None
            fx = record.values() if record is not None else None
            if fx is not None:  # 外貨をどこからいくら用意できたか（契約の画面に出す）
                c["fx"] = {"turn": self.turn, **fx}
            args = contracts.short_args(self.world, c, missing, why, fx)
            seller.log.append(LogEntry("contract_short", args))
            buyer.log.append(LogEntry("contract_short", args))
            c["short_why"], c["short_turn"] = why, self.turn  # 条約の契約の条項の見張りが読む
        c["short"] = want - qty
        if qty <= 0:
            return
        batches = stock.take_batches(seller.warehouse, good, qty)
        amount = money_of(price, qty)
        value = amount if seller.id == currency else fxrates.to_own(seller, currency, amount)
        cost = fxmoney.transfer(
            self.world,
            currency,
            (buyer.id, ledger.GOV),
            (seller.id, ledger.GOV),
            amount,
            "contract",
            value,
            payer=gov_payer(),
        )
        books.add_sector(seller, books.WAREHOUSE, "sales", value)
        books.add_trade(seller, "exports", good, value)
        books.add_sector(buyer, books.WAREHOUSE, "purchases", cost)
        books.add_trade(buyer, "imports", good, cost)
        self.islands[seller.id].exports += value
        self.islands[buyer.id].imports += cost
        self.islands[seller.id].settled(currency, amount)
        self.islands[buyer.id].settled(currency, amount)
        c["delivered"] += qty
        c["paid"] += amount
        if c.get("aid"):  # 物の援助: 送った国の島の値段で、経常収支の貿易と移転に入れる
            donor_value = money_of(seller.economy.prices.get(good, 0), qty)
            value = balance.value_of(self.world, buyer, seller.id, donor_value)
            balance.goods_aid(seller, buyer, donor_value, value)
        self._ship(
            seller,
            buyer,
            good,
            batches,
            None,
            ledger.GOV,
            "contract",
            c["id"],
            delivery=contracts.delivery_of(c),
        )

    # --- 船と港の余裕 -------------------------------------------------------------------

    def _ship_room(self, buyer: IslandState, seller: IslandState, good: str) -> int:
        """買い手の島の空いている船と燃料で、seller から運べる量（1000 分の 1 単位）。"""
        if unit_weight(self.defs, good) <= 0:
            return 10**15  # 船は自分で航海して届く
        if port_of(buyer, self.config) is None:
            return 0
        if naval.blocked(self.world, self.defs, self.config, self.turn, buyer.id, seller.id, good):
            return 0  # 封鎖に止められた（出入りする船を止める）
        return self._room_on(buyer, buyer, seller, good)

    def _room_on(
        self, carrier: IslandState, buyer: IslandState, seller: IslandState, good: str
    ) -> int:
        """carrier の島の空いている船と燃料で、seller から buyer へ運べる量（1000 分の 1 単位）。
        S1: 出した船は ships.occupy で使っている重さに入るので、空きは ships.free だけで数える（前は
        このターンに出した重さをもう一度引いていた）。"""
        f = shipping_of(carrier, self.config)
        if f is None or port_of(carrier, self.config) is None:
            return 0
        oneway = one_way_turns(self.config, buyer, seller)
        trip = 2 * oneway
        room = min(ships.free(self.config, f), ships.fuel_room(self.config, f, trip))
        # 船は走る時間の分だけ使う（_ship）ので、運べる重さは、空いている船の重さ ×（片道の
        # ターン数 × 船の速さ ÷ 区画）。近い島へは、1ターンに何度も行き来できる
        room = room * oneway * self.config.get("trade.ship_speed") // route_cells(buyer, seller)
        return max(0, room) * QUANTITY_SCALE // unit_weight(self.defs, good)

    def _reach(self, buyer: IslandState, bloc: dict[str, Any], good: str) -> int:
        """買い手の島の船と、圏のほかの加盟国の空いた船の全部で運べる量の上限（板寄せの前の
        見込み。売り手はまだ決まらないので、一番近いほかの加盟国から運ぶとして数える。借りられない
        国（船を貸さない国、買い手と戦っている国、買い手との間でその財を禁輸している国）は除く）。"""
        if unit_weight(self.defs, good) <= 0:
            return 10**15
        if port_of(buyer, self.config) is None:
            return 0
        source = self._nearest_member(buyer, bloc)
        if source is None:
            return 0
        others = [
            self.world.islands[m] for m in bloc["members"] if m in self.islands and m != buyer.id
        ]
        carriers = [buyer] + [
            o
            for o in others
            if o.policy.lend_ships
            and not war.fighting(self.world, o.id, buyer.id, self.turn)
            and not policy.blocked(self.world, o.id, buyer.id, good)
        ]
        return sum(self._room_on(c, buyer, source, good) for c in carriers)

    def _nearest_member(self, buyer: IslandState, bloc: dict[str, Any]) -> IslandState | None:
        """圏のほかの加盟国のうち、buyer に一番近い国（なければ None）。"""
        others = [
            self.world.islands[m] for m in bloc["members"] if m in self.islands and m != buyer.id
        ]
        if not others:
            return None
        return min(others, key=lambda o: (route_cells(buyer, o), o.id))

    def _short_load(self, buyer: IslandState, seller: IslandState, good: str, missing: int) -> None:
        """船が足りずに載せられなかった荷 missing（1000 分の 1 単位）を運ぶのに要った船の重さを足す
        （FX-60）。1ターンの船出は往復の間（片道の 2 倍）、荷の重さ × 区画 ÷（片道のターン数 × 船の
        速さ）の船をふさぐので、毎ターン同じだけ荷が出るなら、荷の重さ × 2 × 区画 ÷ 船の速さの船が
        要る。"""
        if missing <= 0:
            return
        load = weight(self.defs, good, missing)
        need = load * 2 * route_cells(buyer, seller) // max(1, self.config.get("trade.ship_speed"))
        self.islands[buyer.id].ship_short_load += need

    def _carriage(
        self, bloc: dict[str, Any], buyer: IslandState, seller: IslandState, good: str, qty: int
    ) -> list[tuple[IslandState, int]]:
        """S1: 圏の市場で約定した荷を運ぶ船を、買い手の島の船、売り手の島の船（積む港にいる）、圏の
        ほかの加盟国の船（売り手に近い順）の順に、空いている分から割り当てる（ほかの国の船は借りて、
        運賃を決済通貨で払う）。[(運ぶ国の島, 量)] を返す。どの船にも載らない分は入れない。"""
        if unit_weight(self.defs, good) <= 0:
            return [(buyer, qty)]  # 船は自分で航海して届く
        if port_of(buyer, self.config) is None:
            return []
        # 売り手の船もほかの国の船も、貸す国（命令 set_ship_lending で止めていない国）のものだけ
        carriers = (
            [buyer] if seller.id == buyer.id or not seller.policy.lend_ships else [buyer, seller]
        )
        ends = (buyer.id, seller.id)
        others = [
            self.world.islands[m]
            for m in bloc["members"]
            if m in self.islands
            and m not in ends
            and self.world.islands[m].policy.lend_ships
            # 戦っている相手の港へは船を出さず、禁輸している相手の荷は運ばない
            and not any(war.fighting(self.world, m, x, self.turn) for x in ends)
            and not any(policy.blocked(self.world, m, x, good) for x in ends)
        ]
        others.sort(key=lambda o: (route_cells(o, seller), o.id))
        parts: list[tuple[IslandState, int]] = []
        left = qty
        for carrier in [*carriers, *others]:
            if left <= 0:
                break
            take = min(left, self._room_on(carrier, buyer, seller, good))
            if take > 0:
                parts.append((carrier, take))
                left -= take
        return parts

    def _freight(
        self, carrier: IslandState, buyer: IslandState, seller: IslandState, load: int
    ) -> int:
        """carrier の島の船で seller から buyer へ重さ load を運ぶ運賃（carrier の通貨。戦争の危険の
        上乗せを含む）。"""
        rate = ships.freight_rate(carrier, self.config, shipping_of(carrier, self.config))
        freight = rate * load * route_cells(buyer, seller) // QUANTITY_SCALE
        return self._war_risk(freight, buyer, seller)

    def _foreign_freight(
        self,
        carrier: IslandState,
        buyer: IslandState,
        seller: IslandState,
        good: str,
        qty: int,
        currency: int,
    ) -> int:
        """S1: ほかの国の船を借りて運ぶときに払う運賃（決済通貨）。買い手の島の船なら 0。"""
        if carrier.id == buyer.id or unit_weight(self.defs, good) <= 0:
            return 0
        freight = self._freight(carrier, buyer, seller, weight(self.defs, good, qty))
        return freight if carrier.id == currency else fxrates.to_foreign(carrier, currency, freight)

    def _settle_cost(
        self,
        buyer: IslandState,
        seller: IslandState,
        good: str,
        qty: int,
        carrier: IslandState,
        price: int,
        currency: int,
        payer: Payer | None,
    ) -> int:
        """約定した qty を carrier の船で運ぶときに、決済通貨で払う額（品物の代金と、ほかの国の船の
        運賃。決済通貨の島（payer がある）は、自分の船の運賃も同じお金から払うので足す）。"""
        cost = money_of(price, qty)
        if carrier.id != buyer.id:
            return cost + self._foreign_freight(carrier, buyer, seller, good, qty, currency)
        if payer is not None and unit_weight(self.defs, good) > 0:
            return cost + self._freight(buyer, buyer, seller, weight(self.defs, good, qty))
        return cost

    def _pay_carrier(
        self,
        buyer: IslandState,
        carrier: IslandState,
        shipping: Facility,
        entity: str,
        f: Facility | None,
        freight: int,
        currency: int,
    ) -> int:
        """S1: 借りたほかの国の船の運賃を、決済通貨で運んだ国の海運業に払う（払えるだけ。
        サービスの輸入と輸出。国際収支の shipping_freight）。払った額（買い手の自国通貨。外貨は
        帳簿の値打ち）を返す。"""
        if freight <= 0:
            return 0
        amount = freight
        if carrier.id != currency:
            amount = fxrates.to_foreign(carrier, currency, freight)
        payer = Payer(entity) if buyer.id == currency else None
        if payer is not None:
            have = funds.available(buyer, payer)
        else:
            have = fxmoney.holding(buyer, entity, currency)
        value = freight
        if have < amount:
            short = amount - have
            self.islands[buyer.id].unpaid += (
                short if buyer.id == currency else fxrates.to_own(buyer, currency, short)
            )
            value = freight * have // max(1, amount)
            amount = have
        if amount <= 0:
            return 0
        cost = fxmoney.transfer(
            self.world,
            currency,
            (buyer.id, entity),
            (carrier.id, entity_of(self.config, shipping)),
            amount,
            "shipping_freight",
            value,
            payer=payer,
        )
        books.add(carrier, shipping.id, books.SALES, value)
        books.add_trade(carrier, "exports", books.FREIGHT_ITEM, value)
        # 貸した船の運賃の収入（船の国の通貨）と、借りた船に払った運賃（買い手の通貨）
        carrier.stats["trade.charter_income"] = carrier.stats.get("trade.charter_income", 0) + value
        buyer.stats["trade.charter_paid"] = buyer.stats.get("trade.charter_paid", 0) + cost
        if f is not None:
            books.add(buyer, f.id, books.FREIGHT, cost)
            traders.spent_abroad(f, currency, amount)
        else:
            books.add_sector(buyer, books.GOVERNMENT, "freight", cost)
        books.add_trade(buyer, "imports", books.FREIGHT_ITEM, cost)
        self.islands[carrier.id].settled(currency, amount)
        self.islands[buyer.id].settled(currency, amount)
        return cost

    def _port_room(self, island: IslandState, good: str) -> int:
        """島の港でこのターンにまだ積める量（1000 分の 1 単位）。港がなければ 0。"""
        port = port_of(island, self.config)
        if port is None:
            return 0
        unit = unit_weight(self.defs, good)
        if unit <= 0:
            return 10**15
        room = ships.port_capacity(self.config, port) * QUANTITY_SCALE
        room -= self.islands[island.id].port_used
        return max(0, room) * QUANTITY_SCALE // unit

    # --- 船出 ------------------------------------------------------------------------------

    def _ship(
        self,
        seller: IslandState,
        buyer: IslandState,
        good: str,
        batches: list[list[int]],
        f: Facility | None,
        entity: str,
        kind: str,
        ref: int,
        seller_payer: Payer | None = None,
        seller_facility: Facility | None = None,
        delivery: str | None = None,
        price: int | None = None,
        currency: int | None = None,
        carrier: IslandState | None = None,
    ) -> None:
        """荷を買い手の島の船に積んで出す。積む港の使用料（売り手が払う）、船の運賃（買い手が自国の
        海運業に払う）、燃料を払い、運ぶ途中の荷を記録する（契約の荷は届いた物の行き先も）。S1:
        carrier を渡すと、その国の船に積み、運賃を決済通貨でその国の海運業に払う
        （_pay_carrier）。"""
        carrier = carrier or buyer
        qty = sum(q for _, q in batches)
        load = weight(self.defs, good, qty)
        oneway = one_way_turns(self.config, buyer, seller)
        span = route_cells(buyer, seller)
        shipment_id = next_id(self.state, "shipment")
        # 積む港の使用料（売り手）
        port = port_of(seller, self.config)
        fee = ships.fee_for(seller, self.config, load)
        self._pay_fee(seller, seller_payer or gov_payer(), port, fee, seller_facility)
        self.islands[seller.id].port_used += load
        self.islands[seller.id].handled += load
        # 船（重さのない財は、自分で航海して届く船など）
        shipping = shipping_of(carrier, self.config)
        arrive = self.turn + oneway
        if load > 0 and shipping is not None:
            arrive = self.turn + 2 * oneway
            # 荷が着くのはターンの区切りだが、船（と船員・燃料）を使うのは実際に走る時間の分だけ
            # （区画 ÷ 船の速さ。片道のターン数に切り上げない）。運賃は今までどおり荷 1 トン ×
            # 区画で払うので、近い島への航海の運賃が、船員の賃金と燃料より安くならない
            held = max(1, load * span // (oneway * self.config.get("trade.ship_speed")))
            ships.occupy(shipping, shipment_id, held)
            here = self.islands[carrier.id]
            here.ships_used += held
            here.fuel_value += ships.burn(carrier, self.config, shipping, held, 2 * oneway)
            here.carried += load * span
            freight = self._freight(carrier, buyer, seller, load)
            if carrier.id == buyer.id:
                payer = Payer(entity) if f is not None else gov_payer()
                paid = self._pay_freight(buyer, payer, freight, f)
            else:
                assert currency is not None  # 借りた船は圏の市場の荷だけ
                paid = self._pay_carrier(buyer, carrier, shipping, entity, f, freight, currency)
            if f is not None:
                traders.add_cost(f, good, paid)
        self.state["shipments"].append(
            {
                "id": shipment_id,
                "kind": kind,
                "ref": ref,
                "from": seller.id,
                "to": buyer.id,
                "facility": f.id if f is not None else None,
                "good": good,
                "batches": batches,
                "load": load,
                "loaded": self.turn,
                "arrive": arrive,
                "ship": load > 0 and shipping is not None,
                "oneway": oneway,
                **({"carrier": carrier.id} if carrier.id != buyer.id else {}),
                **({"delivery": delivery} if delivery is not None else {}),
                # 圏の市場の約定の値段と決済通貨（荷を下ろすときの関税。フェーズ3-3）
                **({"price": price, "currency": currency} if price is not None else {}),
            }
        )
        loaded = self.cargo["loaded"]
        loaded[good] = loaded.get(good, 0) + qty
        self.islands[buyer.id].shipments.append(shipment_id)

    def _war_risk(self, freight: int, buyer: IslandState, seller: IslandState) -> int:
        """戦争の危険の上乗せ（MI-35・MI-57）: 戦争の相手の見張りや封鎖のある区画を通る航海には、
        見込みの損失を運賃に足す（沈む見込み p のとき運賃 ÷ (1 − p)）。"""
        p = naval.risk(
            self.world,
            self.defs,
            self.config,
            self.turn,
            buyer.id,
            naval.route_cells(self.world, buyer.id, seller.id),
        )
        return freight if p <= 0 else round(freight / (1 - min(p, 0.95)))

    # --- 5. 圏の市場 ------------------------------------------------------------------------

    def _blocs(self) -> tuple[dict[tuple[int, str], int], dict[tuple[int, str], int]]:
        unmet: dict[tuple[int, str], int] = {}
        sellers: dict[tuple[int, str], int] = {}
        self._plan_imports()
        self._allocate()
        width = to_basis(self.config.get("trade.bloc_band"))
        budgets: dict[int, int] = {}  # 商社ごとの扱える重さの残り
        money_left: dict[tuple[int, str, int], int] = {}  # (島, 主体, 通貨) → 使える額の残り
        for bloc in list(blocs.blocs(self.world)):
            members = [m for m in bloc["members"] if m in self.islands]
            self.fx_queue = self._plan_fx_queue(bloc, members)
            for good in tradable_goods(self.defs):
                key = (bloc["id"], good)
                for queue in self.fx_queue.values():  # この財の板寄せで、取り置きは要らなくなる
                    queue.pop(good, None)
                buys, sells, bids, asks = self._bloc_orders(
                    bloc, members, good, budgets, money_left
                )
                placed = list(zip(buys, bids, strict=True))  # 取り置きをした買い注文
                fixed = blocs.fixed_price(bloc, good)
                reference = fixed or blocs.reference(self.world, bloc, good)
                if reference is None:
                    self._refund(bloc, good, placed, {}, 0, budgets, money_left)
                    continue
                buys, sells, bids, asks = self._drop_blocked(good, buys, sells, bids, asks)
                # 圏内の固定価格（フェーズ3-3）: 値幅 0 で板寄せし、余った側を量に比例して分ける
                result = auction.clear(buys, sells, reference, 0 if fixed else width)
                price = result.price
                if fixed:
                    price = fixed
                elif result.demand > 0 and result.supply <= 0:
                    price = min(result.high, max(reference, max(o.limit or 0 for o in buys)))
                elif result.supply > 0 and result.demand <= 0:
                    price = max(result.low, min(o.limit or result.low for o in sells))
                elif result.demand <= 0 and result.supply <= 0:
                    price = blocs.home_average(self.world, bloc, good) or reference
                    price = min(result.high, max(result.low, price))
                for order, bid, got in zip(buys, bids, result.bought, strict=True):
                    item = traders.entry(bid.facility, good)
                    if got < order.quantity and "why" not in item:
                        item["why"] = WHY_SELLERS
                    elif got >= order.quantity and item.get("why") == WHY_SHIPS:
                        # 船が足りないと数えるのは、船のために減らした注文が全部約定した（船が
                        # あればもっと買えた）ときだけ（2026-11-02。前は商社の狙う量と空いた船を
                        # 比べたので、売り手がいなくて約定しない分のために船を買い続け、国営の
                        # 海運業が国のお金で1隻 6 億を超える値段の船を買い続けた）
                        self.islands[bid.island].ship_short = True
                for order, ask, got in zip(sells, asks, result.sold, strict=True):
                    item = traders.entry(ask.facility, good)
                    if got < order.quantity and "why" not in item:
                        item["why"] = WHY_BUYERS
                volume = 0
                settled: dict[int, int] = {}  # 買い注文の番号 → 払って船に積んだ量
                if result.volume > 0:
                    volume, settled = self._settle_bloc(
                        bloc, good, result.price, result, bids, asks
                    )
                # 取り置きは、払って積んだ量のほかを戻す（船や決済通貨で減らした分も戻す）
                got = {id(buys[b]): n for b, n in settled.items()}
                self._refund(bloc, good, placed, got, result.price, budgets, money_left)
                self._record_bloc(bloc, good, price, result, volume)
                unmet[key] = result.demand - volume
                sellers[key] = len({id(a.facility) for a in asks})
        return unmet, sellers

    def _drop_blocked(
        self,
        good: str,
        buys: list[Order],
        sells: list[Order],
        bids: list[_Bid],
        asks: list[_Bid],
    ) -> tuple[list[Order], list[Order], list[_Bid], list[_Bid]]:
        """禁輸（フェーズ3-3）で、どの相手とも組めない注文を外す（値段を決める板に入れない）。"""
        if not any(
            self.world.islands[i].policy.embargoes for i in {b.island for b in [*bids, *asks]}
        ):
            return buys, sells, bids, asks
        sellers = {a.island for a in asks}
        buyers = {b.island for b in bids}

        def open_to(island_id: int, others: set[int]) -> bool:
            return any(not policy.blocked(self.world, island_id, o, good) for o in others)

        keep_b = [i for i, b in enumerate(bids) if open_to(b.island, sellers)]
        keep_s = [i for i, a in enumerate(asks) if open_to(a.island, buyers)]
        return (
            [buys[i] for i in keep_b],
            [sells[i] for i in keep_s],
            [bids[i] for i in keep_b],
            [asks[i] for i in keep_s],
        )

    def _plan_imports(self) -> None:
        """S1: 港のある島は、輸入の注文を船の数で前もって減らさない。圏の市場の荷は、約定した後に
        買い手・売り手・圏のほかの加盟国の空いている船に割り当て（_carriage）、どの船にも載らない分
        だけを減らす。S2: 船が足りないと数えるのも、そのとき（荷を残したとき）だけ。前は、空いている
        船の重さを商社の狙う量（実際に運ぶ量よりずっと多い）と比べ、ほとんどの島で毎ターン「船が
        足りない」になって、使わない船を買い続けていた。"""
        for i in self.ids:
            port = port_of(self.world.islands[i], self.config)
            self.islands[i].import_share = 1.0 if port is not None else 0.0

    def _bloc_orders(
        self,
        bloc: dict[str, Any],
        members: list[int],
        good: str,
        budgets: dict[int, int],
        money_left: dict[tuple[int, str, int], int],
    ) -> tuple[list[Order], list[Order], list[_Bid], list[_Bid]]:
        currency = bloc["currency"]
        cut = to_basis(self.config.get("trade.trader_cut"))
        clearance = self.config.get("trade.clearance_turns")
        cap = self.config.get("trade.trader_capacity") * QUANTITY_SCALE
        unit = unit_weight(self.defs, good)
        buys: list[Order] = []
        sells: list[Order] = []
        bids: list[_Bid] = []
        asks: list[_Bid] = []
        for m in members:
            island = self.world.islands[m]
            e = fxrates.rate(island, currency)
            fee_unit = ships.port_fee(island, self.config) * unit // QUANTITY_SCALE
            subsidy = policy.export_subsidy(island, good)
            cap_q = policy.quota(island, good)
            if cap_q is not None:
                self.quota_left.setdefault((m, good), cap_q)
            for f in traders_of(island, self.config, self.turn):  # 止まった商社は注文しない
                item = traders.goods_of(f).get(good)
                if item is None:
                    continue
                entity = entity_of(self.config, f)
                left = budgets.setdefault(f.id + m * 10**6, cap)
                if item.get("dir") == traders.EXPORT and item.get("bx") == bloc["id"]:
                    item.pop("why", None)
                    qty = min(stock.total(f.stock, good), self._port_room(island, good))
                    if unit > 0:
                        qty = min(qty, left * QUANTITY_SCALE // unit)
                    if qty <= 0:
                        continue
                    limit = None
                    if traders.is_planned(island):
                        # F8: 計画経済（フェーズ3-4）の国の商社は、売れ残った物を国内の値段に
                        # よらず輸出するが、圏の今の値段で売る（値段を受け入れる側。国内の値段との
                        # 差は国庫が持つ。FX-52）。指値なしの売りは、買いが値幅の上端にそろう圏で
                        # 売れる量を増やさず、値段だけを毎ターン値幅ずつ下げ、圏の石炭の値段が作る
                        # 費用の 1/15 まで崩れた
                        limit = blocs.fixed_price(bloc, good) or blocs.reference(
                            self.world, bloc, good
                        )
                    elif item["age"] < clearance:
                        own = traders.unit_cost(f, good)
                        own = max(1, own + part(own, cut) + fee_unit - subsidy)  # 輸出補助を見込む
                        limit = max(1, -(-own * BASIS // max(1, e)))
                    sells.append(Order(qty, limit))
                    asks.append(_Bid(m, f, entity, good))
                    budgets[f.id + m * 10**6] = left - weight(self.defs, good, qty)
                    self.islands[m].port_used += weight(self.defs, good, qty)  # 積む分を取っておく
                    continue
                if (
                    item.get("dir") == traders.IMPORT
                    and item.get(traders.REEXPORT_BLOC) == bloc["id"]
                ):
                    order = self._reexport_order(island, f, item, good, bloc, left)
                    if order is not None:  # 輸出の荷に替えた持ちすぎの分（FX-64）
                        sells.append(order)
                        asks.append(_Bid(m, f, entity, good))
                        left -= weight(self.defs, good, order.quantity)
                        budgets[f.id + m * 10**6] = left
                        self.islands[m].port_used += weight(self.defs, good, order.quantity)
                if item.get("dir") == traders.IMPORT and item.get("bm") == bloc["id"]:
                    order = self._import_order(
                        island, f, entity, item, good, bloc, left, money_left
                    )
                    if order is None:
                        continue
                    if (m, good) in self.quota_left:
                        self.quota_left[(m, good)] -= order.quantity
                    buys.append(order)
                    bids.append(_Bid(m, f, entity, good))
                    budgets[f.id + m * 10**6] = left - weight(self.defs, good, order.quantity)
        # 積む分に取っておいた港の余裕を戻す（約定した分だけ、船出のときに使う）
        for order, ask in zip(sells, asks, strict=True):
            self.islands[ask.island].port_used -= weight(self.defs, good, order.quantity)
        return buys, sells, bids, asks

    def _reexport_order(
        self,
        island: IslandState,
        f: Facility,
        item: dict[str, Any],
        good: str,
        bloc: dict[str, Any],
        left: int,
    ) -> Order | None:
        """持ちすぎの分が止まりの値でも島で売れず、輸出の荷に替えた分（FX-64。item["reexport"]）の
        圏の市場の売り。荷を買った圏で、圏の今の値段を指値に売る（値段を受け入れる側。指値なしの
        売りは買いが値幅の上端にそろう圏で値段だけを下げるため。F8 と同じ）。"""
        qty = min(item.get(traders.REEXPORT, 0), stock.total(f.stock, good))
        qty = min(qty, self._port_room(island, good))
        unit = unit_weight(self.defs, good)
        if unit > 0:
            qty = min(qty, left * QUANTITY_SCALE // unit)
        if qty <= 0:
            return None
        price = blocs.fixed_price(bloc, good) or blocs.reference(self.world, bloc, good)
        if not price:
            return None
        return Order(qty, price)

    def _allocate(self) -> None:
        """FX-67: 圏の市場の前に、商社ごとの輸入の注文を全部の圏・財について集め、決済通貨と扱える
        重さが足りなければ、財の名前の順でなく、儲けの割合（島の値段 − 原価）÷ 原価の大きい注文から
        配る（self.alloc。限られた元手は一番儲かる物に回す。割合で薄く配ると、外貨の足りない島で
        食料まで全部の財が細り、値段が何倍にもなった。2026-11-12 の full）。

        注文の量は FX-66 の量を、船（空いた船の全部で運べる量）と輸入の割り当てで縮めたもの。まず
        決済通貨（持ち主ごと・通貨ごとの使えるお金。_import_order と同じく、島の通貨なら賃金と輸出の
        仕入れの見込みを残した預金から運賃と使用料も払う）で、1 ターンに流れる分をその順に、余りを
        足りない分を埋める注文に同じ順に配る。外貨で縮んだ量のまま、扱える重さ（輸出の荷の分を引いた
        残り）を同じ形で割り振る。約定しなかった分の重さは、今どおり板寄せの後に戻す（_refund）。
        計画経済の島の商社は割り振らない（注文は前のまま）。"""
        self.alloc = {}
        cap = self.config.get("trade.trader_capacity") * QUANTITY_SCALE
        for i in self.ids:
            island = self.world.islands[i]
            if traders.is_planned(island):
                # 計画経済の島の商社は前のまま（儲けの順で配ると、公定価格が輸入の原価より安い原料・
                # 部品が後回しになり、化学品・自動車の工場が止まって品切れが増えた。2026-11-13 の
                # full と部分ごとに外した比べで、増えた品切れの半分が計画経済の島だった）
                continue
            # 持ち主・通貨ごとの注文: (商社, 財, 量, 流れる分, 1 単位のお金)
            groups: dict[tuple[str, int], list[tuple[Facility, str, int, int, int]]] = {}
            for f in traders_of(island, self.config, self.turn):
                entity = entity_of(self.config, f)
                for good, item in sorted(traders.goods_of(f).items()):
                    if item.get("dir") != traders.IMPORT or item.get("bm") is None:
                        continue
                    bloc = blocs.find(self.world, item["bm"])
                    if bloc is None:
                        continue
                    terms, _ = self._import_terms(island, good, bloc)
                    if terms is None:
                        continue
                    want = traders.import_want(self.config, item, traders.is_planned(island))
                    if want <= 0:
                        continue
                    want, _, _ = self._import_room(
                        island, good, bloc, want, 10**18, policy.quota(island, good), False
                    )
                    if want <= 0:
                        continue
                    currency = bloc["currency"]
                    unit = terms.limit
                    if currency == island.id:  # 運賃と使用料も同じお金から払う
                        unit += terms.freight_unit + terms.fee_unit
                    else:  # 借りた船の運賃は決済通貨で払う
                        unit += terms.charter
                    flow = min(want, item["m"])
                    rank = terms.profit / max(1, terms.cost)
                    groups.setdefault((entity, currency), []).append(
                        (f, good, want, flow, unit, rank)
                    )
            money_cut: dict[tuple[int, str], tuple[int, int, str | None]] = {}
            ranks: dict[tuple[int, str], float] = {}
            for (entity, currency), rows in sorted(groups.items()):
                if currency == island.id:
                    reserve = self.reserve.get((i, entity), 0)
                    money = max(0, funds.available(island, Payer(entity)) - reserve)
                else:
                    money = fxmoney.holding(island, entity, currency)
                got = traders.share_by_rank(
                    [r[3] for r in rows],
                    [r[2] - r[3] for r in rows],
                    [r[4] for r in rows],
                    money,
                    [r[5] for r in rows],
                )
                why = WHY_FUNDS if currency == island.id else WHY_MONEY
                for (f, good, want, flow, _, rank), qty in zip(rows, got, strict=True):
                    money_cut[(f.id, good)] = (
                        qty,
                        max(0, flow - qty),
                        why if qty < want else None,
                    )
                    ranks[(f.id, good)] = rank
            for f in traders_of(island, self.config, self.turn):
                left = cap
                for good, item in traders.goods_of(f).items():  # 輸出の荷も扱える重さを使う
                    if item.get("dir") == traders.EXPORT:
                        left -= weight(self.defs, good, stock.total(f.stock, good))
                rows2 = [
                    (good, *money_cut[(f.id, good)], traders.goods_of(f)[good]["m"])
                    for good in sorted(traders.goods_of(f))
                    if (f.id, good) in money_cut
                ]
                if not rows2:
                    continue
                flows = [min(qty, m) for _, qty, _, _, m in rows2]
                got = traders.share_by_rank(
                    flows,
                    [qty - fl for (_, qty, _, _, _), fl in zip(rows2, flows, strict=True)],
                    [unit_weight(self.defs, r[0]) for r in rows2],
                    left,
                    [ranks[(f.id, r[0])] for r in rows2],
                )
                for (good, qty, cut_money, why, _), fl, final in zip(
                    rows2, flows, got, strict=True
                ):
                    cut_capacity = max(0, fl - final)
                    if final < qty:
                        why = WHY_CAPACITY
                    self.alloc[(i, f.id, good)] = _Allocated(final, cut_capacity, cut_money, why)

    def _import_terms(
        self, island: IslandState, good: str, bloc: dict[str, Any]
    ) -> tuple[_ImportTerms | None, str | None]:
        """圏 bloc で good を輸入するときの指値と原価（_import_order と外貨の見込み FX-61 で同じ）。
        値がつかない・引き合わないときは (None, わけ)。

        指値は島で売って引き合う一番高い値段を、圏の値幅の上端までにする（それより高くは約定しない
        ので、決済通貨をその分だけ取り置けばよい。島の値段が圏よりずっと高い財で、指値のぶん取り
        置いて外貨を使い切り、ほかの財の注文が出なくなっていた）。関税は、荷を下ろすときに払う分を
        見込んで指値を下げる。"""
        view = traders.views(island).get(str(bloc["id"]))
        if view is None:
            return None, None
        currency = bloc["currency"]
        price = bloc["prices"].get(good) or view.get("prices", {}).get(good)
        if not price:
            return None, WHY_NO_PRICE
        cut = to_basis(self.config.get("trade.trader_cut"))
        unit = unit_weight(self.defs, good)
        home = island.economy.prices.get(good, 0)
        e = fxrates.rate(island, currency)
        freight_unit = (
            ships.freight_rate(island, self.config) * unit * view.get("cells", 1) // QUANTITY_SCALE
        )
        # 借りた船の運賃（戦争の割増を含む）の直近の実績の 1 単位あたり（FX-63。traders.prospects
        # と同じ。自国の船の運賃は自国の船で運んだ割合の分だけ）
        share = view.get("charter_share", {}).get(good, 0)
        freight_unit = freight_unit * (BASIS - share) // BASIS
        charter = view.get("charter", {}).get(good, 0)
        freight_unit += charter * e // BASIS
        fee_unit = ships.port_fee(island, self.config) * unit // QUANTITY_SCALE
        own = home - part(home, cut) - freight_unit - fee_unit
        tariff = 0 if blocs.no_tariff(bloc) else policy.tariff_rate(island, good)
        duty_rate = policy.with_tariff(e, tariff)
        limit = own * BASIS // max(1, duty_rate)
        if traders.is_planned(island):  # 計画経済は、行列のある物を圏の値幅の上端まで買う
            limit = 10**15
        if limit <= 0:
            return None, WHY_PRICE
        reference = (
            blocs.fixed_price(bloc, good) or blocs.reference(self.world, bloc, good) or price
        )
        _, high = auction.band(reference, to_basis(self.config.get("trade.bloc_band")))
        # 1 単位の輸入の原価（自国通貨。圏の値段・関税・運賃・使用料）と、島の値段との差（FX-62）
        cost = price * duty_rate // BASIS + freight_unit + fee_unit
        terms = _ImportTerms(min(limit, high), freight_unit, fee_unit, cost, home - cost, charter)
        return terms, None

    def _import_room(
        self,
        island: IslandState,
        good: str,
        bloc: dict[str, Any],
        want: int,
        left: int,
        quota: int | None,
        effects: bool = True,
        ships_now: bool = True,
        flow: int | None = None,
    ) -> tuple[int, str | None, int]:
        """輸入の量 want を、船・扱える重さの残り left・輸入の割り当ての残り quota で縮める。
        (量, わけ, 扱える重さで削った量（割り当てでも買えない分は除く。FX-62）) を返す。effects
        なら、船が足りないことを海運業の見込みに書く（板寄せの前の注文のとき。外貨の見込み FX-61
        では書かない）。ships_now でなければ、空いた船の数では縮めない（港がなければ 0。出た船は
        次のターンの荷下ろしで戻るので、記録の段で次のターンの見込みを出すときと、何ターン分かの
        見込みのとき）。flow（1 ターンに流れる分）を渡すと、船の不足と扱える重さで削った量はその分
        までで数える（FX-66。持つ量を埋める分の注文で船を買い増さない）。"""
        why = None
        cut_capacity = 0
        unit = unit_weight(self.defs, good)
        counted = want if flow is None else min(want, flow)
        if unit > 0:
            if port_of(island, self.config) is None:  # 港がない
                want, why = 0, WHY_SHIPS
            # 板寄せの前に、自分の船と圏で借りられる空いた船の全部でも運べない分は注文しない
            # （載らない注文が板寄せに残って値段を上げないように）。それでも足りなければ、本当に
            # 船が足りない（S2）
            reach = self._reach(island, bloc, good) if ships_now else want
            if reach < want:
                if effects and port_of(island, self.config) is not None and reach < counted:
                    self.islands[island.id].ship_short = True
                    # 載せられなかった荷は、商社が扱える重さまで（それより多い分は船では直らない）
                    source = self._nearest_member(island, bloc)
                    room = left * QUANTITY_SCALE // unit
                    if source is not None:
                        self._short_load(island, source, good, min(counted, room) - reach)
                want, why = reach, WHY_SHIPS
                counted = min(counted, want)
            room = left * QUANTITY_SCALE // unit
            if room < want:
                cut_capacity = max(0, (counted if quota is None else min(counted, quota)) - room)
                want, why = room, WHY_CAPACITY
        if quota is not None and quota < want:
            want, why = max(0, quota), WHY_QUOTA
        return want, why, cut_capacity

    def _import_order(
        self,
        island: IslandState,
        f: Facility,
        entity: str,
        item: dict[str, Any],
        good: str,
        bloc: dict[str, Any],
        left: int,
        money_left: dict[tuple[int, str, int], int],
    ) -> Order | None:
        """輸入の買い注文（量は狙う量の残り、船、扱える重さ、持っている決済通貨、輸入の割り当ての
        残り self.quota_left で頭打ち。指値は _import_terms）。狙う量に届かなかったわけを
        item["why"] に書く（貿易の画面。届いたら消す）。商社が自分のお金か扱える重さで買えなかった
        引き合う量は、取り逃した儲けとして数える（FX-62。_note_missed）。"""
        item.pop("why", None)
        terms, why = self._import_terms(island, good, bloc)
        if terms is None:
            if why is not None:
                item["why"] = why
            return None
        currency = bloc["currency"]
        limit = terms.limit
        want = traders.import_want(self.config, item, traders.is_planned(island))  # FX-66
        if want <= 0:
            return None
        flow = min(want, item["m"])  # 1 ターンに流れる分
        quota = self.quota_left.get((island.id, good))
        want, why, cut_capacity = self._import_room(
            island, good, bloc, want, left, quota, flow=flow
        )
        # 決済通貨と扱える重さは、圏の市場の前に財の間で儲けの大きい順に割り振った量まで（FX-67）
        plan = self.alloc.get((island.id, f.id, good))
        cut_money = 0
        if plan is not None and plan.qty < want:
            want = plan.qty
            why = plan.why or why
            cut_capacity = max(cut_capacity, plan.cut_capacity)
            cut_money = plan.cut_money
        key = (island.id, entity, currency)
        if key not in money_left:
            if island.id == currency:  # 賃金と輸出の仕入れの見込みは残す（FX-61）
                reserve = self.reserve.get((island.id, entity), 0)
                money_left[key] = max(0, funds.available(island, Payer(entity)) - reserve)
            else:
                money_left[key] = fxmoney.holding(island, entity, currency)
        ahead = self._fx_ahead(key, good)
        money = max(0, money_left[key] - ahead)
        if island.id == currency:  # 運賃と使用料も同じお金から払う
            unit_extra = (terms.freight_unit + terms.fee_unit) * BASIS // max(1, limit)
            money = money * BASIS // (BASIS + unit_extra)
        elif terms.charter > 0:  # 借りた船の運賃も同じ外貨から払う（FX-63）
            money = money * BASIS // (BASIS + terms.charter * BASIS // max(1, limit))
        if money * QUANTITY_SCALE // limit < want:
            short = WHY_FUNDS if island.id == currency else WHY_MONEY
            if ahead > 0 and money_left[key] * QUANTITY_SCALE // limit >= want:
                short = WHY_PRIORITY  # 取り置きがなければ買えた（FX-76）
            cut_money += want - money * QUANTITY_SCALE // limit
            want, why = money * QUANTITY_SCALE // limit, short
        cut_capacity = min(cut_capacity, flow)
        cut_money = min(cut_money, max(0, flow - cut_capacity))
        self._note_missed(island, f, entity, good, terms, cut_capacity, cut_money, currency)
        if why is not None:
            item["why"] = why
        if want <= 0:
            return None
        money_left[key] -= money_of(limit, want)
        return Order(want, limit)

    def _plan_fx_queue(
        self, bloc: dict[str, Any], members: list[int]
    ) -> dict[tuple[int, str, int], dict[str, int]]:
        """FX-76: 計画経済の島の国営の商社が、圏 bloc で財ごとに輸入に要る決済通貨の見込み。圏の
        市場は財の名前の順に回るので、名前の順で先に来る財を買うとき、外貨を使う順（goods.yaml の
        fx_priority）で先の財の分をこの見込みから取り置く（_fx_ahead）。市場経済・混合経済の島は
        数えない。

        見込みは、島・主体ごとに狙う量の残りを足し、前のターンに圏で売りに出た量と輸入の割り当てで
        縮め、空いた船と扱える重さで縮めて、圏の市場に出す指値（_import_terms）で値を付けた額。前の
        ターンに圏で約定がなかった財（売りの値段が値幅に入らない・船が出ないなど）と、前のターンに
        封鎖で船出を断られた財は数えない（入らない財のために外貨を寝かせない）。"""
        currency = bloc["currency"]
        cap = self.config.get("trade.trader_capacity") * QUANTITY_SCALE
        supply = bloc.get("supply", {})
        volume = bloc.get("volume", {})
        wants: dict[tuple[int, str, int], dict[str, list[int]]] = {}  # 鍵 → 財 → [量, 扱える重さ]
        for m in members:
            island = self.world.islands[m]
            if not traders.is_planned(island):
                continue
            for f in traders_of(island, self.config, self.turn):
                entity = entity_of(self.config, f)
                for good, item in sorted(traders.goods_of(f).items()):
                    if item.get("dir") != traders.IMPORT or item.get("bm") != bloc["id"]:
                        continue
                    if item.get("why") == WHY_BLOCKADE or volume.get(good, 0) <= 0:
                        continue
                    want = item["m"] - item["held"][0]
                    if want > 0:
                        row = wants.setdefault((m, entity, currency), {}).setdefault(good, [0, 0])
                        row[0] += want
                        row[1] += cap
        out: dict[tuple[int, str, int], dict[str, int]] = {}
        for key, goods in sorted(wants.items()):
            island = self.world.islands[key[0]]
            for good, (want, room) in sorted(goods.items()):
                terms, _ = self._import_terms(island, good, bloc)
                if terms is None:
                    continue
                quota = self.quota_left.get((key[0], good), policy.quota(island, good))
                want, _, _ = self._import_room(
                    island, good, bloc, min(want, supply.get(good, 0)), room, quota, effects=False
                )
                if want > 0:
                    out.setdefault(key, {})[good] = money_of(terms.limit, want)
        return out

    def _fx_ahead(self, key: tuple[int, str, int], good: str) -> int:
        """FX-76: 財 good を買う前に取り置く決済通貨（外貨を使う順で good より先で、まだ板寄せの来て
        いない財の見込みの和）。取り置くのは順が `trade.plan_fx_reserved` までの財だけ（設備の機械の
        ように、要る額が稼ぎの何十倍もある財まで取り置くと、原料が買えなくなった）。"""
        queue = self.fx_queue.get(key)
        if not queue:
            return 0
        rank = fx_priority(self.defs, good)
        kept = self.config.get("trade.plan_fx_reserved")
        return sum(
            v
            for g, v in queue.items()
            if fx_priority(self.defs, g) < rank and fx_priority(self.defs, g) <= kept
        )

    def _refund(
        self,
        bloc: dict[str, Any],
        good: str,
        placed: list[tuple[Order, _Bid]],
        got: dict[int, int],
        price: int,
        budgets: dict[int, int],
        money_left: dict[tuple[int, str, int], int],
    ) -> None:
        """圏×財の板寄せと払いの後、買い注文ごとに、取り置いた額（指値 × 量。_import_order）
        から約定の値段 × 板寄せで割り当てた量を引いた分を money_left に、運ばなかった重さを budgets
        に戻す（約定しなかった買いは、買い手のお金を使わない）。払いは約定の値段 × 割り当てた量
        以下なので、取り置いた額より多くは戻らない。戻した後の残りは、今の手元（決済通貨が自国の
        通貨なら預金。運賃と使用料も同じお金から払った後。外貨なら持ち高）までにする。"""
        currency = bloc["currency"]
        touched = set()
        for order, bid in placed:
            bought = min(order.quantity, got.get(id(order), 0))
            key = (bid.island, bid.entity, currency)
            if key in money_left:
                back = money_of(order.limit or 0, order.quantity) - money_of(price, bought)
                money_left[key] += max(0, back)
                touched.add(key)
            slot = bid.facility.id + bid.island * 10**6
            if slot in budgets:
                unused = weight(self.defs, good, order.quantity) - weight(self.defs, good, bought)
                budgets[slot] += max(0, unused)
        for key in sorted(touched):
            island = self.world.islands[key[0]]
            if key[0] == currency:
                have = funds.available(island, Payer(key[1]))
            else:
                have = fxmoney.holding(island, key[1], currency)
            money_left[key] = min(money_left[key], have)

    def _record_bloc(
        self, bloc: dict[str, Any], good: str, price: int, result: auction.Result, volume: int
    ) -> None:
        """圏の値段と、約定した量（禁輸で相手がいなかった分を除く）・買い・売りを残す。"""
        bloc["prices"][good] = price
        keep = self.config.get("trade.history_turns")
        history = bloc["history"].setdefault(good, [])
        history.append(price)
        del history[:-keep]
        for name, value in (
            ("volume", volume),
            ("demand", result.demand),
            ("supply", result.supply),
        ):
            if value:
                bloc[name][good] = value
            else:
                bloc[name].pop(good, None)

    def _settle_bloc(
        self,
        bloc: dict[str, Any],
        good: str,
        price: int,
        result: auction.Result,
        bids: list[_Bid],
        asks: list[_Bid],
    ) -> tuple[int, dict[int, int]]:
        """約定を、近い島の組から割り当てて払う（禁輸の組は割り当てない）。割り当てた量と、買い注文の
        番号ごとの払って船に積んだ量を返す。S1: 荷は _carriage で買い手・売り手・圏のほかの加盟国の
        空いている船に割り当て、どの船にも載らない分は買わない（S2: 買い手の島の「船が足りない」に
        数える）。ほかの国の船の運賃も決済通貨で払うので、払える分までにする。"""
        currency = bloc["currency"]
        matched = 0
        settled: dict[int, int] = {}

        def distance(b: int, s: int) -> int:
            here = self.world.islands[bids[b].island]
            there = self.world.islands[asks[s].island]
            return route_cells(here, there) if here.id != there.id else 0

        # 輸出し直す荷（FX-64。売り手の荷が輸入の向き）は、同じ島の輸入に戻さない
        resold = {
            k
            for k, a in enumerate(asks)
            if traders.goods_of(a.facility).get(good, {}).get("dir") == traders.IMPORT
        }

        def allowed(b: int, s: int) -> bool:
            if s in resold and bids[b].island == asks[s].island:
                return False
            return not policy.blocked(self.world, bids[b].island, asks[s].island, good)

        for b, s, qty in auction.match_nearest(result.bought, result.sold, distance, allowed):
            bid, ask = bids[b], asks[s]
            buyer = self.world.islands[bid.island]
            seller = self.world.islands[ask.island]
            payer = Payer(bid.entity) if buyer.id == currency else None
            if unit_weight(self.defs, good) > 0 and naval.blocked(
                self.world, self.defs, self.config, self.turn, buyer.id, seller.id, good
            ):  # 封鎖に止められた船出（MI-34）。船が足りないのとは分けて数える（W-187）
                traders.entry(bid.facility, good)["why"] = WHY_BLOCKADE
                continue
            parts = self._carriage(bloc, buyer, seller, good, qty)
            carried = sum(q for _, q in parts)
            # 決済通貨の島の商社は、運賃と使用料も同じお金から払う。注文のときの運賃は見込み
            # なので、前の約定の運賃（遠い売り手など）を払った後の残りまでにする。S1: ほかの国の
            # 船の運賃も決済通貨で払うので、品物の代金と合わせて払える分までにする（決済通貨の
            # 島は、自分の船の運賃も同じお金から払うので足す）
            if payer is not None:
                have = funds.available(buyer, payer)
            else:
                have = fxmoney.holding(buyer, bid.entity, currency)
            need = sum(
                self._settle_cost(buyer, seller, good, q, carrier, price, currency, payer)
                for carrier, q in parts
            )
            if need > have:
                parts = [(carrier, q * max(0, have) // need) for carrier, q in parts]
                why = WHY_FUNDS if payer is not None else WHY_MONEY
                traders.entry(bid.facility, good)["why"] = why
            elif carried < qty:  # S2: 払えるのに、どの船にも載らない分があった
                if port_of(buyer, self.config) is not None:  # 港のない島は船では直らない
                    self.islands[buyer.id].ship_short = True
                    self._short_load(buyer, seller, good, qty - carried)
                traders.entry(bid.facility, good)["why"] = WHY_SHIPS
            parts = [(carrier, q) for carrier, q in parts if q > 0]
            qty = sum(q for _, q in parts)
            if qty <= 0:
                continue
            # 借りた船の運賃を、次のターンの外貨の見込みに入れる（1 単位あたりにして圏の様子へ）
            seen = self.islands[buyer.id].charter.setdefault((bloc["id"], good), [0, 0, 0])
            seen[0] += sum(
                self._foreign_freight(carrier, buyer, seller, good, q, currency)
                for carrier, q in parts
            )
            seen[1] += qty
            seen[2] += sum(q for carrier, q in parts if carrier.id != buyer.id)
            amount = money_of(price, qty)
            value = amount if seller.id == currency else fxrates.to_own(seller, currency, amount)
            cost = fxmoney.transfer(
                self.world,
                currency,
                (buyer.id, bid.entity),
                (seller.id, ask.entity),
                amount,
                "trade",
                value,
                payer=payer,
            )
            # 売り手: 港の倉庫から出し、売上と輸出に数える（S1: 運ぶ船の国ごとに分けて出す）
            sf, bf = ask.facility, bid.facility
            loads = [(carrier, stock.take_batches(sf.stock, good, q)) for carrier, q in parts]
            moved = sum(q for _, batches in loads for _, q in batches)
            settled[b] = settled.get(b, 0) + moved
            books.stock_moved(seller, sf.id, good, -moved)
            books.add(seller, sf.id, books.SALES, value)
            books.add_trade(seller, "exports", good, value)
            sold_item = traders.entry(sf, good)
            taken = traders.take_cost(sold_item, moved)
            traders.cover_price_gap(seller, sf, ask.entity, taken, value)
            traders.exported(sf, good, moved)
            traders.add_volume(sf, weight(self.defs, good, moved))
            self.islands[seller.id].exports += value
            self.islands[seller.id].settled(currency, amount)
            if sold_item.get("dir") == traders.IMPORT:  # 輸出し直した持ちすぎの分（FX-64）
                left_over = sold_item.get(traders.REEXPORT, 0) - moved
                if left_over > 0:
                    sold_item[traders.REEXPORT] = left_over
                else:
                    sold_item.pop(traders.REEXPORT, None)
            else:  # 輸出補助は島の売り手から買って輸出した荷だけ（輸入した物の輸出し直しは除く）
                self._export_subsidy(seller, sf, ask.entity, good, moved)
            matched += moved
            # 買い手: 仕入れと輸入に数え、運ぶ途中の荷も在庫に数える
            books.add(buyer, bf.id, books.PURCHASES, cost)
            books.add_trade(buyer, "imports", good, cost)
            books.stock_moved(buyer, bf.id, good, moved)
            item = traders.entry(bf, good)
            item["held"][0] += moved
            item["held"][1] += cost
            item["transit"] += moved
            traders.add_volume(bf, weight(self.defs, good, moved))
            traders.spent_abroad(bf, currency, amount)
            self.islands[buyer.id].imports += cost
            self.islands[buyer.id].settled(currency, amount)
            for carrier, batches in loads:
                if not batches:
                    continue
                self._ship(
                    seller,
                    buyer,
                    good,
                    batches,
                    bf,
                    bid.entity,
                    "bloc",
                    bloc["id"],
                    seller_payer=Payer(ask.entity),
                    seller_facility=sf,
                    price=price,
                    currency=currency,
                    carrier=carrier,
                )
        return matched, settled

    def _export_subsidy(
        self, island: IslandState, f: Facility, entity: str, good: str, qty: int
    ) -> None:
        """輸出補助（フェーズ3-3）: 圏の市場で売れた量 × 1 単位あたりの額を、国庫の補助金の予算から
        商社に払う（予算が足りなければ払える分だけ）。国庫直轄の商社は帳簿にだけ残す。"""
        rate = policy.export_subsidy(island, good)
        if rate <= 0 or qty <= 0:
            return
        amount = money_of(rate, qty)
        if entity != ledger.GOV:
            payer = Payer(ledger.GOV, SUBSIDIES)
            paid = min(amount, funds.available(island, payer))
            funds.pay(island, payer, entity, paid, "export_subsidy")
            if paid < amount:
                stats = island.stats
                stats["trade.subsidy_unpaid"] = stats.get("trade.subsidy_unpaid", 0) + amount - paid
            amount = paid
        books.add(island, f.id, books.SUBSIDY, amount)
        island.stats["trade.export_subsidies"] = (
            island.stats.get("trade.export_subsidies", 0) + amount
        )

    # --- 6. 記録 ------------------------------------------------------------------------------

    def _close(
        self, bloc_unmet: dict[tuple[int, str], int], bloc_sellers: dict[tuple[int, str], int]
    ) -> None:
        for i in self.ids:
            island = self.world.islands[i]
            here = self.islands[i]
            ships.close_turn(island, self.config, here.carried, here.fuel_value)
            ships.close_port(island, self.config, here.handled)
            self._keep_missed(island)
            traders.update_targets(
                island, self.defs, self.config, bloc_unmet, bloc_sellers, self.turn
            )
            self._site(island)
            fxrates.keep_turnover(island, self.config, here.turnover)
            self._keep_charter(island, here)
            state = island_state(island)
            state["blocs"] = {
                str(b["id"]): self._view(b, i, here) for b in blocs.member_of(self.world, i)
            }
            self._keep_import_needs(island)
            state["contracts"] = [
                contract_view(c, i, self.turn) for c in contracts.of_island(self.world, i)
            ]
            shipping = shipping_of(island, self.config)
            if shipping is not None:
                keep = self.config.get("trade.cost_turns")
                history = [*shipping.economy.get("ships_short", []), int(here.ship_short)]
                shipping.economy["ships_short"] = history[-keep:]
                loads = [*shipping.economy.get(ship_orders.SHORT_LOAD, []), here.ship_short_load]
                shipping.economy[ship_orders.SHORT_LOAD] = loads[-keep:]
            self._stats(island, here)

    def _keep_charter(self, island: IslandState, here: _IslandTurn) -> None:
        """借りた船の運賃と買った量（圏・財ごと）を、直近 trade.cost_turns ターン分残す（FX-63。
        運賃と港の使用料を決めるのと同じターン数。買わなかったターンも 0 で数え、そのターン数の間に
        買わなければ消える。1 ターンだけ数えると、運賃で引き合わず買わなかった次のターンに運賃を
        忘れてまた買い、1 ターンおきに振れるため）。"""
        keep = self.config.get("trade.cost_turns")
        state = island_state(island)
        seen = state.get(traders.CHARTER, {})
        now = {f"{b}:{good}": row for (b, good), row in here.charter.items()}
        out = {}
        for key in sorted(set(seen) | set(now)):
            history = [*seen.get(key, []), list(now.get(key, [0, 0, 0]))][-keep:]
            if any(row[0] > 0 for row in history):
                out[key] = history
        if out:
            state[traders.CHARTER] = out
        else:
            state.pop(traders.CHARTER, None)

    def _view(self, bloc: dict[str, Any], island_id: int, here: _IslandTurn) -> dict[str, Any]:
        """圏の様子に、借りた船の運賃（財ごとに、直近 trade.cost_turns ターンに買った 1 単位あたりの
        決済通貨。戦争の割増を含み、自分の船で運んだ分も買った量に入れた平均）と、借りた船で運んだ
        割合を足す（次のターンの
        商社の原価。FX-63）。島がほかのどの加盟国とも禁輸で組めない財（embargoed。仕入れ直せない）
        も足す。"""
        view = blocs.view_of(self.world, bloc, island_id)
        island = self.world.islands[island_id]
        charter, share = {}, {}
        prefix = f"{bloc['id']}:"
        for key, history in sorted(island_state(island).get(traders.CHARTER, {}).items()):
            if not key.startswith(prefix):
                continue
            freight = sum(row[0] for row in history)
            qty = sum(row[1] for row in history)
            chartered = sum(row[2] if len(row) > 2 else 0 for row in history)
            if freight > 0 and qty > 0:
                good = key[len(prefix) :]
                charter[good] = freight * QUANTITY_SCALE // qty
                share[good] = min(BASIS, chartered * BASIS // qty)
        if charter:
            view["charter"] = charter
            # 借りた船で運んだ割合（1万分の1。自国の船の運賃の見込みをその分減らす）
            view["charter_share"] = share
        embargoed = self._embargoed(bloc, island_id)
        if embargoed:
            view["embargoed"] = embargoed
        return view

    def _embargoed(self, bloc: dict[str, Any], island_id: int) -> list[str]:
        """島が、圏のほかのどの加盟国とも禁輸で取引できない財（FX-63）。"""
        others = [m for m in bloc["members"] if m != island_id and m in self.world.islands]
        key = str(island_id)
        if not others or not (
            self.world.islands[island_id].policy.embargoes
            or any(key in self.world.islands[m].policy.embargoes for m in others)
        ):
            return []
        return [
            good
            for good in tradable_goods(self.defs)
            if all(policy.blocked(self.world, island_id, m, good) for m in others)
        ]

    def _site(self, island: IslandState) -> None:
        """商社の島の中の運賃を、港のマスから、扱う財で数えるようにする（economy/freight.py）。"""
        port = port_of(island, self.config)
        for f in traders_of(island, self.config):
            if port is not None:
                f.economy["site"] = [port.x, port.y]
            else:
                f.economy.pop("site", None)
            goods = []
            for good, item in sorted(traders.goods_of(f).items()):
                if item.get("dir") == traders.EXPORT and item["x"] > 0:
                    goods.append([good, item["x"], False])
                elif item.get("dir") == traders.IMPORT and item["m"] > 0:
                    goods.append([good, item["m"], True])
            f.economy["freight_goods"] = goods

    def _stats(self, island: IslandState, here: _IslandTurn) -> None:
        stats = island.stats
        shipping = shipping_of(island, self.config)
        if shipping is not None:
            stats["ships"] = ships.fleet(shipping) / QUANTITY_SCALE
            stats["ships.busy"] = (
                ships.busy(shipping)
                / QUANTITY_SCALE
                / max(1, self.config.get("trade.ship_capacity"))
            )
            stats["freight.ship_rate"] = ships.freight_rate(island, self.config, shipping)
        stats["port.handled"] = here.handled / QUANTITY_SCALE
        stats["trade.unpaid"] = here.unpaid
        if fxrates.rates(island):
            # 為替の板の売りと買いの量（fx.offered・fx.wanted。0 は書かない）を記録したターンの印
            stats["fx.book"] = 1
        for key, e in sorted(fxrates.rates(island).items()):
            stats[f"fx.{key}"] = e / BASIS
            # 売り買いのなかったターンも 0 を残す（推移のグラフが途切れないように）
            stats.setdefault(f"fx.volume.{key}", 0)
            stats.setdefault(f"fx.dealt.{key}", 0)
        stats["fx.holdings"] = fxmoney.total_value(island, fxrates.rates(island))
        stats["fx.swap.borrowed"] = swap.debt_value(island)  # 融通で借りている額（自国通貨）
        stats["fx.swap.lent"] = swap.claims_value(island, foreign_only=False)
        stats.setdefault("trade.merchant", 0)
        stats.setdefault("trade.merchant_sold", 0)
        stats.setdefault("trade.charter_income", 0)
        stats.setdefault("trade.charter_paid", 0)
        abroad.keep_rates(island)  # 外国の国債の求める利回りに使う値下がりの率（フェーズ3-3）


def contract_view(c: dict[str, Any], island_id: int, turn: int) -> dict[str, Any]:
    """島に見せる契約（次のターンの市場の段で、売る国の政府が足りない分を買う。画面にも出す）。"""
    selling = c["seller"] == island_id
    return {
        "id": c["id"],
        "side": "sell" if selling else "buy",
        "partner": c["buyer"] if selling else c["seller"],
        "proposer": c["proposer"],
        "good": c["good"],
        "qty": c["qty"],
        "price": c["price"],
        "currency": c["currency"],
        "state": c["state"],
        "start": c["start"],
        "end": c["end"],  # 無期限で終わりの決まっていない契約は None（FX-68）
        "indefinite": bool(c.get("indefinite")),
        "active": contracts.running(c, turn + 1),
        "delivered": c["delivered"],
        "short": c["short"],
        "short_why": c.get("short_why") if c["short"] > 0 else None,  # 直近の不足のわけ
        "delivery": contracts.delivery_of(c),
        # 外貨が足りずに届かなかったターンに、買う国がその通貨をどこからいくら用意できたか
        "fx": None if selling else c.get("fx"),
    }


# --- 貿易の確かめ ------------------------------------------------------------------------


class TradeCheck(WorldStage):
    """島をまたぐお金と、運ぶ途中の荷の確かめ（記録の段の後）。"""

    name = "trade_check"

    def collect(self, island: IslandState, ctx: StageContext) -> Any:
        return None

    def resolve(self, requests: dict[int, Any], ctx: StageContext) -> dict[int, Any]:
        assert ctx.before is not None
        problems = check(ctx.before)
        if problems:
            shown = " / ".join(problems[:5])
            more = f"（ほかに {len(problems) - 5} 件）" if len(problems) > 5 else ""
            raise LedgerCheckFailed(f"島をまたぐお金か荷が合いません: {shown}{more}")
        return {}

    def apply(self, island: IslandState, result: Any, ctx: StageContext) -> None:
        return None


def check(world: WorldState) -> list[str]:
    """世界の確かめ: 外貨の持ち高と非居住者の預金、外国の国債、融通の貸し借り、運ぶ途中の荷（積んだ
    量 − 下ろした量）。"""
    problems = fxmoney.reconcile(world) + abroad.reconcile(world) + swap.reconcile(world)
    state = world.ext.get("trade")
    if not state:
        return problems
    cargo = state.get("cargo", {"loaded": {}, "unloaded": {}})
    moving: dict[str, int] = {}
    for s in state.get("shipments", []):
        qty = sum(q for _, q in s["batches"])
        if qty <= 0 or any(q <= 0 for _, q in s["batches"]):
            problems.append(f"運ぶ途中の荷 {s['id']} の量がおかしい")
        moving[s["good"]] = moving.get(s["good"], 0) + qty
    goods = set(moving) | set(cargo["loaded"]) | set(cargo["unloaded"])
    for good in sorted(goods):
        expected = cargo["loaded"].get(good, 0) - cargo["unloaded"].get(good, 0)
        if moving.get(good, 0) != expected:
            problems.append(
                f"{good} の運ぶ途中の荷 {moving.get(good, 0)} が、積んだ量 − 下ろした量 "
                f"{expected} と合いません"
            )
    return problems


# --- 命令 ------------------------------------------------------------------------------


def _check_started(island: IslandState) -> None:
    if not island.has_economy:
        raise OrderFailed("reason_no_economy")


def check_create_bloc(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_started(island)
    if not str(payload.get("name", "")).strip():
        raise OrderFailed("reason_invalid_payload")


def check_bloc(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_started(island)
    if not isinstance(payload.get("bloc"), int):
        raise OrderFailed("reason_invalid_payload")


def check_contract(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_started(island)
    good = payload.get("good")
    if good not in defs.goods or "freight_weight" not in defs.goods[good]:
        raise OrderFailed("reason_not_tradable")
    if payload.get("partner") == island.id:
        raise OrderFailed("reason_contract_partner")
    if payload.get("side") not in ("sell", "buy"):
        raise OrderFailed("reason_invalid_payload")
    if payload.get("quantity", 0) <= 0:
        raise OrderFailed("reason_invalid_payload")
    # 期間か無期限（FX-68）のどちらか一方
    if payload.get("indefinite"):
        if "turns" in payload:
            raise OrderFailed("reason_invalid_payload")
    elif payload.get("turns", 0) <= 0:
        raise OrderFailed("reason_invalid_payload")
    if payload.get("price", -1) < 0:
        raise OrderFailed("reason_invalid_payload")
    delivery = payload.get("delivery")
    if delivery is not None and delivery not in contracts.DELIVERIES:
        raise OrderFailed("reason_invalid_payload")
    if delivery is not None and payload.get("side") == "sell":
        raise OrderFailed("reason_contract_delivery")


def check_contract_ref(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_started(island)
    if not isinstance(payload.get("contract"), int):
        raise OrderFailed("reason_invalid_payload")
    delivery = payload.get("delivery")
    if delivery is not None and delivery not in contracts.DELIVERIES:
        raise OrderFailed("reason_invalid_payload")


def check_aid(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    """援助（フェーズ3-3）: お金（amount と通貨）か物（good・quantity・turns）のどちらか。"""
    _check_started(island)
    if payload.get("partner") == island.id:
        raise OrderFailed("reason_contract_partner")
    goods = payload.get("good") is not None
    if goods == (payload.get("amount") is not None):
        raise OrderFailed("reason_invalid_payload")
    if goods:
        good = payload["good"]
        if good not in defs.goods or "freight_weight" not in defs.goods[good]:
            raise OrderFailed("reason_not_tradable")
        if payload.get("quantity", 0) <= 0 or payload.get("turns", 0) <= 0:
            raise OrderFailed("reason_invalid_payload")
    elif payload["amount"] <= 0:
        raise OrderFailed("reason_invalid_payload")


def _forward(kind: str):
    def execute(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
        request(island, kind, dict(payload))
        island.log.append(LogEntry("trade_request_sent", {"order": kind}))

    return execute


ORDERS = {
    "create_bloc": check_create_bloc,
    "join_bloc": check_bloc,
    "leave_bloc": check_bloc,
    "bloc_settings": check_bloc,
    "transfer_bloc": check_bloc,
    "take_over_bloc": check_bloc,
    "propose_contract": check_contract,
    "accept_contract": check_contract_ref,
    "cancel_contract": check_contract_ref,
    "send_aid": check_aid,
}


def register(registry: Registry) -> None:
    registry.add_stage(Trade())
    registry.add_stage(TradeCheck())
    for kind, checker in ORDERS.items():
        registry.add_order(OrderHandler(kind, execute=_forward(kind), check=checker))
    for kind, (execute, checker) in policy.ORDERS.items():
        registry.add_order(OrderHandler(kind, execute=execute, check=checker))
