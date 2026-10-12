"""フェーズ1のサーバー側: 施設・在庫・統計の保存、建国、命令の API、登録の制限、掲示板、画面。"""

from __future__ import annotations

import copy

import pytest
from django.contrib.auth.models import User

from hakoniwa_core.state.serialize import world_to_dict
from server.board.models import Post, Thread
from server.game import get_game
from server.orders.models import Order
from server.turns.services import rollback, run_turn
from server.world import models as m
from server.world.repository import WorldRepository

pytestmark = pytest.mark.django_db


def _snapshot(world: m.World) -> dict:
    """DB から読んだ世界の写し（ターンログは DB では別の表なので除く）。"""
    data = world_to_dict(WorldRepository(m.World.objects.get(pk=world.pk)).load())
    for island in data["islands"]:
        island.pop("log")
    return data


# --- 保存 ---------------------------------------------------------------------


def test_turn_saves_facilities_stock_and_stats(world):
    island = m.Island.objects.order_by("id").first()
    assert island.facilities.exists() and island.stocks.exists()
    run_turn(force=True)
    stats = dict(island.stats.filter(turn=1).values_list("name", "value"))
    assert stats["population"] > 0 and "food.ratio" in stats
    assert island.facilities.filter(built=True, operating_rate__gt=0).exists()
    assert not island.stocks.filter(quantity__lte=0).exists()


def test_turns_through_db_match_turns_in_memory(world):
    """DB に書いて読み直しながら進めても、メモリの中だけで進めても同じ結果になる。"""
    state = WorldRepository(world).load()
    memory = copy.deepcopy(state)
    engine = get_game().engine
    for _ in range(4):
        engine.run_turn(memory)
        run_turn(force=True)
    expected = world_to_dict(memory)
    for island in expected["islands"]:
        island.pop("log")
    assert _snapshot(world) == expected


def test_rollback_restores_facilities_stock_and_stats(world):
    run_turn(force=True)
    after_first = _snapshot(world)
    island = m.Island.objects.order_by("id").first()
    target = island.facilities.exclude(x=island.capital_x, y=island.capital_y).first()
    Order.objects.create(
        island=island, seq=1, kind="demolish", payload={"x": target.x, "y": target.y}
    )
    run_turn(force=True)
    run_turn(force=True)
    assert not island.facilities.filter(pk=target.pk).exists()
    rollback(1)
    restored = _snapshot(world)
    assert Order.objects.get().status == "pending"
    for data in (restored, after_first):
        for i in data["islands"]:
            i.pop("orders")
    assert restored == after_first
    assert not m.StatRecord.objects.filter(turn__gt=1).exists()


# --- 建国 ---------------------------------------------------------------------


def _login(client, name="hanako") -> User:
    user = User.objects.create_user(name, password="a-long-pass-123")
    client.force_login(user)
    return user


def _plan(client, seed=5) -> dict:
    defs = client.get("/api/defs").json()
    land = client.post(
        "/api/founding/random-shape", {"seed": seed}, content_type="application/json"
    ).json()["land"]
    regime = {axis: next(iter(a["choices"])) for axis, a in defs["axes"].items()}
    preview = client.post(
        "/api/founding/terrain",
        {"land": land, "seed": seed, "regime": regime},
        content_type="application/json",
    ).json()
    assert preview["problems"] == []
    assert len(preview["terrain"]) == defs["founding"]["size"] ** 2
    cell = client.get("/api/founding/cells").json()["free"][0]
    return {
        "land": land,
        "seed": seed,
        "capital": preview["suggested_capital"],
        "regime": regime,
        "names": {
            "country": "花の国",
            "capital": "花都",
            "currency": "花",
            "currency_symbol": "H",
            "leader": "花子",
        },
        "resources": ["coal", "iron_ore"],
        "edits": [],
        "cell": cell,
    }


def test_found_page_requires_login(client, world):
    res = client.get("/found/")
    assert res.status_code == 302 and "/accounts/login/" in res["Location"]


