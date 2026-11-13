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

圏の造船所への注文（FX-78。2026-11-13）: 島に注文を受けられる造船所がない海運業は、貿易の段で、圏の
加盟国の造船所のうち届く見込み（前の注文を作り終えてから全力で作る見込み ＋ 走る片道のターン）の一番
早い所に注文する（戦っている国・輸送船を禁輸している国は除く）。注文は造船所の ``ship_orders`` に、
買い手の島（"island"）・主体・決済通貨（"currency"。両方が入っている圏の決済通貨）・値段（注文のときの
造船所の島の値段を決済通貨に直して固定）を持たせて置く。
- 払い（手付け・建造の間・引き渡しの残り。割合は同じ）と引き渡しは貿易の段で、決済通貨で払う
  （money.transfer）。海運業は払う外貨を為替の段で買う（計画経済の島の国営の海運業は政府の外貨）。
  払いは前払い（国際収支の assets.orders・liabilities.orders）で、引き渡しのときに財の輸出入
  （GDP・国際収支・関税）に振り替える。届いた船の設備の額は払った額。輸入の割り当てを超える船は
  引き渡さずに待つ。
- 造船所は島の中の注文を先に取り置き、先に作る。
- 戦争・禁輸・圏を抜けて決済通貨の圏が共通でなくなったら、注文を取り消して前払いを返す。返金は
  造船所の島の通貨（払った決済通貨の額を今のレートで直した額）で、買い手の非居住者の預金に返す
  （島ごとの段で返した分は、次の貿易の段で買い手の外貨の持ち高に入る。money.pend）。返しきれない分は
  島の中の注文と同じく造船所の借り・差し押さえの請求になる。
- 注文済みで届いていない隻数は、貿易の段で海運業の ``economy["ships_on_order_abroad"]`` に数え、
  市場で買う量と次の注文から引く。
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
from hakoniwa_core.state import Facility, IslandState, LogEntry, WorldState
from hakoniwa_core.trade import shipping_of
from hakoniwa_core.units import QUANTITY_SCALE, from_milli, money_of, round_money

MILLI = QUANTITY_SCALE
ORDERS = "ship_orders"  # 造船所の economy: 受けた前注文（古い順）
REFUNDS = "order_refunds"  # 造船所の economy: 返しきれなかった前払い（払い先 → 額）
DUE = "ship_order_due"  # 海運業の economy: 次のターンに払う前注文の代金の見込み
SHORT_LOAD = "ships_short_load"  # 載せられなかった荷の船の重さ（ships.SHORT_LOAD と同じ）
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


def is_abroad(o: dict[str, Any]) -> bool:
    """ほかの島の海運業からの注文か（FX-78）。"""
    return "island" in o


def on_order(island: IslandState, buyer: Facility) -> int:
    """海運業 buyer が島の造船所に注文して、まだ届いていない船（1000 分の 1 隻）。"""
    return sum(
        o["qty"]
        for f in island.facilities
        for o in orders_of(f)
        if o["buyer"] == buyer.id and not is_abroad(o)
    )


def all_on_order(island: IslandState, buyer: Facility) -> int:
    """島の造船所と圏のほかの島の造船所に注文して、まだ届いていない船（1000 分の 1 隻）。"""
    from hakoniwa_core.trade import ships

    return on_order(island, buyer) + buyer.economy.get(ships.ABROAD, 0)


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
    have = stock.total(f.stock, config.get("trade.ship_good")) + all_on_order(island, f)
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


def _paid_here(island: IslandState, o: dict[str, Any]) -> int:
    """注文 o の前払いを造船所の島の通貨に直した額（ほかの島の注文は決済通貨で払っているので、
    今のレートで直す。FX-78）。"""
    from hakoniwa_core.trade import fx as fxrates

    if not is_abroad(o) or o["currency"] == island.id:
        return o["paid"]
    return fxrates.to_own(island, o["currency"], o["paid"])


