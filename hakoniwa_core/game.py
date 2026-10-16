"""設定・定義データ・モジュールをまとめて組み立てる入口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hakoniwa_core.config import GameConfig, default_config_path, load_config
from hakoniwa_core.dates import Calendar, world_calendar
from hakoniwa_core.defs import Defs, load_defs
from hakoniwa_core.modules import build_registry
from hakoniwa_core.registry import Registry
from hakoniwa_core.turn import Engine


@dataclass(frozen=True)
class Game:
    config: GameConfig
    defs: Defs
    registry: Registry
    engine: Engine
    calendar: Calendar  # 世界の暦（game.yaml の calendar）


def load_game(config_path: str | Path | None = None) -> Game:
    config = load_config(config_path or default_config_path())
    defs = load_defs(config)
    registry = build_registry(defs)
    return Game(
        config=config,
        defs=defs,
        registry=registry,
        engine=Engine(defs, config, registry),
        calendar=world_calendar(config),
    )
