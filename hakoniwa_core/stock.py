"""在庫（国の倉庫と施設の在庫）の出し入れ。量はすべて 1000 分の 1 単位の整数です。

在庫の財は「作られたターン」ごとの組で持ち、古いものから使います。
持ち越せる期間（goods.yaml の shelf_life）を過ぎた組は、傷んで捨てられます。
国の倉庫（フェーズ1の島の共同倉庫）も施設の在庫も、同じ形（hakoniwa_core.state.Stock）です。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hakoniwa_core.state import Stock

Warehouse = Stock  # フェーズ1からの呼び名

# 持ち越せる期間が無期限の財は、作られたターンを区別せずこの番号の組1つにまとめる
_NO_EXPIRY = 0


def total(stock: Stock, good: str) -> int:
    return sum(qty for _, qty in stock.get(good, ()))


def totals(stock: Stock) -> dict[str, int]:
    return {good: total(stock, good) for good in sorted(stock) if total(stock, good)}


def add(
    stock: Stock,
    good: str,
    qty: int,
    turn: int,
    goods_defs: Mapping[str, Mapping[str, Any]],
) -> None:
    if qty <= 0:
        return
    made = turn if "shelf_life" in goods_defs[good] else _NO_EXPIRY
    _put(stock, good, made, qty)


def _put(stock: Stock, good: str, made: int, qty: int) -> None:
    batches = stock.setdefault(good, [])
    for batch in batches:
        if batch[0] == made:
            batch[1] += qty
            return
    batches.append([made, qty])
    batches.sort(key=lambda b: b[0])


def take(stock: Stock, good: str, qty: int) -> int:
    """古い組から qty だけ取り出す。足りなければあるだけ。取り出した量を返す。"""
    return sum(q for _, q in take_batches(stock, good, qty))


def take_batches(stock: Stock, good: str, qty: int) -> list[list[int]]:
    """古い組から qty だけ取り出し、取り出した組（[作られたターン, 量]）を返す。

    市場で売り手から買い手へ移すときのように、作られたターンを保ったまま移すのに使う。
    """
    batches = stock.get(good)
    if not batches or qty <= 0:
        return []
    taken: list[list[int]] = []
    left = qty
    while batches and left > 0:
        use = min(batches[0][1], left)
        taken.append([batches[0][0], use])
        batches[0][1] -= use
        left -= use
        if batches[0][1] == 0:
            batches.pop(0)
    if not batches:
        del stock[good]
    return taken


def put_batches(stock: Stock, good: str, batches: list[list[int]]) -> None:
    """take_batches で取り出した組を入れる。"""
    for made, qty in batches:
        if qty > 0:
            _put(stock, good, made, qty)


def expiring(
    stock: Stock, good: str, turn: int, goods_defs: Mapping[str, Mapping[str, Any]]
) -> int:
    """ターン turn の終わりに捨てられる量（持ち越せる期間が残り1ターンの組）。"""
    life = goods_defs.get(good, {}).get("shelf_life")
    if life is None:
        return 0
    return sum(qty for made, qty in stock.get(good, ()) if turn - made >= life)


def spoil(stock: Stock, turn: int, goods_defs: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """このターンの終わりに、持ち越せる期間を過ぎた組を捨てる。捨てた量を財ごとに返す。

    shelf_life が L の財は、作られたターン t の後 L 回まで次のターンに持ち越せ、
    ターン t + L の終わりに捨てられる（L = 0 は作ったターンのうちに使い切る）。
    """
    spoiled: dict[str, int] = {}
    for good in sorted(stock):
        life = goods_defs.get(good, {}).get("shelf_life")
        if life is None:
            continue
        keep = []
        for made, qty in stock[good]:
            if turn - made >= life:
                spoiled[good] = spoiled.get(good, 0) + qty
            else:
                keep.append([made, qty])
        if keep:
            stock[good] = keep
        else:
            del stock[good]
    return spoiled


def move_all(source: Stock, target: Stock) -> dict[str, int]:
    """source の在庫をすべて target に移す（作られたターンは保つ）。移した量を財ごとに返す。"""
    moved: dict[str, int] = {}
    for good in sorted(source):
        batches = source[good]
        moved[good] = sum(q for _, q in batches)
        put_batches(target, good, batches)
    source.clear()
    return moved


def move_to_warehouse(
    stock: Stock,
    warehouse: Stock,
    turn: int,
    goods_defs: Mapping[str, Mapping[str, Any]],
) -> dict[str, int]:
    """施設の在庫をすべて倉庫に戻す（取り壊しや工事の余りのとき）。"""
    return move_all(stock, warehouse)
