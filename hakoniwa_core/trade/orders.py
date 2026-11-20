"""船の前注文（FX-60。2026-11-10、W-244）。

島に造船所（輸送船を作る作り方で動いている、差し押さえや閉鎖の途中でない造船所）があれば、海運業は
船を市場で買う代わりに、島の造船所に前もって注文します。島に造船所がなければ今どおり市場で買います
（trade/ships.py の ships_wanted）。

注文する隻数（order_wanted）: 直近 trade.cost_turns ターンの半分以上で船が足りずに荷を残していたら、
載せられなかった荷を運ぶのに要った船（``economy["ships_short_load"]``。荷の重さ × 往復の区画 ÷ 船の
速さ。1ターンの船出の分が往復の間ずっと船をふさぐので、毎ターン同じだけ荷が残るなら、これだけの船が
足りない）の平均を隻数に直した分（1隻に切り上げ。少なくとも1隻）と、寿命の来る船の買い替え（FX-11）の
大きいほうから、手元の船と注文済みで届いていない船を引いた分。注文は1隻単位です。

代金（1隻の値段は注文のときの島の値段）は、注文のときに trade.order_deposit、造船所が作り上げた
割合に合わせて trade.order_progress、引き渡しで残りを払います（現実の新造船の契約の、契約・起工・
進水・引き渡しの分割払い）。払えない分は次のターンに持ち越し、次の支払いの見込み
（economy/start.py の next_payments）に入れて、運転資金を借りるか持ち主が出します。

造船所は、手元の輸送船（作った船と売れ残りの船）を古い注文から順に取り置き（取り置いた船は市場に
出さない）、注文の残りの分を操業目標に足して先に作ります（production_goal）。作り上げて残りの代金が
払われたら引き渡し、海運業はそれを次の貿易の段で船団に入れます（ships.commission）。

取り消し:
- 届く見込みのターン（注文のときの造船所の全力で、前の注文を作り終えてから作る見込み）を
  trade.order_late_turns 過ぎても作り上がらない注文は取り消し、前払いを造船所の預金から返す。返し
  きれない分は造船所の借り（``economy["order_refunds"]``）に残して、後のターンに返す。造船所が倒産
  すると、その借りは差し押さえの記録の請求（貸し手と同じ順のふつうの請求。arrears.ORDERS）になる。
  差し押さえの間に取り消した注文の返しきれない分も、同じ請求に足す。
- 注文した海運業が倒産した（差し押さえられた）か、なくなったら、注文を取り消し、前払いは返さない
  （買い手の不履行）。作りかけの船と取り置いた船は造船所の在庫に戻り、市場で売る。
- 作り上げたのに残りの代金が払われないまま、届く見込みから trade.order_late_turns 過ぎた注文も、
  買い手の不履行として同じく取り消す。
- 造船所を取り壊す・たたむときは、残った注文をみな取り消して前払いを返す（cancel_all。差し押さえた
  造船所の取り壊しでは、返しきれない分を請求に足してから分ける）。
造船所が倒産しても、管財人のもとで動き続けて注文の船を作り続け、居抜きで買った会社が注文を引き継ぐ
（施設はそのままなので、注文もそのまま残る）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    SEIZED,
    STATE_FACILITIES,
    Payer,
    books,
    entity_of,
    funds,
    is_stopped,
    payer_of,
)
from hakoniwa_core.state import Facility, IslandState, LogEntry
from hakoniwa_core.trade import shipping_of
from hakoniwa_core.units import QUANTITY_SCALE, from_milli, money_of, round_money

MILLI = QUANTITY_SCALE
ORDERS = "ship_orders"  # 造船所の economy: 受けた前注文（古い順）
REFUNDS = "order_refunds"  # 造船所の economy: 返しきれなかった前払い（払い先 → 額）
DUE = "ship_order_due"  # 海運業の economy: 次のターンに払う前注文の代金の見込み
SHORT_LOAD = "ships_short_load"  # 海運業の economy: 載せられなかった荷を運ぶのに要った船の重さ
PAYMENT = "ship_order"  # 仕訳の種類: 前注文の代金
REFUND = "ship_order_refund"  # 仕訳の種類: 前払いの返金


# --- 造船所と注文 ------------------------------------------------------------------


def builds_ships(defs: Defs, config: GameConfig, f: Facility) -> bool:
    """f が今の作り方で輸送船を作る、動いている造船所か（差し押さえや閉鎖の途中も含む）。"""
    if not f.built or not fac.located(f):
        return False
    return fac.outputs_of(defs, f).get(config.get("trade.ship_good"), 0) > 0


def yards(island: IslandState, defs: Defs, config: GameConfig) -> list[Facility]:
    """新しい注文を受けられる造船所（差し押さえ・閉鎖の途中・止めた造船所は受けない）。"""
    return [
        f
        for f in sorted(island.facilities, key=lambda f: f.id)
        if builds_ships(defs, config, f)
        and SEIZED not in f.economy
        and "closing" not in f.economy
        and not is_stopped(f)
    ]


def orders_of(f: Facility) -> list[dict[str, Any]]:
    return f.economy.get(ORDERS, [])


def backlog(f: Facility) -> int:
    """造船所の注文で、まだ作っていない船（1000 分の 1 隻）。"""
    return sum(o["qty"] - o["built"] for o in orders_of(f))


def reserved(config: GameConfig, f: Facility, good: str) -> int:
    """造船所が注文のために取り置いた good（輸送船）の量。市場には出さない。"""
    if good != config.get("trade.ship_good"):
        return 0
    return sum(o["built"] for o in orders_of(f))


def on_order(island: IslandState, buyer: Facility) -> int:
    """海運業 buyer が注文して、まだ届いていない船（1000 分の 1 隻）。"""
    return sum(o["qty"] for f in island.facilities for o in orders_of(f) if o["buyer"] == buyer.id)


def _value(o: dict[str, Any]) -> int:
    return money_of(o["price"], o["qty"])


def _scheduled(config: GameConfig, o: dict[str, Any], built: int) -> int:
    """作り上げた量が built のときまでに払う代金（引き渡しの残りは含まない）。"""
    value = _value(o)
    share = config.get("trade.order_deposit") + config.get("trade.order_progress") * built / max(
        1, o["qty"]
    )
    return min(value, round_money(value * share))


def _out(defs: Defs, config: GameConfig, f: Facility) -> int:
    return fac.outputs_of(defs, f).get(config.get("trade.ship_good"), 0)


def _due_turn(defs: Defs, config: GameConfig, f: Facility, qty: int, turn: int) -> int:
    """造船所 f に qty を注文したときに届く見込みのターン（前の注文を作り終えてから全力で作る）。"""
    out = max(1, _out(defs, config, f))
    return turn + -(-(backlog(f) + qty) // out)


# --- 注文する隻数 ------------------------------------------------------------------


def whole(qty: int) -> int:
    """1隻単位に切り上げる。"""
    return -(-qty // MILLI) * MILLI if qty > 0 else 0


def short_ships(config: GameConfig, f: Facility) -> int:
    """直近 trade.cost_turns ターンの船が足りなかったターンの割合と載せられなかった荷から見込んだ、
    足りない船（1000 分の 1 隻。1隻単位。半分より少ないターンしか足りなかったなら 0）。"""
    history = f.economy.get("ships_short", [])
    turns = config.get("trade.cost_turns")
    if len(history) < turns or sum(history) * 2 < len(history):
        return 0
    loads = f.economy.get(SHORT_LOAD, [])
    average = sum(loads) // max(1, len(loads))
    ships = average // max(1, config.get("trade.ship_capacity"))
    return max(MILLI, whole(ships))


def order_wanted(island: IslandState, config: GameConfig, f: Facility, turn: int) -> int:
    """海運業 f が造船所に注文する船（1000 分の 1 隻。1隻単位）。"""
    from hakoniwa_core.trade import ships

    want = max(short_ships(config, f), whole(ships.renewal(config, f, turn)))
    have = stock.total(f.stock, config.get("trade.ship_good")) + on_order(island, f)
    return whole(max(0, want - have))


def place(island: IslandState, defs: Defs, config: GameConfig, f: Facility, turn: int) -> int:
    """海運業 f が島の造船所に船を注文し、手付けを払う。注文した量を返す。造船所がなければ -1
    （市場で買う）。手付けが払えずに注文できなかった分の手付けは、次の支払いの見込みに足す。"""
    candidates = yards(island, defs, config)
    if not candidates:
        return -1
    want = order_wanted(island, config, f, turn)
    if want <= 0:
        return 0
    yard = min(candidates, key=lambda y: (_due_turn(defs, config, y, want, turn), y.id))
    price = island.economy.prices.get(config.get("trade.ship_good"), 0)
    payer = payer_of(defs, config, f)
    payee = entity_of(config, yard)
    deposit = config.get("trade.order_deposit")
    per_ship = round_money(money_of(price, MILLI) * deposit)
    qty = want
    if payer.entity != payee and per_ship > 0:
        qty = min(want, funds.available(island, payer) // per_ship * MILLI)
    if qty < want:
        f.economy[DUE] = f.economy.get(DUE, 0) + per_ship * (want - qty) // MILLI
    if qty <= 0:
        return 0
    order = {
        "buyer": f.id,
        "payer": payer.entity,
        "item": payer.item or "",
        "qty": qty,
        "built": 0,
        "price": price,
        "paid": 0,
        "placed": turn,
        "due": _due_turn(defs, config, yard, qty, turn),
    }
    yard.economy.setdefault(ORDERS, []).append(order)
    _collect(island, config, yard, order, _scheduled(config, order, 0))
    island.log.append(
        LogEntry(
            "ship_order_placed",
            {"count": from_milli(qty), "x": yard.x, "y": yard.y, "turns": order["due"] - turn},
        )
    )
    return qty


# --- 毎ターンの進み ----------------------------------------------------------------


def _buyer_payer(o: dict[str, Any]) -> Payer:
    return Payer(o["payer"], o.get("item") or None)


def _collect(
    island: IslandState, config: GameConfig, yard: Facility, o: dict[str, Any], due: int
) -> None:
    """注文 o の払うべき額 due まで、買い手が払える分を払う。"""
    owed = due - o["paid"]
    if owed <= 0:
        return
    payer = _buyer_payer(o)
    payee = entity_of(config, yard)
    if payer.entity != payee:
        owed = min(owed, funds.available(island, payer))
        funds.pay(island, payer, payee, owed, PAYMENT)
    o["paid"] += max(0, owed)


def _yard_payer(config: GameConfig, yard: Facility) -> Payer:
    entity = entity_of(config, yard)
    return Payer(entity, STATE_FACILITIES if entity == ledger.GOV else None)


def _refund(
    island: IslandState,
    config: GameConfig,
    yard: Facility,
    payee: str,
    amount: int,
    claims: dict[str, Any] | None,
) -> None:
    """前払い amount を造船所の預金から返す。返しきれない分は claims（差し押さえの請求）か、造船所の
    借りに残す。"""
    from hakoniwa_core.economy import arrears

    payer = _yard_payer(config, yard)
    if amount <= 0 or payer.entity == payee:
        return
    paid = min(amount, funds.available(island, payer))
    funds.pay(island, payer, payee, paid, REFUND)
    left = amount - paid
    if left <= 0:
        return
    table = claims.setdefault(arrears.ORDERS, {}) if claims is not None else None
    if table is None:
        table = yard.economy.setdefault(REFUNDS, {})
    table[payee] = table.get(payee, 0) + left


def _claims_of(yard: Facility) -> dict[str, Any] | None:
    seized = yard.economy.get(SEIZED)
    if isinstance(seized, dict) and isinstance(seized.get("claims"), dict):
        return seized["claims"]
    return None


def _cancel(
    island: IslandState,
    config: GameConfig,
    yard: Facility,
    o: dict[str, Any],
    refund: bool,
    claims: dict[str, Any] | None = None,
) -> None:
    """注文 o を取り消す（取り置いた船は造船所の在庫に戻り、市場で売る）。refund なら前払いを
    返す。"""
    yard.economy[ORDERS] = [x for x in orders_of(yard) if x is not o]
    if not yard.economy[ORDERS]:
        yard.economy.pop(ORDERS)
    if refund:
        _refund(island, config, yard, o["payer"], o["paid"], claims)
    island.log.append(
        LogEntry(
            "ship_order_cancelled" if refund else "ship_order_defaulted",
            {"count": from_milli(o["qty"]), "x": yard.x, "y": yard.y},
        )
    )


def cancel_all(island: IslandState, config: GameConfig, yard: Facility) -> None:
    """取り壊す・たたむ造船所の注文をみな取り消し、前払いを返す（差し押さえた造船所は、返しきれない
    分を請求に足す。そうでなければ、返しきれない分は返らない）。"""
    from hakoniwa_core.economy import arrears

    claims = _claims_of(yard)
    lost: dict[str, Any] = {}  # 差し押さえていない造船所の返しきれない分（返らない）
    for o in list(orders_of(yard)):
        _cancel(island, config, yard, o, True, claims if claims is not None else lost)
    owed = yard.economy.pop(REFUNDS, {})
    if claims is not None:
        table = claims.setdefault(arrears.ORDERS, {})
        for payee, value in sorted(owed.items()):
            table[payee] = table.get(payee, 0) + value


def claims_on_bankruptcy(yard: Facility) -> dict[str, int]:
    """倒産した造船所の返しきれていない前払い（差し押さえの請求に入れる）。"""
    return {p: v for p, v in sorted(yard.economy.pop(REFUNDS, {}).items()) if v > 0}


def run(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> None:
    """市場の段の初め: 前注文の取り消し・船の取り置き・代金の支払い・引き渡し（作った船は生産の段で
    造船所の在庫に入っている）。海運業の次の支払いの見込み（DUE）も決め直す。"""
    good = config.get("trade.ship_good")
    late = config.get("trade.order_late_turns")
    shipping = shipping_of(island, config)
    if shipping is not None:
        shipping.economy.pop(DUE, None)
    for yard in sorted(island.facilities, key=lambda f: f.id):
        _pay_refunds(island, config, yard)
        if not orders_of(yard):
            continue
        if not yard.built:  # 念のため（工事に戻った施設は注文を受けていられない）
            cancel_all(island, config, yard)
            continue
        claims = _claims_of(yard)
        for o in list(orders_of(yard)):
            buyer = island.facility(o["buyer"])
            if buyer is None or SEIZED in buyer.economy:
                _cancel(island, config, yard, o, False)
            elif o["built"] < o["qty"] and turn > o["due"] + late:
                _cancel(island, config, yard, o, True, claims)
            elif turn > o["due"] + late:  # 作り上げたのに残りの代金が払われない
                _cancel(island, config, yard, o, False)
        free = stock.total(yard.stock, good) - reserved(config, yard, good)
        for o in orders_of(yard):
            take = max(0, min(free, o["qty"] - o["built"]))
            o["built"] += take
            free -= take
        for o in list(orders_of(yard)):
            if o["built"] < o["qty"]:
                _collect(island, config, yard, o, _scheduled(config, o, o["built"]))
                continue
            _collect(island, config, yard, o, _value(o))
            if o["paid"] >= _value(o):
                _deliver(island, defs, config, yard, o)
        if shipping is not None:
            shipping.economy[DUE] = shipping.economy.get(DUE, 0) + _next_due(
                defs, config, yard, shipping.id
            )
    if shipping is not None and not shipping.economy.get(DUE):
        shipping.economy.pop(DUE, None)


def _pay_refunds(island: IslandState, config: GameConfig, yard: Facility) -> None:
    """造船所の返しきれていない前払いを、払える分だけ返す。"""
    owed = yard.economy.get(REFUNDS)
    if not owed:
        return
    payer = _yard_payer(config, yard)
    for payee in sorted(owed):
        paid = min(owed[payee], funds.available(island, payer))
        funds.pay(island, payer, payee, paid, REFUND)
        owed[payee] -= paid
    left = {p: v for p, v in owed.items() if v > 0}
    if left:
        yard.economy[REFUNDS] = left
    else:
        yard.economy.pop(REFUNDS)


def _next_due(defs: Defs, config: GameConfig, yard: Facility, buyer: int) -> int:
    """造船所が次のターンに全力で作ったときに、海運業 buyer が払う代金の見込み（今の払い残しを
    含む）。"""
    room = _out(defs, config, yard)
    total = 0
    for o in orders_of(yard):
        add = max(0, min(room, o["qty"] - o["built"]))
        room -= add
        built = o["built"] + add
        due = _value(o) if built >= o["qty"] else _scheduled(config, o, built)
        if o["buyer"] == buyer:
            total += max(0, due - o["paid"])
    return total


def _deliver(
    island: IslandState, defs: Defs, config: GameConfig, yard: Facility, o: dict[str, Any]
) -> None:
    """作り上げて代金を払い終えた注文の船を海運業に渡す（次の貿易の段で船団に入る）。帳簿には、
    渡したときに注文の値段で売り買いしたとして付ける。"""
    good = config.get("trade.ship_good")
    buyer = island.facility(o["buyer"])
    assert buyer is not None
    yard.economy[ORDERS] = [x for x in orders_of(yard) if x is not o]
    if not yard.economy[ORDERS]:
        yard.economy.pop(ORDERS)
    qty = o["qty"]
    batches = stock.take_batches(yard.stock, good, qty, None)
    stock.put_batches(buyer.stock, good, batches)
    value = _value(o)
    books.stock_moved(island, yard.id, good, -qty)
    books.add(island, yard.id, books.SALES, value)
    books.stock_moved(island, buyer.id, good, qty)
    books.add(island, buyer.id, books.PURCHASES, value)
    # 届いた船で様子が変わるので、船が足りなかった記録は数え直す（数えたままだと、同じ不足で
    # また注文する）
    buyer.economy.pop("ships_short", None)
    buyer.economy.pop(SHORT_LOAD, None)
    island.log.append(
        LogEntry("ship_order_delivered", {"count": from_milli(qty), "x": yard.x, "y": yard.y})
    )


def production_goal(defs: Defs, config: GameConfig, f: Facility, goal: int, before: int) -> int:
    """造船所の操業目標 goal に、注文の残り（全力の1ターン分まで）を足す（1ターンに変えるのは全力の
    market.max_target_change まで）。"""
    left = backlog(f)
    if left <= 0 or not builds_ships(defs, config, f):
        return goal
    full = _out(defs, config, f)
    extra = fac.ratio(min(left, full), full)
    limit = round_money(config.get("market.max_target_change") * fac.FULL + 0.5)
    return max(goal, min(fac.FULL, goal + extra, before + limit))
