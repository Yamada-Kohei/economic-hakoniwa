"""MCP の地図の施設一覧に出る設備の割合は、施設のページと同じ決まり（100 未満は切り捨て）。

ST-20 の残り: 画面側（facility_page._pct と地図の窓）は直っていたが、MCP の一覧は
四捨五入のままで、99.7% の設備が「設備 100%」と出ていた（傷んでいるのに新品に見える）。
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.accounts import keys
from server.game import get_game
from server.web import display
from server.world.models import Island
from server.world.repository import facility_from_row

pytestmark = pytest.mark.django_db


def _map_text(world) -> str:
    """持ち主の鍵で MCP の map を呼び、一覧の文を返す。設備のある建った施設を 99.7% に傷ませる。"""
    island = Island.objects.order_by("id").first()
    user = User.objects.create_user("player", password="x")
    island.owner = user
    island.save(update_fields=["owner"])
    game = get_game()
    target = None
    for row in island.facilities.filter(built=True).order_by("number"):
        if display.equipment_info(game.defs, game.config, facility_from_row(row)):
            target = row
            break
    assert target is not None, "サンプルの島に設備のある施設がない"
    target.economy = {**(target.economy or {}), "condition": 997}
    target.save(update_fields=["economy"])
    _, token = keys.issue(user)
    client = Client(HTTP_AUTHORIZATION=f"Bearer {token}")
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "map", "arguments": {"island": island.id}},
    }
    res = client.post("/mcp", json.dumps(message), content_type="application/json")
    assert res.status_code == 200, res.content
    result = res.json()["result"]
    assert not result["isError"], result
    return result["content"][0]["text"]


def test_mcp_map_equipment_percent_is_floored_below_100(world):
    text = _map_text(world)
    assert "設備 99%" in text
    assert "設備 100%" not in text
