"""国どうしの手紙（非公開）（フェーズ3-4）。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from server.letters.models import Letter
from server.world.models import Island

pytestmark = pytest.mark.django_db


def _players(world) -> tuple[list[User], list[Island]]:
    """サンプルの島 3 つに、1 人ずつ持ち主を付ける。"""
    users, islands = [], list(Island.objects.order_by("id"))
    for i, island in enumerate(islands):
        user = User.objects.create_user(f"player{i}", password="a-long-pass-123")
        island.owner = user
        island.save(update_fields=["owner"])
        users.append(user)
    return users, islands


def _send(client, to: int, subject="ごあいさつ", body="よろしくお願いします。"):
    value = {"to": to, "subject": subject, "body": body}
    return client.post("/api/letters", value, content_type="application/json")


def test_send_receive_and_read(client, world):
    (alice, bob, _), (a, b, _) = _players(world)
    world.turn = 7
    world.save(update_fields=["turn"])
    client.force_login(alice)
    res = _send(client, b.id)
    assert res.status_code == 201
    sent = res.json()
    assert sent["from"] == {"id": a.id, "name": a.name}
    assert sent["to"] == {"id": b.id, "name": b.name}
    assert sent["author"] == "player0" and sent["turn"] == 7
    assert sent["body"] == "よろしくお願いします。" and not sent["read"]
    letter = Letter.objects.get()
    assert letter.author == alice and letter.sender == a and letter.recipient == b

    # 差出人が開いても既読にならない
    assert client.get(f"/api/letters/{letter.id}").json()["read"] is False
    data = client.get("/api/letters?box=sent").json()
    assert data["box"] == "sent" and [x["id"] for x in data["letters"]] == [letter.id]
    assert client.get("/api/letters").json()["letters"] == []

    client.force_login(bob)
    inbox = client.get("/api/letters").json()
    assert inbox["unread"] == 1 and inbox["count"] == 1
    assert inbox["letters"][0]["subject"] == "ごあいさつ" and "body" not in inbox["letters"][0]
    assert "未読の公電 1" in client.get("/").content.decode()  # 見出しの「外交」
    opened = client.get(f"/api/letters/{letter.id}").json()
    assert opened["read"] and opened["read_at"] and opened["body"] == "よろしくお願いします。"
    assert client.get("/api/letters").json()["unread"] == 0
    assert "未読" not in client.get("/").content.decode()

    client.force_login(alice)
    assert client.get("/api/letters?box=sent").json()["letters"][0]["read"] is True


def test_newest_first_and_pages(client, world):
    (alice, _, _), (_, b, _) = _players(world)
    client.force_login(alice)
    ids = [_send(client, b.id, subject=f"{i} 通目").json()["id"] for i in range(3)]
    data = client.get("/api/letters?box=sent").json()
    assert [x["id"] for x in data["letters"]] == ids[::-1]
    assert (data["page"], data["pages"], data["count"]) == (1, 1, 3)
    assert client.get("/api/letters?box=other").status_code == 400


def test_others_cannot_read(client, world):
    (alice, _, carol), (_, b, _) = _players(world)
    client.force_login(alice)
    letter_id = _send(client, b.id).json()["id"]
    client.force_login(carol)
    assert client.get(f"/api/letters/{letter_id}").status_code == 404
    assert client.get(f"/letters/{letter_id}/").status_code == 404
    assert client.get("/api/letters").json()["letters"] == []
    assert Letter.objects.get().read_at is None
    client.logout()
    assert client.get(f"/api/letters/{letter_id}").status_code == 403
    assert client.get("/api/letters").status_code == 403


def test_cannot_send_to_self_or_missing_island(client, world):
    (alice, _, _), (a, b, _) = _players(world)
    client.force_login(alice)
    res = _send(client, a.id)
    assert res.status_code == 400 and "自分の島" in res.json()["error"]
    res = _send(client, 999999)
    assert res.status_code == 400 and "ありません" in res.json()["error"]
    assert _send(client, b.id, subject=" ").status_code == 400
    assert _send(client, b.id, subject="あ" * 101).status_code == 400
    assert _send(client, b.id, body="").status_code == 400
    assert _send(client, "四").status_code == 400
    bad = client.post("/api/letters", "[1]", content_type="application/json")
    assert bad.status_code == 400
    assert not Letter.objects.exists()


def test_cannot_send_to_an_island_without_an_owner(client, world):
    (alice, _, _), (a, b, c) = _players(world)
    Island.objects.filter(pk=c.pk).update(owner=None)
    client.force_login(alice)
    res = _send(client, c.id)
    assert res.status_code == 400 and "持ち主がいません" in res.json()["error"]
    assert not Letter.objects.exists()
    page = client.get(f"/letters/new/?to={b.id}").content.decode()
    assert f'<option value="{b.id}" selected>' in page
    assert f'<option value="{c.id}"' not in page  # 持ち主のいない島は宛先に出さない
    assert "/letters/new/?to=" not in client.get(f"/islands/{c.id}/").content.decode()


def test_only_island_owners_can_send(client, world):
    _, (_, b, _) = _players(world)
    assert _send(client, b.id).status_code == 403  # ログインしていない
    client.force_login(User.objects.create_user("nobody", password="a-long-pass-123"))
    res = _send(client, b.id)
    assert res.status_code == 403 and "島" in res.json()["error"]
    assert "建国する" in client.get("/letters/").content.decode()
    assert not Letter.objects.exists()


def test_daily_limit(client, world, settings):
    settings.HAKONIWA_LETTERS_PER_DAY = 2
    (alice, bob, _), (a, b, _) = _players(world)
    client.force_login(alice)
    assert _send(client, b.id).status_code == 201
    assert _send(client, b.id).status_code == 201
    res = _send(client, b.id)
    assert res.status_code == 429 and "2 通まで" in res.json()["error"]
    client.force_login(bob)  # 上限は島ごと
    assert _send(client, a.id).status_code == 201
    # 24 時間より前に送った手紙は数えない
    Letter.objects.filter(sender=a).update(sent_at=timezone.now() - timedelta(days=1, minutes=1))
    client.force_login(alice)
    assert _send(client, b.id).status_code == 201


def test_hidden_letters_are_not_shown(client, world):
    (alice, bob, _), (_, b, _) = _players(world)
    client.force_login(alice)
    letter_id = _send(client, b.id).json()["id"]
    Letter.objects.update(hidden=True)
    client.force_login(bob)
    assert client.get(f"/api/letters/{letter_id}").status_code == 404
    inbox = client.get("/api/letters").json()
    assert inbox["letters"] == [] and inbox["unread"] == 0


def test_admin_hides_letters(admin_client, world):
    island_a, island_b = Island.objects.order_by("id")[:2]
    letter = Letter.objects.create(
        sender=island_a, recipient=island_b, subject="見出し", body="中身", turn=0
    )
    assert admin_client.get("/admin/letters/letter/").status_code == 200
    action = {"action": "hide", "_selected_action": [letter.id]}
    admin_client.post("/admin/letters/letter/", action)
    letter.refresh_from_db()
    assert letter.hidden


def test_csrf_is_required_for_posting(world):
    """hk.py と同じく、cookie の csrftoken を X-CSRFToken に入れて送る。"""
    (alice, _, _), (_, b, _) = _players(world)
    client = Client(enforce_csrf_checks=True)
    client.force_login(alice)
    assert _send(client, b.id).status_code == 403
    client.get("/")  # 見出しのログアウトのフォームで csrf の cookie が来る
    token = client.cookies["csrftoken"].value
    value = {"to": b.id, "subject": "題", "body": "本文"}
    res = client.post(
        "/api/letters", value, content_type="application/json", HTTP_X_CSRFTOKEN=token
    )
    assert res.status_code == 201


def test_letter_pages(client, world):
    (alice, bob, _), (a, b, _) = _players(world)
    client.force_login(alice)
    # 島の画面: ほかの島には公電を送るリンク、自分の島にはなし
    island_page = client.get(f"/islands/{b.id}/").content.decode()
    assert f"/letters/new/?to={b.id}" in island_page and "この国に公電を送る" in island_page
    assert "/letters/new/?to=" not in client.get(f"/islands/{a.id}/").content.decode()
    page = client.get(f"/letters/new/?to={b.id}").content.decode()
    assert f'<option value="{b.id}" selected>' in page
    assert f'<option value="{a.id}"' not in page  # 自分の島は宛先に出さない
    assert "公電を送る" in page and "件名" in page and "手紙" not in page
    res = client.post("/letters/new/", {"to": b.id, "subject": "画面から", "body": "こんにちは"})
    letter = Letter.objects.get()
    assert res.status_code == 302 and res["Location"] == f"/letters/{letter.id}/"
    assert "画面から" in client.get("/letters/sent/").content.decode()

    client.force_login(bob)
    inbox = client.get("/letters/").content.decode()
    assert "画面から" in inbox and "届いた公電" in inbox and "手紙" not in inbox
    page = client.get(f"/letters/{letter.id}/").content.decode()
    assert "こんにちは" in page and f"/letters/new/?re={letter.id}" in page
    assert "発信国" in page and "返電を送る" in page and "手紙" not in page
    letter.refresh_from_db()
    assert letter.read_at is not None
    reply = client.get(f"/letters/new/?re={letter.id}").content.decode()
    assert "Re: 画面から" in reply and f'<option value="{a.id}" selected>' in reply

    client.logout()
    assert client.get("/letters/")["Location"].startswith("/accounts/login/")
    assert "/letters/new/?to=" not in client.get(f"/islands/{b.id}/").content.decode()
