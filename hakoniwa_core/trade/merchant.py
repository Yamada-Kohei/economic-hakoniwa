"""海外の商人（フェーズ3-1）。

島の市場の段に「決まった値段・1ターンの上限つきの売り手」として加わります。港も船も為替も要らず、
島に貿易の仕組みを置いたターン（建国、前のフェーズの島は移し替え）から foreign_merchant.turns
ターンの間だけ来ます。

- 値段: 初めの値段（定義のレシピの原価 + 1 割。どの島も同じ）× foreign_merchant.multiplier。最後の
  foreign_merchant.last_turns ターンで、倍率を last_multiplier まで少しずつ上げます。
- 品目: foreign_merchant.goods。1ターンの上限は、その財を作る標準の施設（定義の作り方のうち一番
  多く作るもの）1つの全力の産出です。
- 売りの代金は自国通貨で払い、島の外に出て消えます（仕訳の種類 merchant。お金が消える場面に数え、
  貿易収支では輸入に入れる）。
- 商人は同じ品目を、初めの値段 × foreign_merchant.buy_multiplier の指値で買いもします（W-186 回4。
  島の外への輸出。上限は売りと同じ）。代金は島の外から入るお金で、中央銀行の帳簿から生まれます（仕訳
  の種類 merchant_export。貿易収支では輸出）。島の値段は、商人の買い値と売り値の間に収まりやすく
  なります。
- 戦争で港が封鎖された島には、封鎖の強さ（naval.blockade_share）の割合だけ少なく来ます（前のターンの
  貿易の段が数えた値。BLOCKADE）。
"""

from __future__ import annotations

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.state import IslandState
from hakoniwa_core.trade import island_state
from hakoniwa_core.units import BASIS, scale_price


def active(island: IslandState, config: GameConfig, turn: int) -> bool:
    settings = config.get("foreign_merchant")
    since = island_state(island).get("merchant_from")
    if not settings.get("enabled") or since is None:
        return False
    return since <= turn < since + settings["turns"]


def turns_left(island: IslandState, config: GameConfig, turn: int) -> int:
    since = island_state(island).get("merchant_from")
    if since is None or not active(island, config, turn):
        return 0
    return since + config.get("foreign_merchant.turns") - turn


def multiplier(island: IslandState, config: GameConfig, turn: int) -> float:
    settings = config.get("foreign_merchant")
    left = turns_left(island, config, turn)
    last = settings["last_turns"]
    base = settings["multiplier"]
    if left > last or last <= 0:
        return base
    done = (last - left + 1) / last  # 最後のターンで last_multiplier になる
    return base + (settings["last_multiplier"] - base) * done


BLOCKADE = "merchant_blockade"  # 島の貿易の状態: 封鎖で商人が来なくなる割合（1万分の1）


def _reach(island: IslandState, qty: int) -> int:
    """封鎖で減らした、商人が持ってくる・持ち帰る量。"""
    share = island_state(island).get(BLOCKADE, 0)
    return qty * max(0, BASIS - share) // BASIS


def cap_of(defs: Defs, good: str) -> int:
    """1ターンに売る上限: その財を作る施設の作り方のうち、一番多く作るものの全力の産出。"""
    best = 0
    for fdef in defs.facilities.values():
        for recipe in fdef["recipes"].values():
            best = max(best, recipe["outputs"].get(good, 0))
    return best


def offers(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> list[tuple[str, int, int]]:
    """このターンに商人が売りに出すもの（財, 量, 値段）。来ていなければ空。"""
    if not active(island, config, turn):
        return []
    base = initial_prices(defs, config)
    factor = multiplier(island, config, turn)
    out = []
    for good in sorted(config.get("foreign_merchant.goods")):
        if good not in defs.goods:
            continue
        qty = _reach(island, cap_of(defs, good))
        if qty > 0:
            out.append((good, qty, scale_price(base.get(good, 1), factor)))
    return out


def bids(
    island: IslandState, defs: Defs, config: GameConfig, turn: int
) -> list[tuple[str, int, int]]:
    """このターンに商人が買う注文（財, 量, 指値）。来ていなければ空。"""
    if not active(island, config, turn):
        return []
    factor = config.get("foreign_merchant").get("buy_multiplier", 0)
    if factor <= 0:
        return []
    base = initial_prices(defs, config)
    out = []
    for good in sorted(config.get("foreign_merchant.goods")):
        if good not in defs.goods:
            continue
        qty = _reach(island, cap_of(defs, good))
        if qty > 0:
            out.append((good, qty, scale_price(base.get(good, 1), factor)))
    return out
