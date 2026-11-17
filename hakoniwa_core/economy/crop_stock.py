"""作物の買い置き（MK-48。W-285 の 3b）。

育つ期間のある作物（畑の作り方の産出物。穀物・綿・嗜好作物）を原料に使う施設は、ふだんの原料の
在庫（MK-47 の目安 N = u ＋ S*）の上に、見込みで買い置き、見込みが落ちれば余りを売りに出す。
持ち手の決まりは 1 つの比べ: t ターン持ったときの見込みの値段 p*(t)（prices.expected_path。
作付けの見直し MK-46 と同じ部品）から持つ費用 c(t) = 今の値段 × 預金金利 × t を引いたものが、
今の値段より高いか。

- t* = その比べが成り立つ一番長い t（作物の育つ期間 H まで。日持ちの期間を超えては持たない）。
- 買い置きの目安 = max(0, u × (t* − 1) − S*)（t* ターンの間に使う分のうち、ふだんの分で持たない
  分）。手元の在庫のうちふだんの分を超えた量との差を market.inventory_adjust の割合ずつ埋める。
- どの t でも比べが成り立たなければ（t* = 0）、ふだんの分を超えた在庫を p*(H) − c(H) の指値で
  売りに出す。
- 自分の稼ぎで回す施設（is_self_supporting）だけ。国庫の項目で買う施設と計画経済の島は持たない。

現実: 1943 年のベンガルでは、収穫の落ち込みがまだ小さいうちに米の値段が数倍に上がった。買いだめと
売り惜しみが値段を先に押し上げ、収穫が見えると吐き出されて値段が下がった。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.economy.prices import expected_path
from hakoniwa_core.state import IslandState
from hakoniwa_core.units import BASIS

EXT = "crop_stock"  # economy.ext の記録: 財 → そのターンの見込みと買い置き・売りの量
SCRATCH = "crop_outlook"


def crops(defs: Any) -> dict[str, int]:
    """育つ期間のある作物 → 育つ期間 H（その作物を作る作り方の一番長い育つ期間）。"""
    out: dict[str, int] = {}
    for fdef in defs.facilities.values():
        for recipe in fdef["recipes"].values():
            grow = int(recipe.get("grow_turns", 0))
            if grow <= 0:
                continue
            for good in recipe["outputs"]:
                out[good] = max(out.get(good, 0), grow)
    return out


def holding_rate(island: IslandState) -> float:
    """1ターン持つ費用の割合（寝かせたお金の預金金利。島の預金金利の平均を月割り）。"""
    return island.policy.deposit_rate / BASIS / TURNS_PER_YEAR


def outlook(island: IslandState, ctx: Any, good: str) -> dict[str, Any]:
    """財 good の見込み（このターンに 1 度だけ出して scratch に置く）。

    返す物: p（今の値段）、t（t*）、limit（t* の見込みの値段 − 持つ費用。買い置きの指値の上限）、
    sell（t* = 0 のときの売りの指値 p*(H) − c(H)。それ以外は None）、ratio（見通しの比
    p ÷ p*(H)。1 以上なら値上がりを見込んでいない）。"""
    cache = island.scratch.setdefault(SCRATCH, {})
    if good in cache:
        return cache[good]
    defs, config = ctx.defs, ctx.config
    horizon = crops(defs).get(good, 0)
    life = defs.goods.get(good, {}).get("shelf_life")
    if life is not None:
        horizon = min(horizon, max(0, int(life) - 1))  # 日持ちを過ぎるまでは持たない
    p = island.economy.prices.get(good, 0)
    result: dict[str, Any] = {"p": p, "t": 0, "limit": 0, "sell": None, "ratio": 1.0}
    if p <= 0 or horizon <= 0:
        cache[good] = result
        return result
    path = expected_path(island, good, horizon, defs, config, turn=ctx.turn)
    rate = holding_rate(island)
    best = 0
    for t, value in enumerate(path, start=1):
        if value - p * rate * t > p:
            best = t
    result["ratio"] = p / path[-1] if path and path[-1] > 0 else 1.0
    if best > 0:
        result["t"] = best
        result["limit"] = int(path[best - 1] - p * rate * best)
    else:
        result["sell"] = max(1, int(path[-1] - p * rate * len(path)))
    cache[good] = result
    return result


def extra_goal(use: int, buffer: int, t_star: int) -> int:
    """ふだんの分（u ＋ S*）の上に持つ買い置きの目安: max(0, u × (t* − 1) − S*)。"""
    if t_star <= 1:
        return 0
    return max(0, use * (t_star - 1) - buffer)


def record(island: IslandState, good: str, view: dict[str, Any], bought: int, sold: int) -> None:
    """そのターンの見込みと、買い置きの注文・売りに出した量を残す（測りで別の数として見る）。"""
    row = island.economy.ext.setdefault(EXT, {}).setdefault(good, {"buy": 0, "sell": 0})
    row.update({"t": view["t"], "ratio": round(view["ratio"], 4)})
    row["buy"] += bought
    row["sell"] += sold


def reset(island: IslandState) -> None:
    """市場の段の初めに、前のターンの記録を消す。"""
    island.economy.ext.pop(EXT, None)