def _pay_back(island: IslandState, payer: Payer, payee: str, amount: int) -> None:
    """前払いを返す。ほかの島の海運業（非居住者の名前）への返金は、次の貿易の段でその島の外貨の
    持ち高に入る（money.pend）。"""
    from hakoniwa_core.trade import money as fxmoney

    funds.pay(island, payer, payee, amount, REFUND)
    if ledger.is_nonresident(payee):
        fxmoney.pend(island, payee, amount, prepaid=True)


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
    _pay_back(island, payer, payee, paid)
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
        _refund(island, config, yard, o["payer"], _paid_here(island, o), claims)
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
            if is_abroad(o):  # ほかの島の注文の払い・取り消し・引き渡しは貿易の段（run_abroad）
                continue
            buyer = island.facility(o["buyer"])
            if buyer is None or SEIZED in buyer.economy:
                _cancel(island, config, yard, o, False)
            elif o["built"] < o["qty"] and turn > o["due"] + late:
                _cancel(island, config, yard, o, True, claims)
            elif turn > o["due"] + late:  # 作り上げたのに残りの代金が払われない
                _cancel(island, config, yard, o, False)
        free = stock.total(yard.stock, good) - reserved(config, yard, good)
        for o in _by_priority(yard):
            take = max(0, min(free, o["qty"] - o["built"]))
            o["built"] += take
            free -= take
        for o in list(orders_of(yard)):
            if is_abroad(o):
                continue
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
        _pay_back(island, payer, payee, paid)
        owed[payee] -= paid
    left = {p: v for p, v in owed.items() if v > 0}
    if left:
        yard.economy[REFUNDS] = left
    else:
        yard.economy.pop(REFUNDS)


def _next_due(defs: Defs, config: GameConfig, yard: Facility, buyer: int) -> int:
    """造船所が次のターンに全力で作ったときに、海運業 buyer が払う代金の見込み（今の払い残しを
    含む）。"""
    return sum(
        due for o, due in _next_dues(defs, config, yard) if not is_abroad(o) and o["buyer"] == buyer
    )


def _by_priority(yard: Facility) -> list[dict[str, Any]]:
    """造船所が船を取り置き・作る順: 島の中の注文を先に、それぞれ古い順（FX-78）。"""
    return sorted(orders_of(yard), key=is_abroad)


def _next_dues(defs: Defs, config: GameConfig, yard: Facility) -> list[tuple[dict[str, Any], int]]:
    """造船所が次のターンに全力で作ったときに、注文ごとに払う代金の見込み（今の払い残しを含む）。"""
    room = _out(defs, config, yard)
    out = []
    for o in _by_priority(yard):
        add = max(0, min(room, o["qty"] - o["built"]))
        room -= add
        built = o["built"] + add
        due = _value(o) if built >= o["qty"] else _scheduled(config, o, built)
        out.append((o, max(0, due - o["paid"])))
    return out


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


# --- 圏の造船所への注文（FX-78。貿易の段） ------------------------------------------


def _currency_between(world: WorldState, a: int, b: int) -> int | None:
    """島 a と b が両方入っている圏の決済通貨（番号の一番小さい圏。なければ None）。"""
    from hakoniwa_core.trade import blocs

    for bloc in sorted(blocs.blocs(world), key=lambda x: x["id"]):
        if a in bloc["members"] and b in bloc["members"]:
            return bloc["currency"]
    return None


def _open_between(world: WorldState, config: GameConfig, a: int, b: int, turn: int) -> bool:
    """島 a と b の間で輸送船を注文・引き渡しできるか（戦争と禁輸）。"""
    from hakoniwa_core.diplomacy import war
    from hakoniwa_core.trade import policy

    good = config.get("trade.ship_good")
    return not war.fighting(world, a, b, turn) and not policy.blocked(world, a, b, good)


def _in_currency(world: WorldState, island: IslandState, currency: int, own: int) -> int:
    """島の通貨の額 own を、通貨 currency に直す（レートがなければ 0）。"""
    from hakoniwa_core.trade import fx as fxrates

    if currency == island.id:
        return own
    if not fxrates.has_rate(island, currency):
        return 0
    return fxrates.to_foreign(island, currency, own)


