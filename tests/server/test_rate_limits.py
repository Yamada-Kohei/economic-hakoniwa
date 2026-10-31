"""回数制限: ログインの試しすぎ（通常・管理画面）と、AI 用の鍵の処理数（/api/ と /mcp）。

時間は試験の中で進める（15 分を実際に待たない）。数えは DB なので、複数のワーカーで共有できるかは
本物の別プロセス（SQLite のファイル）で確かめる。公開中の環境や LAN のサーバーには何も送らない。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, RequestFactory
from django.utils import timezone

from server.accounts import clientip, keys, loginguard, throttle
from server.accounts.models import LoginAttempt, RateCounter
from server.letters.models import Letter
from server.world.models import Island

pytestmark = pytest.mark.django_db

REPO = Path(__file__).resolve().parents[2]
PASSWORD = "a-long-pass-123"
LOGIN = "/accounts/login/"
ADMIN_LOGIN = "/admin/login/"


@pytest.fixture(autouse=True)
def fast_password_hasher(settings):
    """パスワードの照合は本番の遅い方式でなくてよい（回数制限の試験で何十回も照合するため）。"""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


class Clock:
    """時刻を試験が決める。ログインの停止（DB の日時）と、回数の窓（UNIX 秒）の両方に効く。"""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 26, 3, 0, 10, tzinfo=UTC)  # 窓の始めから 10 秒

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(timezone, "now", lambda: c.now)
    monkeypatch.setattr(throttle, "time", types.SimpleNamespace(time=lambda: c.now.timestamp()))
    return c


@pytest.fixture
def password_checks(monkeypatch) -> list[str]:
    """パスワードの照合（ModelBackend.authenticate）が呼ばれた数を残す。"""
    calls: list[str] = []
    real = ModelBackend.authenticate

    def spy(self, request, username=None, password=None, **kwargs):
        calls.append(username)
        return real(self, request, username=username, password=password, **kwargs)

    monkeypatch.setattr(ModelBackend, "authenticate", spy)
    return calls


@pytest.fixture
def accounts(db):
    users = {
        name: User.objects.create_user(name, password=PASSWORD)
        for name in ("alice", "bob", "carol")
    }
    users["boss"] = User.objects.create_user("boss", password=PASSWORD, is_staff=True)
    return users


def post(ip: str, username: str, password: str = "wrong", path: str = LOGIN, **extra):
    client = extra.pop("client", None) or Client(REMOTE_ADDR=ip)
    return client.post(path, {"username": username, "password": password}, **extra)


def fail(ip: str, username: str, path: str = LOGIN):
    return post(ip, username, "wrong", path)


# --- ログイン: 失敗の回数 ---------------------------------------------------------------------


def test_locks_on_the_fifth_failure_and_not_before(clock, accounts, password_checks):
    for _ in range(4):
        res = fail("203.0.113.5", "alice")
        assert res.status_code == 200  # 4 回までは、いつもの「違います」の画面
    res = fail("203.0.113.5", "alice")
    assert res.status_code == 429 and res["Retry-After"] == str(15 * 60)
    assert "約 15 分" in res.content.decode()

    # 停止中は正しいパスワードでも入れず、パスワードの照合まで進まない
    checked = len(password_checks)
    res = post("203.0.113.5", "alice", PASSWORD)
    assert res.status_code == 429 and "_auth_user_id" not in res.client.session
    assert len(password_checks) == checked


def test_resumes_after_the_lock_time(clock, accounts):
    for _ in range(5):
        fail("203.0.113.5", "alice")
    clock.advance(15 * 60 - 1)
    assert post("203.0.113.5", "alice", PASSWORD).status_code == 429
    clock.advance(1)
    res = post("203.0.113.5", "alice", PASSWORD)
    assert res.status_code == 302 and res.client.session["_auth_user_id"] == str(
        accounts["alice"].pk
    )


def test_waiting_resends_do_not_extend_the_lock(clock, accounts):
    for _ in range(5):
        fail("203.0.113.5", "alice")
    until = LoginAttempt.objects.get().locked_until
    for _ in range(10):
        clock.advance(50)
        res = post("203.0.113.5", "alice", PASSWORD)
        assert res.status_code == 429
    assert LoginAttempt.objects.get().locked_until == until
    clock.advance(15 * 60)  # 最初の停止から 15 分たったので解ける
    assert post("203.0.113.5", "alice", PASSWORD).status_code == 302


def test_other_address_or_other_account_is_not_mixed(clock, accounts):
    for _ in range(5):
        fail("203.0.113.5", "alice")
    assert post("203.0.113.5", "alice", PASSWORD).status_code == 429
    # 同じアカウントでも、別の接続元から本人が入れる
    assert post("198.51.100.7", "alice", PASSWORD).status_code == 302
    # 同じ接続元でも、別のアカウントの失敗は混ざらない
    for _ in range(4):
        assert fail("203.0.113.5", "bob").status_code == 200
    assert post("203.0.113.5", "bob", PASSWORD).status_code == 302


def test_success_resets_the_pair_but_not_the_address_total(clock, accounts):
    for _ in range(4):
        fail("203.0.113.5", "alice")
    assert post("203.0.113.5", "alice", PASSWORD).status_code == 302
    assert not LoginAttempt.objects.exists()
    for _ in range(4):  # また 4 回まで失敗できる
        assert fail("203.0.113.5", "alice").status_code == 200
    # 接続元全体の送信数は戻っていない（ここまで 9 回）
    assert RateCounter.objects.get(scope="login-ip").count == 9


def test_failures_expire_after_the_window(clock, accounts):
    for _ in range(4):
        fail("203.0.113.5", "alice")
    clock.advance(15 * 60 + 1)  # 数え始めから期間を過ぎたので、また 0 から
    for _ in range(4):
        assert fail("203.0.113.5", "alice").status_code == 200


def test_normal_and_admin_login_share_the_count(clock, accounts):
    for _ in range(3):
        assert fail("203.0.113.5", "boss", LOGIN).status_code == 200
    assert fail("203.0.113.5", "boss", ADMIN_LOGIN).status_code == 200
    assert fail("203.0.113.5", "boss", ADMIN_LOGIN).status_code == 429  # 5 回目
    assert post("203.0.113.5", "boss", PASSWORD, LOGIN).status_code == 429
    assert post("203.0.113.5", "boss", PASSWORD, ADMIN_LOGIN).status_code == 429
    clock.advance(15 * 60)
    res = post("203.0.113.5", "boss", PASSWORD, ADMIN_LOGIN)
    assert res.status_code == 302 and res.wsgi_request.user.is_staff


def test_admin_login_alone_locks(clock, accounts):
    for _ in range(4):
        assert fail("203.0.113.5", "boss", ADMIN_LOGIN).status_code == 200
    res = fail("203.0.113.5", "boss", ADMIN_LOGIN)
    assert res.status_code == 429 and res["Retry-After"] == "900"


def test_account_name_spellings_count_together(clock, accounts):
    for name in ("alice", "ALICE", " Alice ", "ａｌｉｃｅ"):
        assert fail("203.0.113.5", name).status_code == 200
    assert fail("203.0.113.5", "alice").status_code == 429


def test_message_is_the_same_for_real_and_unknown_accounts(clock, accounts):
    for _ in range(5):
        real = fail("203.0.113.5", "alice")
        ghost = fail("203.0.113.5", "no-such-user")
    assert real.status_code == ghost.status_code == 429
    assert real.content == ghost.content and real["Retry-After"] == ghost["Retry-After"]


def test_a_page_that_never_reached_the_password_check_is_not_a_failure(clock, accounts):
    client = Client(REMOTE_ADDR="203.0.113.5", enforce_csrf_checks=True)
    for _ in range(8):  # CSRF の印がなくて 403
        res = post("", "alice", "wrong", client=client)
        assert res.status_code == 403
    attempt = LoginAttempt.objects.get()
    assert attempt.failures == 0 and attempt.locked_until is None
    assert fail("203.0.113.5", "alice").status_code == 200  # 失敗の数はまだ 1 回目


def test_get_is_never_counted(clock, accounts):
    client = Client(REMOTE_ADDR="203.0.113.5")
    for _ in range(40):
        assert client.get(LOGIN).status_code == 200
    assert not RateCounter.objects.exists() and not LoginAttempt.objects.exists()


def test_numbers_are_settings(clock, accounts, settings):
    settings.HAKONIWA_LOGIN_MAX_FAILURES = 2
    settings.HAKONIWA_LOGIN_LOCK_MINUTES = 3
    assert fail("203.0.113.5", "alice").status_code == 200
    res = fail("203.0.113.5", "alice")
    assert res.status_code == 429 and res["Retry-After"] == "180"
    settings.HAKONIWA_LOGIN_MAX_FAILURES = 0  # 0 なら失敗では止めない
    assert fail("203.0.113.5", "carol").status_code == 200
    assert fail("203.0.113.5", "carol").status_code == 200
    assert fail("203.0.113.5", "carol").status_code == 200


# --- ログイン: 接続元ごとの送信数 -----------------------------------------------------------------


def test_address_total_ignores_account_names(clock, accounts, password_checks):
    for i in range(30):
        assert fail("203.0.113.5", f"guess{i}").status_code == 200
    checked = len(password_checks)
    res = fail("203.0.113.5", "guess-new")
    assert res.status_code == 429 and 1 <= int(res["Retry-After"]) <= 60
    assert post("203.0.113.5", "alice", PASSWORD).status_code == 429  # 正しくても
    assert len(password_checks) == checked  # 照合まで進まない
    # 別の接続元は影響を受けない
    assert post("198.51.100.7", "alice", PASSWORD).status_code == 302
    # 窓が変わると再開する。断った送信は数に足さないので延びない
    clock.advance(60)
    assert post("203.0.113.5", "alice", PASSWORD).status_code == 302


def test_address_total_counts_both_screens(clock, accounts):
    for i in range(15):
        fail("203.0.113.5", f"a{i}", LOGIN)
        fail("203.0.113.5", f"b{i}", ADMIN_LOGIN)
    assert fail("203.0.113.5", "c", ADMIN_LOGIN).status_code == 429
    assert fail("203.0.113.5", "c", LOGIN).status_code == 429


def test_posts_per_window_is_a_setting(clock, accounts, settings):
    settings.HAKONIWA_LOGIN_POSTS_PER_WINDOW = 3
    settings.HAKONIWA_LOGIN_POST_WINDOW_SECONDS = 120
    for i in range(3):
        assert fail("203.0.113.5", f"x{i}").status_code == 200
    res = fail("203.0.113.5", "x9")
    assert res.status_code == 429 and int(res["Retry-After"]) > 60
    settings.HAKONIWA_LOGIN_POSTS_PER_WINDOW = 0  # 0 なら上限なし
    assert fail("203.0.113.5", "x9").status_code == 200


# --- ログイン: 記録と保存 ---------------------------------------------------------------------


def test_nothing_secret_is_kept_or_logged(clock, accounts, caplog):
    caplog.set_level("DEBUG", logger="server.accounts.loginguard")
    for _ in range(5):
        fail("203.0.113.5", "alice")
    post("203.0.113.5", "alice", PASSWORD)
    text = caplog.text
    assert "停止します" in text and "停止中" in text
    for secret in ("alice", "203.0.113.5", PASSWORD, "wrong"):
        assert secret not in text
    row = LoginAttempt.objects.get()
    for value in (row.ip_hash, row.user_hash):
        assert len(value) == 64 and "alice" not in value
    assert "203.0.113.5" not in RateCounter.objects.get(scope="login-ip").key


def test_old_records_are_deleted(clock, accounts, settings):
    fail("203.0.113.5", "alice")
    assert LoginAttempt.objects.count() == 1
    clock.advance(24 * 3600 + 120)
    fail("198.51.100.7", "bob")  # 新しい記録を作るついでに、古い記録を消す
    assert list(LoginAttempt.objects.values_list("user_hash", flat=True)) == [
        loginguard._digest("login-user", "bob")
    ]
    assert RateCounter.objects.filter(scope="login-ip").count() == 1
    # 命令でも消せる
    clock.advance(24 * 3600 + 120)
    call_command("purge_throttle")
    assert not LoginAttempt.objects.exists() and not RateCounter.objects.exists()


def test_unreadable_address_is_counted_together_not_skipped(clock, accounts, caplog):
    for _ in range(4):
        assert post("", "alice").status_code == 200
    assert post("", "alice").status_code == 429
    assert "接続元が読めない" in caplog.text
    # 読めない接続元の人どうしは同じ箱になるが、別の接続元の人は入れる
    assert post("198.51.100.7", "alice", PASSWORD).status_code == 302


# --- 接続元の読み方 ----------------------------------------------------------------------------


def _ip(settings, header="REMOTE_ADDR", trusted=("127.0.0.1", "::1"), **meta) -> str:
    settings.HAKONIWA_CLIENT_IP_HEADER = header
    settings.HAKONIWA_TRUSTED_PROXIES = list(trusted)
    return clientip.client_ip(RequestFactory().get("/", **meta))


def test_default_ignores_forwarding_headers(settings):
    assert _ip(settings, REMOTE_ADDR="203.0.113.5", HTTP_X_FORWARDED_FOR="9.9.9.9") == "203.0.113.5"
    assert _ip(settings, REMOTE_ADDR="") == ""
    assert _ip(settings, REMOTE_ADDR="not-an-ip") == ""


def test_header_is_used_only_from_a_trusted_proxy(settings):
    meta = {"HTTP_CF_CONNECTING_IP": "198.51.100.9"}
    h = "HTTP_CF_CONNECTING_IP"
    assert _ip(settings, h, REMOTE_ADDR="127.0.0.1", **meta) == "198.51.100.9"
    # 信頼する中継元でない相手が書いた見出しは捨てて、つないできた相手を使う
    assert _ip(settings, h, REMOTE_ADDR="203.0.113.5", **meta) == "203.0.113.5"
    assert (
        _ip(settings, h, ("203.0.113.0/24",), REMOTE_ADDR="203.0.113.5", **meta) == "198.51.100.9"
    )
    # 空の REMOTE_ADDR は UNIX ソケット（同じ機械の中）
    assert _ip(settings, h, REMOTE_ADDR="", **meta) == "198.51.100.9"
    # 信頼する中継元から来ても、見出しがない・読めないなら分からない
    assert _ip(settings, h, REMOTE_ADDR="127.0.0.1") == ""
    assert _ip(settings, h, REMOTE_ADDR="127.0.0.1", HTTP_CF_CONNECTING_IP="junk") == ""


def test_forwarded_for_is_read_from_the_right(settings):
    h, trusted = "HTTP_X_FORWARDED_FOR", ("127.0.0.1", "10.0.0.0/8")
    chain = "9.9.9.9, 198.51.100.9, 10.1.1.1"  # 左端 9.9.9.9 は利用者が書いた偽物
    assert _ip(settings, h, trusted, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR=chain) == (
        "198.51.100.9"
    )
    only_proxies = "10.1.1.1, 10.2.2.2"
    got = _ip(settings, h, trusted, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR=only_proxies)
    assert got == "10.1.1.1"
    garbage = "198.51.100.9, junk"
    assert _ip(settings, h, trusted, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR=garbage) == ""


def test_ipv6_is_grouped_by_the_64_bit_prefix(settings):
    a = _ip(settings, REMOTE_ADDR="2001:db8:1:2::1")
    b = _ip(settings, REMOTE_ADDR="2001:db8:1:2:ffff:ffff:ffff:ffff")
    c = _ip(settings, REMOTE_ADDR="2001:db8:1:3::1")
    assert a == b == "2001:db8:1:2::" and c == "2001:db8:1:3::"
    assert _ip(settings, REMOTE_ADDR="::ffff:192.0.2.9") == "192.0.2.9"
    h = "HTTP_X_REAL_IP"
    assert _ip(settings, h, REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="[2001:db8:1:2::9]:443") == a
    assert _ip(settings, h, REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="192.0.2.9:8080") == "192.0.2.9"


def test_ipv6_neighbours_share_one_login_budget(clock, accounts):
    for i in range(30):
        assert fail(f"2001:db8:1:2::{i + 1:x}", f"g{i}").status_code == 200
    assert fail("2001:db8:1:2:abcd::1", "g-new").status_code == 429
    assert fail("2001:db8:1:3::1", "g-new").status_code == 200


# --- AI 用の入り口: /mcp と /api/ の処理数 --------------------------------------------------------


@pytest.fixture
def ai(world, clock, settings):
    settings.HAKONIWA_AI_CALLS_PER_MINUTE = 60
    users, islands = [], list(Island.objects.order_by("id"))
    for i, island in enumerate(islands):
        user = User.objects.create_user(f"player{i}", password=PASSWORD)
        island.owner = user
        island.save(update_fields=["owner"])
        users.append(user)
    key, token = keys.issue(users[0])
    client = Client(enforce_csrf_checks=True, HTTP_AUTHORIZATION=f"Bearer {token}")
    return types.SimpleNamespace(client=client, key=key, token=token, users=users, islands=islands)


def _used(ai) -> int:
    row = RateCounter.objects.filter(scope="ai-key", key=str(ai.key.pk)).first()
    return row.count if row else 0


def _post(client, body, **extra):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    return client.post("/mcp", data, content_type="application/json", **extra)


def _ping(i=1):
    return {"jsonrpc": "2.0", "id": i, "method": "ping"}


def _letter(ai, i):
    args = {"to": ai.islands[1].id, "subject": f"件名{i}", "body": "本文"}
    return {
        "jsonrpc": "2.0",
        "id": i,
        "method": "tools/call",
        "params": {"name": "send_letter", "arguments": args},
    }


def test_ten_are_accepted_and_eleven_are_refused_before_running(ai):
    res = _post(ai.client, [_letter(ai, i) for i in range(10)])
    assert res.status_code == 200 and len(res.json()) == 10
    assert Letter.objects.count() == 10 and _used(ai) == 10
    res = _post(ai.client, [_letter(ai, i) for i in range(11)])
    assert res.status_code == 400 and res.json()["error"]["code"] == -32600
    assert Letter.objects.count() == 10  # 1 件も実行されていない
    assert _used(ai) == 11  # 断るだけの送信は 1 件分


def test_batch_limit_is_a_setting(ai, settings):
    settings.HAKONIWA_MCP_MAX_BATCH = 3
    assert _post(ai.client, [_ping(i) for i in range(3)]).status_code == 200
    assert _post(ai.client, [_ping(i) for i in range(4)]).status_code == 400


def test_a_batch_bigger_than_the_remaining_quota_runs_nothing(ai, settings):
    settings.HAKONIWA_AI_CALLS_PER_MINUTE = 15
    assert _post(ai.client, [_ping(i) for i in range(10)]).status_code == 200  # 残り 5
    res = _post(ai.client, [_letter(ai, i) for i in range(10)])
    assert res.status_code == 429 and 1 <= int(res["Retry-After"]) <= 60
    assert Letter.objects.count() == 0 and _used(ai) == 10  # 一部だけ実行も、枠の消費もない
    assert (
        _post(ai.client, [_letter(ai, i) for i in range(5)]).status_code == 200
    )  # 残りはそのまま使える
    assert Letter.objects.count() == 5 and _used(ai) == 15
    assert _post(ai.client, _ping()).status_code == 429


def test_window_resets_next_minute(ai, settings, clock):
    settings.HAKONIWA_AI_CALLS_PER_MINUTE = 2
    assert _post(ai.client, [_ping(1), _ping(2)]).status_code == 200
    res = _post(ai.client, _ping(3))
    assert res.status_code == 429 and res["Retry-After"] == "50"  # 窓の 10 秒目 → あと 50 秒
    clock.advance(50)
    assert _post(ai.client, _ping(3)).status_code == 200


def test_a_batch_that_can_never_fit_says_so(ai, settings):
    settings.HAKONIWA_AI_CALLS_PER_MINUTE = 5
    res = _post(ai.client, [_ping(i) for i in range(10)])
    assert res.status_code == 429 and "Retry-After" in res
    assert "一度に 10 件" in res.json()["error"] and _used(ai) == 0


def test_each_message_counts_even_when_it_is_odd(ai):
    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    odd = [notification, 123, {}, "text", None, {"jsonrpc": "2.0", "id": 9, "method": "no/such"}]
    res = _post(ai.client, odd + [_ping()])
    assert res.status_code == 200 and _used(ai) == 7
    # 単独なら、通知も形の誤りも知らない命令も 1 件
    for body in (notification, 123, {"jsonrpc": "2.0", "id": 2, "method": "no/such"}, _ping()):
        before = _used(ai)
        _post(ai.client, body)
        assert _used(ai) == before + 1


def test_single_batch_and_direct_api_use_the_same_quota(ai):
    assert _post(ai.client, _ping()).status_code == 200
    assert _post(ai.client, [_ping(1), _ping(2), _ping(3)]).status_code == 200
    assert ai.client.get("/api/me").status_code == 200
    assert ai.client.get("/api/me").status_code == 200
    assert _used(ai) == 1 + 3 + 1 + 1


def test_a_tool_that_calls_the_api_inside_is_counted_once(ai):
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    for name in ("whoami", "summary", "world"):
        before = _used(ai)
        res = _post(ai.client, {**call, "params": {"name": name, "arguments": {}}})
        assert res.status_code == 200 and _used(ai) == before + 1


def test_empty_array_runs_nothing(ai):
    res = _post(ai.client, [])
    assert res.status_code == 400 and res.json()["error"]["code"] == -32600
    assert _used(ai) == 1


def test_broken_json_and_oversized_body_keep_their_answers(ai, settings):
    res = _post(ai.client, b"{not json")
    assert res.status_code == 400 and res.json()["error"]["code"] == -32700 and _used(ai) == 1
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 100
    res = _post(ai.client, [_ping(i) for i in range(10)])
    assert res.status_code == 400 and res.get("Content-Type", "").startswith("text/html")
    assert Letter.objects.count() == 0


def test_zero_means_no_quota_but_batch_and_body_limits_still_apply(ai, settings):
    settings.HAKONIWA_AI_CALLS_PER_MINUTE = 0
    for _ in range(12):
        assert _post(ai.client, [_ping(i) for i in range(10)]).status_code == 200
    assert not RateCounter.objects.filter(scope="ai-key").exists()
    assert _post(ai.client, [_ping(i) for i in range(11)]).status_code == 400
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 100
    assert _post(ai.client, [_ping(i) for i in range(10)]).status_code == 400


def test_key_is_checked_before_the_body_and_failures_use_no_quota(ai):
    for headers in (
        {},
        {"HTTP_AUTHORIZATION": "Bearer hk_wrong"},
        {"HTTP_AUTHORIZATION": "Basic x"},
    ):
        res = Client().post(
            "/mcp", b"{broken", content_type="application/json", **headers
        )  # 本文が壊れていても、先に鍵で断る
        assert res.status_code == 401
    keys.revoke(ai.users[0], ai.key.pk)
    res = _post(ai.client, [_ping(i) for i in range(50)])
    assert res.status_code == 401 and _used(ai) == 0
    assert RateCounter.objects.count() == 0


def test_keys_have_separate_quota(ai, settings):
    settings.HAKONIWA_AI_CALLS_PER_MINUTE = 2
    _, other_token = keys.issue(ai.users[1])
    other = Client(HTTP_AUTHORIZATION=f"Bearer {other_token}")
    assert _post(ai.client, [_ping(1), _ping(2)]).status_code == 200
    assert _post(ai.client, _ping(3)).status_code == 429
    assert _post(other, [_ping(1), _ping(2)]).status_code == 200


# --- 数えの部品（時間・窓） ---------------------------------------------------------------------


def test_consume_is_all_or_nothing_and_refusals_add_nothing():
    kw = {"limit": 10, "window_seconds": 60, "now": 1_000_040.0}
    assert throttle.consume("t", "k", cost=7, **kw) == 0
    for _ in range(5):
        assert throttle.consume("t", "k", cost=4, **kw) == 40  # 窓の終わりまで
    assert RateCounter.objects.get().count == 7
    assert throttle.consume("t", "k", cost=3, **kw) == 0
    assert throttle.consume("t", "k", cost=1, **kw) > 0
    assert throttle.consume("t", "k", cost=1, **{**kw, "now": 1_000_081.0}) == 0  # 次の窓


# --- 複数プロセス ------------------------------------------------------------------------------


WORKER = textwrap.dedent(
    """
    import json, os, sys, types
    os.environ["DJANGO_SETTINGS_MODULE"] = "server.settings"
    import django
    django.setup()
    from django.test import Client
    from server.accounts import throttle

    mode, token = sys.argv[1], sys.argv[2]
    fixed = 1_000_000.0  # 同じ窓に入れて、時刻のずれで数が変わらないようにする
    throttle.time = types.SimpleNamespace(time=lambda: fixed)
    accepted = 0
    if mode == "consume":
        for _ in range(40):
            cost = int(token)
            wait = throttle.consume("t", "k" + token, limit=50, window_seconds=3600, cost=cost)
            accepted += wait == 0
    else:  # /mcp に 5 件のまとめ送信を 15 回
        client = Client(HTTP_AUTHORIZATION="Bearer " + token)
        body = json.dumps([{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(5)])
        for _ in range(15):
            res = client.post("/mcp", body, content_type="application/json")
            accepted += res.status_code == 200
    print(accepted)
    """
)


@pytest.fixture
def shared_db(tmp_path):
    """本物の別プロセスが同じ DB を見られるよう、SQLite のファイルを migrate して用意する。"""
    db = tmp_path / "shared.sqlite3"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db}",
        "DJANGO_DEBUG": "1",
        "DJANGO_ALLOWED_HOSTS": "testserver",
        "HAKONIWA_AI_CALLS_PER_MINUTE": "60",
        "PYTHONPATH": str(REPO),
    }
    subprocess.run(
        [sys.executable, "manage.py", "migrate", "-v", "0"], cwd=REPO, env=env, check=True
    )
    return env


def _run_workers(env, mode, arg, n=4) -> list[int]:
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, mode, arg],
            cwd=REPO,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for _ in range(n)
    ]
    counts = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        counts.append(int(out.strip().splitlines()[-1]))
    return counts


def test_several_processes_share_one_count(shared_db):
    # 1 件ずつ: 4 つのプロセスが 40 回ずつ（計 160 回）取りに行って、ちょうど 50 件分だけ通る
    assert sum(_run_workers(shared_db, "consume", "1")) == 50
    # 7 件ずつのまとめ: 50 件の枠に 7 件の束はちょうど 7 つ（49 件分）しか入らない
    assert sum(_run_workers(shared_db, "consume", "7")) == 50 // 7


def test_several_processes_share_the_mcp_quota(shared_db):
    code = textwrap.dedent(
        """
        import os
        os.environ["DJANGO_SETTINGS_MODULE"] = "server.settings"
        import django
        django.setup()
        from django.contrib.auth.models import User
        from server.accounts import keys
        print(keys.issue(User.objects.create_user("ai", password="a-long-pass-123"))[1])
        """
    )
    token = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env=shared_db,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
    # 4 プロセス × 15 回 × 5 件 = 300 件分を送って、1 分 60 件の枠から通るのは 12 回（60 件分）だけ
    assert sum(_run_workers(shared_db, "mcp", token)) == 12
