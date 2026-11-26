"""グラフィック強化 案27: 建国記念日と節目の表示（国のページの印と、年表への記録）。

- 記念日の年（建国から満1年・10年刻みを迎えた後の1年間）だけ、国のページに「建国○年」の
  印（country_data の anniversary）が出る
- 記念日・人口・GDP の節目のログは、年表（主な出来事）に文面つきで載る
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.turns.models import TurnLog
from server.world import models as m

pytestmark = pytest.mark.django_db


@pytest.fixture
def island(world):
    island = m.Island.objects.order_by("id").first()
    user = User.objects.create_user(f"p{island.id}", password="<redacted>")
    island.owner = user
    island.save(update_fields=["owner"])
    return island


def set_founded(island, turn: int, founded: int) -> None:
    m.World.objects.update(turn=turn)
    island.ext = {**(island.ext or {}), "founded_turn": founded}
    island.save(update_fields=["ext"])


def country_json(island) -> dict:
    return Client().get(f"/api/islands/{island.id}/country").json()


@pytest.mark.parametrize(
    ("turn", "founded", "years"),
    [
        (20, 8, 1),  # 満1年を迎えた年
        (31, 8, 1),  # 満1年の年の終わり
        (32, 8, None),  # 満2年の年は節目でない
        (128, 8, 10),  # 満10年の年
        (20, 20, None),  # 建国したばかり
        (19, 8, None),  # まだ満1年になっていない
    ],
)
def test_anniversary_badge_only_in_milestone_years(island, turn, founded, years):
    set_founded(island, turn, founded)
    data = country_json(island)
    assert data["anniversary"] == ({"years": years} if years is not None else None)


def test_country_page_shows_the_anniversary_badge(island):
    set_founded(island, 20, 8)  # 満1年の年
    client = Client()
    client.force_login(island.owner)
    page = client.get(f"/islands/{island.id}/country/").content.decode()
    assert "建国1年" in page

    set_founded(island, 32, 8)  # 満2年の年は出ない
    page = client.get(f"/islands/{island.id}/country/").content.decode()
    assert "建国2年" not in page


def test_timeline_includes_milestone_events(island):
    TurnLog.objects.create(island=island, turn=13, kind="anniversary_founding", args={"years": 1})
    TurnLog.objects.create(
        island=island, turn=14, kind="milestone_population", args={"count": 500_000}
    )
    TurnLog.objects.create(
        island=island, turn=15, kind="milestone_gdp", args={"amount": 1_000_000_000}
    )
    texts = [row["text"] for row in country_json(island)["timeline"]]
    assert "建国1年の記念日を迎えました" in texts
    assert "人口が500,000人に達しました" in texts
    assert "GDP（1ターン）が1,000,000,000に達しました" in texts
