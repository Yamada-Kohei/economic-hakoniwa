"""島の一覧（トップ）の画面: 順位の表・アクセスカウンタとキリ番・最近の出来事・歴代の記録と、
同じ中身を AI の入り口（/api/rankings・/api/world と MCP）でも読めること。"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from hakoniwa_core.units import BASIS
from server.accounts import keys
from server.turns.models import TurnLog
from server.turns.services import run_turn
from server.web import counter, display, mcp, rankings, top
from server.web.models import AccessCounter, KiribanGetter, SiteUpdate, Visit
from server.world import models as m

pytestmark = pytest.mark.django_db


def _browser(ip: str = "10.0.0.1", agent: str = "Mozilla/5.0 test") -> Client:
    return Client(REMOTE_ADDR=ip, HTTP_USER_AGENT=agent)


# --- 島の一覧の表 -------------------------------------------------------------------------


def test_table_lists_islands_by_gdp_with_columns(client, world):
    """既定の並びは GDP（見る人の通貨に直した額）の多い順。直せない国は後ろに人口の順で。"""
    run_turn(force=True)
    res = client.get("/")
    assert res.status_code == 200
    table = res.context["table"]
    assert [r["rank"] for r in table] == [1, 2, 3]

    def key(r):
        gdp, pop = r["columns"]["gdp"]["value"], r["columns"]["population"]["value"]
        return (gdp is None, -(gdp or 0), -(pop or 0), r["island"])

    assert table == sorted(table, key=key)
    assert table[0]["columns"]["gdp"]["value"]  # 見る人の通貨の国は直せる
    html = res.content.decode()
    labels = ("順位", "体制", "人口", "GDP", "1人あたり GDP", "失業率", "物価上昇率", "幸福度")
    labels += ("賞・称号",)
    for label in labels:
        assert f">{label}<" in html, label
    first = table[0]
    assert first["regime"]  # 政治と経済の体制の名前
    assert first["columns"]["population"]["text"].endswith("人")


def test_table_gdp_order_uses_converted_money(world):
    """GDP は通貨が国ごとなので、直した額で比べる（元の額の大きさでは並べない）。"""
    run_turn(force=True)
    w = m.World.objects.get()
    islands = list(m.Island.objects.order_by("id"))
    factors = {islands[0].id: 1.0, islands[1].id: 1000.0, islands[2].id: 0.001}
    rows = rankings.island_table(w, islands[0].id, factors=factors)
    assert [r["island"] for r in rows] == [islands[1].id, islands[0].id, islands[2].id]


def test_table_shows_titles_and_ai_mark(client, world):
    a, b, _ = m.Island.objects.order_by("id")
    world.ext = {"rankings": {"titles": {"steel": b.id}}}
    world.save(update_fields=["ext"])
    user = User.objects.create_user("alice", password="a-long-pass-123")
    a.owner = user
    a.save(update_fields=["owner"])
    keys.issue(user)
    rows = {r["island"]: r for r in client.get("/").context["table"]}
    assert rows[b.id]["titles"] == [display.title_name("steel")]
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
    assert "おめでとう" not in _browser("10.0.0.9").get("/").content.decode()  # 1001


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


def test_kiriban_getter_is_recorded_for_logged_in_visitor(world):
    """ログインしている人がキリ番を踏むと、名前と国名を一覧に残す。ログインしていない人は残さない。"""
    a = m.Island.objects.order_by("id").first()
    user = User.objects.create_user("carol", password="a-long-pass-123")
    a.owner = user
    a.save(update_fields=["owner"])
    AccessCounter.objects.create(name=counter.NAME, total=99)
    visitor = _browser("10.0.0.20")
    visitor.force_login(user)
    res = visitor.get("/")
    assert res.context["counter"].getter
    assert "一覧にお名前を載せました" in res.content.decode()
    getter = KiribanGetter.objects.get()
    assert (getter.number, getter.name, getter.island_name) == (100, "carol", a.name)
    assert visitor.get("/").context["counter"].getter is False  # 開き直しで二重に残さない
    AccessCounter.objects.filter(pk=counter.NAME).update(total=199)
    html = _browser("10.0.0.21").get("/").content.decode()  # 200 はログインしていない人
    assert "ログインして踏むと" in html
    assert KiribanGetter.objects.count() == 1
    assert [g["number"] for g in _browser("10.0.0.22").get("/").context["getters"]] == [100]


def test_online_counts_recent_viewers(world):
    _browser("10.0.0.30").get("/")
    _browser("10.0.0.31").get("/")
    assert _browser("10.0.0.32").get("/").context["counter"].online == 3
    Visit.objects.filter(number=1).update(seen=timezone.now() - timedelta(minutes=10))
    count = _browser("10.0.0.31").get("/").context["counter"]  # 開き直すと時刻が新しくなる
    assert count.online == 2
    assert "現在 2 人が見ています" in _browser("10.0.0.31").get("/").content.decode()


def test_banner_and_its_html(world):
    res = _browser().get("/")
    html = res.content.decode()
    assert "当サイトのバナー" in html
    assert res.context["banner_html"].startswith('<a href="http://testserver/"><img src="http')
    assert "img/banner-88x31.svg" in res.context["banner_html"]
    svg = (Path(settings.BASE_DIR) / "server/static/img/banner-88x31.svg").read_text(
        encoding="utf-8"
    )
    assert 'width="88" height="31"' in svg and "<script" not in svg


# --- 順位の上下・世界の様子・為替の相場表・NEW! -------------------------------------------


def test_rank_move_from_previous_turn(client, world):
    """前回比は、前のターンの GDP（今のレートで直す）の順位からの上下。"""
    run_turn(force=True)
    run_turn(force=True)
    w = m.World.objects.get()
    a, b, c = m.Island.objects.order_by("id")
    factors = {a.id: 1.0, b.id: 1.0, c.id: 1.0}
    gdp = {a.id: (100.0, 300.0), b.id: (200.0, 200.0), c.id: (300.0, 100.0)}
    for island_id, (before, now) in gdp.items():
        m.StatRecord.objects.filter(island_id=island_id, name="gdp", turn=w.turn - 1).update(
            value=before
        )
        m.StatRecord.objects.filter(island_id=island_id, name="gdp", turn=w.turn).update(value=now)
    rows = {r["island"]: r for r in rankings.island_table(w, a.id, factors=factors)}
    assert (rows[a.id]["rank"], rows[a.id]["previous_rank"], rows[a.id]["move"]) == (1, 3, 2)
    assert rows[b.id]["move"] == 0 and rows[c.id]["move"] == -2
    html = client.get("/").content.decode()
    assert ">前回比<" in html


def test_new_mark_for_young_islands_and_updates(client, world):
    w = m.World.objects.get()
    a, b, _ = m.Island.objects.order_by("id")
    w.turn = rankings.NEW_TURNS + 5
    w.save(update_fields=["turn"])
    m.Island.objects.filter(pk=b.id).update(ext={**b.ext, "founded_turn": w.turn - 1})
    rows = {r["island"]: r for r in client.get("/").context["table"]}
    assert rows[b.id]["new"] and not rows[a.id]["new"]
    SiteUpdate.objects.all().delete()
    today = timezone.localdate()
    SiteUpdate.objects.create(date=today, text="新しい")
    SiteUpdate.objects.create(date=today - timedelta(days=30), text="古い")
    marks = {u["text"]: u["new"] for u in client.get("/").context["updates"]}
    assert marks == {"新しい": True, "古い": False}
    assert "NEW!" in client.get("/").content.decode()


def test_world_summary_totals(client, world):
    run_turn(force=True)
    run_turn(force=True)
    w = m.World.objects.get()
    summary = client.get("/").context["world_summary"]
    population = sum(
        m.StatRecord.objects.filter(island__world=w, turn=w.turn, name="population").values_list(
            "value", flat=True
        )
    )
    totals = {t["name"]: t for t in summary["totals"]}
    assert totals["population"]["value"] == population
    assert totals["population"]["change"] is not None
    assert summary["islands"] == 3 and summary["inactive"] == 0
    # 直せない国（為替レートのない国）はお金の合計に入れず、数を返す
    table = client.get("/").context["table"]
    assert summary["unconverted"] == sum(1 for r in table if r["columns"]["gdp"]["value"] is None)
    assert totals["gdp"]["value"] == sum(r["columns"]["gdp"]["value"] or 0 for r in table)
    assert "世界の様子" in client.get("/").content.decode()


def test_fx_table_rates_and_change(client, world):
    """相場表は、通貨 1 が基準の通貨でいくらか。直接の相場は前のターンの統計からの上下も出す。"""
    run_turn(force=True)
    run_turn(force=True)
    w = m.World.objects.get()
    a, b, c = m.Island.objects.order_by("id")
    ext = a.ext
    ext["trade"]["rates"] = {str(b.id): 2 * BASIS}
    m.Island.objects.filter(pk=a.id).update(ext=ext)
    m.StatRecord.objects.create(island=a, turn=w.turn - 1, name=f"fx.{b.id}", value=1.6)
    rows = client.get("/").context["fx"]
    assert [r["currency"] for r in rows] == [b.id]  # c は直せない
    assert rows[0]["rate"] == 2.0 and rows[0]["direct"] and rows[0]["change"] == 25.0
    assert rows[0]["text"].startswith("2.0000")
    html = client.get("/").content.decode()
    assert "為替の相場表" in html and "+25.00%" in html


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
    shown = {
        "table": context["table"],
        "records": context["records"],
        "world_summary": context["world_summary"],
        "fx": context["fx"],
    }
    plain = json.loads(json.dumps(shown, default=str))
    for name, value in plain.items():
        assert data[name] == value, name
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


# --- 更新履歴（管理画面と、コミットの「更新履歴:」の行） ---------------------------------


def test_updates_come_from_commit_lines():
    log = (
        "\x1eaaa\x1f2026-10-19T02:30:00+09:00\x1f島の一覧を作り直す\n\n"
        "更新履歴: トップを島の一覧の表にしました。\n"
        "- 更新履歴・管理人より（箇条書きの中の言葉は拾わない）\n"
        "\x1ebbb\x1f2026-10-18T16:00:00+00:00\x1f地図\n\n更新履歴：地図を大きくしました。\n"
        "\x1eccc\x1f2026-10-17T12:00:00+09:00\x1f試験だけ\n\n更新履歴:\n"
    )
    rows = top.parse_git_updates(log)
    assert rows == [
        {"key": "git:aaa:2", "date": "2026-10-19", "text": "トップを島の一覧の表にしました。"},
        # UTC の 16 時は JST の翌日
        {"key": "git:bbb:2", "date": "2026-10-19", "text": "地図を大きくしました。"},
    ]


def test_sync_copies_once_and_keeps_admin_edits(client, world, monkeypatch):
    SiteUpdate.objects.all().delete()
    found = [{"key": "git:aaa:2", "date": "2026-10-19", "text": "表にしました。"}]
    monkeypatch.setattr(top, "git_updates", lambda: list(found))
    assert top.sync_updates() > 0  # コミットの行と top.yaml の昔の分
    row = SiteUpdate.objects.get(key="git:aaa:2")
    row.text = "トップを表にしました。"  # 管理画面で直す
    row.save()
    assert top.sync_updates() == 0  # 直した行を作り直さない
    SiteUpdate.objects.create(date=date(2026, 10, 20), text="管理画面で足したお知らせ")
    SiteUpdate.objects.create(date=date(2026, 10, 21), text="隠した行", hidden=True)
    texts = [u["text"] for u in client.get("/").context["updates"]]
    assert texts[:2] == ["管理画面で足したお知らせ", "トップを表にしました。"]
    assert "隠した行" not in texts and "表にしました。" not in texts
    assert [u["text"] for u in client.get("/api/world").json()["updates"]] == texts


def test_updates_without_git(monkeypatch):
    def broken(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(top.subprocess, "run", broken)
    assert top.git_updates() == []


def test_default_base_is_the_currency_that_compares_the_most_countries(world, monkeypatch):
    """順位の初めの通貨（U05）: 直せる国がいちばん多い通貨。同じ数なら見る人の国、次に番号順。"""
    a, b, c = m.Island.objects.order_by("id")
    w = m.World.objects.get()
    monkeypatch.setattr(rankings, "comparable_counts", lambda _: {a.id: 1, b.id: 3, c.id: 3})
    assert rankings.default_base(w, a) == b.id  # 自国の通貨では 1 か国しか比べられない
    assert rankings.default_base(w, c) == c.id  # 同じ数なら自国
    assert rankings.default_base(w, None) == b.id  # さらに同じなら番号の小さい国
    monkeypatch.setattr(rankings, "comparable_counts", lambda _: {})
    assert rankings.default_base(w, a) == a.id and rankings.default_base(w, None) is None


def test_turn_schedule_tells_paused_updating_late_and_unscheduled(client, world):
    """更新の予定（U07）: 画面と同じ状態を /api/me と国の要約でも読める。"""
    w = m.World.objects.get()
    island = m.Island.objects.order_by("id").first()
    w.next_turn_at = timezone.now() + timedelta(hours=2)
    w.save()
    me = client.get("/api/me").json()["turn_schedule"]
    assert me["state"] == "scheduled" and me["next_turn_at"] and me["server_now"]
    assert me["interval_minutes"] > 0
    summary = client.get(f"/api/islands/{island.id}/summary").json()["turn_schedule"]
    assert summary["state"] == "scheduled"
    w.next_turn_at = timezone.now() - timedelta(minutes=5)
    w.save()
    assert display.turn_schedule(w)["state"] == "late"
    w.next_turn_at = None
    assert display.turn_schedule(w)["state"] == "unscheduled"
    w.updating_since = timezone.now()
    assert display.turn_schedule(w)["state"] == "updating"
    w.paused = True
    assert display.turn_schedule(w)["state"] == "paused"
    page = client.get("/").content.decode()
    assert "turn-schedule" in page
