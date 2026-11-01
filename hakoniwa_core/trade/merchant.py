"""海外の商人（フェーズ3-1）。

島の市場の段に「決まった値段・1ターンの上限つきの売り手」として加わります。港も船も為替も要らず、
島に貿易の仕組みを置いたターン（建国、前のフェーズの島は移し替え）から foreign_merchant.turns
ターンの間だけ来ます。foreign_merchant.taper_from ターンを過ぎると、1ターンに売る量と買う量を
同じ割合で直線に減らし、foreign_merchant.turns ターンで 0 になって去ります（2026-11-01 本人の
カード「0まで減らして去る」。前は 120 ターンで急に去り、最後に値段を上げていた）。

- 値段: 初めの値段（定義のレシピの原価 + 1 割。どの島も同じ）× foreign_merchant.multiplier × 島の
  物価（小売りの物価指数。建国のとき 1）。島の物価が上がれば、商人の値段も同じだけ上がります（外国
  の値段は島の物価と為替で動くが、商人には為替がないので、島の物価で代える。W-203 の一番小さい形）。
- 品目: foreign_merchant.goods。1ターンの上限は、その財を作る標準の施設（定義の作り方のうち一番
  多く作るもの）1つの全力の産出に、減らす割合（taper）を掛けた量です。
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
from hakoniwa_core.economy import retail
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
    """初めの値段に掛ける倍率（foreign_merchant.multiplier × 島の物価）。"""
    return config.get("foreign_merchant.multiplier") * retail.cpi(island)


def taper(island: IslandState, config: GameConfig, turn: int) -> float:
    """商人が持ってくる・持ち帰る量の割合（1 から、taper_from ターンを過ぎると直線に減り、来なくなる
    ターンで 0）。来ていなければ 0。"""
    if not active(island, config, turn):
        return 0.0
    since = island_state(island)["merchant_from"]
    start, end = config.get("foreign_merchant.taper_from"), config.get("foreign_merchant.turns")
    age = turn - since
    if age <= start or end <= start:
        return 1.0
    return max(0.0, (end - age) / (end - start))


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
    share = taper(island, config, turn)
    out = []
    for good in sorted(config.get("foreign_merchant.goods")):
        if good not in defs.goods:
            continue
        qty = int(_reach(island, cap_of(defs, good)) * share)
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
    factor *= retail.cpi(island)  # 買い値も島の物価に合わせる（売り値と同じ）
    share = taper(island, config, turn)
    base = initial_prices(defs, config)
    out = []
    for good in sorted(config.get("foreign_merchant.goods")):
        if good not in defs.goods:
            continue
        qty = int(_reach(island, cap_of(defs, good)) * share)
        if qty > 0:
            out.append((good, qty, scale_price(base.get(good, 1), factor)))
    return out
