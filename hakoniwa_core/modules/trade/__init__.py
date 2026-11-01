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
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import (
    BASIS,
    QUANTITY_SCALE,
    money_of,
    part,
    qty_for,
    round_money,
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
class _IslandTurn:
    """このターンの島ごとの数（船・港・運賃の見直し）。"""

    port_used: int = 0
    handled: int = 0
    carried: int = 0  # 運んだ量（トン × 区画。1000 分の 1）
    fuel_value: int = 0
    ships_used: int = 0  # このターンに出した船の重さ（1000 分の 1 トン）
    ship_short: bool = False  # 船が足りずに輸入を減らした
    exports: int = 0
    imports: int = 0
    unpaid: int = 0  # お金が足りずに払えなかった運賃と使用料
    import_share: float = 1.0  # 輸入の注文に回せる船の割合
    trips: int = 2  # 輸入の往復の見込みのターン数（燃料の見積もり）
    shipments: list[int] = field(default_factory=list)
    turnover: dict[int, int] = field(default_factory=dict)  # 通貨 → 島の主体が払い・受け取った額

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
            if outcome == naval.TURNED:
                s["arrive"] += 1
                s["delay"] = s.get("delay", 0) + 1
                args = {"good": s["good"]}
                island.log.append(LogEntry("cargo_turned_back", args))
            elif where[1]:
                self._sink_cargo(island, s)
                gone.add(s["id"])
            elif shipping_of(island, self.config) is not None:
                self._sink_ship(island, s)
                s["arrive"] += s["oneway"]
                s["delay"] = s.get("delay", 0) + s["oneway"]
        if gone:
            self.state["shipments"] = [s for s in self.state["shipments"] if s["id"] not in gone]

    def _lose_ships(self, island: IslandState, s: dict[str, Any], release: bool = True) -> int:
        """沈んだ船を船団から除き、傷みと同じ費用に数える（運賃に乗る）。除いた隻数（1000 分の 1）を
        返す。行きで沈んだときは release=False（代わりの船が同じ荷の分を使い続ける）。"""
        shipping = shipping_of(island, self.config)
        if shipping is None:
            return 0
        if release:
            ships.release(shipping, s["id"])
        lost = max(1, ships.ships_of_load(self.config, s["load"]))
        return ships.lose_ships(island, self.config, shipping, lost)

    def _sink_ship(self, island: IslandState, s: dict[str, Any]) -> None:
        count = self._lose_ships(island, s, release=False)
        island.stats["trade.ships_sunk"] = island.stats.get("trade.ships_sunk", 0) + count
        island.log.append(LogEntry("ship_sunk_out", {"ships": count / QUANTITY_SCALE}))

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
        stats = island.stats
        stats["trade.ships_sunk"] = stats.get("trade.ships_sunk", 0) + count
        stats["trade.cargo_sunk"] = stats.get("trade.cargo_sunk", 0) + qty
        args = {"ships": count / QUANTITY_SCALE, "good": good, "amount": qty / QUANTITY_SCALE}
        island.log.append(LogEntry("ship_sunk_cargo", args))

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
        shipping = shipping_of(island, self.config)
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
        """届け先の島がなくなった荷（記録の上で、下ろしたことにして消す）。"""
        qty = sum(q for _, q in s["batches"])
        unloaded = self.cargo["unloaded"]
        unloaded[s["good"]] = unloaded.get(s["good"], 0) + qty

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
            views = traders.views(island)
            payers: dict[str, Payer] = {}
            found = traders.prospects(island, self.defs, self.config)
            wage = island.economy.average_wage or self.config.get("labor.initial_wage")
            for f in traders_of(island, self.config, self.turn):  # 止まった商社は両替しない
                entity = entity_of(self.config, f)
                payers[entity] = Payer(entity)
                cash = funds.available(island, payers[entity])
                probe = {
                    int(v["currency"]): fxrates.to_foreign(
                        island, int(v["currency"]), round_money(cash * traders.FX_PROBE)
                    )
                    for v in views.values()
                }
                needs[entity] = {
                    c: [(amount, rate) for amount, rate in items]
                    for c, items in traders.import_needs(f, views, found, keep, probe).items()
                }
                floors[entity] = traders.export_floors(f, found)
                if traders.is_planned(island):  # 計画経済は外貨を売らず、買わない
                    keep_all.add(entity)
                wages = traders.workers_wanted(self.config, self.defs, f) * wage * keep
                short[entity] = max(0, wages - funds.available(island, payers[entity]))
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
    ) -> None:
        """1つの主体の、1つの通貨の注文。持ち高をこのターンに払う代金 due（政府の契約）と引き合う
        輸入から順に当て、足りない分を買い、余った分を売る。due の足りない分は、値幅の上端の1つの
        指値で、ほかの買いより先に買う（fxrates.add_buy）。keep（計画経済の商社）なら、余った分を
        売らず、輸入のための外貨も買わない（輸入は持っている外貨の範囲。設計「計画経済（3-4）」）。"""
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
            if amount > cover and (limit is None or limit > reference):
                fxrates.add_buy(
                    island, book, entity, payer, amount - cover, reference, width, limit
                )

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
        books.add_sector(seller, books.TRADE, "exports", value)
        books.add_sector(buyer, books.WAREHOUSE, "purchases", cost)
        books.add_sector(buyer, books.TRADE, "imports", cost)
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
        f = shipping_of(buyer, self.config)
        if f is None or port_of(buyer, self.config) is None:
            return 0
        if naval.blocked(self.world, self.defs, self.config, self.turn, buyer.id, seller.id, good):
            return 0  # 封鎖に止められた（出入りする船を止める）
        here = self.islands[buyer.id]
        free = ships.free(self.config, f) - here.ships_used
        oneway = one_way_turns(self.config, buyer, seller)
        trip = 2 * oneway
        room = min(free, ships.fuel_room(self.config, f, trip))
        # 船は走る時間の分だけ使う（_ship）ので、運べる重さは、空いている船の重さ ×（片道の
        # ターン数 × 船の速さ ÷ 区画）。近い島へは、1ターンに何度も行き来できる
        room = room * oneway * self.config.get("trade.ship_speed") // route_cells(buyer, seller)
        return max(0, room) * QUANTITY_SCALE // unit_weight(self.defs, good)

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
    ) -> None:
        """荷を買い手の島の船に積んで出す。積む港の使用料（売り手が払う）、船の運賃（買い手が自国の
        海運業に払う）、燃料を払い、運ぶ途中の荷を記録する（契約の荷は届いた物の行き先も）。"""
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
        shipping = shipping_of(buyer, self.config)
        arrive = self.turn + oneway
        if load > 0 and shipping is not None:
            arrive = self.turn + 2 * oneway
            # 荷が着くのはターンの区切りだが、船（と船員・燃料）を使うのは実際に走る時間の分だけ
            # （区画 ÷ 船の速さ。片道のターン数に切り上げない）。運賃は今までどおり荷 1 トン ×
            # 区画で払うので、近い島への航海の運賃が、船員の賃金と燃料より安くならない
            held = max(1, load * span // (oneway * self.config.get("trade.ship_speed")))
            ships.occupy(shipping, shipment_id, held)
            here = self.islands[buyer.id]
            here.ships_used += held
            here.fuel_value += ships.burn(buyer, self.config, shipping, held, 2 * oneway)
            here.carried += load * span
            freight = (
                ships.freight_rate(buyer, self.config, shipping) * load * span // QUANTITY_SCALE
            )
            freight = self._war_risk(freight, buyer, seller)
            payer = Payer(entity) if f is not None else gov_payer()
            paid = self._pay_freight(buyer, payer, freight, f)
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
        width = to_basis(self.config.get("trade.bloc_band"))
        budgets: dict[int, int] = {}  # 商社ごとの扱える重さの残り
        money_left: dict[tuple[int, str, int], int] = {}  # (島, 主体, 通貨) → 使える額の残り
        for bloc in list(blocs.blocs(self.world)):
            members = [m for m in bloc["members"] if m in self.islands]
            for good in tradable_goods(self.defs):
                key = (bloc["id"], good)
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
                for order, ask, got in zip(sells, asks, result.sold, strict=True):
                    item = traders.entry(ask.facility, good)
                    if got < order.quantity and "why" not in item:
                        item["why"] = WHY_BUYERS
                volume = 0
                if result.volume > 0:
                    volume = self._settle_bloc(bloc, good, result.price, result, bids, asks)
                got = {id(o): n for o, n in zip(buys, result.bought, strict=True)}
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
        """島ごとに、輸入の注文に回せる船の割合（空いている船 ÷ 商社の輸入したい重さ）と、往復の
        見込みのターン数を決める（契約の分は先に使った後）。"""
        for i in self.ids:
            island = self.world.islands[i]
            here = self.islands[i]
            shipping = shipping_of(island, self.config)
            if shipping is None or port_of(island, self.config) is None:
                here.import_share = 0.0
                continue
            want = 0
            for f in traders_of(island, self.config, self.turn):
                for good, item in traders.goods_of(f).items():
                    if item.get("dir") == traders.IMPORT:
                        left = item["m"] - item["held"][0]
                        if left > 0:
                            want += weight(self.defs, good, left)
            views = traders.views(island)
            trips = [2] + [
                2 * max(1, -(-v.get("cells", 1) // self.config.get("trade.ship_speed")))
                for v in views.values()
            ]
            here.trips = max(trips)
            free = ships.free(self.config, shipping) - here.ships_used
            free = min(free, ships.fuel_room(self.config, shipping, here.trips))
            if want <= 0:
                here.import_share = 1.0
            else:
                here.import_share = max(0.0, min(1.0, free / want))
                here.ship_short = free < want

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
                    # 計画経済（フェーズ3-4）の輸出は、売れ残った物を値段によらず売る
                    if item["age"] < clearance and not traders.is_planned(island):
                        own = traders.unit_cost(f, good)
                        own = max(1, own + part(own, cut) + fee_unit - subsidy)  # 輸出補助を見込む
                        limit = max(1, -(-own * BASIS // max(1, e)))
                    sells.append(Order(qty, limit))
                    asks.append(_Bid(m, f, entity, good))
                    budgets[f.id + m * 10**6] = left - weight(self.defs, good, qty)
                    self.islands[m].port_used += weight(self.defs, good, qty)  # 積む分を取っておく
                elif item.get("dir") == traders.IMPORT and item.get("bm") == bloc["id"]:
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
        残り self.quota_left で頭打ち。関税は、荷を下ろすときに払う分を見込んで指値を下げる）。

        指値は島で売って引き合う一番高い値段を、圏の値幅の上端までにする（それより高くは約定しない
        ので、決済通貨をその分だけ取り置けばよい。島の値段が圏よりずっと高い財で、指値のぶん取り
        置いて外貨を使い切り、ほかの財の注文が出なくなっていた）。狙う量に届かなかったわけを
        item["why"] に書く（貿易の画面。届いたら消す）。"""
        item.pop("why", None)
        view = traders.views(island).get(str(bloc["id"]))
        if view is None:
            return None
        currency = bloc["currency"]
        price = bloc["prices"].get(good) or view.get("prices", {}).get(good)
        if not price:
            item["why"] = WHY_NO_PRICE
            return None
        cut = to_basis(self.config.get("trade.trader_cut"))
        unit = unit_weight(self.defs, good)
        home = island.economy.prices.get(good, 0)
        e = fxrates.rate(island, currency)
        freight_unit = (
            ships.freight_rate(island, self.config) * unit * view.get("cells", 1) // QUANTITY_SCALE
        )
        fee_unit = ships.port_fee(island, self.config) * unit // QUANTITY_SCALE
        own = home - part(home, cut) - freight_unit - fee_unit
        tariff = 0 if blocs.no_tariff(bloc) else policy.tariff_rate(island, good)
        limit = own * BASIS // max(1, policy.with_tariff(e, tariff))
        if traders.is_planned(island):  # 計画経済は、行列のある物を圏の値幅の上端まで買う
            limit = 10**15
        if limit <= 0:
            item["why"] = WHY_PRICE
            return None
        reference = (
            blocs.fixed_price(bloc, good) or blocs.reference(self.world, bloc, good) or price
        )
        _, high = auction.band(reference, to_basis(self.config.get("trade.bloc_band")))
        limit = min(limit, high)
        want = item["m"] - item["held"][0]
        if want <= 0:
            return None
        why = None
        if unit > 0:
            share = self.islands[island.id].import_share
            if int(want * share) < want:
                want, why = int(want * share), WHY_SHIPS
            room = left * QUANTITY_SCALE // unit
            if room < want:
                want, why = room, WHY_CAPACITY
        quota = self.quota_left.get((island.id, good))
        if quota is not None and quota < want:
            want, why = max(0, quota), WHY_QUOTA
        key = (island.id, entity, currency)
        if key not in money_left:
            if island.id == currency:
                money_left[key] = funds.available(island, Payer(entity))
            else:
                money_left[key] = fxmoney.holding(island, entity, currency)
        money = money_left[key]
        if island.id == currency:  # 運賃と使用料も同じお金から払う
            money = money * BASIS // (BASIS + (freight_unit + fee_unit) * BASIS // max(1, limit))
        if money * QUANTITY_SCALE // limit < want:
            short = WHY_FUNDS if island.id == currency else WHY_MONEY
            want, why = money * QUANTITY_SCALE // limit, short
        if why is not None:
            item["why"] = why
        if want <= 0:
            return None
        money_left[key] -= money_of(limit, want)
        return Order(want, limit)

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
    ) -> int:
        """約定を、近い島の組から割り当てて払う（禁輸の組は割り当てない）。割り当てた量を返す。"""
        currency = bloc["currency"]
        matched = 0

        def distance(b: int, s: int) -> int:
            here = self.world.islands[bids[b].island]
            there = self.world.islands[asks[s].island]
            return route_cells(here, there) if here.id != there.id else 0

        def allowed(b: int, s: int) -> bool:
            return not policy.blocked(self.world, bids[b].island, asks[s].island, good)

        for b, s, qty in auction.match_nearest(result.bought, result.sold, distance, allowed):
            bid, ask = bids[b], asks[s]
            buyer = self.world.islands[bid.island]
            seller = self.world.islands[ask.island]
            payer = Payer(bid.entity) if buyer.id == currency else None
            if payer is not None:
                # 決済通貨の島の商社は、運賃と使用料も同じお金から払う。注文のときの運賃は見込み
                # なので、前の約定の運賃（遠い売り手など）を払った後の残りまでにする
                qty = min(qty, qty_for(funds.available(buyer, payer), price))
                if qty <= 0:
                    continue
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
            # 売り手: 港の倉庫から出し、売上と輸出に数える
            sf, bf = ask.facility, bid.facility
            batches = stock.take_batches(sf.stock, good, qty)
            moved = sum(q for _, q in batches)
            books.stock_moved(seller, sf.id, good, -moved)
            books.add(seller, sf.id, books.SALES, value)
            books.add_sector(seller, books.TRADE, "exports", value)
            taken = traders.take_cost(traders.entry(sf, good), moved)
            traders.cover_price_gap(seller, sf, ask.entity, taken, value)
            traders.exported(sf, good, moved)
            traders.add_volume(sf, weight(self.defs, good, moved))
            self.islands[seller.id].exports += value
            self.islands[seller.id].settled(currency, amount)
            self._export_subsidy(seller, sf, ask.entity, good, moved)
            matched += moved
            # 買い手: 仕入れと輸入に数え、運ぶ途中の荷も在庫に数える
            books.add(buyer, bf.id, books.PURCHASES, cost)
            books.add_sector(buyer, books.TRADE, "imports", cost)
            books.stock_moved(buyer, bf.id, good, moved)
            item = traders.entry(bf, good)
            item["held"][0] += moved
            item["held"][1] += cost
            item["transit"] += moved
            traders.add_volume(bf, weight(self.defs, good, moved))
            traders.spent_abroad(bf, currency, amount)
            self.islands[buyer.id].imports += cost
            self.islands[buyer.id].settled(currency, amount)
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
            )
        return matched

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
            traders.update_targets(
                island, self.defs, self.config, bloc_unmet, bloc_sellers, self.turn
            )
            self._site(island)
            fxrates.keep_turnover(island, self.config, here.turnover)
            state = island_state(island)
            state["blocs"] = {
                str(b["id"]): blocs.view_of(self.world, b, i)
                for b in blocs.member_of(self.world, i)
            }
            state["contracts"] = [
                contract_view(c, i, self.turn) for c in contracts.of_island(self.world, i)
            ]
            shipping = shipping_of(island, self.config)
            if shipping is not None:
                history = [*shipping.economy.get("ships_short", []), int(here.ship_short)]
                shipping.economy["ships_short"] = history[-self.config.get("trade.cost_turns") :]
            self._stats(island, here)

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
        "end": c["end"],
        "active": c["state"] == contracts.ACTIVE and c["start"] <= turn + 1 < c["end"],
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
    if payload.get("quantity", 0) <= 0 or payload.get("turns", 0) <= 0:
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