def _shipping_buyer(island: IslandState, config: GameConfig) -> Facility | None:
    """圏の造船所に注文できる海運業（建っていて、止まっておらず、差し押さえられていない）。"""
    f = shipping_of(island, config)
    if f is None or not f.built or is_stopped(f) or SEIZED in f.economy:
        return None
    return f


def plan_abroad(
    world: WorldState, defs: Defs, config: GameConfig, turn: int, ids: list[int]
) -> dict[str, Any]:
    """貿易の段の初め（為替の段の前）: 島ごとの段で返した前払いを持ち高に入れ、海運業ごとの圏の
    造船所への注文済みの隻数を数え直し、新しい注文（届く見込みの一番早い造船所と隻数）と、この
    ターンに決済通貨で払う見込み（為替の段で買う外貨）を決める。"""
    from hakoniwa_core.trade import money as fxmoney
    from hakoniwa_core.trade import one_way_turns, ships

    fxmoney.settle_pending(world)
    ordered: dict[tuple[int, int], int] = {}
    owed: dict[int, dict[str, dict[int, int]]] = {}  # 島 → 主体 → 通貨 → このターンの払い
    for j in ids:
        for yard in world.islands[j].facilities:
            for o in orders_of(yard):
                if not is_abroad(o):
                    continue
                key = (o["island"], o["buyer"])
                ordered[key] = ordered.get(key, 0) + o["qty"]
                target = _value(o) if o["built"] >= o["qty"] else _scheduled(config, o, o["built"])
                if target > o["paid"]:
                    mine = owed.setdefault(o["island"], {}).setdefault(o["entity"], {})
                    mine[o["currency"]] = mine.get(o["currency"], 0) + target - o["paid"]
    new: dict[int, dict[str, Any]] = {}
    deposit = config.get("trade.order_deposit")
    for i in ids:
        island = world.islands[i]
        f = shipping_of(island, config)
        if f is None:
            continue
        if ordered.get((i, f.id), 0) > 0:
            f.economy[ships.ABROAD] = ordered[(i, f.id)]
        else:
            f.economy.pop(ships.ABROAD, None)
        f.economy.pop(ships.REACH, None)
        if _shipping_buyer(island, config) is None or yards(island, defs, config):
            continue
        want = order_wanted(island, config, f, turn)
        best: tuple[int, int, int] | None = None
        for j in ids:
            if j == i or _currency_between(world, i, j) is None:
                continue
            if not _open_between(world, config, i, j, turn):
                continue
            other = world.islands[j]
            for yard in yards(other, defs, config):
                sail = one_way_turns(config, other, island)
                due = _due_turn(defs, config, yard, max(MILLI, want), turn) + sail
                if best is None or (due, j, yard.id) < best:
                    best = (due, j, yard.id)
        if best is not None:  # 圏の造船所に注文できる（市場では買わない）
            f.economy[ships.REACH] = True
        if want <= 0:
            continue
        if best is None:
            continue
        due, j, yard_id = best
        currency = _currency_between(world, i, j)
        assert currency is not None
        other = world.islands[j]
        price = _in_currency(
            world, other, currency, other.economy.prices.get(config.get("trade.ship_good"), 0)
        )
        if price <= 0:
            continue
        payer = payer_of(defs, config, f)
        new[i] = {
            "island": j,
            "yard": yard_id,
            "qty": want,
            "currency": currency,
            "price": price,
            "due": due,
            "entity": payer.entity,
            "item": payer.item or "",
        }
        mine = owed.setdefault(i, {}).setdefault(payer.entity, {})
        first = round_money(money_of(price, want) * deposit)
        mine[currency] = mine.get(currency, 0) + first
    return {"new": new, "owed": owed}


