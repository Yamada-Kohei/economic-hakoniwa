"""施設のページ（UI-72・UI-73・UI-78）: 1 施設に 1 ページ、上に「見る」所、下に「打てる手」の箱。

持ち主には帳簿の推移と命令の箱を、ほかの人とログインしていない人には地図のマスの窓と同じ公開の
中身だけを出す。隠した施設（MI-62）は見える国のほかには 404。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.orders.models import Order
from server.turns.services import run_turn
from server.web import mcp
from server.world import models as m
from tests.server.test_phase1 import _own_island

pytestmark = pytest.mark.django_db

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def owned(client, world):
    """持ち主がログインした島（1 ターン回して帳簿を作る）と、ほかの国の人の Client。"""
    island = _own_island(client)
    other_island = m.Island.objects.exclude(pk=island.id).order_by("id").first()
    other_island.owner = User.objects.create_user("someone", password="a-long-pass-123")
    other_island.save()
    run_turn(world.id)
    stranger = Client()
    stranger.force_login(other_island.owner)
    return island, other_island, stranger


def _farm(island: m.Island) -> m.Facility:
    return island.facilities.filter(kind="farm", x__gte=0).order_by("number").first()


def test_owner_page_has_view_book_and_action_boxes(client, owned):
    island, _, _ = owned
    farm = _farm(island)
    Order.objects.create(
        island=island, seq=1, kind="set_priority", payload={"x": farm.x, "y": farm.y, "priority": 8}
    )
    res = client.get(f"/islands/{island.id}/facilities/{farm.number}/")
    assert res.status_code == 200
    html = res.content.decode()
    # 見る: 状態・人手と、帳簿の推移（この施設の番号をはじめから読む）・前のターンの結果
    assert "稼働率" in html and "従業者" in html
    assert f'id="book-detail" data-url="/api/islands/{island.id}/facilities/0/book"' in html
    assert f'data-number="{farm.number}"' in html and 'id="last-turn"' in html
    assert f'href="/islands/{island.id}/?x={farm.x}&amp;y={farm.y}"' in html
    # 打てる手: 1 色の枠の箱に、この施設に出せる命令と出ている命令
    box = html.split('class="panel action-box"')[1]
    for kind in ("set_priority", "rename_facility", "demolish"):
        assert f'data-order="{kind}"' in box
    assert "優先度" in box.split("この施設に出ている命令")[1].split("</ul>")[0]
    assert 'id="orders"' in box and "js/reports/facilities.js" in html
    # 入口は「施設」
    nav = re.search(r'<nav class="island-nav".*?</nav>', html, re.S).group(0)
    assert re.search(r'aria-current="page">施設</a>', nav)
    # JSON API も同じ中身
    data = client.get(f"/api/islands/{island.id}/facilities/{farm.number}").json()
    assert data["number"] == farm.number and data["has_books"] is True
    assert "set_priority" in data["actions"] and data["orders"][0]["kind"] == "set_priority"
    assert data["facility"]["books"]["rows"]


def test_tile_less_facility_has_a_page_without_map_rows(client, owned):
    island, _, _ = owned
    shipping = island.facilities.filter(x__lt=0).exclude(kind="housing").first()
    assert shipping is not None
    html = client.get(f"/islands/{island.id}/facilities/{shipping.number}/").content.decode()
    assert "マスを持たない施設です" in html
    assert "地図でマス" not in html and "data-order=" not in html
    assert "この施設に出せる命令はありません。" in html
    data = client.get(f"/api/islands/{island.id}/facilities/{shipping.number}").json()
    assert data["on_map"] is False and data["actions"] == [] and data["logs"] == []


def test_others_see_a_reduced_view(owned):
    island, _, stranger = owned
    farm = _farm(island)
    for viewer in (stranger, Client()):
        res = viewer.get(f"/islands/{island.id}/facilities/{farm.number}/")
        assert res.status_code == 200
        html = res.content.decode()
        assert "他国の施設のため" in html and "稼働率" in html
        for owner_only in ('id="book-detail"', "action-box", "data-order=", 'id="orders"'):
            assert owner_only not in html
        data = viewer.get(f"/api/islands/{island.id}/facilities/{farm.number}").json()
        assert data["mine"] is False and "actions" not in data and "books" not in data["facility"]
        book = viewer.get(f"/api/islands/{island.id}/facilities/{farm.number}/book")
        assert book.status_code == 403


def test_hidden_facility_is_a_404_for_others(client, owned):
    island, other, stranger = owned
    farm = _farm(island)
    farm.ext = {**farm.ext, "hidden": True}
    farm.save()
    page = f"/islands/{island.id}/facilities/{farm.number}/"
    api = f"/api/islands/{island.id}/facilities/{farm.number}"
    for viewer in (stranger, Client()):
        assert viewer.get(page).status_code == 404
        assert viewer.get(api).status_code == 404
    assert client.get(page).status_code == 200
    farm.ext = {**farm.ext, "found": [other.id]}  # 見つけた国には見える
    farm.save()
    assert stranger.get(page).status_code == 200


def test_towns_and_missing_numbers_have_no_page(client, owned):
    island, _, _ = owned
    town = island.facilities.filter(kind="housing").first()
    assert client.get(f"/islands/{island.id}/facilities/{town.number}/").status_code == 404
    assert client.get(f"/islands/{island.id}/facilities/99999/").status_code == 404


def test_facility_names_link_to_the_page(client, owned):
    island, _, _ = owned
    farm = _farm(island)
    link = f'href="/islands/{island.id}/facilities/{farm.number}/"'
    assert link in client.get(f"/islands/{island.id}/facilities/").content.decode()
    # 原料を買えずに止まった施設（市場）と、止まっている工場（産業の鎖）
    farm.status = "input:coal"
    farm.save()
    assert link in client.get(f"/islands/{island.id}/market/").content.decode()
    assert link in client.get(f"/islands/{island.id}/chain/").content.decode()
    # 地図のマスの窓（UI-78）: 施設のページの URL を渡し、窓の施設の見出しの下にリンクを出す
    page = client.get(f"/islands/{island.id}/").content.decode()
    assert f'data-facility-url="/islands/{island.id}/facilities/0/"' in page
    panel = (REPO / "server/static/js/island/panel.js").read_text(encoding="utf-8")
    assert "${this.facilityLink(f)}" in panel and "施設のページを開く" in panel
    assert mcp.SCREENS["facility_page"] == ("api_get", "tile", "set_orders")
