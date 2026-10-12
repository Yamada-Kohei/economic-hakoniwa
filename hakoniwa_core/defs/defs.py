"""読み込み済みの定義データ。コアは定義を必ずここから読みます。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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

    def name(self, kind: str, entry_id: str, lang: str = "ja") -> str:
        """表示名。見つからなければ名前そのものを返す。"""
        return self.locales.get(lang, {}).get(kind, {}).get(entry_id, entry_id)

    def pack_versions(self) -> dict[str, str]:
        """世界に保存しておく、読み込んだパックとその版。"""
        return {p.name: p.version for p in self.packs}
