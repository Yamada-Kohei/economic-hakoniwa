"""作物の買い置き（MK-48。W-285 の 3b）。

育つ期間のある作物（畑の作り方の産出物。穀物・綿・嗜好作物）を原料に使う施設は、ふだんの原料の
在庫（MK-47 の目安 N = u ＋ S*）の上に、見込みで買い置き、見込みが落ちれば余りを売りに出す。
持ち手の決まりは 1 つの比べ: t ターン持ったときの見込みの値段 p*(t)（prices.expected_path。
作付けの見直し MK-46 と同じ部品）から持つ費用 c(t)（預金の利子と同じ複利の金利だけ）を引いた
ものが、今の値段より高いか。

- t* = 儲け p*(t) − 今の値段 − c(t) が一番大きい t（同じなら短い方。作物の育つ期間 H まで、日持ちの
  期間を超えては持たない。掲示板 #1366）。儲けがどの t でも 0 以下なら t* = 0。
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
RATE_RULE = "monthly_compound"  # 持つ費用の金利の数え方（預金の利子と同じ月ごとの複利）


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


def cost(price: int, island: IslandState, turns: int) -> float:
    """今の値段 price の物を turns ターン持つ費用: その代金を預けておけば付いた利子（島の預金金利の
    平均。預金の利子と同じく毎ターン 年利 ÷ 12 が付いて元に入る複利）。倉の費用や目減りは足さない
    （掲示板 #1366）。"""
    rate = island.policy.deposit_rate / BASIS / TURNS_PER_YEAR
    return price * ((1 + rate) ** turns - 1)


def outlook(island: IslandState, ctx: Any, good: str) -> dict[str, Any]:
    """財 good の見込み（このターンに 1 度だけ出して scratch に置く）。

    返す物: p（今の値段）、t（t*: 持たない t = 0 の儲け 0 も入れて、儲け p*(t) − p − c(t) が一番
    大きい t。同じなら短い方）、gain（その儲け）、capped（t* が見る期間の端か）、tie（同じ儲けの
    t がほかにもあったか）、sell（t* = 0 のときの売りの指値 p*(H) − c(H)。それ以外は None）、
    ratio（見通しの比 p ÷ p*(H)。1 以上なら値上がりを見込んでいない）。見込みには、このターンの
    買い置きの注文を入れない（掲示板 #1366）。"""
    cache = island.scratch.setdefault(SCRATCH, {})
    if good in cache:
        return cache[good]
    defs, config = ctx.defs, ctx.config
    horizon = crops(defs).get(good, 0)
    life = defs.goods.get(good, {}).get("shelf_life")
    if life is not None:
        horizon = min(horizon, max(0, int(life) - 1))  # 日持ちを過ぎるまでは持たない
    p = island.economy.prices.get(good, 0)
    result: dict[str, Any] = {
        "p": p,
        "t": 0,
        "sell": None,
        "ratio": 1.0,
        "gain": 0.0,
        "capped": False,
        "tie": False,
    }
    if p <= 0 or horizon <= 0:
        cache[good] = result
        return result
    path = expected_path(island, good, horizon, defs, config, turn=ctx.turn)
    # t = 0（持たない）の儲け 0 も候補に入れ、儲けが一番大きい t を選ぶ（同じなら短い方。#1371）
    gains = [0.0] + [value - p - cost(p, island, t) for t, value in enumerate(path, start=1)]
    top = max(gains)
    best = gains.index(top)
    result["ratio"] = p / path[-1] if path and path[-1] > 0 else 1.0
    result.update(
        {
            "t": best,
            "gain": round(top, 1),
            "capped": best == horizon,  # 見る期間の端で止まった
            "tie": gains.count(top) > 1,  # 同じ儲けの t がほかにもあり、短い方を選んだ
        }
    )
    if best == 0:
        result["sell"] = max(1, int(path[-1] - cost(p, island, len(path))))
    cache[good] = result
    return result


def extra_goal(use: int, buffer: int, t_star: int) -> int:
    """ふだんの分（u ＋ S*）の上に持つ買い置きの目安: max(0, u × (t* − 1) − S*)。"""
    if t_star <= 1:
        return 0
    return max(0, use * (t_star - 1) - buffer)


def record(
    island: IslandState,
    good: str,
    view: dict[str, Any],
    bought: int,
    sold: int,
    holder: tuple[int, int, int] | None = None,
) -> None:
    """そのターンの見込みと、買い置きの注文・売りに出した量を残す（測りで別の数として見る）。
    holder は買い置きした施設の (番号, 注文の量, 注文の前に使えたお金)（掲示板 #1366: 誰が
    買い置いたかと預金の多さの偏りを測りで見る）。"""
    row = island.economy.ext.setdefault(EXT, {}).setdefault(good, {"buy": 0, "sell": 0})
    keys = ("t", "gain", "capped", "tie")
    row.update({k: view[k] for k in keys} | {"ratio": round(view["ratio"], 4), "rate": RATE_RULE})
    row["buy"] += bought
    row["sell"] += sold
    if holder is not None:
        number, qty, money = holder
        row.setdefault("holders", {})[str(number)] = [qty, money]


def reset(island: IslandState) -> None:
    """市場の段の初めに、前のターンの記録を消す。"""
    island.economy.ext.pop(EXT, None)
