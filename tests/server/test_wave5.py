"""第5波（案30・31・32・34・36）: 書き出しの基盤と国のカード、ターンの新聞、
テーマカラーと改名の年表、地名、姉妹港の壁画。"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.country import services as country_services
from server.turns.models import TurnLog
from server.web import card as card_views
from server.web import country as country_views
from server.web import geonames as geonames_views
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


# --- 案32 テーマカラーと改名の年表 ---------------------------------------------------------


def test_theme_color_choices_and_validation(owner):
    _, island = owner
    profile = country_services.set_theme_color(island, "blue")
    assert profile.theme_color == "blue"
    info = country_views.theme_info(profile)
    assert info == {"key": "blue", "name": "青", "hex": "#2f6db5"}
    with pytest.raises(country_services.CountryError):
        country_services.set_theme_color(island, "#123456")  # 一覧に無い色は受けない
    profile = country_services.set_theme_color(island, "")
    assert country_views.theme_info(profile) is None


def test_theme_color_shows_on_country_page_and_card(owner):
    client, island = owner
    res = client.post(f"/islands/{island.id}/country/", {"action": "theme", "color": "green"})
    assert res.status_code == 302
    html = Client().get(f"/islands/{island.id}/country/").content.decode()
    assert "country-accent" in html and "#3e8e4f" in html
    assert card_views.card_data(island)["accent"] == "#3e8e4f"
    home = Client().get(f"/islands/{island.id}/home/").content.decode()
    assert "#3e8e4f" in home  # 島の見出しの帯


def test_capital_and_leader_renames_are_in_timeline(owner):
    _, island = owner
    _log(island, 4, "capital_renamed", old="旧都", new="新都")
    _log(island, 5, "leader_renamed", name="山田太郎")
    rows = country_views.timeline(island, None)
    texts = [r["text"] for r in rows if r["turn"] in (4, 5)]
    assert any("首都の名前を「旧都」から「新都」に変更しました" == t for t in texts)
    assert any("国家元首の名前を 山田太郎 にしました" == t for t in texts)


# --- 案34 地名 ---------------------------------------------------------------------------


def _tile(island, x, y, terrain):
    return m.Tile.objects.create(island=island, x=x, y=y, terrain=terrain)


@pytest.fixture
def geo_island(owner):
    """山・湾・岬が 1 つずつできる地形を手で置いた島。区画の名前は 地区0〜地区15。"""
    _, island = owner
    island.tiles.all().delete()
    for x, y in [(5, 5), (6, 5), (5, 6)]:
        _tile(island, x, y, "mountain")
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            _tile(island, 10 + dx, 10 + dy, "sea" if dx == dy == 0 else "plain")
    _tile(island, 20, 20, "plain")
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx or dy:
                _tile(island, 20 + dx, 20 + dy, "sea")
    island.ext = {"places": [f"地区{i}" for i in range(16)]}
    island.save(update_fields=["ext"])
    return island


def test_geonames_features_from_terrain(geo_island):
    by_kind = {f["kind"]: f for f in geonames_views.features(geo_island)}
    assert set(by_kind) == {"mountain", "bay", "cape"}
    assert by_kind["mountain"]["key"] == "mountain:5:5"
    assert by_kind["mountain"]["name"] == "地区0山"
    assert by_kind["bay"]["key"] == "bay:10:10"
    assert by_kind["bay"]["name"] == "地区5湾"  # 湾の名前は、近くの陸の区画から
    assert by_kind["cape"]["key"] == "cape:20:20"
    assert by_kind["cape"]["name"] == "地区10岬"
    assert all(not f["renamed"] for f in by_kind.values())


def test_geonames_rename_records_change_and_timeline(geo_island):
    geonames_views.rename(geo_island, "bay:10:10", "入り江")
    feature = {f["key"]: f for f in geonames_views.features(geo_island)}["bay:10:10"]
    assert feature["name"] == "入り江" and feature["renamed"] is True
    change = geo_island.geo_name_changes.get()
    assert (change.old, change.new) == ("地区5湾", "入り江")
    texts = [r["text"] for r in country_views.timeline(geo_island, None)]
    assert "地名を「地区5湾」から「入り江」に変更しました" in texts
    # 自動の名前に戻すと付け替えは消え、記録だけが増える
    geonames_views.rename(geo_island, "bay:10:10", "地区5湾")
    assert geo_island.geo_names.count() == 0
    assert geo_island.geo_name_changes.count() == 2


def test_geonames_rename_rejects_bad_input(geo_island):
    with pytest.raises(ValueError):
        geonames_views.rename(geo_island, "bay:10:10", "地区5湾")  # いまと同じ
    with pytest.raises(ValueError):
        geonames_views.rename(geo_island, "bay:99:99", "何か")  # そんな地形は無い
    with pytest.raises(ValueError):
        geonames_views.rename(geo_island, "bay:10:10", "あ" * 17)  # 長すぎる


def test_geonames_api_param_and_owner_view(owner, geo_island):
    client, island = owner
    assert island.id == geo_island.id
    plain = client.get(f"/api/islands/{island.id}/map").json()
    assert "geonames" not in plain
    data = client.get(f"/api/islands/{island.id}/map?geonames=1").json()
    assert {g["kind"] for g in data["geonames"]} == {"mountain", "bay", "cape"}
    res = client.post(f"/islands/{island.id}/geonames/", {"key": "cape:20:20", "name": "出っ張り"})
    assert res.status_code == 302
    assert geo_island.geo_names.get().name == "出っ張り"
    anon = Client().post(f"/islands/{island.id}/geonames/", {"key": "cape:20:20", "name": "だめ"})
    assert anon.status_code == 404
