"""払えるお金の確かめと取り置き。

段をまたいで払うお金（雇用の段で雇って賃金の段で払う賃金、市場で注文を出すときの「量 × 指値」）
は、先に取り置いてから払います。取り置いた分は他の支払いに使えません。

政府のお金（国庫）は、ターンの初めに支出の項目ごとに取り置きます（予算の段 budget）。
各項目は「自分の項目に取り置いた残り」と「どの項目にも取り置いていない国庫の残り」まで使えます。
年の予算を決める項目（国の倉庫の買い入れなど）は、予算の月割りを超えて使えません。
国庫が足りないときは、game.yaml の fiscal.payment_order の後ろの項目から削られます。順番には
項目の組（リスト）も書け、組の中の項目は、見積もりの合計に足りなければ見積もりに同じ割合を掛けて
取り置きます（今月の人件費と運営費を、1つの項目だけ押し出さずに同じ割合で遅配にする。回5）。

国庫のうち、次のターンまでの支出と利払いを超えた分は、国債の段の終わりに市中銀行へ政府の預金と
して預けます（deposit_surplus。回5）。政府が黒字のとき、税と国営の施設の売上で準備預金が国庫へ
吸い上げられたままにならないようにするためです（アメリカの国庫の税・借入勘定 TT&L、ソ連の国の
予算の余りを国営銀行が貸し出しに回した形）。政府の預金は国庫と同じく取り置きと支払いに使え、
国庫で足りない分は払うときと国債を出す前に引き出します（recall）。預け入れと引き出しは国の収入・支出に数えません（TREASURY_MOVES）。

このターンの取り置きは状態の写しに入れず（island.scratch）、使った額だけを次のターンの
見積もりのために残します（economy.spending）。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
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


def groups(order: Sequence[str | Sequence[str]]) -> list[list[str]]:
    """支払いの順番（fiscal.payment_order）を組の並びにする。項目1つは1つだけの組。"""
    return [[entry] if isinstance(entry, str) else list(entry) for entry in order]


def items(order: Sequence[str | Sequence[str]]) -> list[str]:
    """支払いの順番の項目を、組を開いて順に並べる。"""
    return [item for group in groups(order) for item in group]


def open_treasury(
    island: IslandState,
    estimates: Mapping[str, int],
    order: Sequence[str | Sequence[str]],
    caps: Mapping[str, int],
) -> None:
    """ターンの初めに、国庫を支出の項目の順（order）に見積もり（estimates）だけ取り置く。

    組の中の項目は、残りが見積もりの合計に足りなければ、見積もりに同じ割合を掛けて取り置く
    （端数は組の最後の項目に）。"""
    state = fiscal_state(island)
    left = max(0, spendable(island))  # 市中銀行の政府の預金も使える（払うときに戻す）
    state["reserved"] = {}
    for group in groups(order):
        wants = {}
        for item in group:
            want = max(0, estimates.get(item, 0))
            if item in caps:
                want = min(want, max(0, caps[item]))
            wants[item] = want
        whole = sum(wants.values())
        if whole <= left:
            state["reserved"].update(wants)
            left -= whole
            continue
        given = 0
        for i, item in enumerate(group):
            share = left - given if i == len(group) - 1 else wants[item] * left // whole
            share = min(share, wants[item])
            state["reserved"][item] = share
            given += share
        left -= given
    state["used"] = {item: 0 for item in items(order)}
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
    unclaimed = spendable(island) - committed
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
    if payer.entity == ledger.GOV:  # 国庫で足りない分は、市中銀行の政府の預金から戻して払う
        recall(island, amount - ledger.money(island, ledger.GOV))
    ledger.pay(island, payer.entity, payee, amount, kind)


def spent(island: IslandState) -> dict[str, int]:
    """このターンに政府が項目ごとに使った額。"""
    return {k: v for k, v in fiscal_state(island)["used"].items() if v}


# --- 政府の預金（回5） --------------------------------------------------------------

TREASURY_DEPOSIT = "treasury_deposit"  # 国庫 → 市中銀行の政府の預金
TREASURY_RECALL = "treasury_recall"  # 市中銀行の政府の預金 → 国庫
TREASURY_MOVES = (TREASURY_DEPOSIT, TREASURY_RECALL)


def deposited(island: IslandState) -> dict[str, int]:
    """政府が市中銀行に預けている額（銀行 → 額）。"""
    return {b: v for b, v in ledger.deposits_by_bank(island, ledger.GOV).items() if v > 0}


def government_money(island: IslandState) -> int:
    """国のお金の合計（国庫と、市中銀行の政府の預金）。"""
    return ledger.money(island, ledger.GOV) + sum(deposited(island).values())


def _withdrawable(island: IslandState) -> dict[str, int]:
    """いま引き出せる政府の預金（銀行 → 額）。破綻した銀行からは引き出さず（処理で引き継ぐ）、
    銀行の準備預金をマイナスにしない分まで（足りない分は国債で集める）。"""
    from hakoniwa_core.economy import banks

    active = set(banks.active_banks(island))
    return {
        b: min(v, max(0, banks.reserves_of(island, b)))
        for b, v in deposited(island).items()
        if b in active
    }


def spendable(island: IslandState) -> int:
    """国がいま使えるお金（国庫と、引き出せる政府の預金）。"""
    return ledger.money(island, ledger.GOV) + sum(_withdrawable(island).values())


def recall(island: IslandState, amount: int) -> int:
    """政府の預金から amount まで国庫に戻す（引き出せる額の多い銀行から）。戻した額を返す。"""
    left = amount
    held = _withdrawable(island)
    for bank in sorted(held, key=lambda b: (-held[b], b)):
        if left <= 0:
            break
        take = min(left, held[bank])
        ledger.bank_to_treasury(island, bank, take, TREASURY_RECALL)
        left -= take
    return amount - max(0, left)


def deposit_surplus(island: IslandState, keep: int, weights: Mapping[str, int]) -> int:
    """国庫の keep を超えた分を、銀行に weights（預金の額など）の割合で預ける（端数は最後の
    銀行に）。国庫が keep に足りなければ、足りない分を政府の預金から戻す。預けた額（戻したなら
    マイナス）を返す。"""
    surplus = ledger.money(island, ledger.GOV) - keep
    if surplus < 0:
        return -recall(island, -surplus)
    whole = sum(v for v in weights.values() if v > 0)
    if surplus == 0 or whole <= 0:
        return 0
    names = sorted(b for b, v in weights.items() if v > 0)
    given = 0
    for i, bank in enumerate(names):
        share = surplus - given if i == len(names) - 1 else surplus * weights[bank] // whole
        ledger.treasury_to_bank(island, bank, share, TREASURY_DEPOSIT)
        given += share
    return given
