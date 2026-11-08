"""公電の Web Push。中継所への通信は差し替え、任意の部品なしでも確かめる。"""

from __future__ import annotations

import base64
import json
import sys
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.utils import timezone

from server.accounts import keys
from server.letters import push, services
from server.letters.models import Letter, PushOutbox, PushSubscription
from tests.server.test_letters import _players

pytestmark = pytest.mark.django_db
ENDPOINT = "https://fcm.googleapis.com/send/example"
P256DH = base64.urlsafe_b64encode(b"\x04" + b"p" * 64).rstrip(b"=").decode()
AUTH = base64.urlsafe_b64encode(b"a" * 16).rstrip(b"=").decode()
SUBSCRIPTION = {"endpoint": ENDPOINT, "keys": {"p256dh": P256DH, "auth": AUTH}}


@pytest.fixture
def configured(settings):
    settings.HAKONIWA_VAPID_PUBLIC_KEY = P256DH
    settings.HAKONIWA_VAPID_PRIVATE_KEY = "test-private-key"
    settings.HAKONIWA_VAPID_CONTACT = "mailto:admin@example.com"


@pytest.fixture
def sender(monkeypatch, configured):
    send = Mock()
    monkeypatch.setattr(push, "load_sender", lambda: send)
    return send


def _subscribe(user, endpoint=ENDPOINT):
    return PushSubscription.objects.create(user=user, endpoint=endpoint, p256dh=P256DH, auth=AUTH)


def _queue(world, n=1):
    (alice, bob, _), (a, b, _) = _players(world)
    subscription = _subscribe(bob)
    letters = [
        services.send_letter(alice, a, b.pk, f"秘密の件名{i}", "秘密の本文") for i in range(n)
    ]
    return subscription, letters


def test_push_subscription_auth_csrf_and_ai(client, world, configured):
    (alice, _, _), _ = _players(world)
    assert (
        client.post("/api/letters/push", SUBSCRIPTION, content_type="application/json").status_code
        == 403
    )
    client.force_login(alice)
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(alice)
    assert (
        csrf.post("/api/letters/push", SUBSCRIPTION, content_type="application/json").status_code
        == 403
    )
    page = csrf.get("/letters/")
    token = csrf.cookies["csrftoken"].value
    assert b"letter-push" in page.content
    assert (
        csrf.post(
            "/api/letters/push",
            SUBSCRIPTION,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        ).status_code
        == 200
    )
    assert (
        csrf.delete(
            "/api/letters/push", {"endpoint": ENDPOINT}, content_type="application/json"
        ).status_code
        == 403
    )
    assert (
        csrf.delete(
            "/api/letters/push",
            {"endpoint": ENDPOINT},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        ).status_code
        == 200
    )
    _, token = keys.issue(alice)
    assert (
        client.post(
            "/api/letters/push",
            SUBSCRIPTION,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/x",
        "https://example.com/x",
        "https://127.0.0.1/x",
        "https://fcm.googleapis.com.evil.example/x",
        "https://notify.windows.com/x",
        "https://evilnotify.windows.com/x",
        "https://fcm.googleapis.com@evil.example/x",
        "https://user@fcm.googleapis.com/x",
        "https://fcm.googleapis.com:444/x",
        "https://fcm.googleapis.com/x#fragment",
        "https://fcm.googleapis.com/" + "x" * 2048,
        "https://fcm.googleapis.com/\nx",
        "https://fcm.googleapis.com\\@evil.example/x",
        None,
    ],
)
def test_push_endpoint_allowlist(client, world, configured, endpoint):
    (alice, _, _), _ = _players(world)
    client.force_login(alice)
    response = client.post(
        "/api/letters/push", {**SUBSCRIPTION, "endpoint": endpoint}, content_type="application/json"
    )
    assert response.status_code == 400
    assert not PushSubscription.objects.exists()


@pytest.mark.parametrize(
    "host",
    [
        "fcm.googleapis.com",
        "web.push.apple.com",
        "updates.push.services.mozilla.com",
        "wns2.notify.windows.com",
    ],
)
def test_push_allowed_providers(host):
    assert push.validate_endpoint(f"https://{host}/token") == f"https://{host}/token"


