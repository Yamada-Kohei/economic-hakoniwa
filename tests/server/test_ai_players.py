"""AI が遊ぶための入り口: AI 用の鍵と AI の国の印、国の要約と差分、画面だけにあった中身の API、
MCP のつなぎ先、試し遊びの道具（tools/playtest/hk.py）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client

from server.accounts import keys
from server.accounts.models import ApiKey
from server.letters.models import Letter
from server.orders.models import Order
from server.turns.models import TurnLog
from server.turns.services import run_turn
from server.web import mcp
from server.world.models import Island, World

pytestmark = pytest.mark.django_db

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()  # 鍵で呼んだ回数
    yield
    cache.clear()


def _players(world) -> tuple[list[User], list[Island]]:
    """サンプルの島 3 つに、1 人ずつ持ち主を付ける。"""
    users, islands = [], list(Island.objects.order_by("id"))
    for i, island in enumerate(islands):
        user = User.objects.create_user(f"player{i}", password="a-long-pass-123")
        island.owner = user
        island.save(update_fields=["owner"])
        users.append(user)
    return users, islands


def _keyed(token: str, **extra) -> Client:
    """鍵を付けて呼ぶ（CSRF の印を確かめるクライアントで、鍵なら印が要らないことも見る）。"""
    return Client(enforce_csrf_checks=True, HTTP_AUTHORIZATION=f"Bearer {token}", **extra)


def _set_turn(world: World, turn: int) -> None:
    world.turn = turn
    world.save(update_fields=["turn"])


# --- 鍵 -------------------------------------------------------------------------------


def test_key_page_issues_once_and_keeps_only_a_hash(client, world):
    (alice, _, _), (a, _, _) = _players(world)
    client.force_login(alice)
    assert client.get("/accounts/keys/").status_code == 200
    page = client.post("/accounts/keys/", {"action": "create", "name": "Claude"}).content.decode()
    key = ApiKey.objects.get()
    token = page.split('<code id="new-key">')[1].split("</code>")[0]
    assert token.startswith("hk_") and len(token) > 40
    assert key.name == "Claude" and key.user == alice and key.created_turn == world.turn
    assert key.digest == keys.digest(token) and token not in key.digest
    assert token.startswith(key.prefix) and len(key.prefix) < 12
    # 鍵そのものは2度と出ない
    assert token not in client.get("/accounts/keys/").content.decode()
    assert key.prefix in client.get("/accounts/keys/").content.decode()
    # 見出しの名前から鍵の画面へ
    assert 'href="/accounts/keys/"' in client.get("/").content.decode()

    # 取り消すと使えない。ほかの人の鍵は取り消せない
    other = User.objects.create_user("mallory", password="a-long-pass-123")
    client.force_login(other)
    client.post("/accounts/keys/", {"action": "revoke", "key": key.id})
    assert ApiKey.objects.get().revoked_at is None
    client.force_login(alice)
    client.post("/accounts/keys/", {"action": "revoke", "key": key.id})
    assert ApiKey.objects.get().revoked_at is not None
    assert _keyed(token).get("/api/me").status_code == 401


def test_key_page_limits_active_keys(client, world):
    (alice, _, _), _ = _players(world)
    client.force_login(alice)
    for _ in range(keys.MAX_KEYS):
        client.post("/accounts/keys/", {"action": "create"})
    page = client.post("/accounts/keys/", {"action": "create"}).content.decode()
    assert f"鍵は {keys.MAX_KEYS} つまで" in page
    assert ApiKey.objects.count() == keys.MAX_KEYS
    assert client.get("/accounts/keys/").status_code == 200
    assert (
        Client().get("/accounts/keys/").status_code == 302
    )  # ログインしていなければログインの画面へ


def test_key_logs_in_to_api_only(world):
    (alice, _, _), (a, _, _) = _players(world)
    _, token = keys.issue(alice, "test")
    me = _keyed(token).get("/api/me").json()
    assert me["user"] == "player0" and me["island"] == {"id": a.id, "name": a.name, "ai": True}
    assert me["key"]["name"] == "test" and me["key"]["calls_per_minute"] > 0
    assert ApiKey.objects.get().last_used_turn == world.turn
    # 間違った鍵・形の違う Authorization は 401
    assert _keyed("hk_wrong").get("/api/me").status_code == 401
    res = Client(HTTP_AUTHORIZATION=f"Basic {token}").get("/api/me")
    assert res.status_code == 401
    # 鍵は画面には効かない（ログインの画面へ）
    assert _keyed(token).get("/accounts/keys/").status_code == 302
    assert _keyed(token).get("/found/").status_code == 302
    # 持ち主のアカウントを止めると使えない
    alice.is_active = False
    alice.save()
    assert _keyed(token).get("/api/me").status_code == 401


def test_key_writes_need_no_csrf_but_sessions_still_do(world):
    (alice, bob, _), (a, b, _) = _players(world)
    _, token = keys.issue(alice)
    order = {"orders": [{"kind": "rename_island", "payload": {"name": "鍵の国"}}]}
    res = _keyed(token).put(f"/api/islands/{a.id}/orders", order, content_type="application/json")
    assert res.status_code == 200
    assert Order.objects.get(island=a).payload == {"name": "鍵の国"}
    # ほかの国の命令は、鍵でも変えられない（人と同じ決まり）
    res = _keyed(token).put(f"/api/islands/{b.id}/orders", order, content_type="application/json")
    assert res.status_code == 403
    # 鍵で公電も送れる
    letter = {"to": b.id, "subject": "鍵から", "body": "こんにちは"}
    res = _keyed(token).post("/api/letters", letter, content_type="application/json")
    assert res.status_code == 201 and Letter.objects.get().author == alice
    # cookie のログインでは、これまでどおり CSRF の印が要る
    session = Client(enforce_csrf_checks=True)
    session.force_login(alice)
    res = session.put(f"/api/islands/{a.id}/orders", order, content_type="application/json")
    assert res.status_code == 403


def test_key_calls_per_minute(world, settings):
    settings.HAKONIWA_AI_CALLS_PER_MINUTE = 3
    (alice, _, _), _ = _players(world)
    _, token = keys.issue(alice)
    _, other = keys.issue(alice)
    client = _keyed(token)
    assert [client.get("/api/me").status_code for _ in range(3)] == [200, 200, 200]
    res = client.get("/api/me")
    assert res.status_code == 429 and int(res["Retry-After"]) >= 1
    assert "1 分に 3 回まで" in res.json()["error"]
    assert _keyed(other).get("/api/me").status_code == 200  # 鍵ごとに数える
    assert Client().get("/api/me").status_code == 200  # 画面（鍵なし）は数えない


# --- AI の国の印 ------------------------------------------------------------------------


def _marked(client: Client, island: Island) -> tuple[bool, bool, bool]:
    """国の一覧・国の画面・世界の API に印が出ているか。"""
    top = client.get("/").content.decode()
    row = top.split(f'href="/islands/{island.id}/"')[1].split("</tr>")[0]
    page = client.get(f"/islands/{island.id}/").content.decode()
    header = page.split("<h1>")[1].split("</h1>")[0]
    world = {i["id"]: i for i in client.get("/api/world").json()["islands"]}
    return 'class="ai-mark"' in row, 'class="ai-mark"' in header, world[island.id]["ai"]


def test_ai_mark_on_list_and_island_page(client, world):
    (alice, _, _), (a, b, _) = _players(world)
    assert _marked(client, a) == (False, False, False)
    key, token = keys.issue(alice)
    assert _marked(client, a) == (True, True, True)
    assert _marked(client, b) == (False, False, False)
    # 使わずに取り消せば消える
    keys.revoke(alice, key.id)
    assert _marked(client, a) == (False, False, False)

    # 使った鍵は、取り消しても最後に使ってから MARK_TURNS ターンの間は消えない
    key, token = keys.issue(alice)
    _set_turn(world, 20)
    assert _keyed(token).get("/api/me").status_code == 200
    keys.revoke(alice, key.id)
    _set_turn(world, 20 + keys.MARK_TURNS)
    assert _marked(client, a) == (True, True, True)
    _set_turn(world, 21 + keys.MARK_TURNS)
    assert _marked(client, a) == (False, False, False)


# --- 国の要約と差分 ----------------------------------------------------------------------


@pytest.fixture
def played(world):
    """持ち主の付いた島で 3 ターン進めた世界（統計・ターンログ・帳簿がある）。"""
    users, islands = _players(world)
    Order.objects.create(
        island=islands[0], seq=1, kind="rename_island", payload={"name": "要約の国"}
    )
    for _ in range(3):
        run_turn(force=True)
    world.refresh_from_db()
    return world, users, islands


def test_summary_for_owner_and_others(played):
    world, (alice, bob, _), (a, _, _) = played
    _, mine_token = keys.issue(alice)
    _, other_token = keys.issue(bob)
    Letter.objects.create(
        sender=Island.objects.get(owner=bob),
        recipient=a,
        author=bob,
        subject="ごあいさつ",
        body="本文",
        turn=world.turn,
    )
    mine = _keyed(mine_token).get(f"/api/islands/{a.id}/summary").json()
    assert mine["mine"] and mine["turn"] == world.turn == mine["stats_turn"]
    assert mine["island"]["name"] == "要約の国" and mine["island"]["ai"]
    assert "箱庭暦" in mine["date"]
    labels = {row["key"]: row for row in mine["indicators"]}
    assert {"population", "unemployment", "gdp", "treasury", "fiscal.balance"} <= set(labels)
    assert all("now" in row for row in mine["indicators"])
    assert any("before" in row for row in mine["indicators"])  # 前のターンから変わったもの
    assert mine["events_turn"] == world.turn
    assert mine["events"] == [
        {k: v for k, v in e.items() if k != "turn"}
        for e in _keyed(mine_token).get(f"/api/islands/{a.id}/log").json()["entries"]
    ]
    assert {"stopped", "construction", "partial"} <= set(mine["facilities"])
    assert mine["goods"] and all("name" in g for g in mine["goods"])
    assert mine["orders"]["last_turn"] == 1
    assert mine["orders"]["last_results"][0]["status"] == "done"
    assert mine["letters"]["unread"] == 1
    assert "読んでいない公電が 1 通あります" in mine["alerts"]

    other = _keyed(other_token).get(f"/api/islands/{a.id}/summary").json()
    assert not other["mine"]
    assert "orders" not in other and "letters" not in other and "losing" not in other
    assert other["indicators"] == mine["indicators"]  # 国全体の数は誰にでも見える
    # ログインしていなくても、公開の分は読める（画面と同じ）
    assert Client().get(f"/api/islands/{a.id}/summary").json()["mine"] is False


def test_changes_since_a_turn(played):
    world, (alice, bob, _), (a, b, _) = played
    _, token = keys.issue(alice)
    _, other_token = keys.issue(bob)
    data = _keyed(token).get(f"/api/islands/{a.id}/changes?since=1").json()
    assert data["since"] == 1 and data["turn"] == 3 and not data["truncated"]
    assert {e["turn"] for e in data["log"]} <= {2, 3}
    assert len(data["log"]) == TurnLog.objects.filter(island=a, turn__gt=1).count()
    assert all("before" in row for row in data["indicators"])
    assert data["orders"] == []  # 名前の変更はターン 1 に実行した
    assert data["letters"] == []
    data = _keyed(token).get(f"/api/islands/{a.id}/changes?since=0").json()
    assert [o["status"] for o in data["orders"]] == ["done"]
    # 省くと前のターンから
    assert _keyed(token).get(f"/api/islands/{a.id}/changes").json()["since"] == 2
    assert "letters" not in _keyed(other_token).get(f"/api/islands/{a.id}/changes").json()


def test_changes_keep_secret_failed_orders_from_others(played):
    world, (alice, bob, _), (a, _, _) = played
    Order.objects.create(  # 利払いの停止は、実行するまで伏せる命令（失敗したら誰にも見せない）
        island=a,
        seq=1,
        kind="set_bond_payment",
        payload={"pay": False},
        status="failed",
        reason="reason_unknown_order",
        executed_turn=3,
    )
    _, token = keys.issue(alice)
    _, other_token = keys.issue(bob)
    mine = _keyed(token).get(f"/api/islands/{a.id}/changes?since=2").json()
    others = _keyed(other_token).get(f"/api/islands/{a.id}/changes?since=2").json()
    assert len(mine["orders"]) == 1 and others["orders"] == []


def test_changes_truncates_long_gaps(played):
    world, (alice, _, _), (a, _, _) = played
    _, token = keys.issue(alice)
    _set_turn(world, 100)
    data = _keyed(token).get(f"/api/islands/{a.id}/changes?since=0").json()
    assert data["truncated"] and data["log_from"] == 100 - 24 + 1


def test_turn_log_api(played):
    world, _, (a, _, _) = played
    data = Client().get(f"/api/islands/{a.id}/log").json()
    assert data["from"] == data["to"] == 3
    assert {e["turn"] for e in data["entries"]} == {3}
    data = Client().get(f"/api/islands/{a.id}/log?from=1&to=2").json()
    turns = {e["turn"] for e in data["entries"]}
    assert turns and turns <= {1, 2}  # 範囲の外のターンは出ない
    assert all("text" in e and "kind" in e for e in data["entries"])
    empty = Island.objects.exclude(pk=a.pk).first()
    TurnLog.objects.filter(island=empty).delete()
    assert Client().get(f"/api/islands/{empty.id}/log").json()["entries"] == []


# --- 画面だけにあった中身 -----------------------------------------------------------------


def test_screen_apis_show_the_same_as_screens(played):
    world, (alice, bob, _), (a, _, _) = played
    owner = Client()
    owner.force_login(alice)
    other = Client()
    other.force_login(bob)

    mine = owner.get(f"/api/islands/{a.id}/facilities").json()
    assert mine["mine"] and mine["table"]["rows"] and mine["stats"]
    assert "table" not in other.get(f"/api/islands/{a.id}/facilities").json()

    fiscal = owner.get(f"/api/islands/{a.id}/fiscal").json()
    assert fiscal["fiscal"]["revenue"] and fiscal["bonds"]
    assert all("holders" in b for b in fiscal["bonds"])
    assert all("holders" not in b for b in other.get(f"/api/islands/{a.id}/fiscal").json()["bonds"])

    market = owner.get(f"/api/islands/{a.id}/market").json()
    assert market["rows"] and "short" in market
    assert "short" not in other.get(f"/api/islands/{a.id}/market").json()

    assert owner.get(f"/api/islands/{a.id}/chain").json()["tiers"]
    assert other.get(f"/api/islands/{a.id}/chain").status_code == 403

    ranks = other.get("/api/rankings").json()
    assert ranks["turn"] == world.turn and ranks["categories"]

    orders = Client().get("/api/defs/orders").json()["orders"]
    assert orders["build"]["payload"]["required"] == ["facility", "x", "y"]
    assert orders["build"]["admin_cost"] == 1 and orders["build"]["name"]


# --- MCP ---------------------------------------------------------------------------------


def _rpc(client: Client, method: str, params=None, id_=1, **headers):
    message = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        message["id"] = id_
    if params is not None:
        message["params"] = params
    res = client.post("/mcp", json.dumps(message), content_type="application/json", **headers)
    return res.status_code, (res.json() if res.content else None)


def _tool(client: Client, tool: str, **arguments) -> tuple[str, bool]:
    status, data = _rpc(client, "tools/call", {"name": tool, "arguments": arguments})
    assert status == 200, data
    result = data["result"]
    return result["content"][0]["text"], result["isError"]


def test_mcp_handshake_and_transport(world):
    (alice, _, _), _ = _players(world)
    _, token = keys.issue(alice)
    client = _keyed(token)
    status, data = _rpc(client, "initialize", {"protocolVersion": "2025-06-18"})
    assert status == 200
    result = data["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "economic-hakoniwa" and result["instructions"]
    # 知らない版を言われたら、こちらの新しい版で答える
    _, data = _rpc(client, "initialize", {"protocolVersion": "1999-01-01"})
    assert data["result"]["protocolVersion"] == mcp.PROTOCOL_VERSIONS[0]
    # 通知には 202 と空の本文
    assert _rpc(client, "notifications/initialized", id_=None) == (202, None)
    assert _rpc(client, "ping")[1]["result"] == {}
    assert _rpc(client, "resources/list")[1]["error"]["code"] == -32601
    # 対応していない版の見出しは 400
    status, _ = _rpc(client, "ping", HTTP_MCP_PROTOCOL_VERSION="1999-01-01")
    assert status == 400
    assert _rpc(client, "ping", HTTP_MCP_PROTOCOL_VERSION="2025-11-25")[0] == 200
    # 別のサイトの Origin は 403、同じなら通す
    assert _rpc(client, "ping", HTTP_ORIGIN="http://evil.example")[0] == 403
    assert _rpc(client, "ping", HTTP_ORIGIN="http://testserver")[0] == 200
    # GET（SSE）と DELETE（セッション）は使わない
    assert client.get("/mcp").status_code == 405
    assert client.delete("/mcp").status_code == 405
    # まとめて送る形
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ]
    res = client.post("/mcp", json.dumps(batch), content_type="application/json")
    assert [x["id"] for x in res.json()] == [1, 2]
    res = client.post("/mcp", "{not json", content_type="application/json")
    assert res.status_code == 400 and res.json()["error"]["code"] == -32700


def test_mcp_needs_a_key(world):
    (alice, _, _), _ = _players(world)
    session = Client()
    session.force_login(alice)  # cookie のログインでは入れない（CSRF の印を使わない入り口なので）
    status, data = _rpc(session, "tools/list")
    assert status == 401 and data["error"]["code"] == -32001
    status, _ = _rpc(_keyed("hk_wrong"), "tools/list")
    assert status == 401


def test_mcp_tools_play_by_the_same_rules(played):
    world, (alice, bob, _), (a, b, _) = played
    _, token = keys.issue(alice)
    client = _keyed(token)
    _, data = _rpc(client, "tools/list")
    tools = {t["name"]: t for t in data["result"]["tools"]}
    assert {"summary", "changes", "check_order", "set_orders", "send_letter", "found"} <= set(tools)
    assert tools["summary"]["annotations"]["readOnlyHint"] is True
    assert tools["set_orders"]["annotations"]["destructiveHint"] is True
    assert tools["changes"]["inputSchema"]["required"] == ["since"]

    me, error = _tool(client, "whoami")
    assert not error and json.loads(me)["island"]["id"] == a.id
    summary, error = _tool(client, "summary")
    assert not error and json.loads(summary)["mine"] is True
    other, _ = _tool(client, "summary", island=b.id)
    assert json.loads(other)["mine"] is False
    changes, _ = _tool(client, "changes", since=2)
    assert json.loads(changes)["since"] == 2
    text, error = _tool(client, "changes")
    assert error and "since" in text

    text, error = _tool(client, "map")
    assert not error and "~ 海" in text and "@" in text
    text, _ = _tool(client, "guide", find="行政力", length=200)
    assert "行政力" in text and "offset=" in text
    text, _ = _tool(client, "defs", section="orders", ids=["build"])
    assert list(json.loads(text)["orders"]) == ["build"]
    text, _ = _tool(client, "report", page="fiscal")
    assert "revenue" in json.loads(text)["fiscal"]
    text, error = _tool(client, "report", page="nope")
    assert error

    # 命令: 確かめと置き換えは自分の島だけ。形の誤りは受け付けない
    text, _ = _tool(client, "check_order", kind="rename_island", payload={"name": "MCP の国"})
    assert json.loads(text)["ok"] is True
    text, error = _tool(
        client, "set_orders", orders=[{"kind": "rename_island", "payload": {"name": "MCP の国"}}]
    )
    assert not error and len(json.loads(text)["orders"]) == 1
    assert Order.objects.get(island=a, status="pending").payload == {"name": "MCP の国"}
    text, error = _tool(client, "set_orders", orders=[{"kind": "no_such_order", "payload": {}}])
    assert error and "400" in text
    text, error = _tool(client, "set_orders", orders=["build"])
    assert error and "{kind, payload}" in text
    res = client.put(f"/api/islands/{a.id}/orders", {"orders": ["build"]}, "application/json")
    assert res.status_code == 400 and res.json()["errors"][0]["index"] == 0

    # 公電は人と同じ決まりで送れる
    text, error = _tool(client, "send_letter", to=b.id, subject="MCP から", body="よろしく")
    assert not error and Letter.objects.get().subject == "MCP から"
    text, error = _tool(client, "send_letter", to=a.id, subject="自分へ", body="x")
    assert error and "自分の島には送れません" in text

    # api_get は /api/ だけ
    text, error = _tool(client, "api_get", path="/admin/")
    assert error
    text, error = _tool(client, "api_get", path="/api/../admin/")
    assert error
    text, error = _tool(client, "api_get", path=f"api/islands/{a.id}/log?from=1&to=1")
    assert not error and {e["turn"] for e in json.loads(text)["entries"]} == {1}

    _, data = _rpc(client, "tools/call", {"name": "nope", "arguments": {}})
    assert data["error"]["code"] == -32602


def test_mcp_found(world):
    newcomer = User.objects.create_user("newcomer", password="a-long-pass-123")
    _, token = keys.issue(newcomer)
    client = _keyed(token)
    text, error = _tool(client, "summary")
    assert error and "found" in text
    text, error = _tool(client, "found", name="鍵の国", resources=["iron_ore", "coal"], seed=7)
    assert not error, text
    island = Island.objects.get(owner=newcomer)
    assert json.loads(text)["island"] == island.id and island.name == "鍵の国"
    assert island.resources == ["iron_ore", "coal"]
    text, error = _tool(client, "found", name="二つ目")
    assert error and "1人1島" in text


# --- 試し遊びの道具 -------------------------------------------------------------------------


def _hk():
    spec = importlib.util.spec_from_file_location("hk", REPO / "tools" / "playtest" / "hk.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hk_paths_and_key(tmp_path, monkeypatch):
    hk = _hk()
    assert hk.fix_path("api/islands/4/economy") == "/api/islands/4/economy"
    assert hk.fix_path("/api/world") == "/api/world"
    assert hk.fix_path("C:/Program Files/Git/api/islands/4/map") == "/api/islands/4/map"
    monkeypatch.setattr(hk, "KEY_FILE", tmp_path / "key")
    monkeypatch.delenv("HK_KEY", raising=False)
    assert hk.load_key() == ""
    (tmp_path / "key").write_text("hk_from_file\n")
    assert hk.load_key() == "hk_from_file"
    monkeypatch.setenv("HK_KEY", "hk_from_env")
    assert hk.load_key() == "hk_from_env"
