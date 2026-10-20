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