def test_push_subscription_upsert_delete_and_cascade(client, world, configured):
    (alice, bob, _), _ = _players(world)
    client.force_login(alice)
    for _ in range(2):
        assert (
            client.post(
                "/api/letters/push", SUBSCRIPTION, content_type="application/json"
            ).status_code
            == 200
        )
    assert PushSubscription.objects.count() == 1
    client.force_login(bob)
    client.delete("/api/letters/push", {"endpoint": ENDPOINT}, content_type="application/json")
    assert PushSubscription.objects.get().user == alice
    # 同じブラウザを別の持ち主が明示して登録したら、宛先の持ち主を上書きする。
    client.post("/api/letters/push", SUBSCRIPTION, content_type="application/json")
    assert PushSubscription.objects.get().user == bob
    client.delete("/api/letters/push", {"endpoint": ENDPOINT}, content_type="application/json")
    assert not PushSubscription.objects.exists()
    _subscribe(bob)
    bob.delete()
    assert not PushSubscription.objects.exists()


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {**SUBSCRIPTION, "keys": []},
        {**SUBSCRIPTION, "keys": {"p256dh": "x", "auth": AUTH}},
        {**SUBSCRIPTION, "keys": {"p256dh": P256DH, "auth": "x" * 1000}},
    ],
)
def test_push_invalid_json_and_keys(client, world, configured, data):
    (alice, _, _), _ = _players(world)
    client.force_login(alice)
    assert (
        client.post("/api/letters/push", data, content_type="application/json").status_code == 400
    )


def test_push_outbox_only_for_subscribed_owner(world):
    (alice, bob, _), (a, b, _) = _players(world)
    services.send_letter(alice, a, b.pk, "件名", "本文")
    assert not PushOutbox.objects.exists()
    _subscribe(bob)
    letter = services.send_letter(alice, a, b.pk, "購読後の件名", "本文")
    assert PushOutbox.objects.get().letter == letter
    assert PushOutbox.objects.get().user == bob


@pytest.mark.parametrize("n", [1, 3])
def test_send_push_group_and_privacy(world, sender, n):
    subscription, letters = _queue(world, n)
    call_command("send_push")
    sender.assert_called_once()
    args = sender.call_args.kwargs
    payload = json.loads(args["data"])
    expected = (
        f"{letters[0].sender.name}から公電が届きました" if n == 1 else "未読の公電が 3 通あります"
    )
    assert payload["body"] == expected
    assert payload["url"] == "/letters/"
    assert "秘密の件名" not in args["data"] and "秘密の本文" not in args["data"]
    assert args["headers"]["Topic"] == push.TOPIC
    assert args["ttl"] == 86400 and args["timeout"] == 10
    assert not PushOutbox.objects.filter(sent_at__isnull=True).exists()
    subscription.refresh_from_db()
    assert subscription.last_ok_at is not None
    call_command("send_push")
    sender.assert_called_once()


@pytest.mark.parametrize("status", [404, 410])
def test_send_push_expired_subscription(world, sender, status):
    _queue(world)
    exc = RuntimeError("gone")
    exc.response = SimpleNamespace(status_code=status)
    sender.side_effect = exc
    call_command("send_push")
    assert not PushSubscription.objects.exists()
    assert PushOutbox.objects.get().sent_at is not None


def test_send_push_retry_three_times(world, sender, caplog):
    _queue(world)
    sender.side_effect = TimeoutError("timeout")
    for attempt in range(1, 4):
        call_command("send_push")
        row = PushOutbox.objects.get()
        assert row.attempts == attempt
        assert (row.sent_at is not None) == (attempt == 3)
    call_command("send_push")
    assert sender.call_count == 3
    assert "破棄" in caplog.text
    assert ENDPOINT not in caplog.text and "秘密" not in caplog.text


def test_send_push_partial_failure_does_not_resend_success(world, sender):
    subscription, _ = _queue(world)
    _subscribe(subscription.user, "https://web.push.apple.com/other")
    sender.side_effect = [None, TimeoutError(), None]
    call_command("send_push")
    assert PushOutbox.objects.get().sent_at is None
    call_command("send_push")
    assert sender.call_count == 3
    assert PushOutbox.objects.get().sent_at is not None
    assert sender.call_args.kwargs["subscription_info"]["endpoint"] != subscription.endpoint


