"""施設の生産性（フェーズ2-3）: インフラ・教育・汚染の係数。

係数は f = 1 + w ×（low + (high − low) × L − 1）です（low と high は game.yaml の
infrastructure.factor。既定の 0.7 と 1.2 なら、L が 0.6 で 1、0 で 1 − 0.3w、1 で 1 + 0.2w）。
w はレシピの factors（infra・education）で、L は次のとおりです。

- インフラ: 施設のマスと周り8マスの陸のマスごとの交通の整備度（道路と鉄道の高い方）の平均。
  電力の届き方は送電網で別に効くので、送電網の整備度は入れません。
- 教育: 島の教育水準（economy.education）。

農場と漁港には汚染の効き目も掛け、施設のマスの汚染1あたり産出を w（factors の pollution）だけ
下げます（最低で infrastructure.pollution_floor）。

フェーズ3-2 から、自然条件の係数も掛けます（hakoniwa_core/nature.py）。factors の climate は島の
気候帯の産出の倍率、fertility は施設のマスの肥沃度で、どちらも 1 + w ×（値 − 1）です。

フェーズ3-3 から、経験と集積の係数も掛けます（hakoniwa_core/economy/experience.py）。経験は作り方の
施設の産業（industry）の島の累積で、集積は施設のマスの周りの同じ産業の施設の数で決まります。どちらも
factors の重みは使わず、産業のある施設にいつも掛けます。

フェーズ3-4 から、体制を変えた後の混乱期間の倍率（hakoniwa_core/politics.py の
productivity_factor）をどの施設にも掛けます。

係数を掛けた値を施設の生産性として economy["productivity"]（1000 分の 1）に残し、生産の段の
産出の量、発電所の能力、変動費の利益の見込み（economy/prices.py）、操業目標に掛けます。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, nature, politics
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import experience
from hakoniwa_core.geo import NEIGHBORS8
from hakoniwa_core.state import Facility, IslandState

FULL = 1000


def infra_level(island: IslandState, defs: Defs, x: int, y: int) -> int:
    """(x, y) とその周り8マスの陸のマスの、交通の整備度の平均（1000 分の 1）。"""
    total = count = 0
    for dx, dy in ((0, 0), *NEIGHBORS8):
        px, py = x + dx, y + dy
        if not island.in_bounds(px, py):
            continue
        tile = island.tile(px, py)
        if not fac.is_land(defs, tile.terrain):
            continue
        total += infra.transport(defs, tile)
        count += 1
    return total // count if count else 0


def factor(weight: float, level: int, config: GameConfig) -> float:
    """係数 1 + w ×（low + (high − low) × L − 1）。level は 1000 分の 1。"""
    low, high = config.get("infrastructure.factor")
    return 1 + weight * (low + (high - low) * level / FULL - 1)


def productivity(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    recipe: Mapping[str, Any],
    x: int,
    y: int,
) -> int:
    """作り方 recipe の施設を (x, y) に置いたときの生産性（1000 分の 1）。

    (x, y) が島の外なら、場所で決まるインフラと汚染の係数は 1 と見ます（場所を決める前の見込み）。
    """
    weights = recipe.get("factors") or {}
    industry = recipe.get("industry")
    if not island.has_economy:
        return FULL
    # 体制を変えた後の混乱期間（フェーズ3-4）は、どの施設にも掛ける
    value = politics.productivity_factor(island)
    if not weights and not industry:
        return max(1, round(value * FULL))
    if weights.get("infra") and island.in_bounds(x, y):
        value *= factor(weights["infra"], infra_level(island, defs, x, y), config)
    if weights.get("education"):
        value *= factor(weights["education"], island.economy.education, config)
    if weights.get("pollution") and island.in_bounds(x, y):
        floor = config.get("infrastructure.pollution_floor")
        value *= max(floor, 1 - weights["pollution"] * island.tile(x, y).pollution)
    if weights.get("climate"):
        value *= 1 + weights["climate"] * (
            nature.recipe_multiplier(defs, config, island, recipe) - 1
        )
    if weights.get("fertility") and island.in_bounds(x, y):
        rich = nature.fertility(island.tile(x, y)) / FULL
        value *= 1 + weights["fertility"] * (rich - 1)
    if industry:
        value *= experience.factor(island, config, industry)
        value *= experience.cluster_factor(island, defs, config, recipe, x, y)
    return max(1, round(value * FULL))


def probe(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    kind: str,
    recipe: str,
    pos: tuple[int, int] | None = None,
) -> Facility:
    """建てる前の見込みの計算に使う仮の施設（生産性を入れておく）。pos がなければ場所によらない
    係数（教育）だけを掛ける。"""
    x, y = pos if pos is not None else (-1, -1)
    f = Facility(id=-1, kind=kind, x=x, y=y, recipe=recipe, built=True)
    value = productivity(island, defs, config, defs.facilities[kind]["recipes"][recipe], x, y)
    if value != FULL:
        f.economy["productivity"] = value
    return f


def update(island: IslandState, defs: Defs, config: GameConfig, f: Facility) -> int:
    """施設 f の生産性を計算し直して economy["productivity"] に残す。"""
    if not f.built or not fac.on_map(f) or fac.is_housing(config, f):
        return FULL
    value = productivity(island, defs, config, fac.recipe_of(defs, f), f.x, f.y)
    if value == FULL:
        f.economy.pop("productivity", None)
    else:
        f.economy["productivity"] = value
    return value
