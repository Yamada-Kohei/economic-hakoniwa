"""フェーズ4-2 の②のサーバー側: 兵の命令の説明と、国の要約の軍（持ち主だけ）。"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.turns.services import run_turn
from server.web import display
from server.world import models as m

pytestmark = pytest.mark.django_db


def _player():
    island = m.Island.objects.order_by("id").first()
    user = User.objects.create_user("soldier", password="a-long-pass-123")
    island.owner = user
    island.save()
    client = Client()
    client.force_login(user)
    return client, island


def test_summary_shows_the_army_to_its_owner_only(world):
    client, island = _player()
    body = {
        "orders": [
            {"kind": "set_defense", "payload": {"annual": 2_000_000}},
            {"kind": "recruit", "payload": {"count": 20}},
        ]
    }
    res = client.put(f"/api/islands/{island.id}/orders", body, content_type="application/json")
    assert res.status_code == 200, res.content
    run_turn(force=True)
    run_turn(force=True)
    mine = client.get(f"/api/islands/{island.id}/summary").json()["military"]
    assert mine["soldiers"] == 20 and mine["volunteers"] == 20 and mine["conscripts"] == 0
    assert mine["budget"]["personnel"] == 2_000_000 and mine["budget"]["procurement"] == 0
    assert 0 < mine["morale"] <= 1 and mine["wages_late_turns"] == 0
    public = Client().get(f"/api/islands/{island.id}/summary").json()
    assert public["military"] is None


def test_orders_are_described():
    assert "20" in display.describe_order("recruit", {"count": 20})
    text = display.describe_order("set_defense", {"annual": 1_000_000, "personnel_share": 60})
    assert "1,000,000" in text and "60%" in text


def test_units_and_bases_show_in_the_summary_and_the_military_api(world):
    from hakoniwa_core.military import army

    client, island = _player()
    row = m.Facility.objects.create(
        island=island, number=900, kind="barracks", x=1, y=1, built=True
    )
    w = island.world
    w.ext = {
        army.KEY: {
            "seq": 1,
            "units": [
                {
                    "id": 1,
                    "owner": island.id,
                    "kind": "infantry",
                    "x": 1,
                    "y": 1,
                    "soldiers": 100,
                    "equipment": {"weapons": 100},
                    "status": "ready",
                    "order": None,
                }
            ],
        }
    }
    w.save()
    tile = island.tiles.get(x=2, y=2)
    tile.ext = {army.TRENCH: 1}
    tile.save()
    mine = client.get(f"/api/islands/{island.id}/military").json()["military"]
    assert mine["units"][0]["name"] == "歩兵中隊" and mine["units"][0]["soldiers_full"] == 100
    assert [b["kind"] for b in mine["bases"]] == ["barracks"] and mine["bases"][0][
        "barracks"
    ] == 500
    assert mine["trenches"] == [{"x": 2, "y": 2}]
    assert (
        client.get(f"/api/islands/{island.id}/summary").json()["military"]["units"] == mine["units"]
    )
    assert Client().get(f"/api/islands/{island.id}/military").json() == {"military": None}
    assert row.pk
