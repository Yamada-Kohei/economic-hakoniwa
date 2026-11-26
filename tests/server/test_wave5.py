"""第5波（案30・31・32・34・36）: 書き出しの基盤と国のカード、ターンの新聞、
テーマカラーと改名の年表、地名、姉妹港の壁画。"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.turns.models import TurnLog
from server.web import card as card_views
from server.web import paper as paper_views
from server.world import models as m

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(world):
    island = m.Island.objects.order_by("id").first()
    user = User.objects.create_user(f"o{island.id}", password="x")
    island.owner = user
    island.save(update_fields=["owner"])
    client = Client()
    client.force_login(user)
    return client, island


# --- 案30 国のカード -------------------------------------------------------------------


def test_card_data_has_public_values(owner):
    _, island = owner
    island.names = {**island.names, "leader": "山田太郎", "capital": "山田市"}
    island.save(update_fields=["names"])
    m.StatRecord.objects.create(island=island, turn=3, name="population", value=12345)
    m.StatRecord.objects.create(island=island, turn=3, name="gdp", value=6789000)
    data = card_views.card_data(island)
    assert data["title"] == island.name
    lines = {row["label"]: row["value"] for row in data["lines"]}
    assert lines["首都"] == "山田市"
    assert lines["元首"] == "山田太郎"
    assert lines["人口"].startswith("12,345")
    assert "GDP" in lines
    assert data["map_url"].endswith(f"/api/islands/{island.id}/map")
    assert data["flag_url"].endswith(f"/islands/{island.id}/flag")


def test_card_page_renders(owner):
    client, island = owner
    html = client.get(f"/islands/{island.id}/card/").content.decode()
    assert "card-data" in html and "card.js" in html
    assert island.name in html


def test_map_page_has_export_tools(owner):
    client, island = owner
    html = client.get(f"/islands/{island.id}/").content.decode()
    assert 'id="export-map"' in html and 'id="export-style"' in html
    assert f"/islands/{island.id}/card/" in html


# --- 案31 ターンの新聞 -------------------------------------------------------------------


def _log(island, turn, kind, **args):
    return TurnLog.objects.create(island=island, turn=turn, kind=kind, args=args)


def test_paper_picks_milestone_as_lead_and_latest_turn(owner):
    _, island = owner
    _log(island, 2, "economy_started")
    _log(island, 2, "island_founded", name=island.name)
    _log(island, 3, "mobilization_started")
    _log(island, 3, "facility_stopped", facility="farm", x=1, y=1)  # 見出しでない種類は載らない
    data = paper_views.paper_data(island)
    assert data["turn"] == 3
    assert data["lead"]["kind"] == "mobilization_started"
    assert data["articles"] == []
    assert data["map_shown"] is True
    assert data["prev_turn"] == 2 and data["next_turn"] is None
    older = paper_views.paper_data(island, 2)
    assert older["lead"]["kind"] == "island_founded"  # 節目が先頭の記事より優先
    assert [a["kind"] for a in older["articles"]] == ["economy_started"]
    assert older["map_shown"] is False  # 地図は今の姿なので古いターンには付けない


def test_paper_empty_without_headlines(owner):
    _, island = owner
    TurnLog.objects.filter(island=island).delete()  # 見本の世界の建国などの記録を消してから
    _log(island, 1, "facility_stopped", facility="farm", x=1, y=1)
    assert paper_views.paper_data(island)["empty"] is True


def test_paper_page_renders(owner):
    client, island = owner
    _log(island, 2, "island_founded", name=island.name)
    html = client.get(f"/islands/{island.id}/paper/").content.decode()
    assert "ターンの新聞" in html and 'id="paper-map"' in html
    html_old = client.get(f"/islands/{island.id}/paper/?turn=2").content.decode()
    assert "paper-lead" in html_old
