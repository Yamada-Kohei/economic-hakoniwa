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
少なくとも平均賃金の fx.min_depth 倍）の分を段に分けて買い、高い側で、持っている分を同じように売り
ます。島に商社が1つしかないと、売りと買いがそろわず両替ができないためです。輸出の稼ぎが多い島は
中央銀行が買い取って通貨が高くなり、輸入が多い島は中央銀行の持ち高が尽きると通貨が安くなります。

中央銀行の持ち高が尽きて買いしかないときは、出ている一番高い買いの指値の方へ、「相手のない量 ÷
（相手のない量 + 市場の厚み）」の割合だけ動きます（売りしかないときも同じ）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hakoniwa_core import ledger
from hakoniwa_core.economy import Payer, books, funds
from hakoniwa_core.market import auction
from hakoniwa_core.market.auction import Order
from hakoniwa_core.state import IslandState, TradeRecord, WorldState
from hakoniwa_core.trade import island_state
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.units import BASIS, to_basis

TIERS = 5  # 注文を分ける段の数


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
    """島が通貨 currency を使い始めるとき、初めのレートを置く。"""
    if not has_rate(island, currency):
        rates(island)[str(currency)] = initial_rate(island, world.islands[currency])


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


def add_buy(
    island: IslandState,
    book: Book,
    entity: str,
    payer: Payer,
    qty: int,
    reference: int,
    width: int,
    limit: int | None = None,
) -> int:
    """通貨を qty 買う注文を段に分けて足す。指値は前のレートから、値幅の上端か limit（払ってよい
    一番高いレート）の低いほうまで。払える自国通貨までに減らし、その分を取り置く。足した量を返す。"""
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

    中央銀行が受け身の注文（dealer_orders）を足してから板寄せする。"""
    reference = rate(island, currency)
    dealer_orders(island, currency, book, reference, width)
    buys = [o for o, _, _ in book.buys]
    sells = [o for o, _, _ in book.sells]
    result = auction.clear(buys, sells, reference, width)
    price = result.price
    # 片側だけのときに動かす量は、中央銀行の受け身の注文を除いた量（中央銀行の注文はレートを
    # 動かさない）
    demand = sum(o.quantity for o, entity, _ in book.buys if entity != ledger.CB)
    supply = sum(o.quantity for o, entity, _ in book.sells if entity != ledger.CB)
    if result.demand > 0 and result.supply <= 0:  # 売りがなければ、一番高い買いの指値の方へ上がる
        target = min(result.high, max(reference, max(o.limit or result.high for o in buys)))
        price = _step(reference, target, demand, book.depth)
    elif result.supply > 0 and result.demand <= 0:  # 買いがなければ、一番低い売りの指値の方へ下がる
        target = max(result.low, min(reference, min(o.limit or result.low for o in sells)))
        price = _step(reference, target, supply, book.depth)
    for payer, amount in book.held:  # 取り置きを戻してから払う
        funds.release(island, payer, amount)
    dealt = 0
    for b, s, qty in auction.match(result.bought, result.sold):
        _, buyer, buyer_payer = book.buys[b]
        _, seller, _ = book.sells[s]
        own = qty * price // BASIS
        if own <= 0:
            continue
        if buyer == ledger.CB:  # 中央銀行が買う: 自国通貨を作って払う
            ledger.pay(island, ledger.CB, seller, own, "fx_exchange")
            dealt += qty
        else:
            funds.pay(island, buyer_payer, seller, own, "fx_exchange")
            if seller == ledger.CB:
                dealt -= qty
        cost = fxmoney.transfer(
            world, currency, (island.id, seller), (island.id, buyer), qty, "fx_exchange", own
        )
        _realized(island, seller, own - cost)
        island.trades.append(TradeRecord(f"fx:{currency}", buyer, seller, qty, own))
    rates(island)[str(currency)] = price
    stats = island.stats
    stats[f"fx.dealt.{currency}"] = stats.get(f"fx.dealt.{currency}", 0) + dealt
    return price, result.volume, result.demand, result.supply


def dealer_orders(
    island: IslandState, currency: int, book: Book, reference: int, width: int
) -> None:
    """中央銀行の受け身の注文（フェーズ3-1）。前のレートより安い側に、市場の厚みの分を TIERS 段に
    分けて買い、前のレートより高い側に、持っている通貨 currency を同じだけ売る（持っている分まで）。
    レートが値幅の端まで動くと厚みの分だけ引き受けるので、ふだんの取引に比べて少しの偏りなら少し
    だけ動き、輸出の稼ぎを島で両替できる（買った分は中央銀行の外貨の持ち高になり、自国通貨が
    増える。売った分は自国通貨が減る）。"""
    depth = book.depth
    if depth <= 0:
        return
    low, high = auction.band(reference, width)
    payer = Payer(ledger.CB)
    have = fxmoney.holding(island, ledger.CB, currency)
    for j in range(1, TIERS + 1):
        qty = depth // TIERS
        if qty <= 0:
            break
        book.buys.append((Order(qty, reference - (reference - low) * j // TIERS), ledger.CB, payer))
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


def depth(island: IslandState, currency: int, config) -> int:
    """市場の厚み: 島の主体が通貨 currency で払い・受け取った額の、直近 fx.depth_turns ターンの
    平均（圏の市場と契約の代金）。少なくとも平均賃金の fx.min_depth 倍（通貨 currency に直す）。"""
    history = island_state(island).get("turnover", {}).get(str(currency), [])
    average = sum(history) // len(history) if history else 0
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    return max(average, to_foreign(island, currency, wage * config.get("fx.min_depth")))


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
    """施設が外貨を売った損益を、施設の帳簿に足す（為替の実現損益）。"""
    if gain and entity.startswith("fac:"):
        books.add(island, int(entity[4:]), books.FX_REALIZED, gain)


def band_width(config) -> int:
    return to_basis(config.get("fx.band"))
