"""国の住宅（公営住宅。W-190・W-202）: 国の床・国の資材の在庫・命令の工事の列・家賃。

町のマスの床（人数の単位）のうち、国が建てた分を ext["public"] に持つ（残りは家計の床）。国は
予算の項目 housing（住宅費）で資材を市場で買い、国の資材の在庫（economy.housing["public"]）から
床を足す。計画経済の国では家計は住宅の資材を買わず住宅ローンも借りないので、町ができる・育つ・
手入れの資材は国が買う（床はみな国の床になる）。市場・混合の国は、命令 build_housing で国の床を
足せる。

国の床に住む層は、所得の低い層から（失業 → 一般 → 技術者 → 資産を持つ層）望む広さに足りない分を
埋める形で割り当て、使われている国の床 1 人分につき家賃 housing.public_rent（household.base_income
の割合）を毎ターン国庫に払う。家賃は物価に合わせて動かさない（ソ連は長く据え置いた）。払えない分は
国の未収にせず減免する。家賃は物価指数と GDP に入れない。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.economy import ENGINEERS, GENERAL, JOBLESS, OWNERS, Payer
from hakoniwa_core.ledger import GOV
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.units import round_money

HOUSING = "housing"  # 支出の項目（住宅費。game.yaml の fiscal.budget_items）
PAYER = Payer(GOV, HOUSING)
PUBLIC = "public"  # 町のマスの ext と economy.housing の、国の分の名前（国の床は人数の単位）
RENT = "housing_rent"  # 仕訳の種類（家計 → 政府）
# 家賃の払い手の順（所得の低い層から国の床に入る）
TENANTS = (JOBLESS, GENERAL, ENGINEERS, OWNERS)


def planned(island: IslandState) -> bool:
    """計画経済の国か（家計は住宅の資材を買わず、住宅ローンも借りない。国が建てる）。"""
    return island.regime.get("economy") == "planned"


def state(island: IslandState) -> dict[str, Any]:
    """国の住宅の置き場（economy.housing["public"]）: 資材の在庫 stock、次のターンに買う資材
    want・手入れの資材 repair、命令の工事の列 works。"""
    held = island.economy.housing.setdefault(PUBLIC, {})
    held.setdefault("stock", {})
    held.setdefault("want", {})
    held.setdefault("repair", {})
    held.setdefault("works", [])
    return held


def floor_of(home: Facility) -> int:
    """町の床のうち国の床（人数の単位）。"""
    return int(home.ext.get(PUBLIC, 0))


def set_floor(home: Facility, count: int) -> None:
    if count > 0:
        home.ext[PUBLIC] = int(count)
    else:
        home.ext.pop(PUBLIC, None)


def share(home: Facility, floor: int) -> float:
    """町の床のうち国の床の割合（0〜1）。"""
    return min(1.0, floor_of(home) / floor) if floor > 0 else 0.0


def rent_per_floor(config: GameConfig) -> float:
    """国の床 1 人分の 1 ターンの家賃（お金）。household.base_income の housing.public_rent。"""
    return config.get("household.base_income") * config.get("housing.public_rent")


def tenants(layers: dict[str, float], units: float, min_space: float) -> dict[str, float]:
    """国の床 units（人数の単位）に入る層ごとの床（人数の単位）。所得の低い層から、層の望む広さ
    （㎡）を最低の広さで割った床の分だけ入る。"""
    out: dict[str, float] = {}
    left = units
    for name in TENANTS:
        if left <= 0:
            break
        need = layers.get(name, 0.0) / min_space
        if need <= 0:
            continue
        take = min(left, need)
        out[name] = take
        left -= take
    return out


def rent(config: GameConfig, by_layer: dict[str, float]) -> dict[str, int]:
    """層ごとの 1 ターンの家賃（お金）。"""
    unit = rent_per_floor(config)
    return {name: round_money(units * unit) for name, units in by_layer.items() if units > 0}


def works_pending(island: IslandState) -> list[dict[str, Any]]:
    return state(island)["works"]


def tile_total(island: IslandState) -> int:
    """島の国の床の合計（人数の単位）。"""
    return sum(floor_of(f) for f in island.facilities if f.ext.get(PUBLIC))
