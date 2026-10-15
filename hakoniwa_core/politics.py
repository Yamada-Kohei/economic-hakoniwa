"""政治（フェーズ3-4）の共通の部品: 政治の状態と、体制の特色。

段と命令（政情・選挙・議会・憲法改正・報道の統制・労働の動員）は hakoniwa_core/modules/politics、
計画経済（公定価格・生産目標・配給）は hakoniwa_core/economy/plan.py にあります。ほかの段は、ここの
関数で体制の特色と政治の状態を読みます（行政力・工期・経験・幸福度の上限・混乱期間の生産性など）。

体制の特色は regimes.yaml の各体制の traits に書き、島が選んだ体制（軸ごとに1つ）の特色を合わせて
使います（traits）。足し合わせるもの（政情の補正 stability_bonus・行政力 admin_bonus）、掛け合わせる
もの（工期の倍率 works_turns・priority_build_turns、経験の伸び priority_experience、ぜいたく品の
満足 luxury_variety）、小さいほうを取るもの（幸福度の上限 happiness_cap）、どれかが真なら真のもの
（議会 parliament・労働の動員 mobilization ほか）があります。報道の統制 press_control は、どれかが
偽なら偽です。

島の政治の状態は ``island.ext["politics"]`` に、JSON にそのまま保存できる形で置きます。

- stability: 政情（0〜100）、level: 段階（calm・demo・riot・rebellion）
- target・parts・facts: このターンの目標と、その内訳と元にした数（画面）
- support: 支持率（0〜100）、history: 支持率の元にする直近の失業率
- seats: 与党の議席の割合（民主主義だけ。0〜1）、elected: 選挙のときの支持率、
  election: 次の選挙のターン、elections: 選挙の履歴
- votes: 議会の採決の履歴（新しい順）
- press: 報道と集会の扱い（free・limited・controlled）、grievance: たまった不満
- police: 警察の人手が足りている割合（0〜1。画面）
- events: 出来事の補正 [{"kind", "value", "since", "until"}]
- transition: 混乱期間 {"since", "until", "kind"}、productivity: 混乱期間の生産性の倍率
- bonus_until: 共和制の選挙の補正が続く最後のターン
- monarch_age: 君主の年齢（君主制）
- building: 首都の建物の見た目（体制を変えて建て替えている間は前の体制のもの）、
  rebuild: 建て替えの残りの資材 {財: 量}、rebuild_stock: 建て替えのために買えた資材（在庫の形）
- mobilized: 労働の動員で働く人の割合に足す分（全体主義。0 は動員していない）
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import IslandState

KEY = "politics"

CALM = "calm"  # 落ち着いている
DEMO = "demo"  # デモ
RIOT = "riot"  # 暴動
REBELLION = "rebellion"  # 反乱の前触れ
LEVELS = (CALM, DEMO, RIOT, REBELLION)

FREE = "free"  # 報道と集会: 自由
LIMITED = "limited"  # 制限
CONTROLLED = "controlled"  # 統制
PRESS = (FREE, LIMITED, CONTROLLED)

_SUMS = ("stability_bonus", "admin_bonus")
_PRODUCTS = ("works_turns", "priority_build_turns", "priority_experience", "luxury_variety")
_ALL = ("press_control",)  # どれかが偽なら偽


def traits(island: IslandState, defs: Defs) -> dict[str, Any]:
    """島の体制（軸ごとの選択）の特色を合わせたもの。"""
    out: dict[str, Any] = {}
    for axis in sorted(island.regime):
        spec = defs.regimes.get(island.regime[axis], {})
        for name, value in (spec.get("traits") or {}).items():
            if name in _SUMS:
                out[name] = out.get(name, 0) + value
            elif name in _PRODUCTS:
                out[name] = out.get(name, 1.0) * value
            elif name == "happiness_cap":
                out[name] = min(out.get(name, value), value)
            elif name in _ALL:
                out[name] = out.get(name, True) and bool(value)
            else:
                out[name] = value
    return out


def trait(island: IslandState, defs: Defs, name: str, default: Any = None) -> Any:
    return traits(island, defs).get(name, default)


def state_of(island: IslandState) -> dict[str, Any]:
    return island.ext.setdefault(KEY, {})


def started(island: IslandState) -> bool:
    """政情の計算が始まっているか（政情の段か移し替えが、初めの値を置いたか）。"""
    return "stability" in island.ext.get(KEY, {})


def level_of(stability: float, config: GameConfig) -> str:
    """政情の値の段階（game.yaml の politics.levels の境で）。"""
    bounds = config.get("politics.levels")
    if stability >= bounds["demo"]:
        return CALM
    if stability >= bounds["riot"]:
        return DEMO
    if stability >= bounds["rebellion"]:
        return RIOT
    return REBELLION


def level(island: IslandState) -> str:
    return island.ext.get(KEY, {}).get("level", CALM)


def in_transition(island: IslandState, turn: int) -> bool:
    """体制を変えた後の混乱期間の中か。"""
    t = island.ext.get(KEY, {}).get("transition")
    return t is not None and turn <= t["until"]


def productivity_factor(island: IslandState) -> float:
    """混乱期間の生産性の倍率（政情の段が毎ターン書く。混乱期間の外は 1）。"""
    return island.ext.get(KEY, {}).get("productivity", 1.0)


def adjust_admin(
    island: IslandState, defs: Defs, config: GameConfig, power: int, low: int, high: int
) -> int:
    """体制と政情で行政力を足し引きする: 体制の admin_bonus（権威主義 +1）を足し、デモは −1、
    暴動は半分、反乱の前触れは 4 分の 1（どれも最低 low）。"""
    bonus = trait(island, defs, "admin_bonus", 0)
    power = max(low, min(high + max(0, bonus), power + bonus))
    now = level(island)
    if now == DEMO:
        power -= 1
    elif now == RIOT:
        power //= 2
    elif now == REBELLION:
        power //= 4
    return max(low, power)


def rebuild_stock(island: IslandState) -> dict[str, list[list[int]]]:
    """首都の建物の建て替えのために市場で買えた資材（在庫の形。ほかの国の工事には使わない）。"""
    return state_of(island).setdefault("rebuild_stock", {})


def has_parliament(island: IslandState, defs: Defs) -> bool:
    return bool(trait(island, defs, "parliament", False))


def mobilized_share(island: IslandState) -> float:
    """労働の動員で、住民のうち働く人の割合に足す分（動員していなければ 0）。"""
    return island.ext.get(KEY, {}).get("mobilized", 0.0)


def happiness_cap(island: IslandState, defs: Defs) -> int:
    return int(trait(island, defs, "happiness_cap", 100))


def capital_building(island: IslandState, defs: Defs) -> str | None:
    """首都の建物の見た目（体制を変えて建て替えている間は、前の体制の建物）。"""
    found = island.ext.get(KEY, {}).get("building")
    if found:
        return str(found)
    spec = defs.regimes.get(island.regime.get("politics", ""), {})
    return spec.get("capital_building")


def gini(groups: Mapping[str, tuple[int, float]]) -> float:
    """層ごとの (人数, 1人あたりの所得) から出す格差の指数（ジニ係数。層の中はみな同じと見る）。"""
    rows = [(n, max(0.0, y)) for n, y in groups.values() if n > 0]
    people = sum(n for n, _ in rows)
    total = sum(n * y for n, y in rows)
    if people <= 0 or total <= 0:
        return 0.0
    gap = sum(a * b * abs(ya - yb) for a, ya in rows for b, yb in rows)
    return gap / (2 * people * total)


def income_groups(island: IslandState, turns: int) -> dict[str, tuple[int, float]]:
    """家計の層ごとの (人数, 直近 turns ターンの1人あたりの可処分所得の平均)。"""
    out = {}
    for name, h in sorted(island.economy.households.items()):
        history = h.get("income", [])[-turns:]
        persons = h.get("persons", 0)
        if not history or persons <= 0:
            continue
        out[name] = (persons, sum(history) / len(history) / persons)
    return out
