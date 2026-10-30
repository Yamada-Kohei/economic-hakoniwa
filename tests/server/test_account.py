"""アカウントの画面（UI-55）: ログイン名・パスワードの変更、国を手放して建て直す、
登録の名前の注意。"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from server.world import models as m

pytestmark = pytest.mark.django_db

PASS = "a-long-pass-123"


def _owner(client, world, name="hanako") -> tuple[User, m.Island]:
    user = User.objects.create_user(name, password=PASS)
    island = m.Island.objects.order_by("id").first()
    island.owner = user
    island.save()
    client.force_login(user)
    return user, island


def _post(client, action, **fields):
    data = {"action": action, **{f"{action}-{k}": v for k, v in fields.items()}}
    return client.post("/accounts/settings/", data)


def test_signup_refuses_an_email_as_the_login_name(client, db):
    res = client.post(
        "/accounts/signup/",
        {"username": "me@example.com", "password1": PASS, "password2": PASS},
        REMOTE_ADDR="10.0.0.9",
    )
    assert res.status_code == 200 and "メールアドレス" in res.content.decode()
    assert not User.objects.exists()
    page = client.get("/accounts/signup/").content.decode()
    assert "誰にでも見えます" in page


def test_rename_changes_the_name_shown_on_the_island(client, world):
    user, island = _owner(client, world, "me@example.com")
    assert "メールアドレスのまま" in client.get("/").content.decode()  # 気づけるように上に出す
    assert _post(client, "rename", username="yamato", password="wrong").status_code == 200
    assert _post(client, "rename", username="you@example.com", password=PASS).status_code == 200
    User.objects.create_user("Taken", password=PASS)
    assert _post(client, "rename", username="taken", password=PASS).status_code == 200
    user.refresh_from_db()
    assert user.username == "me@example.com"
    assert _post(client, "rename", username="yamato", password=PASS).status_code == 302
    user.refresh_from_db()
    assert user.username == "yamato"
    page = client.get(f"/islands/{island.id}/").content.decode()
    assert "yamato" in page and "me@example.com" not in page
    client.logout()
    assert client.login(username="yamato", password=PASS)


def test_password_change_keeps_the_user_logged_in(client, world):
    user, _ = _owner(client, world)
    res = _post(
        client,
        "password",
        old_password=PASS,
        new_password1="another-pass-456",
        new_password2="another-pass-456",
    )
    assert res.status_code == 302
    assert client.get("/accounts/settings/").status_code == 200  # ログインのまま
    user.refresh_from_db()
    assert user.check_password("another-pass-456")


def test_release_leaves_an_empty_island_and_lets_the_user_found_again(client, world):
    user, island = _owner(client, world)
    assert _post(client, "release", island_name="違う国", password=PASS).status_code == 200
    assert _post(client, "release", island_name=island.name, password="wrong").status_code == 200
    island.refresh_from_db()
    assert island.owner_id == user.pk
    res = _post(client, "release", island_name=island.name, password=PASS)
    assert res["Location"] == "/found/"
    island.refresh_from_db()
    assert island.owner is None and island.ext["released_by"] == user.pk  # 島は残る
    assert client.get("/found/").status_code == 200  # 建国の画面に入れる


def test_release_waits_after_the_last_release(client, world):
    user, island = _owner(client, world)
    assert _post(client, "release", island_name=island.name, password=PASS).status_code == 302
    other = m.Island.objects.exclude(pk=island.pk).filter(owner=None).first()
    other.owner = user
    other.save()
    res = _post(client, "release", island_name=other.name, password=PASS)
    assert res.status_code == 200 and "ターン後" in res.content.decode()
    other.refresh_from_db()
    assert other.owner_id == user.pk
    world.turn += 12
    world.save()
    assert _post(client, "release", island_name=other.name, password=PASS).status_code == 302


def test_release_is_refused_during_a_war(client, world):
    user, island = _owner(client, world)
    enemy = m.Island.objects.exclude(pk=island.pk).first()
    war = {"state": "war", "attacker": enemy.id, "defender": island.id}
    world.ext = {"diplomacy": {"wars": [war]}}
    world.save()
    res = _post(client, "release", island_name=island.name, password=PASS)
    assert res.status_code == 200 and "戦争の最中" in res.content.decode()
    island.refresh_from_db()
    assert island.owner_id == user.pk


def test_account_page_needs_login(client, db):
    assert client.get("/accounts/settings/").status_code == 302
