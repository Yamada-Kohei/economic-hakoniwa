from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from server.world.models import Island

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