def fx_needs(
    island: IslandState,
    plan: dict[str, Any],
    needs: dict[str, dict[int, list[tuple[int, int | None]]]],
    payers: dict[str, Payer],
) -> None:
    """為替の段: 海運業が圏の造船所に払う外貨（このターンの払いと新しい注文の手付け）を、主体の
    外貨の見込みに足す。持ち高の分は為替の段が引く。計画経済の国営の海運業は政府の主体なので、
    政府の外貨の見込みに入る（政府の外貨から先に払う）。

    指値は前のレートまで（売りがあれば買う。足りなければ、買えた分を持ち高にためて次のターンに
    払う）。値幅の上端まで、ほかの買いより先に買うと、1隻で島の輸入の何か月分にもなる船の代金が
    毎ターン通貨を値幅いっぱい下げ、輸入を締め出した（2026-11-13 の測り）。"""
    from hakoniwa_core.trade import fx as fxrates

    for entity, currencies in sorted(plan.get("owed", {}).get(island.id, {}).items()):
        for currency, amount in sorted(currencies.items()):
            if currency != island.id and amount > 0:
                limit = fxrates.rate(island, currency) + 1  # 前のレートより高くは買わない
                needs.setdefault(entity, {}).setdefault(currency, []).append((amount, limit))
                payers.setdefault(entity, Payer(entity))


def _available(world: WorldState, o: dict[str, Any]) -> int:
    """注文 o の買い手が今払える決済通貨。"""
    from hakoniwa_core.trade import money as fxmoney

    buyer_island = world.islands[o["island"]]
    if o["currency"] == buyer_island.id:
        return funds.available(buyer_island, _abroad_payer(o))
    return fxmoney.holding(buyer_island, o["entity"], o["currency"])


def _abroad_payer(o: dict[str, Any]) -> Payer:
    return Payer(o["entity"], o.get("item") or None)


def _collect_abroad(
    world: WorldState, config: GameConfig, yard_island: IslandState, yard: Facility, o, due: int
) -> None:
    """ほかの島の注文 o の払うべき額 due まで、買い手が払える分を決済通貨で払う（前払い）。"""
    from hakoniwa_core.trade import balance
    from hakoniwa_core.trade import money as fxmoney

    amount = min(due - o["paid"], _available(world, o))
    if amount <= 0:
        return
    buyer_island = world.islands[o["island"]]
    currency = o["currency"]
    values = {
        buyer_island.id: balance.value_of(world, buyer_island, currency, amount),
        yard_island.id: balance.value_of(world, yard_island, currency, amount),
    }
    fxmoney.transfer(
        world,
        currency,
        (buyer_island.id, o["entity"]),
        (yard_island.id, entity_of(config, yard)),
        amount,
        PAYMENT,
        values[yard_island.id],
        payer=_abroad_payer(o) if buyer_island.id == currency else None,
        values=values,
    )
    balance.add(buyer_island, "assets.orders", values[buyer_island.id])
    balance.add(yard_island, "liabilities.orders", values[yard_island.id])
    o["paid"] += amount
    o["paid_buyer"] = o.get("paid_buyer", 0) + values[buyer_island.id]
    o["paid_yard"] = o.get("paid_yard", 0) + values[yard_island.id]


