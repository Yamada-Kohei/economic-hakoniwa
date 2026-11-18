"""作物の売り惜しみ（MK-48。W-285 の 3b の作り直し。掲示板 #1459）。

育つ期間のある作物（畑の作り方の産出物。穀物・綿・嗜好作物）を持つ側は、先に値が上がると見れば、
手元の荷を今の値段では売らずに持つ。いまは自分の稼ぎで回す農場だけ（商社の輸入の荷は、売れ残りの
ターン数で輸出し直す決まり FX-64 との組み方を決めてから。測った島では商社の穀物の荷は 0 だった）。
売りの指値の下限を「見込みの値段 p*(t) を今に割り引いた値の一番高いもの」にする:

    V = max_t p*(t) ÷ (1 ＋ r)^t    （t = 1..T、r = 預金の月の利子。持つ費用はお金を寝かせる分）

- 見込みは持ち手に見える物だけで出す（本当のショックや島じゅうの本当の在庫は使わない）:
  - 農場: 市場の値段と量の記録（誰にでも見える）と、自分の畑と通勤圏の中のほかの畑の作り替え
    （prices.expected_path の seen）。
- T は作物の育つ期間（その作物を作る作り方の一番長い育つ期間。次の収穫で値が戻る）まで、持ち越せる
  期間（shelf_life − 1）を超えない。
- 歯止め:
  1. 倉の大きさ: 農場は全力の産出の T ターン分（1回の作付けの収穫を入れる倉）まで。超えた分は
     ふだんの指値で売る。
  2. 値段が見込みより高ければ売る: 下限 V は割り引いた p* なので、今の値段が V を超えれば売れる
     （別の上乗せの数は置かない）。
  3. 放出: 島の家計が前のターンに食べ物を食べきれなかった（food.ratio < 1）か、値段が前のターンに
     値幅以上跳ねたら、持たずにみな売りに出す（飢えと値の跳ねは誰にでも見える）。
- V が値幅の上端以上なら、持つ分は売りに出さない。上端より下なら、V を指値に別の注文で出す。持つ分の
  売れ残りは、希望価格の見直し（MK-07）・操業目標（MK-09）・見切り売り（MK-41）に数えない。
- 公定価格・値段の上限と下限の財、国庫直轄の施設、計画経済の島は持たない。

現実: 収穫が減ると見込むと、農家と穀物商は出荷を控え、値段が先に上がる（1943 年のベンガル）。
どれだけ持てるかは倉の大きさと持つ費用（利子）で決まり、飢えが広がると世論と取り締まりで吐き出される。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.economy.prices import expected_path
from hakoniwa_core.geo import distance
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.units import BASIS

EXT = "crop_hold"  # economy.ext の記録: 財 → そのターンの持った量・売りに出した量・放出
WITHHELD = "withheld"  # 施設の economy: 財 → このターンに持った量（操業目標で在庫から除く）


def crops(defs: Any) -> dict[str, int]:
    """育つ期間のある作物 → 育つ期間（その作物を作る作り方の一番長い育つ期間）。"""
    out: dict[str, int] = {}
    for fdef in defs.facilities.values():
        for recipe in fdef["recipes"].values():
            grow = int(recipe.get("grow_turns", 0))
            if grow <= 0:
                continue
            for good in recipe["outputs"]:
                out[good] = max(out.get(good, 0), grow)
    return out


def horizon(defs: Any, good: str, grown: dict[str, int]) -> int:
    """持つターン数の上限 T: 育つ期間まで、持ち越せる期間（shelf_life − 1）を超えない。"""
    turns = grown.get(good, 0)
    life = defs.goods.get(good, {}).get("shelf_life")
    if life is not None:
        turns = min(turns, max(0, int(life) - 1))
    return turns


def monthly_rate(island: IslandState) -> float:
    """持つ費用の利子（島の預金金利の月の分。預金の利子と同じく毎ターン 年利 ÷ 12）。"""
    return island.policy.deposit_rate / BASIS / TURNS_PER_YEAR


def released(island: IslandState, good: str, config: Any) -> str | None:
    """放出の合図（歯止め 3）。合図があればそのわけ、なければ None。"""
    ratio = island.stats.get("food.ratio")
    if ratio is not None and ratio < 1:
        return "hunger"
    history = island.economy.price_history.get(good, [])
    if len(history) >= 2 and history[-2] > 0:
        if history[-1] / history[-2] - 1 >= config.get("market.band") - 1e-9:
            return "jump"
    return None


def value(path: list[int], rate: float) -> int:
    """V = max_t p*(t) ÷ (1 ＋ r)^t（p* の列 path は 1 ターン先から）。"""
    if not path:
        return 0
    return int(max(p / (1 + rate) ** t for t, p in enumerate(path, start=1)))


def farm_path(island: IslandState, ctx: Any, f: Facility, good: str, turns: int) -> list[int]:
    """農場に見える見込み: 市場の記録と、自分と通勤圏の中の畑の作り替えだけ。"""
    radius = fac.commute_radius(ctx.defs, ctx.config, f.kind)
    here = (f.x, f.y)

    def seen(other: Facility) -> bool:
        return other is f or (fac.located(other) and distance(here, (other.x, other.y)) <= radius)

    return expected_path(island, good, turns, ctx.defs, ctx.config, turn=ctx.turn, seen=seen)


def farm_capacity(defs: Any, f: Facility, good: str, turns: int) -> int:
    """農場の倉の大きさ: 全力の産出の turns ターン分（1 回の作付けの収穫）。"""
    full = fac.outputs_of(defs, fac.steady(f)).get(good, 0)
    return max(0, full * turns)


def plan(
    island: IslandState,
    config: Any,
    good: str,
    held: int,
    limit: int,
    high: int,
    path: list[int],
    capacity: int,
) -> tuple[int, int | None]:
    """持つ量と、その分の指値（None なら売りに出さない）。持たなければ (0, None)。

    持つのは、割り引いた見込み V が今の島の値段とふだんの指値 limit の両方より高いときだけ（ふだんの
    指値が値段より低い売り手が、見込みが平らでも持つことにならないように）。"""
    if held <= 0 or capacity <= 0 or released(island, good, config) is not None:
        return 0, None
    floor = value(path, monthly_rate(island))
    if floor <= max(limit, island.economy.prices.get(good, 0)):
        return 0, None
    keep = min(held, capacity)
    return keep, (None if floor >= high else floor)


def record(
    island: IslandState,
    good: str,
    holder: Facility,
    kept: int,
    floor: int | None,
    path: list[int],
) -> None:
    """そのターンに持った量（売りに出さなかった分と、下限で出した分）を残す。"""
    row = island.economy.ext.setdefault(EXT, {}).setdefault(
        good, {"kept": 0, "withheld": 0, "offered": 0, "sold": 0, "holders": {}}
    )
    row["kept"] += kept
    if floor is None:
        row["withheld"] += kept
    row["holders"][str(holder.id)] = [kept, floor, path[-1] if path else None]
    holder.economy.setdefault(WITHHELD, {})[good] = kept


def note_release(island: IslandState, good: str, why: str) -> None:
    island.economy.ext.setdefault(EXT, {}).setdefault(good, {}).setdefault("released", why)


def reset(island: IslandState) -> None:
    """市場の段の初めに、前のターンの記録と施設ごとの持った量を消す。"""
    island.economy.ext.pop(EXT, None)
    for f in island.facilities:
        f.economy.pop(WITHHELD, None)
