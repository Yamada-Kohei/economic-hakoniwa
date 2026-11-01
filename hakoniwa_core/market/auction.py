"""板寄せ（1回の売り買いの突き合わせ）。財の市場・電力・後のフェーズの金利の市場で使い回します。

買い注文と売り注文を受け取り、基準の値段から値幅（既定 ±10%）の中で、約定する量が最も多い
値段を1つ決めます。並んだら売りと買いの差が小さい方、それでも並べば余っている側に不利な方
（売りが余れば安い方、買いが余れば高い方。板寄せのふつうの決め方）、売りと買いが同じ量なら
基準の値段に近い方です。

売りと買いがかみ合わず約定しなかったときは、一番安い売りの指値と一番高い買いの指値の真ん中
（値幅の中に収める）を「気配」として返します。市場は気配を次のターンの基準の値段にするので、
作る費用が値幅の外にある財も、1ターンに値幅ずつ値段が動いて売り買いが始まります。

配り方は「値段の有利な注文から」です。約定価格より有利な指値の注文はすべて約定し、約定価格
ちょうどの注文は割当ルールで分けます。値幅の端で止まったとき（それ以上値段が動けないのに、
片側が余っているとき）は、多い側の注文をまとめて割当ルールで分けます。割当ルールの既定は
「優先順位の高い順、同じ優先順位の中は量に比例」（hakoniwa_core/allocation.py の by_priority）。

約定価格は指値より不利にならないので、「量 × 指値」のお金を取り置いて出した注文は、
払えない約定にはなりません。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from hakoniwa_core.allocation import by_priority
from hakoniwa_core.units import BASIS

Allocate = Callable[[int, Sequence[tuple[int, int, int]]], dict[int, int]]


@dataclass(frozen=True)
class Order:
    """注文。limit が None の注文は指値なし（買いは値幅の上端、売りは下端まで受け入れる）。"""

    quantity: int
    limit: int | None = None
    priority: int = 0


@dataclass
class Result:
    price: int  # 約定価格（売買がなければ気配。注文が片側だけなら基準の値段）
    volume: int  # 約定した量
    demand: int  # 出された買い注文の量の合計
    supply: int  # 出された売り注文の量の合計
    low: int  # 値幅の下端
    high: int  # 値幅の上端
    bought: list[int] = field(default_factory=list)  # 買い注文ごとの約定量（注文と同じ並び）
    sold: list[int] = field(default_factory=list)  # 売り注文ごとの約定量

    @property
    def unfilled_demand(self) -> int:
        """買えなかった量。"""
        return self.demand - self.volume

    @property
    def unsold_supply(self) -> int:
        """売れ残った量。"""
        return self.supply - self.volume


def band(reference: int, width: int, cap: int | None = None) -> tuple[int, int]:
    """基準の値段 reference の値幅（下端, 上端）。width は1万分の1（10% は 1000）。

    cap（公共料金の上限など）があれば、上端をそこで止める。
    """
    low = max(1, reference * (BASIS - width) // BASIS)
    high = max(low, reference * (BASIS + width) // BASIS)
    if cap is not None and cap > 0:
        high = max(1, min(high, cap))
        low = min(low, high)
    return low, high


def buy_limit(order: Order, high: int) -> int:
    """買い注文が受け入れる最も高い値段。"""
    return high if order.limit is None else order.limit


def sell_limit(order: Order) -> int:
    """売り注文が受け入れる最も安い値段。"""
    return 0 if order.limit is None else order.limit


def clear(
    buys: Sequence[Order],
    sells: Sequence[Order],
    reference: int,
    width: int = 1000,
    cap: int | None = None,
    allocate: Allocate = by_priority,
    bounds: tuple[int, int] | None = None,
    allocate_buys: Allocate | None = None,
) -> Result:
    """板寄せで約定価格と注文ごとの約定量を決める。

    bounds（下端, 上端）を渡すと、値幅を割合でなくその範囲にする（金利や利回りを値段にする市場。
    例: 銀行間の金利は政策金利 ± 1%。計画経済の公定価格は下端と上端を同じにする）。
    allocate_buys を渡すと、買いが余ったときの割当ルールをそれにする（計画経済の割当の順位と配給。
    hakoniwa_core/economy/plan.py）。
    """
    low, high = bounds if bounds is not None else band(reference, width, cap)
    demand = sum(o.quantity for o in buys)
    supply = sum(o.quantity for o in sells)
    result = Result(
        price=min(max(reference, low), high),
        volume=0,
        demand=demand,
        supply=supply,
        low=low,
        high=high,
        bought=[0] * len(buys),
        sold=[0] * len(sells),
    )
    if demand <= 0 or supply <= 0:
        return result
    bids = [buy_limit(o, high) for o in buys]
    asks = [sell_limit(o) for o in sells]
    # 基準の値段も候補に入れる。売りと買いが同じ量になる値段の幅に基準の値段が入るとき、仕様どおり
    # 基準の値段で約定させる（入れないと幅の端の指値が選ばれ、安いほうに寄っていた）
    candidates = {low, high, min(max(reference, low), high)}
    candidates.update(p for p in bids if low <= p <= high)
    candidates.update(p for p in asks if low <= p <= high)
    best = None
    for p in sorted(candidates):
        d = sum(o.quantity for o, b in zip(buys, bids, strict=True) if b >= p)
        s = sum(o.quantity for o, a in zip(sells, asks, strict=True) if a <= p)
        if d > s:  # 買いが余る → 高い方
            pressure = -p
        elif s > d:  # 売りが余る → 安い方
            pressure = p
        else:
            pressure = abs(p - reference)
        key = (-min(d, s), abs(d - s), pressure, p)
        if best is None or key < best[0]:
            best = (key, p, d, s)
    assert best is not None
    _, price, d, s = best
    volume = min(d, s)
    if volume <= 0:
        result.price = min(max((min(asks) + max(bids)) // 2, low), high)
        return result
    result.price = price
    result.volume = volume
    buy_side = [
        (i, o.quantity, o.priority, bids[i]) for i, o in enumerate(buys) if bids[i] >= price
    ]
    sell_side = [
        (i, o.quantity, o.priority, -asks[i]) for i, o in enumerate(sells) if asks[i] <= price
    ]
    if d >= s:
        result.sold = _fill_all(len(sells), sell_side)
        result.bought = _fill_long(
            len(buys), buy_side, volume, price == high, allocate_buys or allocate
        )
    else:
        result.bought = _fill_all(len(buys), buy_side)
        result.sold = _fill_long(len(sells), sell_side, volume, price == low, allocate)
    return result


def _fill_all(n: int, side: list[tuple[int, int, int, int]]) -> list[int]:
    filled = [0] * n
    for i, qty, _, _ in side:
        filled[i] = qty
    return filled


def _fill_long(
    n: int,
    side: list[tuple[int, int, int, int]],
    volume: int,
    at_band_end: bool,
    allocate: Allocate,
) -> list[int]:
    """多い側に volume を配る。side の4つ目は「有利さ」（大きいほど有利な指値）。"""
    filled = [0] * n
    if at_band_end:
        got = allocate(volume, [(i, qty, priority) for i, qty, priority, _ in side])
        for i, qty in got.items():
            filled[i] = qty
        return filled
    left = volume
    for level in sorted({rank for *_, rank in side}, reverse=True):
        group = [(i, qty, priority) for i, qty, priority, rank in side if rank == level]
        total = sum(qty for _, qty, _ in group)
        if total <= left:
            for i, qty, _ in group:
                filled[i] = qty
            left -= total
        else:
            for i, qty in allocate(left, group).items():
                filled[i] = qty
            left = 0
        if left <= 0:
            break
    return filled


def match(bought: Sequence[int], sold: Sequence[int]) -> list[tuple[int, int, int]]:
    """約定量を、買い注文と売り注文の組（買いの番号, 売りの番号, 量）に分ける。

    注文の並びの順に前から突き合わせる（記録と、物とお金を相手ごとに動かすため）。
    """
    pairs = []
    buyers = [[i, q] for i, q in enumerate(bought) if q > 0]
    sellers = [[i, q] for i, q in enumerate(sold) if q > 0]
    b = s = 0
    while b < len(buyers) and s < len(sellers):
        qty = min(buyers[b][1], sellers[s][1])
        pairs.append((buyers[b][0], sellers[s][0], qty))
        buyers[b][1] -= qty
        sellers[s][1] -= qty
        if buyers[b][1] == 0:
            b += 1
        if sellers[s][1] == 0:
            s += 1
    return pairs


def match_nearest(
    bought: Sequence[int],
    sold: Sequence[int],
    distance: Callable[[int, int], int],
    allowed: Callable[[int, int], bool] | None = None,
) -> list[tuple[int, int, int]]:
    """約定量を、道のり distance(買いの番号, 売りの番号) の近い組から順に突き合わせる。

    同じ道のりの組は、買い注文、売り注文の並びの順。買いと売りの約定量の合計は同じなので、
    どちらも残らない。allowed（買いの番号, 売りの番号）が False の組は突き合わせない（禁輸。
    フェーズ3-3）。そのときは、相手のいない約定量が残ることがある。
    """
    left_b, left_s = list(bought), list(sold)
    candidates = sorted(
        (distance(b, s), b, s)
        for b in range(len(bought))
        if bought[b] > 0
        for s in range(len(sold))
        if sold[s] > 0 and (allowed is None or allowed(b, s))
    )
    pairs = []
    for _, b, s in candidates:
        qty = min(left_b[b], left_s[s])
        if qty > 0:
            pairs.append((b, s, qty))
            left_b[b] -= qty
            left_s[s] -= qty
    return pairs
