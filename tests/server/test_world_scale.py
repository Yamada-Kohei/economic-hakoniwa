"""世界ごとの国の大きさの倍（段6 A3）: 倍を入れる前の世界は倍 1 のまま動く。"""

from __future__ import annotations

from django.conf import settings

from hakoniwa_core import scale
from hakoniwa_core.game import load_game
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
    # get_game() の使い回しは触らない（空にすると、DB を使わない後の試験が組み立てに失敗する）
    game = load_game(settings.HAKONIWA_GAME_CONFIG, server_game.world_scale())
    assert scale.people(game.config) == 20


def test_no_world_uses_game_yaml(db):
    assert server_game.world_scale() is None


def test_facility_rows_show_the_size(world):
    from server.web import reports
    from server.world.models import Island

    island = Island.objects.order_by("id").first()
    rows = reports.facility_rows(island, "")["rows"]
    assert rows and all(r["size"] == 1 and r["expands"] is None for r in rows)
