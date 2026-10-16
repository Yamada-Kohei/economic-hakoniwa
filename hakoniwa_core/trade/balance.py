"""国際収支（フェーズ3-3）: 経常収支と金融収支。

島をまたぐお金はすべて money.transfer を通るので、そこで払い手と受け手の島ごとに、このターンの流れを
``island.scratch["bop"]`` に足します（record）。額は、その島の通貨でそのターンのレートで直した値です
（為替の売り買いのように約定の値段が決まっているときは、呼ぶ側がその値を渡す）。記録の段が統計に
します（write_stats）。

経常収支（仕訳の種類で分ける。CURRENT）:
- 貿易収支 goods: 圏の市場の代金（trade）・二国間契約（contract）・海外の商人（merchant）。物の
  援助は、送った国の島の値段で、輸出（受け取った国は輸入）と移転の両方に同じ額を入れる
- サービス収支 services: 他国の船の運賃と港の使用料（shipping_freight・port_fee）。今の仕組みでは、
  運賃は買い手が自国の海運業に、使用料は積む国と下ろす国がそれぞれ自国の港に払うので、島をまたが
  ず 0 になります
- 所得収支 income: 外国の国債の利子（bond_interest）と、中央銀行どうしの融通の利子（swap_interest。
  払えずに借りた額に足した分も）
- 移転 transfers: お金の援助（aid）と物の援助、移った人の仕送り（remittance）と、移る人が
  持ち出す預金（migrant_deposits。バランス調整の回3）

金融収支（資産の増加 − 負債の増加）:
- 資産 assets.fx: 外貨の持ち高の増減。assets.bonds: 外国の国債を買った・売った・償還された額。
  assets.swaps: 中央銀行どうしの融通で貸した・返された額（swap.py）
- 負債 liabilities.deposits: 外国の主体が持つ自国通貨（非居住者の預金）の増減。liabilities.bonds:
  外国が持つ自国の国債の増減。liabilities.swaps: 融通で借りた・返した額。liabilities.merchant:
  海外の商人に払ったお金（島の外で消えるので、負債が増えて消えたものとして入れ、経常収支と釣り
  合わせる）

どの流れも経常収支の側と金融収支の側に同じ額を入れるので、経常収支 = 金融収支 になります（テストで
確かめる）。非居住者の預金の増減と、外貨の持ち高の量の増減は、帳簿と外貨の持ち高の残高の増減とも
突き合わせられます（fx_moved に通貨ごとの量を残す）。為替の評価損益（持っている外貨の値打ちが
レートで変わる分）は流れではないので入れません。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.state import IslandState, WorldState
from hakoniwa_core.units import BASIS

KEY = "bop"  # island.scratch: このターンの国際収支の流れ
MOVED = "bop_fx_moved"  # island.scratch: 通貨ごとの外貨の持ち高の量の増減（確かめ用）

CURRENT = {
    "trade": "goods",
    "contract": "goods",
    "merchant": "goods",
    "shipping_freight": "services",
    "port_fee": "services",
    "bond_interest": "income",
    "swap_interest": "income",
    "aid": "transfers",
    "remittance": "transfers",
    "migrant_deposits": "transfers",
}
CURRENT_ITEMS = ("goods", "services", "income", "transfers")
ASSET_ITEMS = ("assets.fx", "assets.bonds", "assets.swaps")
LIABILITY_ITEMS = (
    "liabilities.deposits",
    "liabilities.bonds",
    "liabilities.swaps",
    "liabilities.merchant",
)


def flows(island: IslandState) -> dict[str, int]:
    return island.scratch.setdefault(KEY, {})


def add(island: IslandState, item: str, amount: int) -> None:
    if amount:
        table = flows(island)
        table[item] = table.get(item, 0) + amount


def value_of(world: WorldState, island: IslandState, currency: int, amount: int) -> int:
    """通貨 currency の amount を、島の通貨に直した値（そのターンのレート。組がなければ、共通の
    通貨を通した目安）。"""
    from hakoniwa_core.trade import fx as fxrates

    if currency == island.id:
        return amount
    if fxrates.has_rate(island, currency):
        return fxrates.to_own(island, currency, amount)
    e = fxrates.cross(world, island, currency)
    return amount * e // BASIS if e else 0


def record(
    world: WorldState,
    currency: int,
    source: tuple[int, str],
    target: tuple[int, str],
    amount: int,
    kind: str,
    values: dict[int, int] | None = None,
) -> None:
    """通貨 currency で source（島, 主体）から target へ amount 払った流れを、両方の島に足す。同じ
    島の中の支払いは足さない。values は島ごとの、その島の通貨に直した値（省くとそのターンのレート
    で直す）。"""
    if amount <= 0 or source[0] == target[0]:
        return
    item = CURRENT.get(kind)
    for (island_id, _), sign in ((source, -1), (target, 1)):
        island = world.islands.get(island_id)
        if island is None:
            continue
        if currency == island_id:
            value = amount
        elif values is not None and island_id in values:
            value = values[island_id]
        else:
            value = value_of(world, island, currency, amount)
        if item is not None:
            add(island, item, sign * value)
        if currency == island_id:  # 自国通貨: 外国の主体の非居住者の預金が増える・減る（負債）
            add(island, "liabilities.deposits", -sign * value)
        else:  # 外貨: 外貨の持ち高が増える・減る（資産）
            add(island, "assets.fx", sign * value)
            moved = island.scratch.setdefault(MOVED, {})
            moved[str(currency)] = moved.get(str(currency), 0) + sign * amount


def bonds(world: WorldState, issuer: int, holder: int, values: dict[int, int], sign: int) -> None:
    """外国の国債を買った（sign 1）・売った・償還された（sign -1）額面の値打ちを、持ち手の島の
    資産と、発行した島の負債に足す（values は島ごとの、その島の通貨の値）。"""
    if issuer == holder:
        return
    if holder in world.islands:
        add(world.islands[holder], "assets.bonds", sign * values.get(holder, 0))
    if issuer in world.islands:
        add(world.islands[issuer], "liabilities.bonds", sign * values.get(issuer, 0))


def merchant(island: IslandState, amount: int) -> None:
    """海外の商人に払ったお金（輸入。島の外で消えるので、金融収支の負債にも入れて釣り合わせる）。"""
    add(island, "goods", -amount)
    add(island, "liabilities.merchant", amount)


def goods_aid(donor: IslandState, recipient: IslandState, donor_value: int, value: int) -> None:
    """物の援助: 送った国は輸出と移転の支払い、受け取った国は輸入と移転の受け取り（どちらもその国の
    通貨の値。値段 0 の契約なのでお金は動かない）。"""
    add(donor, "goods", donor_value)
    add(donor, "transfers", -donor_value)
    add(recipient, "goods", -value)
    add(recipient, "transfers", value)


def totals(table: dict[str, Any]) -> tuple[int, int]:
    """（経常収支, 金融収支）。"""
    current = sum(int(table.get(k, 0)) for k in CURRENT_ITEMS)
    assets = sum(int(table.get(k, 0)) for k in ASSET_ITEMS)
    liabilities = sum(int(table.get(k, 0)) for k in LIABILITY_ITEMS)
    return current, assets - liabilities


def write_stats(island: IslandState) -> None:
    """このターンの国際収支を統計にする（流れのなかった項目も 0 を残す）。"""
    table = island.scratch.get(KEY, {})
    stats = island.stats
    for item in (*CURRENT_ITEMS, *ASSET_ITEMS, *LIABILITY_ITEMS):
        stats[f"bop.{item}"] = table.get(item, 0)
    current, financial = totals(table)
    stats["bop.current"] = current
    stats["bop.financial"] = financial
