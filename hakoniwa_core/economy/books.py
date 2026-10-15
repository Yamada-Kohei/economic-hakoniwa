"""このターンのお金と物の流れの記録。施設の帳簿・決算・GDP をここから作ります。

段はお金を払うたびに、施設ごとの収支の項目（sales・purchases・wages など）か、家計・工事・
国の倉庫・政府の項目に額を足します。施設の在庫の出入り（作った・使った・買った・売った・
傷んだ量）も財ごとに足し、在庫の増減の値打ちはそのターンの値段で数えます。
取り壊しで倉庫に戻した物のような「持ち主が変わるだけ」の動きは数えません。

GDP（生産の側）= 施設の付加価値（売上 − 仕入れ − 電気代 − 払った運賃 + 在庫の増減）
               ＋ 工事の作業員の賃金 ＋ 公務員の賃金
GDP（支出の側）= 家計の消費（食料・消費財・電気・運賃）＋ 住宅の資材
               ＋ 工事（資材・運賃・作業員の賃金）＋ 施設の在庫の増減
               ＋ 国の倉庫の買い入れ − 売り渡し ＋ 公務員の賃金
               ＋ 学校と大学の教材（代金・運賃。フェーズ2-3）
               ＋ 輸出 − 輸入（貿易圏の市場・二国間契約・海外の商人。フェーズ3-1）
売り買いの代金と運賃は払う側と受け取る側の両方に同じ額を足すので、2つは一致します。
輸出と輸入は、商社の売上・仕入れ（圏の市場の代金を、そのターンの為替で自国通貨に直した額）と同じ額を
TRADE の部門に足します。
銀行の利ざやはフェーズ2では入れません（銀行の職員の賃金は、家計への移転として扱う）。
"""

from __future__ import annotations

from collections.abc import Mapping

from hakoniwa_core.state import IslandState
from hakoniwa_core.units import money_of

# 施設の収支の項目
SALES = "sales"
PURCHASES = "purchases"
WAGES = "wages"
ELECTRICITY = "electricity"
FREIGHT = "freight"
INVENTORY = "inventory_change"
DEPRECIATION = "depreciation"
INTEREST = "interest"
TAX = "tax"
DIVIDEND = "dividend"
CAPITAL = "capital_injection"
SUBSIDY = "subsidy"  # 受け取った補助金（フェーズ2-2。輸出補助もここ。フェーズ3-3）
TARIFF = "tariff"  # 払った関税（フェーズ3-3。損益の費用。付加価値には入れない）
WRITE_OFF = "write_off"  # 銀行の貸し倒れ（損失）
REALIZED = "bond_realized"  # 銀行の国債を売った・償還された・切り下げられた損益（実現損益）
VALUATION = "valuation"  # 銀行の国債のまだ売っていない評価損益の増減（損益に入れない）
PROFIT = "profit"

# 施設でない部門
HOUSEHOLDS = "households"  # 項目: goods, electricity, freight, housing, tax
CONSTRUCTION = "construction"  # 項目: materials, freight, wages
WAREHOUSE = "warehouse"  # 項目: purchases, sales
GOVERNMENT = "government"  # 項目: wages（公務員）、goods・freight（学校と大学の教材）
# 項目: interest（受け取った利子 − 払った利子）、bond_realized（国債の実現損益）、
# valuation（国債のまだ売っていない評価損益の増減）
CENTRAL_BANK = "central_bank"
TRADE = "trade"  # 項目: exports（輸出）、imports（輸入）。フェーズ3-1
FX_REALIZED = "fx_realized"  # 施設の外貨を売った損益（為替の実現損益。フェーズ3-1）


def _flows(island: IslandState) -> dict:
    return island.scratch.setdefault("flows", {"facilities": {}, "sectors": {}, "stock": {}})


def add(island: IslandState, facility: int, item: str, amount: int) -> None:
    """施設 facility の収支の項目 item に amount を足す。"""
    if amount:
        items = _flows(island)["facilities"].setdefault(facility, {})
        items[item] = items.get(item, 0) + amount


def add_sector(island: IslandState, sector: str, item: str, amount: int) -> None:
    if amount:
        items = _flows(island)["sectors"].setdefault(sector, {})
        items[item] = items.get(item, 0) + amount


def stock_moved(island: IslandState, facility: int, good: str, qty: int) -> None:
    """施設の在庫の出入り（入れば正、出れば負）。"""
    if qty:
        goods = _flows(island)["stock"].setdefault(facility, {})
        goods[good] = goods.get(good, 0) + qty


def facility_items(island: IslandState) -> dict[int, dict[str, int]]:
    return _flows(island)["facilities"]


def sector(island: IslandState, name: str) -> dict[str, int]:
    return _flows(island)["sectors"].get(name, {})


def inventory_changes(island: IslandState, prices: Mapping[str, int]) -> dict[int, int]:
    """施設ごとの在庫の増減の値打ち（そのターンの値段で）。"""
    out = {}
    for facility, goods in _flows(island)["stock"].items():
        out[facility] = sum(money_of(prices.get(g, 0), q) for g, q in sorted(goods.items()))
    return out


def value_added(items: Mapping[str, int]) -> int:
    """施設の付加価値（在庫の増減は items の inventory_change に入れておく）。"""
    return (
        items.get(SALES, 0)
        - items.get(PURCHASES, 0)
        - items.get(ELECTRICITY, 0)
        - items.get(FREIGHT, 0)
        + items.get(INVENTORY, 0)
    )


def gdp_gap(production: int, spending: int, floor: int) -> float:
    """生産の側と支出の側の GDP の差の割合（関門で見る。Codex のレビュー A08）。

    分母は2つの絶対値の大きい方で、floor（1人の1ターンの賃金など、小さすぎる額）より小さくしない。
    片方だけが 0 のときや、両方が負のとき、符号が違うときも、差を見逃さない（0 なら一致）。
    """
    return abs(production - spending) / max(abs(production), abs(spending), floor, 1)


def gdp(island: IslandState, inventory: Mapping[int, int]) -> tuple[int, int]:
    """このターンの GDP（生産の側, 支出の側）。inventory は inventory_changes の結果。"""
    facilities = facility_items(island)
    production = 0
    for facility, items in facilities.items():
        production += value_added({**items, INVENTORY: inventory.get(facility, 0)})
    for facility, change in inventory.items():
        if facility not in facilities:
            production += change
    hh = sector(island, HOUSEHOLDS)
    site = sector(island, CONSTRUCTION)
    store = sector(island, WAREHOUSE)
    gov = sector(island, GOVERNMENT)
    trade = sector(island, TRADE)
    production += site.get("wages", 0) + gov.get("wages", 0)
    spending = (
        hh.get("goods", 0)
        + hh.get("electricity", 0)
        + hh.get("freight", 0)
        + hh.get("housing", 0)
        + site.get("materials", 0)
        + site.get("freight", 0)
        + site.get("wages", 0)
        + sum(inventory.values())
        + store.get("purchases", 0)
        - store.get("sales", 0)
        + gov.get("wages", 0)
        + gov.get("goods", 0)
        + gov.get("freight", 0)
        + trade.get("exports", 0)
        - trade.get("imports", 0)
    )
    return production, spending
