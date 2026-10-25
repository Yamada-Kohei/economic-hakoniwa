"""サーバー設定 game.yaml の読み込み。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hakoniwa_core import scale


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
        """ "orders.base_admin_power" のような名前で数値を取り出す。

        ターンの中で何十万回も呼ばれるので、名前の区切りだけを覚えておく（値は毎回 raw から引く
        ので、raw を書き換えても古い値は返さない）。
        """
        parts = _PARTS.get(dotted)
        if parts is None:
            parts = _PARTS[dotted] = tuple(dotted.split("."))
        value: Any = self.raw
        try:
            for part in parts:
                value = value[part]
        except (KeyError, TypeError, IndexError):  # 名前がない・途中が表でない
            raise KeyError(f"game.yaml に {dotted} がありません") from None
        return value


# 設定の名前ごとの区切り（GameConfig.get）
_PARTS: dict[str, tuple[str, ...]] = {}


def load_config(path: str | Path) -> GameConfig:
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw = scale.apply_config(raw)  # 国の大きさの倍（段6 A3）
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
