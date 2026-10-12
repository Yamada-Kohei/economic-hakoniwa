"""サーバー設定 game.yaml の読み込み。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class GameConfig:
    """game.yaml の中身。パスは絶対パスに直してあります。"""

    base_dir: Path
    packs_dirs: tuple[Path, ...]
    packs: tuple[str, ...]
    schemas_dir: Path
    stages: tuple[str, ...]
    formulas: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, dotted: str) -> Any:
        """ "orders.base_admin_power" のような名前で数値を取り出す。"""
        value: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(value, dict) or part not in value:
                raise KeyError(f"game.yaml に {dotted} がありません")
            value = value[part]
        return value


def load_config(path: str | Path) -> GameConfig:
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    base = path.parent
    return GameConfig(
        base_dir=base,
        packs_dirs=tuple(base / p for p in raw.get("packs_dirs", ["packs"])),
        packs=tuple(raw.get("packs", ["base"])),
        schemas_dir=base / raw.get("schemas_dir", "schemas"),
        stages=tuple(raw.get("stages", [])),
        formulas=dict(raw.get("formulas") or {}),
        raw=raw,
    )


def default_config_path() -> Path:
    """リポジトリ直下の game.yaml。"""
    return Path(__file__).resolve().parent.parent / "game.yaml"
