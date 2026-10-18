"""島の一覧（トップ）の画面: 順位の表・アクセスカウンタとキリ番・最近の出来事・歴代の記録と、
同じ中身を AI の入り口（/api/rankings・/api/world と MCP）でも読めること。"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from server.accounts import keys
from server.turns.models import TurnLog
from server.turns.services import run_turn
from server.web import counter, mcp, top
from server.web.models import AccessCounter, Visit
from server.world import models as m

pytestmark = pytest.mark.django_db


def _browser(ip: str = "10.0.0.1", agent: str = "Mozilla/5.0 test") -> Client:
    return Client(REMOTE_ADDR=ip, HTTP_USER_AGENT=agent)


# --- 島の一覧の表 -------------------------------------------------------------------------


def test_table_lists_islands_by_population_with_columns(client, world):
    run_turn(force=True)
    islands = list(m.Island.objects.order_by("id"))
    stats = m.StatRecord.objects.filter(turn=1, name="population")
    pop = {i.id: stats.get(island=i).value for i in islands}
    res = client.get("/")
    assert res.status_code == 200
    table = res.context["table"]
    assert [r["island"] for r in table] == sorted(pop, key=lambda i: (-pop[i], i))
    assert [r["rank"] for r in table] == [1, 2, 3]
    html = res.content.decode()
    labels = ("順位", "体制", "人口", "GDP", "1人あたり GDP", "失業率", "物価上昇率", "幸福度")
    labels += ("称号",)
    for label in labels:
        assert f">{label}<" in html, label
    first = table[0]
    assert first["regime"]  # 政治と経済の体制の名前
    assert first["columns"]["population"]["text"].endswith("人")


def test_table_shows_titles_and_ai_mark(client, world):
    a, b, _ = m.Island.objects.order_by("id")
    world.ext = {"rankings": {"titles": {"steel": b.id}}}
    world.save(update_fields=["ext"])
    user = User.objects.create_user("alice", password="a-long-pass-123")
    a.owner = user
    a.save(update_fields=["owner"])
    keys.issue(user)
    rows = {r["island"]: r for r in client.get("/").context["table"]}
    assert rows[b.id]["titles"] == ["鉄鋼の国"]
    assert rows[a.id]["ai"] and not rows[b.id]["ai"]
    html = client.get("/").content.decode()
    row = html.split(f'href="/islands/{a.id}/"')[1].split("</tr>")[0]
    assert 'class="ai-mark"' in row


def test_money_without_rate_is_not_ranked_as_zero(client, world):
    """為替レートのない通貨の島は、お金の列を「直せない」にし、0 として並べない。"""
    run_turn(force=True)
    for row in client.get("/").context["table"]:
        gdp = row["columns"]["gdp"]
        assert gdp["value"] is None or gdp["value"] > 0
        if gdp["value"] is None:
            assert gdp["text"] == "直せない"


def test_top_without_world(client, db):
    res = client.get("/")
    assert res.status_code == 200
    assert "世界がまだありません" in res.content.decode()


# --- アクセスカウンタとキリ番 -------------------------------------------------------------


def test_counter_counts_once_a_day_per_visitor(world):
    alice = _browser("10.0.0.1")
    first = alice.get("/").context["counter"]
    assert (first.number, first.total, first.today, first.new) == (1, 1, 1, True)
    again = alice.get("/").context["counter"]  # 開き直しは数えない
    assert (again.number, again.total, again.new) == (1, 1, False)
    bob = _browser("10.0.0.2").get("/").context["counter"]
    assert (bob.number, bob.total, bob.today) == (2, 2, 2)
    other_browser = _browser("10.0.0.1", "Mozilla/5.0 another").get("/").context["counter"]
    assert other_browser.number == 3


def test_counter_keeps_no_address_and_only_two_days(world, monkeypatch):
    _browser("192.168.1.23").get("/")
    visit = Visit.objects.get()
    assert "192.168" not in visit.digest and len(visit.digest) == 64
    today = timezone.localdate()
    Visit.objects.create(day=today - timedelta(days=5), digest="old", number=0)
    Visit.objects.create(day=today - timedelta(days=1), digest="yesterday", number=0)
    Client().get("/")  # 数えない相手が開いても、古い行は消す
    assert not Visit.objects.filter(digest="old").exists()
    count = _browser("192.168.1.24").get("/").context["counter"]
    assert (count.today, count.yesterday) == (2, 1)
    # 次の日は同じ人をまた1人と数える（日付を混ぜたハッシュなので、前の日の行とは別）
    monkeypatch.setattr(timezone, "localdate", lambda: today + timedelta(days=1))
    next_day = _browser("192.168.1.23").get("/").context["counter"]
    assert next_day.new and next_day.number == 3 and next_day.yesterday == 2


def test_logged_in_visitor_is_counted_by_account(client, world):
    user = User.objects.create_user("bob", password="a-long-pass-123")
    one, two = _browser("10.0.0.5"), _browser("10.0.0.6", "Mozilla/5.0 phone")
    one.force_login(user)
    two.force_login(user)
    assert one.get("/").context["counter"].number == 1
    assert two.get("/").context["counter"].number == 1


def test_kiriban_message(world):
    AccessCounter.objects.create(name=counter.NAME, total=999)
    html = _browser().get("/").content.decode()
    assert "キリ番 1000 です" in html and 'href="/board/"' in html
    assert "キリ番" not in _browser("10.0.0.9").get("/").content.decode()  # 1001


@pytest.mark.parametrize(
    ("n", "kiri"),
    [
        (99, False),
        (100, True),
        (300, True),
        (777, True),
        (1000, True),
        (1100, False),
        (1111, True),
        (1234, True),
        (12345, True),
        (1235, False),
        (5000, True),
        (10001, False),
    ],
)
def test_kiriban_numbers(n, kiri):
    assert counter.is_kiriban(n) is kiri


def test_seven_segment_draws_six_digits():
    svg = counter.seven_segment(42)
    assert 'aria-label="000042"' in svg
    assert svg.count("<polygon") == 6 * 7
    assert counter.seven_segment(1234567).count("<polygon") == 7 * 7  # 桁が足りなければ増やす


# --- 最近の出来事と歴代の記録 -------------------------------------------------------------


def test_recent_events_are_headlines_newest_first(client, world):
    a, b, _ = m.Island.objects.order_by("id")
    TurnLog.objects.all().delete()
    TurnLog.objects.create(island=a, turn=3, kind="bonds_issued", args={})
    flood = {"x": 1, "y": 2, "facility": "farm", "turns": 2}
    TurnLog.objects.create(island=a, turn=4, kind="disaster_flood", args=flood)
    TurnLog.objects.create(island=b, turn=5, kind="island_founded", args={})
    events = client.get("/").context["recent"]
    assert [(e["turn"], e["island"], e["kind"]) for e in events] == [
        (5, b.id, "island_founded"),
        (4, a.id, "disaster_flood"),
    ]
    assert events[0]["name"] == b.name and events[0]["text"]
    for i in range(top.RECENT + 5):
        args = {"industry": "steel"}
        TurnLog.objects.create(island=a, turn=10 + i, kind="title_gained", args=args)
    assert len(top.recent_events(m.World.objects.get())) == top.RECENT


def test_records_from_turn_history(client, world):
    a, b, _ = m.Island.objects.order_by("id")
    m.StatRecord.objects.create(island=a, turn=3, name="population", value=500_000)
    m.StatRecord.objects.create(island=b, turn=7, name="population", value=900_000)
    m.StatRecord.objects.create(island=b, turn=9, name="population", value=800_000)  # 今は減った
    world.turn = 24
    world.save(update_fields=["turn"])
    a.ext = {**a.ext, "founded_turn": 0}
    b.ext = {**b.ext, "founded_turn": 12}
    a.save(update_fields=["ext"])
    b.save(update_fields=["ext"])
    records = {r["key"]: r for r in client.get("/").context["records"]}
    assert records["population"]["island"] == b.id
    assert records["population"]["turn"] == 7 and records["population"]["text"] == "900,000人"
    assert records["longest"]["island"] == a.id and records["longest"]["text"] == "建国から 2.0 年"


# --- AI の入り口 --------------------------------------------------------------------------


def test_ai_reads_what_the_top_shows(client, world):
    run_turn(force=True)
    TurnLog.objects.create(island=m.Island.objects.first(), turn=1, kind="island_founded", args={})
    context = client.get("/").context
    data = client.get("/api/rankings").json()
    plain = json.loads(json.dumps({"t": context["table"], "r": context["records"]}, default=str))
    assert data["table"] == plain["t"]
    assert data["records"] == plain["r"]
    world_data = client.get("/api/world").json()
    assert world_data["recent"] == json.loads(json.dumps(context["recent"]))
    assert [u["text"] for u in world_data["updates"]] == [u["text"] for u in context["updates"]]
    assert world_data["message"] == context["message"]
    assert world_data["links"] == context["links"]
    assert set(mcp.SCREENS["top"]) == {"world", "rankings"}


def test_api_and_tools_do_not_count_visits(client, world):
    client.get("/api/world")
    client.get("/api/rankings")
    assert not AccessCounter.objects.exists() and not Visit.objects.exists()
    # 試し遊びの道具（hk.py）は cookie を取りに / を開く。ブラウザでないので数えない
    tool = Client(HTTP_USER_AGENT="Python-urllib/3.12").get("/").context["counter"]
    assert (tool.total, tool.visitor, tool.kiriban) == (0, False, False)
    assert not Visit.objects.exists()
