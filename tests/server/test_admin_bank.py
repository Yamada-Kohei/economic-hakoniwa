"""ためた行政力（W-265・OV-30）が命令の列の API と国の要約に出る。"""

from __future__ import annotations

import pytest

from server.game import get_game
from server.turns.services import run_turn
from server.web import overview
from tests.server.test_phase1 import _own_island

pytestmark = pytest.mark.django_db


def test_orders_api_shows_banked_admin_power(client, world):
    island = _own_island(client)
    run_turn(force=True)  # 命令のないターン: 使わなかった行政力をためる
    run_turn(force=True)
    island.refresh_from_db()
    data = client.get(f"/api/islands/{island.id}/orders").json()
    stats = dict(island.stats.filter(turn=island.world.turn).values_list("name", "value"))
    assert data["admin_bank"] == stats["admin_bank"] > 0
    burst = get_game().config.get("orders.burst")
    assert data["admin_power"] == stats["admin_usable"]
    assert data["admin_power"] == min(
        stats["admin_power"] + stats["admin_bank"], stats["admin_power"] * burst
    )
    brief = overview.orders_brief(island)
    assert brief["admin_power"] == data["admin_power"] and brief["admin_bank"] > 0
