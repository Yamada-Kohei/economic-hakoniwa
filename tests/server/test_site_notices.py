"""運営からのお知らせの印（PL-63）と更新のお知らせの窓（UI-96）、リンク集の新貿易版箱庭諸国。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from server.board import services
from server.board.models import Post
from server.web import top
from server.web.models import SiteUpdate, UpdateMark

pytestmark = pytest.mark.django_db


def _user(name: str, **kwargs) -> User:
    return User.objects.create_user(name, password="a-long-pass-123", **kwargs)


def _post(client, url: str, value: dict):
    return client.post(url, value, content_type="application/json")


def _announce(client, staff: User, title="ゲームを更新しました", body="内容です") -> int:
    client.force_login(staff)
    res = _post(
        client,
        "/api/board/threads",
        {"board": "news", "title": title, "body": body, "identity": "staff"},
    )
    assert res.status_code == 201, res.content
    return res.json()["thread"]["id"]


# --- 運営からのお知らせの印（PL-63） ------------------------------------------------------


def test_announcement_shows_a_red_mark_until_the_board_is_opened(client):
    staff, alice = _user("admin", is_staff=True), _user("alice")
    thread_id = _announce(client, staff)
    assert services.unread_announcements(staff) == 0  # 自分の告知は数えない
    client.force_login(alice)
    page = client.get("/").content.decode()
    assert 'class="badge">お知らせ 1</span>' in page  # 見出しの「掲示板」に返信と同じ赤い印
    data = client.get("/api/board/notices").json()
    assert data["unread_announcements"] == 1
    assert data["announcements"][0]["thread"] == {"id": thread_id, "title": "ゲームを更新しました"}
    assert services.unread_announcements(alice) == 1  # GET の API では既読にしない
    board = client.get("/board/").content.decode()
    assert "運営からのお知らせ" in board and "ゲームを更新しました" in board
    assert services.unread_announcements(alice) == 0
    assert "お知らせ 1" not in client.get("/").content.decode()
    # 続きの書き込みも全員への知らせ。告知の板を開くと見たことになる
    client.force_login(staff)
    _post(client, f"/api/board/threads/{thread_id}/posts", {"body": "追記", "identity": "staff"})
    client.force_login(alice)
    assert services.unread_announcements(alice) == 1
    client.get("/board/b/news/")
    assert services.unread_announcements(alice) == 0


def test_opening_one_announcement_thread_keeps_the_others_unread(client):
    staff, alice = _user("admin", is_staff=True), _user("alice")
    first = _announce(client, staff, title="一つ目")
    second = _announce(client, staff, title="二つ目")
    client.force_login(alice)
    client.get(f"/board/t/{first}/")
    assert services.unread_announcements(alice) == 1  # 開いたスレッドの分だけ見た。二つ目は残る
    assert [a["thread"]["id"] for a in services.announcements(alice)] == [second]
    client.get(f"/board/t/{second}/")
    assert services.unread_announcements(alice) == 0
    # 次の告知は、また印になる
    _announce(client, staff, title="三つ目")
    client.force_login(alice)
    assert services.unread_announcements(alice) == 1


def test_old_announcements_and_other_boards_do_not_count(client):
    staff, alice = _user("admin", is_staff=True), _user("alice")
    _announce(client, staff)
    old = timezone.now() - timedelta(days=services.ANNOUNCE_DAYS + 1)
    Post.objects.update(created_at=old)
    assert services.unread_announcements(alice) == 0
    # 運営が雑談の板に書いた返事は、全員への知らせではない
    client.force_login(staff)
    res = _post(
        client,
        "/api/board/threads",
        {"board": "chat", "title": "雑談", "body": "やあ", "identity": "staff"},
    )
    assert res.status_code == 201
    assert services.unread_announcements(alice) == 0
    # 非表示にした告知も数えない
    _announce(client, staff, title="消す")
    Post.objects.filter(thread__title="消す").update(hidden=True)
    assert services.unread_announcements(alice) == 0


# --- 更新のお知らせの窓（UI-96） ----------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_synced_updates():
    SiteUpdate.objects.all().delete()  # migrate で写した git の記録の更新履歴を外す


def _old_user(name: str) -> User:
    user = _user(name)
    User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(days=30))
    user.refresh_from_db()
    return user


def test_update_popup_once_per_new_update(client):
    alice = _old_user("alice")
    today = timezone.localdate()
    SiteUpdate.objects.create(date=today - timedelta(days=30), text="ずっと前の更新")
    SiteUpdate.objects.create(date=today, text="船の仕組みを直しました。")
    client.force_login(alice)
    page = client.get("/help/").content.decode()
    assert 'id="site-news"' in page and "船の仕組みを直しました。" in page
    assert "ずっと前の更新" not in page  # 印のない人には NEW! の日数のうちだけ
    news = client.get("/api/world").json()["unseen_updates"]
    assert [r["text"] for r in news["rows"]] == ["船の仕組みを直しました。"]
    res = _post(client, "/api/updates/seen", {"last": news["last"]})
    assert res.status_code == 200 and res.json()["last"] == news["last"]
    assert 'id="site-news"' not in client.get("/help/").content.decode()
    assert client.get("/api/world").json()["unseen_updates"] is None
    # 次の更新が来たら、その分だけまた1度出る。隠した行は出さない
    SiteUpdate.objects.create(date=today, text="隠す行", hidden=True)
    SiteUpdate.objects.create(date=today, text="市場の画面を直しました。")
    page = client.get("/help/").content.decode()
    assert "市場の画面を直しました。" in page and "船の仕組みを直しました。" not in page
    assert "隠す行" not in page
    # 印は戻らない・ない番号より先へは進まない
    assert top.mark_updates_seen(alice, 0) == news["last"]
    newest = SiteUpdate.objects.order_by("-id").first().id
    assert top.mark_updates_seen(alice, newest + 100) == newest


def test_update_popup_skips_updates_before_signing_up_and_guests(client):
    SiteUpdate.objects.create(date=timezone.localdate() - timedelta(days=2), text="登録の前")
    bob = _user("bob")  # 今日登録した
    client.force_login(bob)
    assert 'id="site-news"' not in client.get("/").content.decode()
    assert not UpdateMark.objects.exists()
    client.logout()
    assert 'id="site-news"' not in client.get("/").content.decode()
    assert client.get("/api/world").json()["unseen_updates"] is None
    assert _post(client, "/api/updates/seen", {"last": 1}).status_code == 403


def test_update_popup_more_rows_go_to_the_top_page(client):
    alice = _old_user("alice")
    for i in range(top.NEWS_LIMIT + 3):
        SiteUpdate.objects.create(date=timezone.localdate(), text=f"更新 {i}")
    client.force_login(alice)
    page = client.get("/help/").content.decode()
    assert "ほかに 3 件あります。" in page and 'href="/#updates"' in page
    assert _post(client, "/api/updates/seen", {"last": "x"}).status_code == 400


# --- リンク集 -----------------------------------------------------------------------------


def test_links_include_the_owners_home_hakoniwa(client):
    links = top.site_notes()["links"]
    assert {"title": "新貿易版箱庭諸国", "url": "https://tanstaafl.tokyo/"}.items() <= next(
        link for link in links if link["title"] == "新貿易版箱庭諸国"
    ).items()
    assert "https://tanstaafl.tokyo/" in client.get("/").content.decode()
