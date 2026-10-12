"""サーバーで使うゲーム（設定・定義データ・モジュール）。一度だけ組み立てて使い回す。"""

from __future__ import annotations

from functools import cache

from django.conf import settings

from hakoniwa_core.game import Game, load_game


@cache
def get_game() -> Game:
    return load_game(settings.HAKONIWA_GAME_CONFIG)
