"""建国の資源選びで、ほかの国がもう選んだ地下資源の数を見せる（W-248、掲示板 #24 の 11 番）。"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.game import get_game

pytestmark = pytest.mark.django_db


def test_cells_count_the_resources_other_nations_chose(world) -> None:
    islands = list(world.islands.order_by("id"))
    islands[0].resources = ["iron_ore", "coal"]
    islands[1].resources = ["iron_ore", "stone"]
    islands[2].resources = ["coal", "coal"]  # 同じ資源を2回書いても1国
    islands[2].inactive = True  # 休止中の国は数えない
    for island in islands:
        island.save(update_fields=["resources", "inactive"])
    user = User.objects.create_user("newcomer", password="a-long-pass-123")
    client = Client()
    client.force_login(user)
    data = client.get("/api/founding/cells").json()
    assert data["nations"] == 2
    counts = data["resource_counts"]
    assert set(counts) == set(get_game().defs.mining_resources())
    assert counts["iron_ore"] == 2 and counts["coal"] == 1 and counts["stone"] == 1
    assert all(n == 0 for g, n in counts.items() if g not in ("iron_ore", "coal", "stone"))
    page = client.get("/found/").content.decode()
    assert 'id="resource-others"' in page
