"""払えるお金の確かめと取り置き。

段をまたいで払うお金（雇用の段で雇って賃金の段で払う賃金、市場で注文を出すときの「量 × 指値」）
は、先に取り置いてから払います。取り置いた分は他の支払いに使えません。

政府のお金（国庫）は、ターンの初めに支出の項目ごとに取り置きます（予算の段 budget）。
各項目は「自分の項目に取り置いた残り」と「どの項目にも取り置いていない国庫の残り」まで使えます。
年の予算を決める項目（国の倉庫の買い入れなど）は、予算の月割りを超えて使えません。
国庫が足りないときは、game.yaml の fiscal.payment_order の後ろの項目から削られます。

このターンの取り置きは状態の写しに入れず（island.scratch）、使った額だけを次のターンの
見積もりのために残します（economy.spending）。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any

from hakoniwa_core import ledger
from hakoniwa_core.economy import Payer
from hakoniwa_core.state import IslandState

UNLIMITED = sys.maxsize  # 銀行と中央銀行（お金を作れる）


class FundsError(Exception):
    pass


def _committed(island: IslandState) -> dict[str, int]:
    return island.scratch.setdefault("committed", {})


def fiscal_state(island: IslandState) -> dict[str, Any]:
    """このターンの国庫の取り置き（reserved）・使った額（used）・予算の月割り（caps）。"""
    return island.scratch.setdefault("fiscal", {"reserved": {}, "used": {}, "caps": {}})


def open_treasury(
    island: IslandState, estimates: Mapping[str, int], order: list[str], caps: Mapping[str, int]
) -> None:
    """ターンの初めに、国庫を支出の項目の順（order）に見積もり（estimates）だけ取り置く。"""
    state = fiscal_state(island)
    left = ledger.money(island, ledger.GOV)
    state["reserved"] = {}
    for item in order:
        want = estimates.get(item, 0)
        if item in caps:
            want = min(want, caps[item])
        give = max(0, min(want, left))
        state["reserved"][item] = give
        left -= give
    state["used"] = {item: 0 for item in order}
    state["caps"] = dict(caps)


def available(island: IslandState, payer: Payer, capped: bool = True) -> int:
    """payer がいま使えるお金（取り置いた分を除く）。capped が偽なら、年の予算の月割りで止めない
    （お金が足りないのか、予算で止まっているのかを分ける。賃金の遅配）。"""
    entity = payer.entity
    if entity == ledger.CB or ledger.is_bank(entity):
        return UNLIMITED
    committed = _committed(island).get(entity, 0)
    if entity != ledger.GOV:
        return max(0, ledger.money(island, entity) - committed)
    state = fiscal_state(island)
    unclaimed = ledger.money(island, ledger.GOV) - committed
    reserved, used = state["reserved"], state["used"]
    item = payer.item or ""
    own = max(0, reserved.get(item, 0) - used.get(item, 0))
    held = sum(max(0, r - used.get(i, 0)) for i, r in reserved.items())
    free = max(0, unclaimed - held)
    amount = min(unclaimed, own + free)
    if capped and item in state["caps"]:
        amount = min(amount, state["caps"][item] - used.get(item, 0))
    return max(0, amount)


def _claim(island: IslandState, payer: Payer, amount: int) -> None:
    if amount > available(island, payer):
        raise FundsError(f"{payer.entity}（{payer.item}）のお金が足りません: {amount}")
    if payer.entity == ledger.GOV:
        used = fiscal_state(island)["used"]
        item = payer.item or ""
        used[item] = used.get(item, 0) + amount


def commit(island: IslandState, payer: Payer, amount: int) -> None:
    """後で払うお金を取り置く。"""
    if amount <= 0:
        return
    _claim(island, payer, amount)
    committed = _committed(island)
    committed[payer.entity] = committed.get(payer.entity, 0) + amount


def release(island: IslandState, payer: Payer, amount: int) -> None:
    """取り置いたお金を、払わずに戻す。"""
    if amount <= 0:
        return
    committed = _committed(island)
    committed[payer.entity] = committed.get(payer.entity, 0) - amount
    if payer.entity == ledger.GOV:
        used = fiscal_state(island)["used"]
        item = payer.item or ""
        used[item] = used.get(item, 0) - amount


def pay(
    island: IslandState,
    payer: Payer,
    payee: str,
    amount: int,
    kind: str,
    committed: bool = False,
) -> None:
    """payer から payee へ払う。committed なら取り置いた分から、そうでなければ今あるお金から。"""
    if amount <= 0:
        return
    if committed:
        held = _committed(island)
        held[payer.entity] = held.get(payer.entity, 0) - amount
    else:
        _claim(island, payer, amount)
    ledger.pay(island, payer.entity, payee, amount, kind)


def spent(island: IslandState) -> dict[str, int]:
    """このターンに政府が項目ごとに使った額。"""
    return {k: v for k, v in fiscal_state(island)["used"].items() if v}
