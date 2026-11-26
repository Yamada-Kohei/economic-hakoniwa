"""地図の変遷と変化レンズ（グラフィック強化 案29）。

サーバー側（server/web/history.py）: 節目の写しの一覧・写しそのもの・写しと今の差
（変化レンズ）を、持ち主の地図の API だけに出す。差は見た目で決め、新しくできた
物は new、無くなった・衰えた物は down、残りの姿の変化は changed。
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from server.web.history import find_snapshot, lens_marks, snapshot_list, snapshot_map
from server.world.models import Island

pytestmark = pytest.mark.django_db


def _own_island(client) -> Island:
    user = User.objects.create_user("owner", password="<redacted>")
    client.force_login(user)
    island = Island.objects.order_by("id").first()
    island.owner = user
    island.save(update_fields=["owner"])
    return island


def _snapshot(island: Island, turn: int = 10) -> dict:
    """今の姿そのままの写し（テストの土台）。"""
    terrain = [row[2] for row in island.tiles.order_by("y", "x").values_list("x", "y", "terrain")]
    facilities = [
        {
            "number": f.number,
            "x": f.x,
            "y": f.y,
            "kind": f.kind,
            "recipe": f.recipe,
            "built": f.built,
            "level": (f.ext or {}).get("level"),
            "grade": (f.ext or {}).get("grade"),
            "size": (f.ext or {}).get("size", 1),
        }
        for f in island.facilities.filter(x__gte=0)
    ]
    return {
        "turn": turn,
        "kinds": ["start"],
        "size": island.size,
        "terrain": terrain,
        "capital": [island.capital_x, island.capital_y],
        "facilities": facilities,
    }


def _seed(island: Island, snap: dict) -> None:
    island.ext = {"map_snapshots": [snap]}
    island.save(update_fields=["ext"])
    island.refresh_from_db()


def _marks(island: Island, snap: dict) -> set[tuple[int, int, str]]:
    return {(row["x"], row["y"], row["kind"]) for row in lens_marks(island, snap)}


def test_same_look_no_marks(world):
    island = Island.objects.order_by("id").first()
    snap = _snapshot(island)
    _seed(island, snap)
    assert lens_marks(island, snap) == []


def test_lens_classifies_new_down_and_changed(world):
    island = Island.objects.order_by("id").first()
    placed = [f for f in island.facilities.all() if f.x >= 0]
    assert placed, "サンプルの世界にはマスを持つ施設があるはず"
    snap = _snapshot(island)
    gone = snap["facilities"][0]  # 写しから消す → 今ある物は new
    snap["facilities"] = snap["facilities"][1:]
    empty = next(
        (x, y)
        for y in range(island.size)
        for x in range(island.size)
        if all(f["x"] != x or f["y"] != y for f in snap["facilities"])
        and island.tiles.get(x=x, y=y).terrain not in ("sea",)
    )
    snap["facilities"].append(  # 写しにだけある施設 → 今は無いので down
        {
            "number": 9999,
            "x": empty[0],
            "y": empty[1],
            "kind": gone["kind"],
            "recipe": gone["recipe"],
            "built": True,
            "level": None,
            "grade": None,
            "size": 1,
        }
    )
    cell = next(  # 施設の無いマスの地形を写しで変える → changed
        (x, y)
        for y in range(island.size)
        for x in range(island.size)
        if all(f["x"] != x or f["y"] != y for f in snap["facilities"]) and (x, y) != empty
    )
    index = cell[1] * island.size + cell[0]
    snap["terrain"][index] = "sea" if snap["terrain"][index] != "sea" else "plain"
    _seed(island, snap)
    marks = _marks(island, snap)
    assert (gone["x"], gone["y"], "new") in marks
    assert (empty[0], empty[1], "down") in marks
    assert (cell[0], cell[1], "changed") in marks


def test_snapshot_list_and_map(world):
    island = Island.objects.order_by("id").first()
    snap = _snapshot(island, turn=42)
    snap["kinds"] = ["start", "milestone_population"]
    _seed(island, snap)
    rows = snapshot_list(island)
    assert len(rows) == 1
    assert rows[0]["turn"] == 42
    assert "42" in rows[0]["label"]
    data = snapshot_map(island, snap)
    assert data["turn"] == 42
    assert data["size"] == island.size
    assert len(data["terrain"]) == island.size * island.size
    assert find_snapshot(island, "42") is snap or find_snapshot(island, 42)["turn"] == 42
    assert find_snapshot(island, "abc") is None
    assert find_snapshot(island, 7) is None


def test_api_is_owner_only(client, world):
    island = _own_island(client)
    snap = _snapshot(island, turn=10)
    _seed(island, snap)
    url = reverse("api_island_map", args=[island.id])

    response = client.get(url, {"history": "1"})
    data = response.json()
    assert data["history"]["snapshots"][0]["turn"] == 10
    assert data["history"]["now"]

    response = client.get(url, {"snapshot": "10"})
    data = response.json()
    assert data["turn"] == 10
    assert "population" not in data  # 写しの形（今の地図の材料は混ぜない）

    response = client.get(url, {"lens": "10"})
    assert response.json()["lens"] == []

    client.logout()
    response = client.get(url, {"history": "1", "snapshot": "10", "lens": "10"})
    data = response.json()
    assert "history" not in data
    assert "lens" not in data
    assert "population" in data  # 持ち主でなければ写しは返さず、今の地図を返す
