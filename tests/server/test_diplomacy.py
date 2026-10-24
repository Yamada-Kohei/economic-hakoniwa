"""フェーズ4-1 のサーバー側: 国際会議場（声明と、ターンログから作る出来事）、外交の画面と API、
国の要約と差分、命令の説明。"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.hall.models import Statement
from server.turns.services import rollback, run_turn
from server.web import display
from server.world import models as m

pytestmark = pytest.mark.django_db


def _players(world):
    """サンプルの島 3 つに持ち主を付ける（議会の採決が要らない権威主義に）。"""
    out = []
    for island in m.Island.objects.order_by("id"):
        user = User.objects.create_user(f"p{island.id}", password="a-long-pass-123")
        island.owner = user
        island.regime = {**island.regime, "politics": "authoritarian"}
        island.save()
        client = Client()
        client.force_login(user)
        out.append((client, island))
    return out


def _orders(client, island, *orders):
    body = {"orders": [{"kind": k, "payload": p} for k, p in orders]}
    res = client.put(f"/api/islands/{island.id}/orders", body, content_type="application/json")
    assert res.status_code == 200, res.content
    return res.json()


def _treaty(players):
    (ca, a), (cb, b), _ = players
    _orders(ca, a, ("propose_treaty", {"partner": b.id, "clauses": [{"type": "non_aggression"}]}))
    run_turn(force=True)
    return ca, a, cb, b


def test_treaty_flow_shows_on_screens_and_the_hall(world):
    players = _players(world)
    ca, a, cb, b = _treaty(players)
    # 返事待ちの提案は当事国の持ち主にだけ見える
    mine = cb.get(f"/api/islands/{b.id}/diplomacy").json()
    assert [t["title"] for t in mine["incoming"]] == ["不可侵条約"]
    assert mine["choices"]["partners"] and mine["incoming"][0]["proposer"]["id"] == a.id
    public = Client().get(f"/api/islands/{b.id}/diplomacy").json()
    assert "incoming" not in public and "choices" not in public and public["treaties"] == []
    summary = cb.get(f"/api/islands/{b.id}/summary").json()
    assert summary["diplomacy"]["to_answer"][0]["id"] == mine["incoming"][0]["id"]
    assert any("条約の提案" in text for text in summary["alerts"])

    html = cb.get(f"/islands/{b.id}/diplomacy/").content.decode()
    assert 'data-order="sign_treaty"' in html and 'data-order="propose_treaty"' in html
    assert 'data-order="declare_war"' in html
    html = Client().get(f"/islands/{b.id}/diplomacy/").content.decode()
    assert "data-order" not in html and "戦争の状態" in html

    treaty = mine["incoming"][0]["id"]
    _orders(cb, b, ("sign_treaty", {"treaty": treaty}))
    run_turn(force=True)
    data = Client().get(f"/api/islands/{a.id}/diplomacy").json()
    assert [t["id"] for t in data["treaties"]] == [treaty]
    assert data["treaties"][0]["clauses"][0]["name"] == "不可侵"

    # 効き始めは国際会議場に自動で載る（当事国のログから作る。1 つの出来事に 1 件）
    hall = Client().get("/api/hall").json()
    events = [e for e in hall["entries"] if e["type"] == "event"]
    assert len(events) == 1 and a.name in events[0]["text"] and b.name in events[0]["text"]
    assert hall["treaties"][0]["id"] == treaty and hall["wars"] == []
    page = Client().get("/hall/").content.decode()
    assert "国際会議場" in page and "不可侵" in page


def test_war_shows_in_the_hall_and_summaries(world):
    (ca, a), (cb, b), _ = _players(world)
    _orders(ca, a, ("declare_war", {"target": b.id, "reason": "領海の侵犯"}))
    run_turn(force=True)
    hall = Client().get("/api/hall").json()
    assert [(w["attacker"]["id"], w["defender"]["id"]) for w in hall["wars"]] == [(a.id, b.id)]
    text = next(e["text"] for e in hall["entries"] if e["type"] == "event")
    assert "宣戦" in text and "領海の侵犯" in text
    for client, island, other in ((ca, a, b), (cb, b, a)):
        brief = client.get(f"/api/islands/{island.id}/summary").json()["diplomacy"]
        assert [w["with"]["id"] for w in brief["wars"]] == [other.id]
        assert any(
            "戦争" in t for t in client.get(f"/api/islands/{island.id}/summary").json()["alerts"]
        )
    changes = cb.get(f"/api/islands/{b.id}/changes", {"since": 0}).json()
    assert any(e["type"] == "event" for e in changes["hall"])
    assert any("宣戦を布告されました" in e["text"] for e in changes["log"])


def test_hall_events_disappear_with_a_rollback(world):
    (ca, a), (_, b), _ = _players(world)
    _orders(ca, a, ("declare_war", {"target": b.id}))
    run_turn(force=True)
    assert Client().get("/api/hall").json()["wars"]
    rollback(0)
    hall = Client().get("/api/hall").json()
    assert hall["wars"] == [] and hall["entries"] == []


def test_statements(world, settings):
    (ca, a), (cb, b), _ = _players(world)
    assert Client().post("/api/hall", {"title": "a", "body": "b"}).status_code == 403
    res = ca.post(
        "/api/hall",
        {"title": "ごあいさつ", "body": "各国に平和を。"},
        content_type="application/json",
    )
    assert res.status_code == 201
    bad = ca.post("/api/hall", {"title": "", "body": "x"}, content_type="application/json")
    assert bad.status_code == 400
    entries = Client().get("/api/hall").json()["entries"]
    assert entries[0]["type"] == "statement" and entries[0]["island"]["id"] == a.id
    assert "各国に平和を。" in Client().get("/hall/").content.decode()

    # 画面からも出せる。管理者が非表示にすると見えなくなる
    cb.post("/hall/", {"title": "抗議", "body": "遺憾です"})
    assert Statement.objects.count() == 2
    Statement.objects.filter(title="抗議").update(hidden=True)
    titles = [e["title"] for e in Client().get("/api/hall").json()["entries"]]
    assert titles == ["ごあいさつ"]

    # 出しすぎの歯止め
    settings.HAKONIWA_HALL_PER_DAY = 1
    res = ca.post("/api/hall", {"title": "二度目", "body": "x"}, content_type="application/json")
    assert res.status_code == 429


def test_the_hall_has_the_treaty_form_for_island_owners_only(world):
    """国際会議場からも条約を提案できる（外交の画面と同じ入力欄と命令の欄）。声明の欄では結べない
    ことも書く。島を持たない人と読むだけの人には出ない。"""
    (ca, a), (_, b), _ = _players(world)
    html = ca.get("/hall/").content.decode()
    assert 'data-order="propose_treaty"' in html and 'id="propose"' in html
    assert f'data-url="/api/islands/{a.id}/orders"' in html and 'id="orders"' in html
    assert "js/reports/diplomacy.js" in html and "声明の書き込み欄では結べません" in html
    partners = html.split('name="partner"')[1].split("</select>")[0]
    assert (
        f'<option value="{b.id}">{b.name}' in partners
        and f'<option value="{a.id}">' not in partners
    )
    public = Client().get("/hall/").content.decode()
    assert "data-order" not in public and 'id="orders"' not in public
    stranger = Client()
    stranger.force_login(User.objects.create_user("nobody", password="a-long-pass-123"))
    html = stranger.get("/hall/").content.decode()
    assert "data-order" not in html and 'id="orders"' not in html
    # 画面の入力欄が出す命令は、外交の画面と同じ命令でそのまま通る
    _orders(ca, a, ("propose_treaty", {"partner": b.id, "clauses": [{"type": "non_aggression"}]}))


def test_order_descriptions(world):
    (_, a), (_, b), _ = _players(world)
    text = display.describe_order("declare_war", {"target": b.id, "reason": "理由"})
    assert b.name in text and "理由" in text
    text = display.describe_order(
        "propose_treaty", {"partner": b.id, "title": "仲良し", "clauses": [{"type": "alliance"}]}
    )
    assert "仲良し" in text and "同盟" in text and b.name in text
    assert "3 番" in display.describe_order("sign_treaty", {"treaty": 3})


def test_economic_clauses_show_their_terms_on_screens_summary_and_orders(world):
    (ca, a), (cb, b), _ = _players(world)
    clauses = [
        {"type": "swap"},
        {"type": "aid", "payer": a.id, "amount": 500, "currency": a.id, "period": 2, "count": 3},
        {"type": "peg", "pegger": b.id, "rate": 1.5},
    ]
    _orders(ca, a, ("propose_treaty", {"partner": b.id, "clauses": clauses}))
    run_turn(force=True)
    mine = cb.get(f"/api/islands/{b.id}/diplomacy").json()
    [offer] = mine["incoming"]
    by_type = {c["type"]: c for c in offer["clauses"]}
    assert set(by_type) == {"swap", "aid", "peg"}
    assert by_type["aid"]["terms"]["payer"]["id"] == a.id and by_type["aid"]["terms"]["count"] == 3
    assert "3 回" in by_type["aid"]["detail"] and "1.5" in by_type["peg"]["detail"]
    assert by_type["swap"]["terms"] == {}
    assert "通貨の融通" in display.clause_summary({"type": "swap"})
    html = cb.get(f"/islands/{b.id}/diplomacy/").content.decode()
    assert 'name="aid_amount"' in html and 'name="contract_good"' in html
