"""貿易と為替（フェーズ3-1）の共通の部品。

島と島をつなぐ処理は世界の段 trade（hakoniwa_core/modules/trade）で行い、その中身をこのパッケージに
分けて置きます。

- money.py: 島をまたぐお金（非居住者の預金と、外貨の持ち高）と、世界の確かめ
- fx.py: 為替のレートと、為替の板寄せ（変動相場）
- ships.py: 港と海運業（輸送船・航海・燃料・運賃と使用料）
- blocs.py: 貿易圏（作る・入る・抜ける・設定）と、圏の市場
- traders.py: 商社（島の市場と圏の市場の注文、狙う量、仕入れ値）
- contracts.py: 二国間契約
- merchant.py: 海外の商人
- setup.py: 建国と移し替えのときに置くもの（港・海運業・輸送船・商社）

世界の状態（貿易圏・契約・運ぶ途中の荷）は ``world.ext["trade"]``、島ごとの状態（外貨の持ち高・
為替のレート・入っている圏の前のターンの値段など）は ``island.ext["trade"]`` に置きます。どちらも
JSON にそのまま保存できる形（辞書のキーは文字列）にします。島の番号は文字列のキーで持ちます。

距離は世界マップの区画の中心どうしの直線の距離で、船は1ターンに trade.ship_speed 区画進みます。
量は 1000 分の 1 単位、重さは財の freight_weight（トン）を掛けた 1000 分の 1 トンで数えます。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import is_stopped
from hakoniwa_core.state import Facility, IslandState, WorldState
from hakoniwa_core.units import QUANTITY_SCALE, to_milli

KEY = "trade"


def world_state(world: WorldState) -> dict[str, Any]:
    """世界の貿易の状態（貿易圏・契約・運ぶ途中の荷と、番号の振り出し）。"""
    state = world.ext.setdefault(KEY, {})
    state.setdefault("seq", {"bloc": 0, "contract": 0, "shipment": 0})
    state.setdefault("blocs", [])
    state.setdefault("contracts", [])
    state.setdefault("shipments", [])
    return state


def next_id(state: dict[str, Any], kind: str) -> int:
    seq = state["seq"]
    seq[kind] = seq.get(kind, 0) + 1
    return seq[kind]


def island_state(island: IslandState) -> dict[str, Any]:
    """島の貿易の状態（外貨の持ち高・為替のレート・入っている圏の見え方・海外の商人）。"""
    return island.ext.setdefault(KEY, {})


def started(island: IslandState) -> bool:
    """島に貿易の仕組み（港・海運業・商社）を置き終えたか（setup.py）。"""
    return "since" in island.ext.get(KEY, {})


# --- 国の倉庫に届いた腐る物 ------------------------------------------------------

TO_MARKET = "to_market"


def to_market(island: IslandState) -> dict[str, int]:
    """国の倉庫に船で届いた腐る物（契約で買った食品など）のうち、まだ島の市場で売れていない量
    （財 → 量）。市場の段が、売り切れるか傷んで捨てられるまで、毎ターン指値なしで売りに出す
    （試し遊びで、契約で輸入した食品が倉庫で腐り、住民に回らなかったため）。"""
    return island.ext.get(KEY, {}).get(TO_MARKET, {})


def send_to_market(island: IslandState, good: str, qty: int) -> None:
    """国の倉庫に届いた腐る物 qty を、市場に出す量に足す。"""
    left = island_state(island).setdefault(TO_MARKET, {})
    left[good] = left.get(good, 0) + qty


# --- 通貨の名前 ------------------------------------------------------------------


def currency_labels(names: dict[int, tuple[str, str]]) -> dict[int, str]:
    """島の番号 → (国名, 通貨の名前) から、見せる通貨の名前を作る。同じ名前の通貨がほかの島にも
    あれば国名を前に付ける（「〇〇国のクレジット」。建国のときに通貨の名前を自由に決めるので、
    重なると契約やレートでどの国の通貨か分からないため）。"""
    count = Counter(currency for _, currency in names.values())
    return {
        i: f"{name}の{currency}" if count[currency] > 1 else currency
        for i, (name, currency) in names.items()
    }


def currency_label(world: WorldState, island_id: int) -> str:
    """通貨（番号は島の番号）の見せる名前。島がなければ番号。"""
    names = {
        i: (island.name, island.names.get("currency", island.name))
        for i, island in world.islands.items()
    }
    return currency_labels(names).get(island_id, str(island_id))


# --- 施設 ------------------------------------------------------------------------


def is_port(config: GameConfig, f: Facility) -> bool:
    return f.kind == config.get("trade.port_facility")


def is_shipping(config: GameConfig, f: Facility) -> bool:
    return f.kind == config.get("trade.shipping_facility")


def is_trader(config: GameConfig, f: Facility) -> bool:
    return f.kind == config.get("trade.trader_facility")


def is_trade_facility(config: GameConfig, f: Facility) -> bool:
    """港・海運業・商社（ふつうの工場とは別の決まりで動く施設）。"""
    return is_port(config, f) or is_shipping(config, f) or is_trader(config, f)


def port_of(island: IslandState, config: GameConfig) -> Facility | None:
    """動いている港（建っていて、閉鎖中・差し押さえ中でない。なければ None）。"""
    for f in sorted(island.facilities, key=lambda f: f.id):
        if is_port(config, f) and f.built and not is_stopped(f):
            return f
    return None


def shipping_of(island: IslandState, config: GameConfig) -> Facility | None:
    for f in sorted(island.facilities, key=lambda f: f.id):
        if is_shipping(config, f):
            return f
    return None


def traders_of(island: IslandState, config: GameConfig) -> list[Facility]:
    """動いている商社（番号の順）。"""
    return sorted(
        (f for f in island.facilities if is_trader(config, f) and f.built and not is_stopped(f)),
        key=lambda f: f.id,
    )


def site_of(island: IslandState, config: GameConfig) -> tuple[int, int] | None:
    """島の外との荷の出入り口（港のマス）。港がなければ首都。"""
    port = port_of(island, config)
    if port is not None:
        return (port.x, port.y)
    return island.capital


# --- 財の重さ --------------------------------------------------------------------


def tradable(defs: Defs, good: str) -> bool:
    """島の外へ運べる財か（goods.yaml に freight_weight がある）。"""
    return "freight_weight" in defs.goods.get(good, {})


def tradable_goods(defs: Defs) -> list[str]:
    return sorted(g for g in defs.goods if tradable(defs, g))


def unit_weight(defs: Defs, good: str) -> int:
    """財 1 単位の重さ（1000 分の 1 トン）。"""
    return to_milli(defs.goods[good].get("freight_weight", 0))


def weight(defs: Defs, good: str, qty: int) -> int:
    """量 qty（1000 分の 1 単位）の重さ（1000 分の 1 トン。切り上げ）。"""
    return -(-qty * unit_weight(defs, good) // QUANTITY_SCALE)


def qty_of_weight(defs: Defs, good: str, load: int) -> int:
    """重さ load（1000 分の 1 トン）で運べる量（1000 分の 1 単位。重さのない財は限りなし）。"""
    unit = unit_weight(defs, good)
    if unit <= 0:
        return 10**15
    return load * QUANTITY_SCALE // unit


# --- 距離と航海 --------------------------------------------------------------------


def distance(a: IslandState, b: IslandState) -> float:
    """世界マップの区画の中心どうしの直線の距離（区画）。"""
    return math.hypot(a.wx - b.wx, a.wy - b.wy)


def one_way_turns(config: GameConfig, a: IslandState, b: IslandState) -> int:
    """片道のターン数: 距離 ÷ trade.ship_speed を切り上げ、少なくとも 1。"""
    return max(1, math.ceil(distance(a, b) / config.get("trade.ship_speed") - 1e-9))


def cells(a: IslandState, b: IslandState) -> int:
    """運賃を数える区画の数（距離を切り上げた整数。少なくとも 1）。"""
    return max(1, math.ceil(distance(a, b) - 1e-9))
