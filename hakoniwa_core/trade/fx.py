"""為替（フェーズ3-1。変動相場）。

レート e は「通貨 C の 1 単位が自国通貨でいくらか」で、1万分の1 の整数（units.BASIS）で持ちます
（``island.ext["trade"]["rates"]``: 通貨の番号の文字列 → e）。自国通貨は BASIS です。初めの e は、
両方の島の平均賃金の比です（どの島も賃金 1,000 から始まるので、建国したばかりなら約 1）。

為替の市場は「自国通貨と、その国が使う決済通貨」の組ごとで、注文を出すのはその島の主体（商社と政府）
です。買い手は自国通貨で払って C を受け取り、売り手は C を渡して自国通貨を受け取ります。自国の帳簿で
はふつうの支払い、C の島の帳簿では非居住者の預金の付け替えになります（money.transfer）。

板寄せは財の市場と同じ clear で、値幅は fx.band（1ターン ±2%）です。注文は量を5つの段に分け、買いは
「前のレート」から「値幅の上端と、輸入が引き合う一番高いレートの低いほう」まで、売りは「前のレート」から
「値幅の下端と、輸出が引き合う一番低いレートの高いほう」までの指値にします。

中央銀行は受け身の注文を出します（dealer_orders。3-1 で足した。介入と固定は 3-3）。前のレートより
安い側で、市場の厚み（その島の主体がこの通貨で払い・受け取った額の直近 fx.depth_turns ターンの平均。
少なくとも平均賃金の fx.min_depth 倍と、このターンに効く契約で払い・受け取る額）の分を段に分けて
買い、高い側で、持っている分を同じように売ります。島に商社が1つしかないと、売りと買いがそろわず
両替ができないためです。輸出の稼ぎが多い島は中央銀行が買い取って通貨が高くなり、輸入が多い島は
中央銀行の持ち高と借りられる額（下の融通）が尽きると通貨が
安くなります。

中央銀行は、受け身の売りに要る通貨 C が手元に足りなければ、C を借ります（中央銀行どうしの融通。
swap.py。3-3 の続き）。同じ圏で C が余っている加盟国の中央銀行から先に、足りなければ C を発行した
島の中央銀行から借り、借りられる額には上限があります（Book.borrow_room と Book.borrow）。3-3 までは
C を発行した島の中央銀行が外国の板で C を作って売り（issuer_orders）、赤字の国に売った分と黒字の国の
中央銀行が買った分が両側にたまり続けたので、やめました。

受け身の買いで持つ外国の資産には上限があります（フェーズ3-3）: 中央銀行の外国の資産（外貨と外国の
国債と融通で貸している外貨。abroad.foreign_assets）から借りている額を引いた正味が、年の GDP の
fx.reserve_share に届くと、受け身の買いを出しません（abroad.reserve_room。Book.reserve_room）。
それより多く持ちたい国は、介入の命令で買います。

同じ通貨の組のレートは1つです: 島 A の板で決まった通貨 B のレートの逆数を、島 B の通貨 A のレート
にも入れます（3-3 の続き。前は、ほとんど売り買いのない側の板のレートが初めのまま残っていた）。

片側だけで買いしかないとき（中央銀行の注文も相手にならないほど偏ったとき）は、出ている一番
高い買いの指値の方へ、「相手のない量 ÷（相手のない量 + 市場の厚み）」の割合だけ動きます（売りしか
ないときも同じ）。

介入と固定相場（フェーズ3-3。intervention_orders）: 中央銀行は命令で「毎ターン通貨 C をいくら買う・
売る」（policy.fx_intervention）を決められ、買いは値幅の上端、売りは下端の指値で出します（持っている分
まで）。固定相場（policy.fx_peg）では、決めたレートの ±fx.peg_band で、下の端に量の限りのない
買い（自国通貨を作って払う）と、上の端に持っている C の全部の売りを出します。決めたレートが値幅の
外なら、寄せる向きの注文だけを値幅の端に出すので、1ターンに値幅ずつ寄っていきます。介入と固定の
注文はレートを動かす量に数え、出している通貨では受け身の注文（dealer_orders）を出しません。中央
銀行が買うときの代金は仕訳の種類 fx_intervention で自国通貨を作ります。通貨安の圧力で、持っている
C を全部売っても買いが残るか、レートが上の端を超えたターンに固定が外れ（準備が尽きた）、ログで
知らせて変動相場に戻ります。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from hakoniwa_core import ledger
from hakoniwa_core.economy import Payer, books, funds
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.state import IslandState, LogEntry, TradeRecord, WorldState
from hakoniwa_core.trade import currency_label, island_state
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.units import BASIS, to_basis

TIERS = 5  # 注文を分ける段の数
INTERVENE = (
    "cb_fx"  # 板の上での、この島の中央銀行の介入と固定相場の注文（レートを動かす量に数える）
)
_DEALERS = (ledger.CB,)  # 受け身の注文を出す主体（レートを動かす量に数えない）
UNLIMITED = 10**15  # 固定相場の買いの量（自国通貨を作って払うので限りがない）


def rates(island: IslandState) -> dict[str, int]:
    """島のレート（通貨の番号の文字列 → 1 単位が自国通貨でいくらか × BASIS）。"""
    return island_state(island).setdefault("rates", {})


def rate(island: IslandState, currency: int) -> int:
    if currency == island.id:
        return BASIS
    return rates(island).get(str(currency), BASIS)


def has_rate(island: IslandState, currency: int) -> bool:
    return currency == island.id or str(currency) in rates(island)


def initial_rate(island: IslandState, other: IslandState) -> int:
    """初めのレート: 平均賃金の比（自国の賃金 ÷ 相手の賃金）。"""
    mine = island.economy.average_wage or 1
    theirs = other.economy.average_wage or 1
    return max(1, mine * BASIS // theirs)


def ensure(world: WorldState, island: IslandState, currency: int) -> None:
    """島が通貨 currency を使い始めるとき、初めのレートを置く（相手の島にこの島の通貨のレートが
    あれば、その逆数。同じ組のレートは1つ）。"""
    if has_rate(island, currency):
        return
    other = world.islands[currency]
    if has_rate(other, island.id):
        rates(island)[str(currency)] = inverse(rate(other, island.id))
    else:
        rates(island)[str(currency)] = initial_rate(island, other)


def inverse(e: int) -> int:
    """レートの逆数（相手の島から見たレート）。"""
    return max(1, BASIS * BASIS // max(1, e))


def to_own(island: IslandState, currency: int, amount: int) -> int:
    """通貨 currency の amount を、自国通貨に直す（切り捨て）。"""
    return amount * rate(island, currency) // BASIS


def to_foreign(island: IslandState, currency: int, own: int) -> int:
    """自国通貨 own を、通貨 currency に直す（切り捨て）。"""
    return own * BASIS // max(1, rate(island, currency))


def cross(world: WorldState, island: IslandState, other: int) -> int | None:
    """画面向け: 通貨 other の 1 単位が島 island の通貨でいくらか（× BASIS）。組がなければ、
    両方が使っている共通の通貨を通して計算する。どちらもなければ None。"""
    if has_rate(island, other):
        return rate(island, other)
    there = world.islands.get(other)
    if there is None:
        return None
    for key in sorted(rates(island)):
        common = int(key)
        if has_rate(there, common):
            return rate(island, common) * BASIS // max(1, rate(there, common))
    if has_rate(there, island.id):  # 相手が自分の通貨を使っている
        return BASIS * BASIS // max(1, rate(there, island.id))
    return None


@dataclass
class Book:
    """1つの組（島の通貨と通貨 currency）の注文。量は currency の額。"""

    buys: list[tuple[Order, str, Payer]] = field(default_factory=list)
    sells: list[tuple[Order, str, Payer]] = field(default_factory=list)
    held: list[tuple[Payer, int]] = field(default_factory=list)  # 買い注文に取り置いた自国通貨
    depth: int = 0  # 市場の厚み（島の主体がこの通貨で払い・受け取った額の、直近の平均）
    # 受け身の買いで、まだ持てる外国の資産（この通貨の額。None は上限なし）
    reserve_room: int | None = None
    # 受け身の売りに、中央銀行が借りて足せる量（この通貨の額）と、借りる関数（swap.draw。借りた量を
    # 返す）
    borrow_room: int = 0
    borrow: Callable[[int], int] | None = None
    peg_band: int = 100  # 固定相場の幅（1万分の1。fx.peg_band）
    intervening: bool = False  # 中央銀行が介入している（受け身の注文を出さない）


def add_buy(
    island: IslandState,
    book: Book,
    entity: str,
    payer: Payer,
    qty: int,
    reference: int,
    width: int,
    limit: int | None = None,
    due: bool = False,
) -> int:
    """通貨を qty 買う注文を段に分けて足す。指値は前のレートから、値幅の上端か limit（払ってよい
    一番高いレート）の低いほうまで。払える自国通貨までに減らし、その分を取り置く。足した量を返す。

    due はこのターンに払う代金（政府の契約）の分: 段に分けず、値幅の上端の1つの指値にして、ほかの
    買いより先に配る（段に分けると、低い段の指値が売りの段に届かず、厚みの分があっても6割ほどしか
    買えなかった）。"""
    if qty <= 0:
        return 0
    high = auction.band(reference, width)[1]
    top = high if limit is None else min(high, limit)
    if top <= 0:
        return 0
    money = funds.available(island, payer)
    qty = min(qty, money * BASIS // max(1, top))
    if qty <= 0:
        return 0
    hold = -(-qty * top // BASIS)
    funds.commit(island, payer, hold)
    book.held.append((payer, hold))
    if due:
        book.buys.append((Order(qty, top, priority=1), entity, payer))
        return qty
    if top <= reference:
        book.buys.append((Order(qty, top), entity, payer))
        return qty
    for j, part_qty in _split(qty):
        price = reference + (top - reference) * j // (TIERS - 1)
        book.buys.append((Order(part_qty, price), entity, payer))
    return qty


def add_sell(
    book: Book,
    entity: str,
    payer: Payer,
    qty: int,
    reference: int,
    width: int,
    floor: int | None = None,
) -> int:
    """通貨を qty 売る注文を段に分けて足す（qty は持ち高までにしておくこと）。指値は前のレートから、
    値幅の下端か floor（受け取ってよい一番低いレート）の高いほうまで。"""
    if qty <= 0:
        return 0
    low = auction.band(reference, width)[0]
    bottom = low if floor is None else max(low, floor)
    if bottom >= reference:
        book.sells.append((Order(qty, bottom), entity, payer))
        return qty
    for j, part_qty in _split(qty):
        price = reference - (reference - bottom) * j // (TIERS - 1)
        book.sells.append((Order(part_qty, price), entity, payer))
    return qty


def _split(qty: int) -> list[tuple[int, int]]:
    base = qty // TIERS
    out = []
    for j in range(TIERS):
        amount = base + (qty - base * TIERS if j == TIERS - 1 else 0)
        if amount > 0:
            out.append((j, amount))
    return out


def clear(
    world: WorldState, island: IslandState, currency: int, book: Book, width: int
) -> tuple[int, int, int, int]:
    """組の板寄せをして、約定を払う。(新しいレート, 約定した量, 買い, 売り) を返す。

    島の中央銀行が受け身の注文（dealer_orders）を足してから板寄せする。受け身の売りで、持っている
    通貨が足りない分は、払う前に借りる（book.borrow。中央銀行どうしの融通）。決まったレートの逆数を
    相手の島のレートにも入れる。"""
    reference = rate(island, currency)
    peg = intervention_orders(island, currency, book, reference, width)
    if peg is None:
        dealer_orders(island, currency, book, reference, width)
    buys = [o for o, _, _ in book.buys]
    sells = [o for o, _, _ in book.sells]
    result = auction.clear(buys, sells, reference, width)
    price = result.price
    # 片側だけのときに動かす量は、中央銀行の受け身の注文を除いた量（中央銀行の注文はレートを
    # 動かさない）
    demand = sum(o.quantity for o, entity, _ in book.buys if entity not in _DEALERS)
    supply = sum(o.quantity for o, entity, _ in book.sells if entity not in _DEALERS)
    if result.demand > 0 and result.supply <= 0:  # 売りがなければ、一番高い買いの指値の方へ上がる
        target = min(result.high, max(reference, max(o.limit or result.high for o in buys)))
        price = _step(reference, target, demand, book.depth)
    elif result.supply > 0 and result.demand <= 0:  # 買いがなければ、一番低い売りの指値の方へ下がる
        target = max(result.low, min(reference, min(o.limit or result.low for o in sells)))
        price = _step(reference, target, supply, book.depth)
    for payer, amount in book.held:  # 取り置きを戻してから払う
        funds.release(island, payer, amount)
    matches = list(auction.match(result.bought, result.sold))
    _borrow_for_sells(island, currency, book, matches, price)
    dealt = 0
    intervened = 0  # 介入と固定相場で中央銀行が買った量（売りはマイナス）
    for b, s, qty in matches:
        _, buyer, buyer_payer = book.buys[b]
        _, seller, _ = book.sells[s]
        if seller == ledger.CB:  # 借りられなかった分は売らない（ふつうは起きない）
            qty = min(qty, fxmoney.holding(island, ledger.CB, currency))
        own = qty * price // BASIS
        if own <= 0 or buyer == seller:
            continue
        kind = "fx_intervention" if INTERVENE in (buyer, seller) else "fx_exchange"
        if buyer == INTERVENE:
            intervened += qty
        if seller == INTERVENE:
            intervened -= qty
        # 介入と固定相場の注文も、お金と外貨の持ち手は中央銀行
        buyer = ledger.CB if buyer == INTERVENE else buyer
        seller = ledger.CB if seller == INTERVENE else seller
        values = {island.id: own, currency: qty}  # 国際収支に入れる値（約定の値段で）
        if buyer == ledger.CB:  # 中央銀行が買う: 自国通貨を作って払う
            ledger.pay(island, ledger.CB, seller, own, kind)
            if kind == "fx_exchange":
                dealt += qty
        else:
            funds.pay(island, buyer_payer, seller, own, kind)
            if seller == ledger.CB and kind == "fx_exchange":
                dealt -= qty
        cost = fxmoney.transfer(
            world, currency, (island.id, seller), (island.id, buyer), qty, kind, own, values=values
        )
        _realized(island, seller, own - cost)
        island.trades.append(TradeRecord(f"fx:{currency}", buyer, seller, qty, own))
    rates(island)[str(currency)] = price
    other = world.islands.get(currency)
    if other is not None and has_rate(other, island.id):  # 同じ組のレートは1つ
        rates(other)[str(island.id)] = inverse(price)
    stats = island.stats
    stats[f"fx.dealt.{currency}"] = stats.get(f"fx.dealt.{currency}", 0) + dealt
    if intervened or currency_key(currency) in island.policy.fx_intervention or peg is not None:
        key = f"fx.intervened.{currency}"
        stats[key] = stats.get(key, 0) + intervened
    if peg is not None:
        _check_peg(world, island, currency, book, result, peg)
    return price, result.volume, result.demand, result.supply


def _borrow_for_sells(
    island: IslandState, currency: int, book: Book, matches: list[tuple[int, int, int]], price: int
) -> None:
    """受け身の売りの約定のうち、中央銀行が持っている通貨で足りない分を借りる（book.borrow）。"""
    if book.borrow is None:
        return
    sold = sum(
        qty for _, s, qty in matches if book.sells[s][1] == ledger.CB and qty * price // BASIS > 0
    )
    short = sold - fxmoney.holding(island, ledger.CB, currency)
    if short > 0:
        book.borrow(short)


def currency_key(currency: int) -> str:
    return str(currency)


def peg_band(config) -> int:
    return to_basis(config.get("fx.peg_band"))


@dataclass
class Peg:
    """このターンの固定相場の注文（固定が外れたかを確かめるため）。"""

    rate: int
    # 上の端の売り注文の番号（book.sells の中。持っている外貨がなければ None）
    sell_index: int | None
    upper: int  # 上の端の指値
    defends: bool  # 上の端を守っている（売って下げる側にいる）か


def intervention_orders(
    island: IslandState, currency: int, book: Book, reference: int, width: int
) -> Peg | None:
    """中央銀行の介入（policy.fx_intervention）と固定相場（policy.fx_peg）の注文を足す。固定相場なら
    その注文の様子を返す。介入も固定もない通貨は何もしない（受け身の注文は呼ぶ側が出す）。

    介入か固定のある通貨では、受け身の注文を出さない（自分どうしで売り買いしないように）。そのため、
    介入の額が 0 の通貨は、ここで何もせず None を返す。"""
    policy = island.policy
    key = currency_key(currency)
    low, high = auction.band(reference, width)
    payer = Payer(ledger.CB)
    have = fxmoney.holding(island, ledger.CB, currency)
    pegged = policy.fx_peg.get(key)
    if pegged:
        spread = book.peg_band
        bottom = pegged * (BASIS - spread) // BASIS
        top = pegged * (BASIS + spread) // BASIS
        lower = min(high, max(low, bottom))
        upper = max(low, min(high, top))
        # 決めたレートが値幅の外なら、寄せる向きの注文だけを出す（両方を値幅の同じ端に出すと、
        # 自分の買いと売りが互いに当たり、外貨を売っても相手に届かないため）
        if top > low:
            book.buys.append((Order(UNLIMITED, lower), INTERVENE, payer))
        sell_index = None
        defends = bottom < high  # 決めたレートが値幅より上なら、上の端は守らない（上げる向き）
        if have > 0 and defends:
            sell_index = len(book.sells)
            book.sells.append((Order(have, upper), INTERVENE, payer))
        return Peg(pegged, sell_index, upper, defends)
    amount = policy.fx_intervention.get(key, 0)
    if amount > 0:
        book.buys.append((Order(amount, high), INTERVENE, payer))
    elif amount < 0 and have > 0:
        book.sells.append((Order(min(-amount, have), low), INTERVENE, payer))
    if amount:
        book.intervening = True
    return None


def _check_peg(
    world: WorldState, island: IslandState, currency: int, book: Book, result, peg: Peg
) -> None:
    """固定相場の確かめ: 持っている外貨を全部売っても、上の端より高い買いが残ったか、レートが上の端
    を超えた（準備が尽きた）なら、固定を外して変動相場に戻し、ログで知らせる。"""
    if not peg.defends or fxmoney.holding(island, ledger.CB, currency) > 0:
        return
    sold_all = peg.sell_index is None or result.sold[peg.sell_index] >= (
        book.sells[peg.sell_index][0].quantity
    )
    unfilled = sum(
        o.quantity - got
        for (o, entity, _), got in zip(book.buys, result.bought, strict=True)
        if entity != INTERVENE and (o.limit is None or o.limit >= peg.upper)
    )
    if sold_all and (unfilled > 0 or rate(island, currency) > peg.upper):
        island.policy.fx_peg.pop(currency_key(currency), None)
        island.stats["fx.peg_broken"] = island.stats.get("fx.peg_broken", 0) + 1
        args = {"currency": currency_label(world, currency), "rate": peg.rate / BASIS}
        island.log.append(LogEntry("fx_peg_broken", args))


def dealer_orders(
    island: IslandState, currency: int, book: Book, reference: int, width: int
) -> None:
    """中央銀行の受け身の注文（フェーズ3-1）。前のレートより安い側に、市場の厚みの分を TIERS 段に
    分けて買い、前のレートより高い側に、通貨 currency を同じだけ売る（持っている分と、借りられる
    分 book.borrow_room まで）。
    レートが値幅の端まで動くと厚みの分だけ引き受けるので、ふだんの取引に比べて少しの偏りなら少し
    だけ動き、輸出の稼ぎを島で両替できる（買った分は中央銀行の外貨の持ち高になり、自国通貨が
    増える。売った分は自国通貨が減る）。"""
    depth = book.depth
    if depth <= 0 or book.intervening:
        return
    low, high = auction.band(reference, width)
    payer = Payer(ledger.CB)
    have = fxmoney.holding(island, ledger.CB, currency) + book.borrow_room  # 借りて売れる分も
    room = book.reserve_room  # 上限まで買える量（None は上限なし）
    for j in range(1, TIERS + 1):
        # 段に分けた余り（TIERS 未満）は、いちばん遠い段に足す（余りを出さずにいると、厚みちょうどの
        # 額を両替する契約の支払いが数円足りず、0.001 トン届かなかった。Codex の試し遊び）
        qty = depth // TIERS + (depth % TIERS if j == TIERS else 0)
        if qty <= 0:
            break
        buy = qty if room is None else max(0, min(qty, room))
        if buy > 0:
            price = reference - (reference - low) * j // TIERS
            book.buys.append((Order(buy, price), ledger.CB, payer))
            if room is not None:
                room -= buy
        sell = min(qty, have)
        if sell > 0:
            book.sells.append(
                (Order(sell, reference + (high - reference) * j // TIERS), ledger.CB, payer)
            )
            have -= sell


def _step(reference: int, target: int, unmatched: int, depth: int) -> int:
    """片側だけの注文で動く先: 指値の方へ、相手のない量 ÷（相手のない量 + 市場の厚み）の割合だけ
    動く（ふだんの取引の額に比べて少しの偏りなら、少しだけ動く）。"""
    if depth <= 0:
        return target
    return reference + (target - reference) * unmatched // (unmatched + depth)


def depth(island: IslandState, currency: int, config, scheduled: int = 0) -> int:
    """市場の厚み: 島の主体が通貨 currency で払い・受け取った額の、直近 fx.depth_turns ターンの
    平均（圏の市場と契約の代金）。少なくとも平均賃金の fx.min_depth 倍（通貨 currency に直す）と、
    このターンに効く契約で払い・受け取る額 scheduled（2026-10-15 セビーリャさんの決定「上限を
    上げる」。前は厚みが1ターンに払える額の上限になり、それを超える外貨建ての契約は毎ターン
    2割ほど届かず、払えた額しか平均に入らないので厚みも増えなかった）。"""
    history = island_state(island).get("turnover", {}).get(str(currency), [])
    average = sum(history) // len(history) if history else 0
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    floor = to_foreign(island, currency, wage * config.get("fx.min_depth"))
    return max(average, floor, scheduled)


def turnover(island: IslandState, currency: int) -> int:
    """島の主体が通貨 currency で払い・受け取った額の、記録のあるターンの平均（下限なし）。"""
    history = island_state(island).get("turnover", {}).get(str(currency), [])
    return sum(history) // len(history) if history else 0


def keep_turnover(island: IslandState, config, turnover: dict[int, int]) -> None:
    """このターンに払い・受け取った額を、通貨ごとの記録に足す（直近 fx.depth_turns ターン）。"""
    keep = config.get("fx.depth_turns")
    record = island_state(island).setdefault("turnover", {})
    for key in sorted(set(record) | {str(c) for c in turnover}):
        amount = turnover.get(int(key), 0)
        record[key] = [*record.get(key, []), amount][-keep:]
        if not any(record[key]):
            del record[key]


def _realized(island: IslandState, entity: str, gain: int) -> None:
    """施設と市中銀行（フェーズ3-3）が外貨を売った損益を、帳簿に足す（為替の実現損益）。"""
    if gain and entity.startswith("fac:"):
        books.add(island, int(entity[4:]), books.FX_REALIZED, gain)
    elif gain and ledger.is_bank(entity):
        books.add(island, int(entity.split(":")[1]), books.FX_REALIZED, gain)


def band_width(config) -> int:
    return to_basis(config.get("fx.band"))
