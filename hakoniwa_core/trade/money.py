"""島をまたぐお金（フェーズ3-1）。

通貨は島ごとに1つで、通貨の番号は島の番号です。島の帳簿（hakoniwa_core/ledger.py）はその島の通貨だけ
で書きます。

- 外国の主体が持つこの島の通貨は、この島の帳簿の「非居住者の預金」（持ち手 ``nr:<島>:<主体>``、
  中央銀行に置く）です。
- 持っている側の島では、外貨は帳簿の勘定に入れず、主体ごとの「外貨の持ち高」に持ちます
  （``island.ext["trade"]["fx"]``: 主体 → 通貨 → [量, 帳簿の値打ち]）。帳簿の値打ちは、その外貨を
  手に入れたときに自国通貨で払った・受け取った額の合計で、払うときは平均の値打ちで減らします。
  外貨を売ったときの「受け取った自国通貨 − 帳簿の値打ち」が為替の実現損益です。

通貨 C での支払い（transfer）は、C の島の帳簿の上で払い手から受け手へのふつうの支払いになり、払い手
と受け手が C の島の主体でなければ、それぞれの外貨の持ち高も同じだけ動きます。どちらの島の帳簿の確か
めも崩れません。世界の確かめ（reconcile）は、どの島 X と Y の組でも「Y の主体が持つ X の外貨の合計 =
X の帳簿の nr:Y:* の預金の合計」になっていることを見ます。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import ledger
from hakoniwa_core.economy import Payer, funds
from hakoniwa_core.state import IslandState, WorldState
from hakoniwa_core.trade import balance, island_state
from hakoniwa_core.units import BASIS


def holdings(island: IslandState) -> dict[str, dict[str, list[int]]]:
    """島の主体ごとの外貨の持ち高（主体 → 通貨の番号の文字列 → [量, 帳簿の値打ち]）。"""
    return island_state(island).setdefault("fx", {})


def holding(island: IslandState, entity: str, currency: int) -> int:
    """主体 entity が持つ通貨 currency の量（自国通貨なら預金・国庫）。"""
    if currency == island.id:
        return ledger.money(island, entity)
    return holdings(island).get(entity, {}).get(str(currency), [0, 0])[0]


def book_value(island: IslandState, entity: str, currency: int) -> int:
    """外貨の持ち高の帳簿の値打ち（自国通貨）。"""
    return holdings(island).get(entity, {}).get(str(currency), [0, 0])[1]


def _add(island: IslandState, entity: str, currency: int, amount: int, value: int) -> None:
    table = holdings(island)
    mine = table.setdefault(entity, {})
    entry = mine.setdefault(str(currency), [0, 0])
    entry[0] += amount
    entry[1] += value
    if entry[0] == 0:
        del mine[str(currency)]
        if not mine:
            del table[entity]


def cost_of(island: IslandState, entity: str, currency: int, amount: int) -> int:
    """外貨 amount の帳簿の値打ち（持ち高の平均で。切り捨て）。"""
    have = holding(island, entity, currency)
    if currency == island.id:
        return amount
    if have <= 0:
        return 0
    return book_value(island, entity, currency) * amount // have


def transfer(
    world: WorldState,
    currency: int,
    source: tuple[int, str],
    target: tuple[int, str],
    amount: int,
    kind: str,
    value: int | None = None,
    payer: Payer | None = None,
    values: dict[int, int] | None = None,
) -> int:
    """通貨 currency で source（島, 主体）から target（島, 主体）へ amount 払う。

    受け手が外貨として受け取るときは、帳簿の値打ちを value（自国通貨。省くと 0）だけ足す。
    払い手が外貨で払ったときは、払った分の帳簿の値打ち（平均で）を返す。自国通貨で払ったときは
    amount を返す。自国通貨で払うときに payer（政府なら支出の項目）を渡すと、funds を通して払う
    （国庫の取り置きの残りを正しく数えるため）。払えるかどうかは呼ぶ側が確かめる（記録の段の帳簿の
    確かめと、世界の確かめで見つかる）。

    途中で例外が出ると、持ち高だけ動いて帳簿が動かない形になるが、ターンの計算はサーバーが1つの
    トランザクションで行い、例外のターンは捨てる（turn/engine.py の run_turn）ので、保存はされない。

    島をまたぐ支払いは、国際収支の流れにも足す（balance.record。values は島ごとの、その島の通貨に
    直した値。省くとそのターンのレートで直す）。
    """
    if amount <= 0:
        return 0
    issuer = world.islands[currency]
    payer_name = _name(currency, *source)
    payee_name = _name(currency, *target)
    spent = amount
    if source[0] != currency:
        spent = cost_of(world.islands[source[0]], source[1], currency, amount)
        _add(world.islands[source[0]], source[1], currency, -amount, -spent)
    if target[0] != currency:
        _add(world.islands[target[0]], target[1], currency, amount, value or 0)
    if payer is not None and source[0] == currency:
        funds.pay(issuer, payer, payee_name, amount, kind)
    else:
        ledger.pay(issuer, payer_name, payee_name, amount, kind)
    balance.record(world, currency, source, target, amount, kind, values)
    return spent


def _name(currency: int, island_id: int, entity: str) -> str:
    """通貨 currency の島の帳簿での、島 island_id の主体 entity の名前。"""
    return entity if island_id == currency else ledger.nonresident(island_id, entity)


def entities_holding(island: IslandState, currency: int) -> list[str]:
    """通貨 currency を持っている主体（名前の順）。"""
    key = str(currency)
    return sorted(e for e, cur in holdings(island).items() if cur.get(key, [0])[0] > 0)


def nonresident_deposits(issuer: IslandState) -> dict[int, dict[str, int]]:
    """発行した島の帳簿の非居住者の預金（持ち主の島 → 主体 → 額）。"""
    out: dict[int, dict[str, int]] = {}
    for name, value in issuer.accounts.items():
        holder, item, _ = ledger.parse(name)
        if item != ledger.NONRESIDENT:
            continue
        _, island_id, entity = holder.split(":", 2)
        out.setdefault(int(island_id), {})[entity] = value
    return out


def reconcile(world: WorldState) -> list[str]:
    """世界の確かめ: 外貨の持ち高と、発行した島の帳簿の非居住者の預金が合うか。問題を文で返す。"""
    problems: list[str] = []
    held: dict[tuple[int, int, str], int] = {}  # (通貨, 持ち主の島, 主体) → 量
    for island_id in world.island_ids():
        for entity, currencies in holdings(world.islands[island_id]).items():
            for key, (amount, _) in currencies.items():
                if amount < 0:
                    problems.append(f"島 {island_id} の {entity} の通貨 {key} の持ち高がマイナス")
                held[(int(key), island_id, entity)] = amount
    booked: dict[tuple[int, int, str], int] = {}
    for currency in world.island_ids():
        for owner, entities in nonresident_deposits(world.islands[currency]).items():
            for entity, value in entities.items():
                booked[(currency, owner, entity)] = value
    for key in sorted(set(held) | set(booked)):
        if held.get(key, 0) != booked.get(key, 0):
            currency, owner, entity = key
            problems.append(
                f"島 {owner} の {entity} が持つ通貨 {currency} の量 {held.get(key, 0)} が、"
                f"島 {currency} の帳簿の非居住者の預金 {booked.get(key, 0)} と合いません"
            )
    return problems


def total_value(island: IslandState, rates: dict[str, int]) -> int:
    """島の主体が持つ外貨の、今のレートでの自国通貨の値打ちの合計（rates は fx.rates）。"""
    total = 0
    for currencies in holdings(island).values():
        for key, (amount, _) in currencies.items():
            total += amount * rates.get(key, BASIS) // BASIS
    return total


def summary(island: IslandState) -> dict[str, Any]:
    """画面向け: 通貨ごとの持ち高の合計。"""
    out: dict[str, int] = {}
    for currencies in holdings(island).values():
        for key, (amount, _) in currencies.items():
            out[key] = out.get(key, 0) + amount
    return out
