"""島の共同倉庫と施設の在庫の出し入れ。量はすべて 1000 分の 1 単位の整数です。

倉庫の財は「作られたターン」ごとの組で持ち、古いものから使います。
持ち越せる期間（goods.yaml の shelf_life）を過ぎた組は、傷んで捨てられます。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

Warehouse = dict[str, list[list[int]]]

# 持ち越せる期間が無期限の財は、作られたターンを区別せずこの番号の組1つにまとめる
_NO_EXPIRY = 0


def total(warehouse: Warehouse, good: str) -> int:
    return sum(qty for _, qty in warehouse.get(good, ()))


def totals(warehouse: Warehouse) -> dict[str, int]:
    return {good: total(warehouse, good) for good in sorted(warehouse) if total(warehouse, good)}


def add(
    warehouse: Warehouse,
    good: str,
    qty: int,
    turn: int,
    goods_defs: Mapping[str, Mapping[str, Any]],
) -> None:
    if qty <= 0:
        return
    made = turn if "shelf_life" in goods_defs[good] else _NO_EXPIRY
    batches = warehouse.setdefault(good, [])
    for batch in batches:
        if batch[0] == made:
            batch[1] += qty
            return
    batches.append([made, qty])
    batches.sort(key=lambda b: b[0])


def take(warehouse: Warehouse, good: str, qty: int) -> int:
    """古い組から qty だけ取り出す。足りなければあるだけ。取り出した量を返す。"""
    batches = warehouse.get(good)
    if not batches or qty <= 0:
        return 0
    taken = 0
    while batches and taken < qty:
        use = min(batches[0][1], qty - taken)
        batches[0][1] -= use
        taken += use
        if batches[0][1] == 0:
            batches.pop(0)
    if not batches:
        del warehouse[good]
    return taken


def spoil(
    warehouse: Warehouse, turn: int, goods_defs: Mapping[str, Mapping[str, Any]]
) -> dict[str, int]:
    """このターンの終わりに、持ち越せる期間を過ぎた組を捨てる。捨てた量を財ごとに返す。

    shelf_life が L の財は、作られたターン t の後 L 回まで次のターンに持ち越せ、
    ターン t + L の終わりに捨てられる（L = 0 は作ったターンのうちに使い切る）。
    """
    spoiled: dict[str, int] = {}
    for good in sorted(warehouse):
        life = goods_defs.get(good, {}).get("shelf_life")
        if life is None:
            continue
        keep = []
        for made, qty in warehouse[good]:
            if turn - made >= life:
                spoiled[good] = spoiled.get(good, 0) + qty
            else:
                keep.append([made, qty])
        if keep:
            warehouse[good] = keep
        else:
            del warehouse[good]
    return spoiled


def move_to_warehouse(
    stock: dict[str, int],
    warehouse: Warehouse,
    turn: int,
    goods_defs: Mapping[str, Mapping[str, Any]],
) -> None:
    """施設の在庫をすべて倉庫に戻す（取り壊しや工事の余りのとき）。"""
    for good in sorted(stock):
        add(warehouse, good, stock[good], turn, goods_defs)
    stock.clear()


def stock_take(stock: dict[str, int], good: str, qty: int) -> int:
    have = stock.get(good, 0)
    use = min(have, max(0, qty))
    if use:
        if have - use:
            stock[good] = have - use
        else:
            del stock[good]
    return use


def stock_add(stock: dict[str, int], good: str, qty: int) -> None:
    if qty > 0:
        stock[good] = stock.get(good, 0) + qty
