"""火力発電所の燃料の取得原価と、このターンの受け取り・使用量（FM-63・FM-64）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import Facility
from hakoniwa_core.units import money_of, unit_price

BOOK = "fuel_book"  # f.economy: 財 → {quantity（1000分の1単位）, value（運賃を除く取得原価）}
FLOW = "fuel_flow"  # f.economy: turn, acquired・used（財ごとの量）, value（原価の増減）
FUELS = frozenset(("coal", "fuel"))


def fuel_goods(defs: Defs, f: Facility, all_recipes: bool = False) -> set[str]:
    """発電所の作り方で使う燃料。作り方を戻すまで持っておく燃料も取得原価を残す。"""
    definition = defs.facilities[f.kind]
    if definition.get("category") != "power":
        return set()
    recipes = definition["recipes"].values() if all_recipes else (fac.recipe_of(defs, f),)
    return {g for recipe in recipes for g, q in recipe["inputs"].items() if q > 0 and g in FUELS}


def is_thermal(defs: Defs, f: Facility) -> bool:
    return bool(fuel_goods(defs, f, all_recipes=True))


def ensure_book(defs: Defs, f: Facility, prices: Mapping[str, int]) -> dict[str, dict[str, int]]:
    """帳簿のないセーブは、移し替えの時点の在庫と値段で始める。"""
    fuels = fuel_goods(defs, f, all_recipes=True)
    if not fuels:
        return {}
    book = f.economy.setdefault(BOOK, {})
    for good in sorted(fuels):
        if good not in book:
            quantity = stock.total(f.stock, good)
            book[good] = {"quantity": quantity, "value": money_of(prices.get(good, 0), quantity)}
    return book


def reset_book(defs: Defs, f: Facility, prices: Mapping[str, int]) -> None:
    """所有権が移るときは、引き継ぐ在庫の現価で取得原価を置き直す。"""
    if not is_thermal(defs, f):
        return
    f.economy.pop(BOOK, None)
    ensure_book(defs, f, prices)


def _flow(f: Facility, turn: int) -> dict[str, Any]:
    flow = f.economy.get(FLOW)
    if flow is None or flow.get("turn") != turn:
        flow = {"turn": turn, "acquired": {}, "used": {}, "value": {}}
        f.economy[FLOW] = flow
    return flow


def _reconcile_good(f: Facility, good: str, price: int, turn: int) -> dict[str, int]:
    """在庫の損失など、通常の受け取り・燃焼以外の増減を原価帳簿に合わせる。"""
    book = f.economy.setdefault(BOOK, {})
    quantity = stock.total(f.stock, good)
    if good not in book:
        book[good] = {"quantity": quantity, "value": money_of(price, quantity)}
    item = book[good]
    change = quantity - item["quantity"]
    if change:
        value = (
            money_of(price, change)
            if change > 0
            else -(item["value"] * -change // item["quantity"])
        )
        item["quantity"] = quantity
        item["value"] += value
        flow = _flow(f, turn)
        flow["value"][good] = flow["value"].get(good, 0) + value
    return item


def reconcile_book(defs: Defs, f: Facility, prices: Mapping[str, int], turn: int) -> None:
    """戦災や徴発で失った燃料は平均原価で減らす。燃焼量・正規の受け取りには数えない。"""
    for good in sorted(fuel_goods(defs, f, all_recipes=True)):
        _reconcile_good(f, good, prices.get(good, 0), turn)


def acquire(defs: Defs, f: Facility, good: str, qty: int, price: int, turn: int) -> None:
    """在庫へ入れる前に、市場の約定価格か倉庫へ払った値段で足す（運賃を含めない）。"""
    if qty <= 0 or good not in fuel_goods(defs, f, all_recipes=True):
        return
    item = _reconcile_good(f, good, price, turn)
    value = money_of(price, qty)
    item["quantity"] += qty
    item["value"] += value
    flow = _flow(f, turn)
    flow["acquired"][good] = flow["acquired"].get(good, 0) + qty
    flow["value"][good] = flow["value"].get(good, 0) + value


def consume(defs: Defs, f: Facility, good: str, qty: int, turn: int) -> int:
    """在庫から取り出す前に平均取得原価で減らし、使った額を返す。"""
    if qty <= 0 or good not in fuel_goods(defs, f, all_recipes=True):
        return 0
    item = f.economy[BOOK][good]
    used = min(qty, item["quantity"])
    value = item["value"] * used // item["quantity"] if item["quantity"] else 0
    item["quantity"] -= used
    item["value"] -= value
    flow = _flow(f, turn)
    flow["used"][good] = flow["used"].get(good, 0) + qty
    flow["value"][good] = flow["value"].get(good, 0) - value
    return value


def input_prices(
    defs: Defs,
    config: GameConfig,
    f: Facility,
    prices: Mapping[str, int],
    utilization: int | None = None,
) -> dict[str, int]:
    """次に使う燃料のうち在庫は平均と現価の安い方、足りない割合は現価で見る。"""
    fuels = fuel_goods(defs, f)
    if not fuels:
        return {}
    book = ensure_book(defs, f, prices)
    rate = (
        f.economy.get("input_plan", f.economy.get("target", fac.FULL))
        if utilization is None
        else utilization
    )
    recipe = fac.recipe_of(defs, f)
    out = {}
    for good in sorted(fuels):
        need = fac.scale(recipe["inputs"][good], rate)
        current = prices.get(good, 0)
        item = book[good]
        held = min(need, stock.total(f.stock, good), item["quantity"])
        if need > 0 and held > 0:
            average = unit_price(item["value"], item["quantity"])
            out[good] = (min(average, current) * held + current * (need - held)) // need
        else:
            out[good] = current
    return out


def inventory_value(defs: Defs, f: Facility, prices: Mapping[str, int]) -> int:
    """施設の帳簿の在庫。燃料は平均取得原価、ほかの物は今の値段。"""
    book = ensure_book(defs, f, prices)
    return sum(
        book[g]["value"] if g in book else money_of(prices.get(g, 0), q)
        for g, q in stock.totals(f.stock).items()
    )
