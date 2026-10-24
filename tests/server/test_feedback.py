"""不具合の報告（W-146）: 画面と API と MCP から送れ、島・ターン・版・直前の命令が自動で添わること、
連投の歯止め、本人だけが一覧を読めること、管理画面で状態を扱えること。"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.accounts import keys
from server.feedback import services
from server.feedback.models import BugReport
from server.turns.services import get_world
from server.world import models as m
from tests.server.test_ai_players import _keyed, _tool

pytestmark = pytest.mark.django_db

URL = "/api/bug-reports"


def _player(world, name="reporter"):
    user = User.objects.create_user(name, password="a-long-pass-123")
    island = m.Island.objects.order_by("id").first()
    island.owner = user
    island.save()
    client = Client()
    client.force_login(user)
    return client, user, island


def _post(who, **body):
    return who.post(URL, json.dumps(body), content_type="application/json")


def test_a_report_carries_the_context_and_returns_a_number(world):
    client, user, island = _player(world)
    client.put(
        f"/api/islands/{island.id}/orders",
        json.dumps({"orders": [{"kind": "set_taxes", "payload": {"income_tax": 10}}]}),
        content_type="application/json",
    )
    long_error = "x" * 1000
    res = _post(
        client,
        text="  財政の画面で表が崩れる  ",
        kind="display",
        url="/islands/1/fiscal/",
        client={
            "viewport": "390x800",
            "user_agent": "UA/1.0",
            "unknown": "捨てる",
            "js_errors": [{"message": long_error, "page": "/islands/1/fiscal/", "evil": "捨てる"}],
        },
    )
    assert res.status_code == 201
    number = res.json()["id"]
    report = BugReport.objects.get(pk=number)
    assert report.text == "財政の画面で表が崩れる" and report.kind == "display"
    assert report.user == user and report.island == island and report.status == "new"
    assert report.world_turn == get_world().turn and report.url == "/islands/1/fiscal/"
    assert report.version and report.via == "web"
    ctx = report.context
    assert ctx["viewport"] == "390x800" and ctx["user_agent"] == "UA/1.0" and "unknown" not in ctx
    assert len(ctx["js_errors"][0]["message"]) == services.FIELD_MAX
    assert "evil" not in ctx["js_errors"][0]
    assert [o["kind"] for o in ctx["last_orders"]] == ["set_taxes"]


def test_only_logged_in_people_can_report_and_see_their_own(world):
    client, user, _ = _player(world)
    assert _post(Client(), text="x").status_code == 403
    assert Client().get(URL).status_code == 403
    for bad in ({"text": ""}, {"text": "a" * 2001}, {"text": "a", "kind": "zzz"}):
        assert _post(client, **bad).status_code == 400
    assert _post(client, text="1つめ").status_code == 201
    other = User.objects.create_user("other", password="a-long-pass-123")
    BugReport.objects.create(user=other, text="他人の報告")
    rows = client.get(URL).json()["reports"]
    assert [r["text"] for r in rows] == ["1つめ"] and rows[0]["status_name"] == "未読"
    BugReport.objects.filter(user=user).update(status="done")
    assert client.get(URL).json()["reports"][0]["status_name"] == "済み"


def test_flooding_is_stopped(world, settings):
    client, _, _ = _player(world)
    assert _post(client, text="同じ内容").status_code == 201
    dup = _post(client, text="同じ内容")
    assert dup.status_code == 429 and "報告済み" in dup.json()["error"]
    assert _post(client, text="2").status_code == 201
    assert _post(client, text="3").status_code == 201
    assert _post(client, text="4").status_code == 429  # 1 分に 3 件まで
    BugReport.objects.update(created_at=BugReport.objects.first().created_at.replace(year=2000))
    settings.HAKONIWA_BUG_REPORTS_PER_DAY = 2
    assert _post(client, text="5").status_code == 201
    assert _post(client, text="6").status_code == 201
    assert _post(client, text="7").status_code == 429


def test_the_page_has_the_report_button_for_logged_in_people_only(world):
    client, _, _ = _player(world)
    html = client.get("/").content.decode()
    assert "data-bug-open" in html and 'id="bug-report"' in html and "js/feedback.js" in html
    assert "data-bug-open" not in Client().get("/").content.decode()


def test_reports_through_the_ai_entrance(world):
    _, user, island = _player(world, "ai-player")
    _, token = keys.issue(user)
    client = _keyed(token)
    text, error = _tool(client, "report_bug", text="MCP から", kind="rule")
    assert not error and json.loads(text)["id"]
    report = BugReport.objects.get()
    assert report.via == "mcp" and report.island == island and report.kind == "rule"
    text, error = _tool(client, "bug_reports")
    assert not error and json.loads(text)["reports"][0]["text"] == "MCP から"
    # 鍵で API を直接呼んだときは入り口 api
    res = client.post(URL, json.dumps({"text": "API から"}), content_type="application/json")
    assert res.status_code == 201 and BugReport.objects.get(pk=res.json()["id"]).via == "api"


def test_admin_lists_and_changes_the_status(world):
    admin = User.objects.create_superuser("boss", password="a-long-pass-123")
    client, _, _ = _player(world)
    number = _post(client, text="管理画面で見る").json()["id"]
    boss = Client()
    boss.force_login(admin)
    assert "管理画面で見る" in boss.get("/admin/feedback/bugreport/").content.decode()
    page = boss.get(f"/admin/feedback/bugreport/{number}/change/").content.decode()
    assert "何が起きたか" in page and "管理画面で見る" in page
    res = boss.post(
        "/admin/feedback/bugreport/",
        {"action": "mark_working", "_selected_action": [number]},
    )
    assert res.status_code == 302 and BugReport.objects.get(pk=number).status == "working"


def test_nul_characters_are_dropped(world):
    """PostgreSQL は文字列の中の NUL を受けないので、報告の文・URL・添え物から除く。"""
    client, _, _ = _player(world)
    res = _post(
        client, text="a\u0000b", url="/x\u0000y", client={"js_errors": [{"message": "m\u0000"}]}
    )
    assert res.status_code == 201
    report = BugReport.objects.get()
    assert report.text == "ab" and report.url == "/xy"
    assert report.context["js_errors"][0]["message"] == "m"
