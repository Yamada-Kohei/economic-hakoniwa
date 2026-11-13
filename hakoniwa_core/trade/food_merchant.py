"""物がないときの海外の食料商人（W-239。設計書 9 貿易 FX-73。仮の決まり）。

島にただ1つの食料の施設が災害で止まるなどして、食べ物が物理的になくなった島へ、島の外の世界（遊ぶ国
のいない外国）から商人が穀物を運んできます。命令なしの国は貿易圏に入らず輸入の道がないので、前は
漁港が火事で止まると島じゅうが飢えていました。外の世界との貿易を入れるとき（FX-27 と一緒に）、この
形を置き換えます。

- 来る条件（市場の段の始めに決める）: 前のターンに飢えた家計（PO-58）があり、お金のある買いが食料を
  買えなかった量（買えなかった栄養）があり、商人のほかの売り手（島の施設・国の倉庫・商社・契約の荷）の
  食料に売れ残りがなかった。来ている商人は、前のターンに穀物が売れ、ほかの売り手に売れ残りがない間は
  来続ける（食べられたターンに去ると、次のターンにまた飢えて、来る・去るが入れ替わるため）。
  foreign_merchant.turns を過ぎても、貿易圏に入っていなくても来る。
- 量: 前のターンの買えなかった栄養と前のターンに商人が売れた栄養の和から、このターンに国の倉庫が
  売りに出す穀物の栄養を引き、穀物の量に直す。上限は trade.port_capacity（大きさの倍を掛けた値）を
  穀物の重さで量に直した量（港の有無や止まりによらない。大きな船で運べる量の目安）。封鎖された島には
  封鎖の強さの割合だけ少なく来る（FX-50）。
- 値段: 今の海外の商人と同じく、その島の穀物の初めの値段 × foreign_merchant.multiplier × 島の物価
  （FX-59）。島の物価は来始めたターンの前の値で止める（飢饉で物価が上がり、商人の値段がそれを追って
  つり上がらないように）。
- 代金・計画経済の差額・売れ残りは今の海外の商人と同じ（modules/market の _trade_orders と精算）。
- 品物は国の穀物の備蓄の財（food_reserve.good）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.state import IslandState
from hakoniwa_core.trade import island_state, merchant, qty_of_weight
from hakoniwa_core.units import QUANTITY_SCALE, scale_price, to_milli

KEY = "food_merchant"  # 島の貿易の状態の欄
# 他の売り手の売れ残りは、島の食べる量（栄養）のこの割合までなら「ない」とみなす（層への取り分と
# 注文の切り捨てで数単位欠ける分。飢えのわけの見分け food_short_reason の 1% と同じ）
UNSOLD_SLACK = 0.01


def state_of(island: IslandState) -> dict[str, Any]:
    return island_state(island).setdefault(KEY, {})


def good_of(config: GameConfig) -> str:
    return config.get("food_reserve.good")


def nutrition(defs: Defs, good: str) -> int:
    """財 1 単位の栄養（1000 分の 1）。"""
    return to_milli(defs.goods[good]["food"]["nutrition"])


def active(island: IslandState) -> bool:
    """このターンに来ているか（市場の段の始めに decide が決めた値）。"""
    return bool(island_state(island).get(KEY, {}).get("active"))


def decide(island: IslandState, hungry: bool, turn: int, enabled: bool = True) -> bool:
    """市場の段の始めに、このターンに来るかを決めて状態に書く。hungry は前のターンに飢えた家計が
    あったか。enabled は foreign_merchant.enabled（海外の商人を来させない設定なら来ない）。"""
    state = state_of(island)
    seen = state.get("seen", {})
    clear = not seen.get("others_unsold", True)  # 記録のない島（初めのターン）は来ない
    came = bool(state.get("active"))
    stay = came and seen.get("sold", 0) > 0
    start = hungry and seen.get("unmet", 0) > 0
    now = bool(enabled) and clear and (stay or start)
    if now and not came:
        state["since"] = turn
        state["cpi"] = seen.get("cpi", 1.0)  # 来始めたターンの前の物価で止める
    state["active"] = now
    if not now:
        state.pop("since", None)
        state.pop("cpi", None)
    return now


def offer(
    island: IslandState, defs: Defs, config: GameConfig, released: int
) -> tuple[str, int, int] | None:
    """このターンに商人が売りに出す (財, 量, 値段)。来ていなければ None。released は、このターンに
    国の倉庫が売りに出した穀物の量（1000 分の 1 単位）。"""
    if not active(island):
        return None
    state = state_of(island)
    seen = state.get("seen", {})
    good = good_of(config)
    unit = nutrition(defs, good)
    if unit <= 0:
        return None
    want = seen.get("unmet", 0) + seen.get("sold", 0) * unit // QUANTITY_SCALE
    want -= released * unit // QUANTITY_SCALE
    qty = -(-max(0, want) * QUANTITY_SCALE // unit)
    cap = qty_of_weight(defs, good, config.get("trade.port_capacity") * QUANTITY_SCALE)
    qty = merchant.reach(island, min(qty, cap))
    if qty <= 0:
        return None
    base = initial_prices(defs, config, island.resources).get(good, 1)
    factor = config.get("foreign_merchant.multiplier") * state.get("cpi", 1.0)
    return good, qty, scale_price(base, factor)


def remember(
    island: IslandState,
    unmet: int,
    others_unsold: bool,
    sold: int,
    cpi: float,
    placed: tuple[int, int] | None,
    cut: bool,
) -> None:
    """市場の段の終わりに、次のターンの見方を書く。unmet は買えなかった栄養、others_unsold は商人の
    ほかの売り手に食料の売れ残りがあったか、sold はこのターンに商人が売れた量、placed は売りに
    出した (量, 値段)、cut は計画経済の差額の予算で量を削ったか。"""
    state = state_of(island)
    state["seen"] = {"unmet": unmet, "others_unsold": others_unsold, "sold": sold, "cpi": cpi}
    if placed is not None:
        state["last"] = {"qty": placed[0], "price": placed[1], "sold": sold, "cut": cut}
    else:
        state.pop("last", None)