def _deliver_abroad(
    world: WorldState,
    config: GameConfig,
    yard_island: IslandState,
    yard: Facility,
    o: dict[str, Any],
    buyer: Facility,
) -> None:
    """作り上げて代金を払い終えた注文の船を、ほかの島の海運業に渡す（次の貿易の段で船団に入る）。
    前払いを財の輸出入に振り替え、輸送船の関税を払う。"""
    from hakoniwa_core.trade import balance, policy, ships
    from hakoniwa_core.units import part

    good = config.get("trade.ship_good")
    buyer_island = world.islands[o["island"]]
    yard.economy[ORDERS] = [x for x in orders_of(yard) if x is not o]
    if not yard.economy[ORDERS]:
        yard.economy.pop(ORDERS)
    qty = o["qty"]
    stock.put_batches(buyer.stock, good, stock.take_batches(yard.stock, good, qty, None))
    sold, bought = o.get("paid_yard", 0), o.get("paid_buyer", 0)
    books.stock_moved(yard_island, yard.id, good, -qty)
    books.add(yard_island, yard.id, books.SALES, sold)
    books.add_trade(yard_island, "exports", good, sold)
    books.stock_moved(buyer_island, buyer.id, good, qty)
    books.add(buyer_island, buyer.id, books.PURCHASES, bought)
    books.add_trade(buyer_island, "imports", good, bought)
    balance.add(yard_island, "goods", sold)
    balance.add(yard_island, "liabilities.orders", -sold)
    balance.add(buyer_island, "goods", -bought)
    balance.add(buyer_island, "assets.orders", -bought)
    paid = buyer.economy.get(ships.PAID, [0, 0])
    buyer.economy[ships.PAID] = [paid[0] + qty, paid[1] + bought]
    tariff = part(bought, policy.tariff_rate(buyer_island, good))  # 輸入の関税
    payer = _abroad_payer(o)
    if tariff > 0 and payer.entity != ledger.GOV:
        tariff = min(tariff, funds.available(buyer_island, payer))
        funds.pay(buyer_island, payer, ledger.GOV, tariff, "tariff")
        books.add(buyer_island, buyer.id, books.TARIFF, tariff)
        stats = buyer_island.stats
        stats["trade.tariffs"] = stats.get("trade.tariffs", 0) + tariff
    buyer.economy.pop("ships_short", None)
    buyer.economy.pop(SHORT_LOAD, None)
    count = from_milli(qty)
    yard_island.log.append(
        LogEntry(
            "ship_order_exported",
            {"count": count, "x": yard.x, "y": yard.y, "country": buyer_island.name},
        )
    )
    buyer_island.log.append(
        LogEntry("ship_order_imported", {"count": count, "country": yard_island.name})
    )


def run_abroad(
    world: WorldState,
    defs: Defs,
    config: GameConfig,
    turn: int,
    ids: list[int],
    plan: dict[str, Any],
) -> None:
    """貿易の段（為替の段の後）: ほかの島の注文の取り消し・払い・引き渡しと、新しい注文（手付け）。
    海運業の次の支払いの見込み（DUE）に、次のターンの払いの見込みを足す（島の通貨に直す）。"""
    from hakoniwa_core.trade import policy

    late = config.get("trade.order_late_turns")
    good = config.get("trade.ship_good")
    for j in ids:
        yard_island = world.islands[j]
        for yard in sorted(yard_island.facilities, key=lambda f: f.id):
            claims = _claims_of(yard)
            for o in [x for x in orders_of(yard) if is_abroad(x)]:
                buyer_island = world.islands.get(o["island"])
                buyer = buyer_island.facility(o["buyer"]) if buyer_island is not None else None
                if buyer is None or SEIZED in buyer.economy or o["island"] not in ids:
                    _cancel(yard_island, config, yard, o, False)  # 買い手の不履行
                elif _currency_between(world, o["island"], j) != o["currency"] or not (
                    _open_between(world, config, o["island"], j, turn)
                ):
                    _cancel(yard_island, config, yard, o, True, claims)
                    buyer_island.log.append(
                        LogEntry(
                            "ship_order_cancelled_abroad",
                            {"count": from_milli(o["qty"]), "country": yard_island.name},
                        )
                    )
                elif o["built"] < o["qty"] and turn > o["due"] + late:
                    _cancel(yard_island, config, yard, o, True, claims)
                    buyer_island.log.append(
                        LogEntry(
                            "ship_order_cancelled_abroad",
                            {"count": from_milli(o["qty"]), "country": yard_island.name},
                        )
                    )
                elif o["paid"] < _value(o) and turn > o["due"] + late:
                    _cancel(yard_island, config, yard, o, False)  # 残りの代金が払われない
            for o in [x for x in orders_of(yard) if is_abroad(x)]:
                done = o["built"] >= o["qty"]
                _collect_abroad(
                    world,
                    config,
                    yard_island,
                    yard,
                    o,
                    _value(o) if done else _scheduled(config, o, o["built"]),
                )
                if not done or o["paid"] < _value(o):
                    continue
                buyer_island = world.islands[o["island"]]
                quota = policy.quota(buyer_island, good)
                if quota is not None and o["qty"] > quota:  # 輸入の割り当てを超える船は待つ
                    continue
                buyer = buyer_island.facility(o["buyer"])
                assert buyer is not None
                _deliver_abroad(world, config, yard_island, yard, o, buyer)
    _place_abroad(world, defs, config, turn, plan)
    _abroad_dues(world, defs, config, ids)


