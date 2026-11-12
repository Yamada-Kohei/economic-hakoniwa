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
    # 宣戦などの戻せない命令は確かめが要るので、確かめた印（confirm）を付けて送る（UI-75）
    body = {"orders": [{"kind": k, "payload": p} for k, p in orders], "confirm": True}
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


def test_mutual_war_is_visible_in_the_api_screens_summary_and_mcp(world):
    (ca, a), (cb, b), _ = _players(world)
    _orders(ca, a, ("declare_war", {"target": b.id}))
    _orders(cb, b, ("declare_war", {"target": a.id}))
    run_turn(force=True)
    assert ca.get(f"/api/islands/{a.id}/diplomacy").json()["wars"][0]["mutual"] is True
    assert ca.get(f"/api/islands/{a.id}/summary").json()["diplomacy"]["wars"][0]["mutual"] is True
    assert "互いの宣戦" in ca.get(f"/islands/{a.id}/diplomacy/").content.decode()
    assert "互いの宣戦" in ca.get("/hall/").content.decode()
    assert "互いの宣戦" in ca.get("/help/").content.decode()
    from server.web.mcp import REPORTS

    assert "互いの宣戦" in REPORTS["diplomacy"]
    a.world.refresh_from_db()
    a.world.ext["diplomacy"]["wars"][0].pop("mutual")
    a.world.save()
    assert ca.get(f"/api/islands/{a.id}/diplomacy").json()["wars"][0]["mutual"] is False


def test_removed_country_finishes_war_in_the_api_and_screen(world):
    (ca, a), (_, b), _ = _players(world)
    _orders(ca, a, ("declare_war", {"target": b.id}))
    run_turn(force=True)
    b.delete()
    run_turn(force=True)
    data = ca.get(f"/api/islands/{a.id}/diplomacy").json()
    assert data["wars"] == []
    assert data["past_wars"][0]["ended_reason"] == "island_removed"
    assert "国の削除で終結" in ca.get(f"/islands/{a.id}/diplomacy/").content.decode()


def test_violation_title_survives_treaty_pruning_and_old_records_fall_back(world):
    (ca, a), _, _ = _players(world)
    a.ext["diplomacy"] = {
        "violations": [
            {
                "turn": 1,
                "treaty": 91,
                "clause": "peace",
                "weight": 0.5,
                "reason": "reparations",
                "title": "残す題名",
            },
            {"turn": 1, "treaty": 92, "clause": "peace", "weight": 1, "reason": "reparations"},
            {"turn": 1, "treaty": 93, "clause": "peace", "weight": 1, "reason": "reparations"},
        ]
    }
    a.save()
    a.world.ext = {"diplomacy": {"treaties": [{"id": 92, "title": "古い題名", "parties": []}]}}
    a.world.save()
    rows = ca.get(f"/api/islands/{a.id}/diplomacy").json()["violations"]
    assert [r["treaty_title"] for r in rows] == ["残す題名", "古い題名", ""]


def test_admin_can_hide_hall_words_and_the_treaty_text(world):
    from server.turns.models import TurnLog

    players = _players(world)
    ca, a, cb, b = _treaty(players)
    treaty = cb.get(f"/api/islands/{b.id}/diplomacy").json()["incoming"][0]["id"]
    _orders(cb, b, ("sign_treaty", {"treaty": treaty}))
    run_turn(force=True)
    log = TurnLog.objects.get(kind="treaty_in_force", args__hall=True)
    declared = TurnLog.objects.create(
        island=a,
        turn=2,
        kind="war_declared",
        args={
            "hall": True,
            "a": a.name,
            "b": b.name,
            "turn": 3,
            "text": "隠す理由",
        },
    )
    private = TurnLog.objects.create(island=a, turn=2, kind="facility_started", args={})
    # 同じ条約のほかの出来事（選んでいない）と違反の記録の題名も隠れる
    later = TurnLog.objects.create(
        island=b,
        turn=2,
        kind="treaty_violated",
        args={**log.args, "by": b.name, "clause": "clause_non_aggression", "why": "why_x"},
    )
    b.refresh_from_db()
    b.ext = {
        **(b.ext or {}),
        "diplomacy": {
            "violations": [
                {
                    "turn": 2,
                    "treaty": treaty,
                    "clause": "non_aggression",
                    "weight": 1,
                    "reason": "war_declared",
                    "title": "不可侵条約",
                }
            ]
        },
    }
    b.save()
    admin = Client()
    admin.force_login(User.objects.create_superuser("moderator", password="a-long-pass-123"))
    response = admin.post(
        "/admin/turns/turnlog/",
        {
            "action": "moderate_hall_text",
            "_selected_action": [log.pk, declared.pk, private.pk],
        },
    )
    assert response.status_code == 302
    for event in (log, declared, later):
        event.refresh_from_db()
        assert event.args["moderated"] is True
    private.refresh_from_db()
    assert "moderated" not in private.args
    hall = ca.get("/api/hall").json()
    events = [e for e in hall["entries"] if e["type"] == "event"]
    assert all("運営が非表示にしました" in e["text"] for e in events)
    assert all("隠す理由" not in e["text"] and "不可侵条約" not in e["text"] for e in events)
    shown = hall["treaties"][0]
    assert shown["title"] == shown["text"] == "運営が非表示にしました"
    b.refresh_from_db()
    assert b.ext["diplomacy"]["violations"][0]["title"] == "運営が非表示にしました"


def test_rollback_keeps_later_statements_at_the_restored_turn(world):
    (ca, a), _, _ = _players(world)
    run_turn(force=True)
    run_turn(force=True)
    statement = Statement.objects.create(
        world=a.world, island=a, author=a.owner, title="残る声明", body="本文", turn=2
    )
    rollback(0)
    statement.refresh_from_db()
    assert statement.turn == 0 and statement.title == "残る声明"
    assert ca.get("/api/hall").json()["entries"][0]["turn"] == 0


def test_surrender_is_an_event_in_the_hall(world):
    from hakoniwa_core.military import occupation
    from hakoniwa_core.turn.context import StageContext
    from server.game import get_game
    from server.turns.models import TurnLog
    from server.world.repository import WorldRepository

    (ca, a), (_, b), _ = _players(world)
    _orders(ca, a, ("declare_war", {"target": b.id}))
    run_turn(force=True)
    game = get_game()
    state = WorldRepository(m.World.objects.get(pk=a.world_id)).load()
    ctx = StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.registry,
        formulas={},
        turn=state.turn,
        seed=state.seed,
        stage="operations",
        before=state,
    )
    occupation.surrender(state, ctx, state.islands[b.id], state.islands[a.id])
    entry = next(e for e in state.islands[b.id].log if e.kind == "surrendered")
    TurnLog.objects.create(island=b, turn=state.turn, kind=entry.kind, args=entry.args)
    event = next(e for e in ca.get("/api/hall").json()["entries"] if e.get("kind") == "surrendered")
    assert f"{b.name}が首都を占領され、{a.name}に降伏しました" in event["text"]
    assert "降伏文書" in event["text"]


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