def test_send_push_read_hidden_and_transferred(world, sender):
    subscription, letters = _queue(world, 3)
    Letter.objects.filter(pk=letters[0].pk).update(read_at=timezone.now())
    Letter.objects.filter(pk=letters[1].pk).update(hidden=True)
    # 受信箱の未読数で数える（予定の数ではない）。
    call_command("send_push")
    assert (
        json.loads(sender.call_args.kwargs["data"])["body"]
        == f"{letters[2].sender.name}から公電が届きました"
    )
    sender.reset_mock()
    services.send_letter(
        letters[0].author, letters[0].sender, letters[0].recipient_id, "件名", "本文"
    )
    island = letters[0].recipient
    island.owner = None
    island.save(update_fields=["owner"])
    call_command("send_push")
    sender.assert_not_called()
    assert not PushOutbox.objects.filter(sent_at__isnull=True).exists()


def test_send_push_no_keys_and_cleanup(client, world, sender, settings):
    _queue(world)
    settings.HAKONIWA_VAPID_PUBLIC_KEY = ""
    call_command("send_push")
    sender.assert_not_called()
    assert PushOutbox.objects.get().sent_at is None
    client.force_login(PushSubscription.objects.get().user)
    assert 'id="letter-push"' not in client.get("/letters/").content.decode()
    settings.HAKONIWA_VAPID_PUBLIC_KEY = P256DH
    call_command("send_push")
    PushOutbox.objects.update(sent_at=timezone.now() - timedelta(days=8))
    call_command("send_push")
    assert not PushOutbox.objects.exists()


def test_push_dependency_optional(client, monkeypatch, configured):
    monkeypatch.setitem(sys.modules, "pywebpush", None)
    assert client.get("/sw.js").status_code == 200
    with pytest.raises(CommandError, match="pywebpush"):
        call_command("send_push")


def test_push_assets_and_opt_in(client, world, configured):
    worker = client.get("/sw.js")
    assert worker["Content-Type"] == "application/javascript"
    assert "max-age=60" in worker["Cache-Control"]
    script = worker.content.decode()
    assert 'addEventListener("push"' in script and 'addEventListener("notificationclick"' in script
    assert '"/letters/"' in script and "caches." not in script
    manifest = client.get("/manifest.webmanifest")
    assert manifest["Content-Type"] == "application/manifest+json"
    assert manifest.json()["display"] == "standalone" and manifest.json()["icons"]
    (alice, _, _), _ = _players(world)
    client.force_login(alice)
    page = client.get("/letters/").content.decode()
    assert (
        'rel="manifest"' in page and "このブラウザに知らせる" in page and "知らせをやめる" in page
    )
    assert 'id="letter-push"' not in client.get("/letters/sent/").content.decode()


def test_make_vapid_keys_optional_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "cryptography", None)
    with pytest.raises(CommandError, match="push"):
        call_command("make_vapid_keys", stdout=StringIO())


def test_push_ai_country_is_not_notified(client, world, sender):
    subscription, letters = _queue(world)
    keys.issue(subscription.user)
    client.force_login(subscription.user)
    assert (
        client.post("/api/letters/push", SUBSCRIPTION, content_type="application/json").status_code
        == 403
    )
    assert 'id="letter-push"' not in client.get("/letters/").content.decode()
    call_command("send_push")
    sender.assert_not_called()
    assert PushOutbox.objects.get().sent_at is not None
    letter = letters[0]
    services.send_letter(letter.author, letter.sender, letter.recipient_id, "件名", "本文")
    assert PushOutbox.objects.count() == 1


@pytest.mark.parametrize("status", [201, 302, 404])
def test_push_sender_disables_redirects(monkeypatch, status):
    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return SimpleNamespace(status_code=status)

    def webpush(*, requests_session, **kwargs):
        return requests_session.request("POST", ENDPOINT, allow_redirects=True, timeout=10)

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(Session=Session))
    monkeypatch.setitem(sys.modules, "pywebpush", SimpleNamespace(webpush=webpush))
    send = push.load_sender()
    if status == 201:
        send()
    else:
        with pytest.raises(RuntimeError) as error:
            send()
        assert error.value.response.status_code == status
    assert calls == [("POST", ENDPOINT, {"allow_redirects": False, "timeout": 10})]
