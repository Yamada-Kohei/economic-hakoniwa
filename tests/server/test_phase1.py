"""フェーズ1のサーバー側: 施設・在庫・統計の保存、建国、命令の API、登録の制限、掲示板、画面。"""

from __future__ import annotations

import copy
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from hakoniwa_core.state.serialize import world_to_dict
from server.accounts.models import SignupRecord
from server.accounts.views import ip_hash
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
    # 最初の更新の前から、人口・国の倉庫・国庫が島の画面に出る
    stats = dict(island.stats.values_list("name", "value"))
    assert stats["population"] > 0 and stats["treasury"] > 0 and stats["stock.wood"] > 0
    page = client.get(f"/islands/{island.id}/").content.decode()
    assert "国庫" in page and "未集計です" not in page and "記録がありません" not in page
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
    island = _own_island(client)
    url = f"/api/islands/{island.id}/orders/check"
    sea = {"kind": "build", "payload": {"facility": "farm", "x": 0, "y": 0}}
    data = client.post(url, sea, content_type="application/json").json()
    assert data["ok"] is False and data["text"]
    rename = {"kind": "rename_island", "payload": {"name": "よい名前"}}
    assert client.post(url, rename, content_type="application/json").json()["ok"] is True
    # 建てられる場所なら、通勤圏の労働者の数を添える（首都の隣には町の住民がいる）
    free = island.tiles.filter(terrain="plain", population=0).exclude(
        x__in=[f.x for f in island.facilities.all()]
    )
    tile = min(free, key=lambda t: abs(t.x - island.capital_x) + abs(t.y - island.capital_y))
    near = {"kind": "build", "payload": {"facility": "farm", "x": tile.x, "y": tile.y}}
    data = client.post(url, near, content_type="application/json").json()
    assert data["ok"] is True, data
    commute = data["commute"]
    assert commute["radius"] == 3 and commute["labor"] >= commute["employed"] >= 0
    assert commute["labor"] > 0
    assert isinstance(data["notes"], list)  # 建てた後に止まりそうなわけ（なければ空）


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


def test_pages_render(client, world):
    run_turn(force=True)
    island = m.Island.objects.order_by("id").first()
    for url in ["/", "/world/", "/help/", f"/islands/{island.id}/log/"]:
        assert client.get(url).status_code == 200, url
    log = client.get(f"/islands/{island.id}/log/").content.decode()
    assert "ターン 1" in log
    page = client.get(f"/islands/{island.id}/").content.decode()
    assert 'id="orders"' not in page and "国勢" in page
    assert 'id="order-notice"' not in page
    _own_island(client)
    page = client.get(f"/islands/{island.id}/").content.decode()
    assert 'id="orders"' in page and "自国" in page
    # マスの欄から足した命令の結果は、地図の上に重ねて出す（命令の欄まで見に行かなくてよいように）
    assert 'id="order-notice"' in page
    assert island.name in client.get("/world/").content.decode()


def test_signup_groups_ipv6_by_64_and_ignores_unreadable_ip(client, db, caplog):
    assert _signup(client, "v6a", "2001:db8:1:2::1").status_code == 302
    client.logout()
    # 同じ /64 の別の接続元は同じ回線として数える
    res = _signup(client, "v6b", "2001:db8:1:2:ffff::9")
    assert res.status_code == 200 and "すでに登録されています" in res.content.decode()
    client.logout()
    assert _signup(client, "v6c", "2001:db8:1:3::1")["Location"] == "/found/"
    client.logout()
    # IPv4 を IPv6 の形で書いたものは IPv4 と同じ接続元
    assert _signup(client, "m1", "::ffff:10.0.0.7").status_code == 302
    client.logout()
    assert _signup(client, "m2", "10.0.0.7").status_code == 200
    client.logout()
    # ポート付き・括弧付きも読める
    assert _signup(client, "p1", "10.1.1.1:5555").status_code == 302
    client.logout()
    assert _signup(client, "p2", "10.1.1.1").status_code == 200
    client.logout()
    assert _signup(client, "p3", "[2001:db8:9:9::1]:80").status_code == 302
    client.logout()
    assert _signup(client, "p4", "2001:db8:9:9::2").status_code == 200
    client.logout()
    # 接続元が読めないときは数えない（全員が同じ接続元になって誰も登録できなくなるのを避ける）
    for i in range(3):
        assert _signup(client, f"blank{i}", "")["Location"] == "/found/"
        client.logout()
    assert not SignupRecord.objects.filter(ip_hash=ip_hash("")).exists()
    assert "接続元の IP が読めない" in caplog.text


def test_admin_can_let_one_account_register_more_from_the_same_ip(client, db):
    assert _signup(client, "first", "10.2.2.2").status_code == 302
    client.logout()
    assert _signup(client, "second", "10.2.2.2").status_code == 200  # 上限は 1 のまま
    record = SignupRecord.objects.get(user__username="first")
    record.extra = 2  # 管理画面で「追加で登録できる人数」を入れる
    record.save()
    for name in ("second", "third"):
        assert _signup(client, name, "10.2.2.2")["Location"] == "/found/"
        client.logout()
    assert _signup(client, "fourth", "10.2.2.2").status_code == 200
    assert not User.objects.filter(username="fourth").exists()
    # ほかの接続元には効かず、古くなっても認めた分の記録は消えない
    assert _signup(client, "other", "10.2.2.3")["Location"] == "/found/"
    SignupRecord.objects.filter(pk=record.pk).update(created_at=timezone.now() - timedelta(days=90))
    client.logout()
    _signup(client, "after-expiry", "10.2.2.9")  # 掃除が走る
    assert SignupRecord.objects.filter(pk=record.pk).exists()
