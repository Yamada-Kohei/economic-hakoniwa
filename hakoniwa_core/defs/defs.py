"""読み込み済みの定義データ。コアは定義を必ずここから読みます。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PackInfo:
    name: str
    version: str
    requires: tuple[str, ...]
    path: Path


@dataclass(frozen=True)
class Defs:
    """書き換えられない定義データ。

    tables["facilities"]["steel_mill"] のように、種類と名前で引きます。
    量（財）は 1000 分の 1 単位の整数に直してあります。
    """

    tables: Mapping[str, Mapping[str, Mapping[str, Any]]]
    locales: Mapping[str, Mapping[str, Mapping[str, str]]]
    packs: tuple[PackInfo, ...]

    @property
    def goods(self) -> Mapping[str, Mapping[str, Any]]:
        return self.tables["goods"]

    @property
    def terrain(self) -> Mapping[str, Mapping[str, Any]]:
        return self.tables["terrain"]

    @property
    def facilities(self) -> Mapping[str, Mapping[str, Any]]:
        return self.tables["facilities"]

    @property
    def orders(self) -> Mapping[str, Mapping[str, Any]]:
        return self.tables["orders"]

    @property
    def settlements(self) -> Mapping[str, Mapping[str, Any]]:
        return self.tables["settlements"]

    @property
    def workers(self) -> Mapping[str, Mapping[str, Any]]:
        return self.tables["workers"]

    @property
    def infrastructure(self) -> Mapping[str, Mapping[str, Any]]:
        return self.tables["infrastructure"]

    def infra_kinds(self, role: str) -> list[str]:
        """インフラの種類のうち、効き目が role（transport・water・power）のもの（定義した順）。"""
        return list(self._infra_roles.get(role, ()))

    @cached_property
    def _infra_roles(self) -> dict[str, tuple[str, ...]]:
        roles: dict[str, list[str]] = {}
        for kind, spec in self.infrastructure.items():
            roles.setdefault(spec["role"], []).append(kind)
        return {role: tuple(kinds) for role, kinds in roles.items()}

    @property
    def regimes(self) -> Mapping[str, Mapping[str, Any]]:
        return self.tables["regimes"]

    @property
    def start_island(self) -> Mapping[str, Mapping[str, Any]]:
        return self.tables["start_island"]

    @property
    def naming(self) -> Mapping[str, Mapping[str, Any]]:
        return self.tables["naming"]

    @property
    def finance(self) -> Mapping[str, Mapping[str, Any]]:
        return self.tables["finance"]

    @property
    def climate(self) -> Mapping[str, Mapping[str, Any]]:
        """気候帯（フェーズ3-2。climate.yaml）。"""
        return self.tables["climate"]

    def journal_kinds(self) -> list[str]:
        """仕訳の種類（finance.yaml の type: journal）。"""
        return [k for k, v in self.finance.items() if v["type"] == "journal"]

    def bond_terms(self) -> dict[str, Mapping[str, Any]]:
        """国債の期間（finance.yaml の type: bond_term）。"""
        return {k: v for k, v in self.finance.items() if v["type"] == "bond_term"}

    def regime_axes(self) -> dict[str, list[str]]:
        """体制の軸ごとの選択肢（定義した順）。例: {"politics": ["democracy", ...], ...}"""
        axes: dict[str, list[str]] = {}
        for rid, regime in self.regimes.items():
            axes.setdefault(regime["axis"], []).append(rid)
        return axes

    def mining_resources(self) -> list[str]:
        """建国時に選べる鉱業資源（定義した順）。"""
        return [gid for gid, good in self.goods.items() if good.get("mining")]

    def industries(self) -> list[str]:
        """施設の産業の分類（facilities.yaml の industry。定義に出てくる順。フェーズ3-3）。"""
        return list(self._industries)

    @cached_property
    def _industries(self) -> tuple[str, ...]:
        found: dict[str, None] = {}
        for spec in self.facilities.values():
            if spec.get("industry"):
                found[spec["industry"]] = None
        return tuple(found)

    def settlement_levels(self) -> list[str]:
        """町（住宅地）の段階を、小さい順に。"""
        return list(self._settlement_levels)

    @cached_property
    def _settlement_levels(self) -> tuple[str, ...]:
        return tuple(sorted(self.settlements, key=lambda s: self.settlements[s]["min_population"]))

    def recipe_name(self, facility: str, recipe: str, lang: str = "ja") -> str:
        """作り方の表示名。recipes の表示名がなければ、作るものの名前を並べる。"""
        key = f"{facility}_{recipe}"
        names = self.locales.get(lang, {}).get("recipes", {})
        if key in names:
            return names[key]
        outputs = self.facilities[facility]["recipes"][recipe]["outputs"]
        return "・".join(self.name("goods", g, lang) for g in outputs) or recipe

    def unit_name(self, good: str, lang: str = "ja") -> str:
        """資源の量の単位の表示名（トンなど）。単位を決めていなければ空。"""
        unit = self.goods.get(good, {}).get("unit")
        return self.name("units", unit, lang) if unit else ""

    def display_scale(self, good: str) -> float:
        """見せるときに量に掛ける倍率（goods.yaml の display_scale。書かなければ 1。W-30）。"""
        return self.goods.get(good, {}).get("display_scale", 1)

    def weight_scale(self) -> float:
        """船の荷の重さ（トン。freight_weight）を見せるときの倍率（トンで数える財と同じ）。"""
        return next(
            (g.get("display_scale", 1) for g in self.goods.values() if g.get("unit") == "ton"), 1
        )

    def shown_amount(self, good: str, value: float) -> float:
        """縮めた量（1 単位で数えた数）を、見せる量に。"""
        return value * self.display_scale(good)

    def shown_price(self, good: str, price: float) -> float:
        """1 単位あたりの値段を、見せる単位 1 つあたりの値段に（100 より安ければ小数第1位まで）。"""
        scale = self.display_scale(good)
        if scale == 1:
            return price
        value = price / scale
        return round(value) if abs(value) >= 100 else round(value, 1)

    def game_amount(self, good: str, value: float) -> float:
        """見せる量で打ち込まれた量を、縮めた量に戻す。"""
        return value / self.display_scale(good)

    def game_price(self, good: str, value: float) -> int:
        """見せる単位 1 つあたりで打ち込まれた値段を、1 単位あたりの値段（整数）に戻す。"""
        return round(value * self.display_scale(good))

    def name(self, kind: str, entry_id: str, lang: str = "ja") -> str:
        """表示名。見つからなければ名前そのものを返す。"""
        return self.locales.get(lang, {}).get(kind, {}).get(entry_id, entry_id)

    def pack_versions(self) -> dict[str, str]:
        """世界に保存しておく、読み込んだパックとその版。"""
        return {p.name: p.version for p in self.packs}
