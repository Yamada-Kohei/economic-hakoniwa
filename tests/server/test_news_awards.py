"""フェーズ4-1 ③b: 世界の近況、賞、島の一覧の表の旗と賞。"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.accounts import keys
from server.turns.models import TurnLog
from server.web import awards
from server.world import models as m

pytestmark = pytest.mark.django_db


@pytest.fixture
def islands(world):
    m.World.objects.update(turn=300)
    world.refresh_from_db()
    return list(m.Island.objects.order_by("id"))


def _stat(island, turn, value, name="population"):
    m.StatRecord.objects.create(island=island, turn=turn, name=name, value=value)


def _names(world, island):
    return [a["name"] for a in awards.holders(world).get(island.id, [])]


def test_prosperity_awards_use_the_highest_level_reached(world, islands):
    a, b, c = islands
    for island in islands:  # ほかの賞が混ざらないように、建国を最近にする
        island.ext = {**island.ext, "founded_turn": 290}
        island.save(update_fields=["ext"])
    _stat(a, 100, 49_999)
    _stat(b, 100, 50_000)
    _stat(c, 100, 120_000)
    _stat(c, 200, 80_000)  # 一度届けば、そのあと減っても賞は残る
    assert _names(world, a) == []
    assert _names(world, b) == ["繁栄賞"]
    assert _names(world, c) == ["超繁栄賞"]
    _stat(c, 250, 250_000)
    assert _names(world, c) == ["究極繁栄賞"]


def test_peace_award_counts_quiet_turns_since_the_last_unrest_or_founding(world, islands):
    a, b, _ = islands
    for island in islands:
        island.ext = {**island.ext, "founded_turn": 100}
        island.save(update_fields=["ext"])
    TurnLog.objects.create(island=a, turn=250, kind="disaster_flood", args={})
    TurnLog.objects.create(island=b, turn=150, kind="war_declared_on", args={})
    # ターン 300: a は 50 ターン、b は 150 ターン（120 以上）静か。c は建国から 200 ターン
    assert "平和賞" not in _names(world, a)
    assert "平和賞" in _names(world, b)
    assert "平和賞" in _names(world, islands[2])
    TurnLog.objects.create(island=b, turn=299, kind="politics_riot", args={})
    assert "平和賞" not in _names(world, b)


def test_calamity_award_counts_disasters_and_failures(world, islands):
    a, *_ = islands
    for turn in range(23):
        TurnLog.objects.create(island=a, turn=turn + 1, kind="disaster_fire", args={})
    assert "災難賞" not in _names(world, a)
    TurnLog.objects.create(island=a, turn=40, kind="firm_bankrupt", args={})
    assert "災難賞" in _names(world, a)
    TurnLog.objects.filter(island=a, turn=40).delete()  # 巻き戻しで出来事が消えれば賞も消える
    assert "災難賞" not in _names(world, a)


def test_cup_goes_to_the_oldest_active_country_that_lasted_long_enough(world, islands):
    a, b, c = islands
    for island, founded in ((a, 10), (b, 5), (c, 50)):
        island.ext = {**island.ext, "founded_turn": founded}
        island.save(update_fields=["ext"])
    assert "ターン杯" in _names(world, b)
    assert "ターン杯" not in _names(world, a) and "ターン杯" not in _names(world, c)
    m.Island.objects.filter(pk=b.pk).update(inactive=True)  # 休止中の国には出さない
    assert "ターン杯" in _names(world, a)
    m.World.objects.update(turn=100)  # まだ続いた期間が足りない
    world.refresh_from_db()
    assert "ターン杯" not in _names(world, a)


def test_awards_show_on_the_top_table_the_country_page_and_the_api(world, islands):
    a, *_ = islands
    _stat(a, 100, 60_000)
    html = Client().get("/").content.decode()
    assert "繁栄賞" in html and f"/islands/{a.id}/flag" in html
    rows = Client().get("/api/rankings").json()["table"]
    row = next(r for r in rows if r["island"] == a.id)
    assert "繁栄賞" in row["awards"] and row["flag"] == f"/islands/{a.id}/flag"
    page = Client().get(f"/api/islands/{a.id}/country").json()
    assert page["awards"][0]["name"] == "繁栄賞" and "50,000" in page["awards"][0]["detail"]
    assert "繁栄賞" in Client().get(f"/islands/{a.id}/country/").content.decode()


def test_news_lists_main_events_of_all_countries_newest_first(world, islands):
    a, b, _ = islands
    TurnLog.objects.create(
        island=a, turn=10, kind="island_renamed", args={"old": "ア", "new": "新ア"}
    )
    TurnLog.objects.create(
        island=b, turn=20, kind="island_renamed", args={"old": "イ", "new": "新イ"}
    )
    TurnLog.objects.create(island=a, turn=30, kind="order_failed", args={})  # 主な出来事ではない
    TurnLog.objects.create(
        island=a,
        turn=25,
        kind="war_declared",
        args={"hall": True, "a": a.name, "b": b.name, "turn": 26, "text": ""},
    )
    data = Client().get("/api/news").json()
    turns = [e["turn"] for e in data["entries"]]
    assert turns == sorted(turns, reverse=True) and 30 not in turns
    assert {e["island"]["id"] for e in data["entries"]} >= {a.id, b.id}
    assert any("宣戦" in e["text"] for e in data["entries"] if e["turn"] == 25)
    assert data["entries"][0]["island"]["flag"].endswith("/flag")
    html = Client().get("/news/").content.decode()
    assert a.name in html and f"/islands/{a.id}/country/" in html


def test_news_adds_the_countrys_own_date_when_it_has_another_era(world, islands):
    from server.country import services

    a, *_ = islands
    TurnLog.objects.create(
        island=a, turn=20, kind="island_renamed", args={"old": "ア", "new": "新ア"}
    )
    first = Client().get("/api/news").json()["entries"][0]
    assert "country_date" not in first
    services.set_era(a, "regnal", "天明")
    first = Client().get("/api/news").json()["entries"][0]
    assert first["country_date"].startswith("天明")


def test_news_pages(world, islands):
    a, *_ = islands
    for turn in range(1, 41):
        TurnLog.objects.create(
            island=a, turn=turn, kind="island_renamed", args={"old": "ア", "new": "ア"}
        )
    data = Client().get("/api/news?page=2").json()
    assert data["pages"] == 2 and data["page"] == 2 and len(data["entries"]) == data["count"] - 30
    assert Client().get("/news/?page=2").status_code == 200


def test_mcp_news_tool(world, islands, settings):
    settings.HAKONIWA_AI_CALLS_PER_MINUTE = 10_000
    a, *_ = islands
    user = User.objects.create_user("ai", password="a-long-pass-123")
    a.owner = user
    a.save(update_fields=["owner"])
    TurnLog.objects.create(
        island=a, turn=10, kind="island_renamed", args={"old": "ア", "new": "新ア"}
    )
    _, token = keys.issue(user, "test")
    ai = Client(HTTP_AUTHORIZATION=f"Bearer {token}")
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    message["params"] = {"name": "news", "arguments": {}}
    res = ai.post("/mcp", json.dumps(message), content_type="application/json")
    result = res.json()["result"]
    assert not result["isError"]
    assert json.loads(result["content"][0]["text"])["entries"][0]["island"]["id"] == a.id
