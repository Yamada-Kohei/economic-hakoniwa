from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from server.turns.models import Backup
from server.world import admin as world_admin
from server.world.models import Island, World

pytestmark = pytest.mark.django_db


def test_top_lists_islands(client, world):
    res = client.get("/")
    assert res.status_code == 200
    assert "サンプル島1" in res.content.decode()


def test_island_page_and_map_api(client, world):
    island = Island.objects.first()
    assert client.get(f"/islands/{island.id}/").status_code == 200
    data = client.get(f"/api/islands/{island.id}/map").json()
    assert data["size"] == island.size
    assert len(data["terrain"]) == island.size**2
    assert set(data["terrain"]) <= {"sea", "shallows", "plain", "forest", "mountain"}


def test_tile_api(client, world):
    island = Island.objects.first()
    data = client.get(f"/api/islands/{island.id}/tiles/0,0").json()
    assert {k: data[k] for k in ("x", "y", "terrain", "terrain_name", "population")} == {
        "x": 0,
        "y": 0,
        "terrain": "sea",
        "terrain_name": "海",
        "population": 0,
    }
    assert data["place"] and "facility" not in data
    cx, cy = island.capital_x, island.capital_y
    capital = client.get(f"/api/islands/{island.id}/tiles/{cx},{cy}").json()
    assert capital["capital"] and capital["facility"]["owner"] in ("state", "private")
    assert capital["capital_buildings"]
    assert client.get(f"/api/islands/{island.id}/tiles/99,-1").status_code == 404


def test_tile_api_construction_progress(client, world):
    """工事の進みは、工事全体のうち進んだ割合で返す（工期6ターンの5ターン分なら 5/6）。"""
    island = Island.objects.first()
    tile = island.tiles.filter(terrain="plain", population=0).exclude(
        x=island.capital_x, y=island.capital_y
    )[0]
    island.facilities.filter(x=tile.x, y=tile.y).delete()
    number = max(island.facilities.values_list("number", flat=True), default=0) + 1
    island.facilities.create(
        number=number, kind="steel_mill", x=tile.x, y=tile.y, built=False, progress=5000
    )
    data = client.get(f"/api/islands/{island.id}/tiles/{tile.x},{tile.y}").json()
    assert data["facility"]["turns"] == 6
    assert data["facility"]["progress"] == pytest.approx(5 / 6)


def test_defs_api(client, db):
    data = client.get("/api/defs").json()
    assert data["terrain"]["mountain"] == {"name": "山", "height": 2, "land": True}
    assert data["facilities"]["farm"]["buildable"] is True
    assert data["founding"]["land_terrains"] == ["plain", "mountain", "forest"]
    assert data["theme"]["tile_width"] == 64


def test_signup_and_login(client, db):
    res = client.post(
        "/accounts/signup/",
        {"username": "taro", "password1": "a-long-pass-123", "password2": "a-long-pass-123"},
    )
    assert res.status_code == 302
    assert User.objects.filter(username="taro").exists()


def test_admin_creates_island_with_tiles(admin_client, world):
    res = admin_client.post("/admin/world/island/add/", {"name": "管理島", "owner": ""})
    assert res.status_code == 302, res.content.decode()[:2000]
    island = Island.objects.get(name="管理島")
    assert island.tiles.count() == island.size**2
    assert island.tiles.exclude(terrain__in=["sea", "shallows"]).count() >= 120


def _advance_turns(admin_client, world):
    return admin_client.post(
        "/admin/world/world/",
        {"action": "advance_turns", "_selected_action": [world.pk]},
        follow=True,
    )


def test_admin_advances_ten_turns(admin_client, world):
    page = admin_client.get("/admin/world/world/").content.decode()
    assert "今すぐ10ターン進める（テスト用）" in page
    res = _advance_turns(admin_client, world)
    assert res.status_code == 200
    assert World.objects.get().turn == 10
    assert Backup.objects.count() == 10  # ターンごとに巻き戻せる
    assert "10 ターン進めました（ターン 10、" in res.content.decode()


def test_admin_ten_turns_stops_at_failed_turn(admin_client, world, monkeypatch):
    real = world_admin.run_turn
    calls = []

    def fails_on_fourth(**kwargs):
        calls.append(1)
        if len(calls) == 4:
            raise RuntimeError("壊れた")
        return real(**kwargs)

    monkeypatch.setattr(world_admin, "run_turn", fails_on_fourth)
    res = _advance_turns(admin_client, world)
    assert World.objects.get().turn == 3
    assert "3 ターン進めたところで止まりました（ターン 3）: 壊れた" in res.content.decode()