def _place_abroad(
    world: WorldState, defs: Defs, config: GameConfig, turn: int, plan: dict[str, Any]
) -> None:
    """plan_abroad で決めた注文を出し、手付けを払う（持っている外貨で払える分の隻数だけ）。

    払えなかった分の手付けは、次の支払いの見込み（DUE）に足さない。足すと運転資金を借りるか持ち主が
    出して自国通貨が海運業にたまるのに、外貨は前のレートまでしか買わないので注文が出ないまま、
    次のターンも同じ額（通貨が下がると膨らむ額）を借り、お金が増えて通貨がさらに下がった
    （2026-11-13 の測り: plan 種 2 で海運業の手元のお金が 500T に 1700 億、通貨が 90 倍に
    下がった）。
    手付けは、海運業が運賃で集めた自分のお金で外貨を買ってためる。"""
    from hakoniwa_core.trade import ships

    deposit = config.get("trade.order_deposit")
    for i, p in sorted(plan.get("new", {}).items()):
        island = world.islands[i]
        f = _shipping_buyer(island, config)
        yard_island = world.islands[p["island"]]
        yard = yard_island.facility(p["yard"])
        if f is None or yard is None or yard not in yards(yard_island, defs, config):
            continue
        per_ship = round_money(money_of(p["price"], MILLI) * deposit)
        o = {
            "buyer": f.id,
            "island": i,
            "entity": p["entity"],
            "item": p["item"],
            "payer": ledger.nonresident(i, p["entity"]) if p["island"] != i else p["entity"],
            "currency": p["currency"],
            "qty": p["qty"],
            "built": 0,
            "price": p["price"],
            "paid": 0,
            "placed": turn,
            "due": p["due"],
        }
        qty = p["qty"]
        if per_ship > 0:
            qty = min(qty, _available(world, o) // per_ship * MILLI)
        if qty <= 0:
            continue
        o["qty"] = qty
        yard.economy.setdefault(ORDERS, []).append(o)
        _collect_abroad(world, config, yard_island, yard, o, _scheduled(config, o, 0))
        f.economy[ships.ABROAD] = f.economy.get(ships.ABROAD, 0) + qty
        island.log.append(
            LogEntry(
                "ship_order_placed_abroad",
                {
                    "count": from_milli(qty),
                    "country": yard_island.name,
                    "x": yard.x,
                    "y": yard.y,
                    "turns": p["due"] - turn,
                },
            )
        )
        yard_island.log.append(
            LogEntry(
                "ship_order_received",
                {"count": from_milli(qty), "country": island.name, "x": yard.x, "y": yard.y},
            )
        )


def _abroad_dues(world: WorldState, defs: Defs, config: GameConfig, ids: list[int]) -> None:
    """海運業の次の支払いの見込み（DUE）に、圏の造船所の注文の次のターンの払いを島の通貨で足す。"""
    from hakoniwa_core.trade import fx as fxrates

    for j in ids:
        for yard in sorted(world.islands[j].facilities, key=lambda f: f.id):
            for o, due in _next_dues(defs, config, yard):
                if not is_abroad(o) or due <= 0:
                    continue
                island = world.islands.get(o["island"])
                f = island.facility(o["buyer"]) if island is not None else None
                if f is None:
                    continue
                if o["currency"] != island.id:
                    due = fxrates.to_own(island, o["currency"], due)
                f.economy[DUE] = f.economy.get(DUE, 0) + due
