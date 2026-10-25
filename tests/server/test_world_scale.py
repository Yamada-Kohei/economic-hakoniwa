"""世界ごとの国の大きさの倍（段6 A3）: 倍を入れる前の世界は倍 1 のまま動く。"""

from __future__ import annotations

from hakoniwa_core import scale
from server import game as server_game
from server.world.models import World


def test_new_world_records_its_scale(world):
    assert world.ext[scale.WORLD_KEY] == 1  # 試験は HK_SCALE=1


def test_old_world_without_scale_loads_at_one(db, monkeypatch):
    monkeypatch.delenv(scale.ENV, raising=False)
    World.objects.create(seed=1, ext={})  # 倍を入れる前の世界
    assert server_game.world_scale() == 1


def test_world_scale_wins_over_game_yaml(db, monkeypatch):
    monkeypatch.delenv(scale.ENV, raising=False)
    World.objects.create(seed=1, ext={scale.WORLD_KEY: 20})
    assert server_game.world_scale() == 20
    server_game.get_game.cache_clear()
    try:
        assert scale.people(server_game.get_game().config) == 20
    finally:
        server_game.get_game.cache_clear()


def test_no_world_uses_game_yaml(db):
    assert server_game.world_scale() is None
