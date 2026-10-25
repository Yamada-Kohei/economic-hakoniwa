"""サーバーで使うゲーム（設定・定義データ・モジュール）。一度だけ組み立てて使い回す。

国の大きさの倍（段6 A3）は世界ごとに決まります。世界があれば、その拡張欄の倍で設定を読みます。
倍を入れる前に作った世界は拡張欄に倍がないので、倍 1 のままです（hakoniwa_core/scale.py）。
"""

from __future__ import annotations

from functools import cache

from django.conf import settings
from django.db import DatabaseError

from hakoniwa_core import scale
from hakoniwa_core.game import Game, load_game


def world_scale() -> float | None:
    """保存した世界の倍。世界がまだなければ None（game.yaml の倍で作る）。"""
    from server.world.models import World

    try:
        world = World.objects.order_by("id").only("ext").first()
    except DatabaseError:  # 表を作る前（migrate の途中）
        return None
    if world is None:
        return None
    return float((world.ext or {}).get(scale.WORLD_KEY, 1))


@cache
def get_game() -> Game:
    return load_game(settings.HAKONIWA_GAME_CONFIG, world_scale())