def test_founding_creates_one_island_per_user(client, world):
    user = _login(client)
    assert client.get("/found/").status_code == 200
    plan = _plan(client)
    res = client.post("/api/founding", plan, content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    island = m.Island.objects.get(owner=user)
    assert res.json()["url"] == f"/islands/{island.id}/"
    assert island.name == "花の国" and island.resources == ["coal", "iron_ore"]
    assert (island.capital_x, island.capital_y) == tuple(plan["capital"])
    assert island.facilities.filter(x=island.capital_x, y=island.capital_y).exists()
    assert island.tiles.count() == island.size**2
    # 2つ目は作れない。建国の画面は自分の島へ移る
    again = client.post("/api/founding", _plan(client, 6), content_type="application/json")
    assert again.status_code == 400 and again.json()["problems"]
    assert client.get("/found/")["Location"] == f"/islands/{island.id}/"


def test_founding_reports_problems(client, world):
    _login(client)
    plan = _plan(client)
    plan["land"] = plan["land"][:10]
    plan["names"]["country"] = ""
    plan["cell"] = [world.islands.first().wx, world.islands.first().wy]
    res = client.post("/api/founding", plan, content_type="application/json")
    assert res.status_code == 400
    assert len(res.json()["problems"]) >= 2
    assert not m.Island.objects.filter(name="").exists()
    bad = client.post("/api/founding", {"land": "x"}, content_type="application/json")
    assert bad.status_code == 400


# --- 命令 ---------------------------------------------------------------------


def _own_island(client) -> m.Island:
    user = _login(client)
    island = m.Island.objects.order_by("id").first()
    island.owner = user
    island.save()
    return island


def test_orders_can_only_be_changed_by_owner(client, world):
    island = m.Island.objects.order_by("id").first()
    url = f"/api/islands/{island.id}/orders"
    body = {"orders": [{"kind": "rename_island", "payload": {"name": "乗っ取り"}}]}
    assert client.put(url, body, content_type="application/json").status_code == 403
    _login(client, "other")
    assert client.put(url, body, content_type="application/json").status_code == 403
    assert not Order.objects.exists()


def test_orders_put_get_and_run(client, world):
    island = _own_island(client)
    url = f"/api/islands/{island.id}/orders"
    body = {
        "orders": [
            {"kind": "rename_island", "payload": {"name": "新しい名前"}},
            {"kind": "build", "payload": {"facility": "farm", "x": 0, "y": 0}},
        ]
    }
    res = client.put(url, body, content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    data = res.json()
    assert [o["kind"] for o in data["orders"]] == ["rename_island", "build"]
    assert "農場 (0, 0)" in data["orders"][1]["text"]
    # 海には建てられないが、前の命令しだいで変わることもあるので警告だけにする
    assert [w["index"] for w in data["warnings"]] == [1]
    assert client.get(url).json()["admin_power"] > 0

    run_turn(force=True)
    island.refresh_from_db()
    assert island.name == "新しい名前"
    recent = client.get(url).json()["recent"]
    assert {r["status"] for r in recent} == {"done", "failed"}
    assert any(r["reason"] for r in recent if r["status"] == "failed")


def test_orders_reject_bad_input(client, world):
    island = _own_island(client)
    url = f"/api/islands/{island.id}/orders"
    unknown = {"orders": [{"kind": "launch_rocket", "payload": {}}]}
    res = client.put(url, unknown, content_type="application/json")
    assert res.status_code == 400 and res.json()["errors"][0]["index"] == 0
    wrong = {"orders": [{"kind": "set_priority", "payload": {"x": 1, "y": 1, "priority": 99}}]}
    assert client.put(url, wrong, content_type="application/json").status_code == 400
    limit = get_game().config.get("orders.max_queue")
    many = {"orders": [{"kind": "rename_island", "payload": {"name": "a"}}] * (limit + 1)}
    assert client.put(url, many, content_type="application/json").status_code == 400
    assert client.put(url, "not json", content_type="application/json").status_code == 400
    assert not Order.objects.exists()


def test_order_check(client, world):
    island = m.Island.objects.order_by("id").first()
    url = f"/api/islands/{island.id}/orders/check"
    sea = {"kind": "build", "payload": {"facility": "farm", "x": 0, "y": 0}}
    data = client.post(url, sea, content_type="application/json").json()
    assert data["ok"] is False and data["text"]
    rename = {"kind": "rename_island", "payload": {"name": "よい名前"}}
    assert client.post(url, rename, content_type="application/json").json()["ok"] is True


# --- 登録・掲示板・画面 -----------------------------------------------------------


def _signup(client, name, ip):
    return client.post(
        "/accounts/signup/",
        {"username": name, "password1": "a-long-pass-123", "password2": "a-long-pass-123"},
        REMOTE_ADDR=ip,
    )


def test_signup_is_limited_per_ip(client, db):
    assert _signup(client, "first", "10.0.0.1").status_code == 302
    client.logout()
    res = _signup(client, "second", "10.0.0.1")
    assert res.status_code == 200 and "すでに登録されています" in res.content.decode()
    assert not User.objects.filter(username="second").exists()
    assert _signup(client, "third", "10.0.0.2")["Location"] == "/found/"


def test_board(client, db):
    assert client.get("/board/").status_code == 200
    assert client.post("/board/new/", {"title": "t", "body": "b"}).status_code == 302
    assert not Thread.objects.exists()
    _login(client)
    res = client.post("/board/new/", {"title": "はじめまして", "body": "よろしく<b>"})
    thread = Thread.objects.get()
    assert res["Location"] == f"/board/{thread.id}/"
    client.post(f"/board/{thread.id}/", {"body": "二つ目"})
    page = client.get(f"/board/{thread.id}/").content.decode()
    assert "よろしく&lt;b&gt;" in page and "二つ目" in page
    assert "はじめまして" in client.get("/board/").content.decode()
    Post.objects.filter(body="二つ目").update(hidden=True)
    assert "二つ目" not in client.get(f"/board/{thread.id}/").content.decode()
    Thread.objects.update(hidden=True)
    assert client.get(f"/board/{thread.id}/").status_code == 404


def test_pages_render(client, world):
    run_turn(force=True)
    island = m.Island.objects.order_by("id").first()
    for url in ["/", "/world/", "/help/", f"/islands/{island.id}/log/"]:
        assert client.get(url).status_code == 200, url
    log = client.get(f"/islands/{island.id}/log/").content.decode()
    assert "ターン 1" in log
    page = client.get(f"/islands/{island.id}/").content.decode()
    assert 'id="orders"' not in page and "国勢" in page
    _own_island(client)
    page = client.get(f"/islands/{island.id}/").content.decode()
    assert 'id="orders"' in page and "自国" in page
    assert island.name in client.get("/world/").content.decode()
