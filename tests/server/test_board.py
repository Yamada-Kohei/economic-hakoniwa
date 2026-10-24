"""掲示板: 板・番号付きの書き込み・>>返信・名無しと国名とトリップ・日ごとの ID・sage・
1000 で止まる・過去ログ・検索・返信の知らせ・いいね・NG・通報・歯止め（W-147。設計書 PL-35・
PL-51〜PL-55）。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from server.board import identity, services
from server.board.models import Board, Post, Reply, Report, Thread
from server.world.models import Island

pytestmark = pytest.mark.django_db


def _user(name="alice", **kwargs) -> User:
    return User.objects.create_user(name, password="a-long-pass-123", **kwargs)


def _json(client, url: str, value: dict | None = None, method="post"):
    send = getattr(client, method)
    return send(url, value or {}, content_type="application/json")


def _thread(client, user, title="はじめまして", body="よろしく", board="chat", **extra):
    client.force_login(user)
    res = _json(
        client, "/api/board/threads", {"board": board, "title": title, "body": body, **extra}
    )
    assert res.status_code == 201, res.content
    return res.json()["thread"]["id"]


def _say(client, thread_id: int, body: str, **extra):
    return _json(client, f"/api/board/threads/{thread_id}/posts", {"body": body, **extra})


def test_boards_are_seeded():
    assert [b.slug for b in Board.objects.all()] == [
        "chat",
        "diplomacy",
        "questions",
        "strategy",
        "news",
    ]
    assert Board.objects.get(slug="news").staff_only


def test_threads_posts_are_numbered_and_hide_the_account(client):
    alice, bob = _user("alice"), _user("bob")
    thread_id = _thread(client, alice, body="一つ目")
    client.force_login(bob)
    assert _say(client, thread_id, "二つ目").json()["number"] == 2
    data = client.get(f"/api/board/threads/{thread_id}").json()
    assert [p["number"] for p in data["posts"]] == [1, 2]
    assert [p["name"] for p in data["posts"]] == ["名無し", "名無し"]
    assert data["thread"]["posts"] == 2 and data["thread"]["board"]["slug"] == "chat"
    assert data["posts"][0]["id"] != data["posts"][1]["id"]  # 人ごとに違う ID
    client.logout()
    text = str(data) + client.get(f"/board/t/{thread_id}/").content.decode()
    assert "alice" not in text and "bob" not in text  # 書いた人のアカウントは出さない


def test_id_is_per_board_and_per_day(client):
    alice = _user()
    first = _thread(client, alice, board="chat")
    second = _thread(client, alice, title="別", body="別の板", board="questions")
    ids = [client.get(f"/api/board/threads/{t}").json()["posts"][0]["id"] for t in (first, second)]
    assert ids[0] != ids[1]
    tomorrow = timezone.localdate() + timedelta(days=1)
    chat = Board.objects.get(slug="chat")
    assert identity.day_ident(tomorrow, chat.id, alice.id) != ids[0]
    assert identity.day_ident(timezone.localdate(), chat.id, alice.id) == ids[0]
    _say(client, first, "もう一度")
    again = client.get(f"/api/board/threads/{first}").json()["posts"]
    assert again[0]["id"] == again[1]["id"]  # 同じ日・同じ板・同じ人は同じ ID


def test_trip_and_name(client):
    alice = _user()
    thread_id = _thread(client, alice, name="太郎★◆#ひみつ")
    _say(client, thread_id, "同じ秘密", name="次郎#ひみつ")
    _say(client, thread_id, "別の秘密", name="三郎#べつ")
    posts = client.get(f"/api/board/threads/{thread_id}").json()["posts"]
    assert posts[0]["name"] == "太郎" and posts[1]["name"] == "次郎"
    assert posts[0]["trip"] and posts[0]["trip"] == posts[1]["trip"] != posts[2]["trip"]
    assert "ひみつ" not in str(posts)
    page = client.get(f"/board/t/{thread_id}/").content.decode()
    assert f"◆{posts[0]['trip']}" in page and "ひみつ" not in page
    assert identity.split_name("太郎#") == ("太郎", "")  # 秘密が空ならトリップは付かない


def test_country_name_links_to_the_country_page(client, world):
    alice = _user()
    island = Island.objects.order_by("id").first()
    island.owner = alice
    island.save(update_fields=["owner"])
    thread_id = _thread(client, alice, identity="country", name="無視される")
    post = client.get(f"/api/board/threads/{thread_id}").json()["posts"][0]
    assert post["island"] == {"id": island.id, "name": island.name}
    assert post["name"] == island.name
    assert post["id"] == ""  # 名無しで書いた同じ日の書き込みと結びつけない
    page = client.get(f"/board/t/{thread_id}/").content.decode()
    assert f'href="/islands/{island.id}/country/"' in page
    # 国のない人は国名で書けない
    client.force_login(_user("nocountry"))
    res = _say(client, thread_id, "x", identity="country")
    assert res.status_code == 403
    assert _say(client, thread_id, "x", identity="bogus").status_code == 400
    assert _say(client, thread_id, "x", identity="staff").status_code == 403


def test_sage_does_not_bump(client):
    alice = _user()
    first = _thread(client, alice, title="古い")
    second = _thread(client, alice, title="新しい", body="別")
    titles = [t["title"] for t in client.get("/api/board/threads?board=chat").json()["threads"]]
    assert titles == ["新しい", "古い"]
    _say(client, first, "sage で書く", sage=True)
    titles = [t["title"] for t in client.get("/api/board/threads").json()["threads"]]
    assert titles == ["新しい", "古い"]
    _say(client, first, "上げる")
    titles = [t["title"] for t in client.get("/api/board/threads").json()["threads"]]
    assert titles == ["古い", "新しい"] and second


def test_anchors_become_links_and_replies_are_recorded(client):
    alice, bob = _user("alice"), _user("bob")
    thread_id = _thread(client, alice, body="最初")
    client.force_login(bob)
    _say(client, thread_id, ">>1 に賛成です <b>x</b>")
    _say(client, thread_id, ">>1-2 まとめて")
    assert Reply.objects.count() == 3  # 2→1、3→1、3→2
    page = client.get(f"/board/t/{thread_id}/").content.decode()
    assert 'class="anchor"' in page and 'data-n="1"' in page
    assert "&lt;b&gt;x&lt;/b&gt;" in page and "<b>x</b>" not in page
    assert "返信:" in page
    posts = client.get(f"/api/board/threads/{thread_id}").json()["posts"]
    assert posts[0]["replied_by"] == [2, 3] and posts[1]["replied_by"] == [3]
    # 一つの書き込みを指す API（浮き出し用）
    one = client.get(f"/api/board/threads/{thread_id}/posts/1").json()
    assert one["body"] == "最初" and one["thread"] == thread_id
    assert client.get(f"/api/board/threads/{thread_id}/posts/99").status_code == 404


def test_notices_count_unread_replies_and_mark_read(client):
    alice, bob = _user("alice"), _user("bob")
    thread_id = _thread(client, alice, body="最初")
    client.force_login(alice)
    _say(client, thread_id, ">>1 自分への返信は知らせない")
    assert services.unread_count(alice) == 0
    client.force_login(bob)
    _say(client, thread_id, ">>1 返信です")
    assert services.unread_count(alice) == 1
    client.force_login(alice)
    assert "返信 1" in client.get("/").content.decode()  # 見出しの「掲示板」
    data = client.get("/api/board/notices").json()
    assert data["unread"] == 1 and data["notices"][0]["to"] == 1
    assert data["notices"][0]["post"]["number"] == 3 and data["notices"][0]["unread"]
    client.get("/api/board/notices?mark=1")
    assert client.get("/api/board/notices").json()["unread"] == 0
    # 画面を開いても既読になる
    client.force_login(bob)
    _say(client, thread_id, ">>1 もう一つ")
    client.force_login(alice)
    page = client.get("/board/notices/").content.decode()
    assert "もう一つ" in page and "NEW" in page
    assert services.unread_count(alice) == 0
    assert _json(_anon(client), "/api/board/ng", method="get").status_code == 403


def _anon(client):
    client.logout()
    return client


def test_like_toggles_and_not_own(client):
    alice, bob = _user("alice"), _user("bob")
    thread_id = _thread(client, alice)
    url = f"/api/board/threads/{thread_id}/posts/1/like"
    assert _json(client, url).status_code == 400  # 自分の書き込み
    client.force_login(bob)
    assert _json(client, url).json() == {"number": 1, "liked": True, "likes": 1}
    assert client.get(f"/api/board/threads/{thread_id}").json()["posts"][0]["liked"] is True
    assert _json(client, url).json()["liked"] is False
    assert Post.objects.get().like_count == 0
    _anon(client)
    assert _json(client, url).status_code == 403


def test_report_once_and_admin_can_hide(client):
    alice, bob = _user("alice"), _user("bob")
    thread_id = _thread(client, alice, body="ひどい")
    url = f"/api/board/threads/{thread_id}/posts/1/report"
    assert _json(client, url, {"reason": "x"}).status_code == 400  # 自分
    client.force_login(bob)
    assert _json(client, url, {"reason": "荒らし" * 100}).status_code == 201
    assert _json(client, url).status_code == 409
    report = Report.objects.get()
    assert len(report.reason) == 200 and report.reporter == bob
    # 管理者が非表示にすると、番号は残って本文は出ない（あぼーん）
    Post.objects.update(hidden=True)
    post = client.get(f"/api/board/threads/{thread_id}").json()["posts"][0]
    assert post["hidden"] and post["body"] is None and post["number"] == 1
    page = client.get(f"/board/t/{thread_id}/").content.decode()
    assert "あぼーん" in page and "ひどい" not in page
    assert _json(client, url).status_code == 404
    Thread.objects.update(hidden=True)
    assert client.get(f"/api/board/threads/{thread_id}").status_code == 404
    assert client.get(f"/board/t/{thread_id}/").status_code == 404
    assert client.get("/api/board/threads").json()["threads"] == []


def test_ng_person_ident_and_word(client):
    alice, bob, carol = _user("alice"), _user("bob"), _user("carol")
    thread_id = _thread(client, alice, body="アリスです")
    client.force_login(bob)
    _say(client, thread_id, "ボブです", name="ボブ")
    client.force_login(carol)
    _say(client, thread_id, "キャロルです スパム")
    client.force_login(bob)
    # 人: 名前を変えても効く。誰かは返さない
    res = _json(client, "/api/board/ng", {"kind": "person", "thread": thread_id, "number": 1})
    assert res.status_code == 201 and "target" not in res.json()
    assert (
        _json(
            client, "/api/board/ng", {"kind": "person", "thread": thread_id, "number": 2}
        ).status_code
        == 400
    )
    posts = client.get(f"/api/board/threads/{thread_id}").json()["posts"]
    assert [p["ng"] for p in posts] == [True, False, False]
    assert posts[0]["body"] is None
    shown = client.get(f"/api/board/threads/{thread_id}?ng=show").json()["posts"]
    assert shown[0]["body"] == "アリスです"
    assert "NG の書き込み" in client.get(f"/board/t/{thread_id}/").content.decode()
    # 言葉と ID
    assert _json(client, "/api/board/ng", {"kind": "word", "word": "スパム"}).status_code == 201
    assert (
        _json(
            client, "/api/board/ng", {"kind": "ident", "thread": thread_id, "number": 3}
        ).status_code
        == 201
    )
    posts = client.get(f"/api/board/threads/{thread_id}").json()["posts"]
    assert [p["ng"] for p in posts] == [True, False, True]
    rules = client.get("/api/board/ng").json()["rules"]
    assert [r["kind"] for r in rules] == ["person", "word", "ident"]
    assert _json(client, f"/api/board/ng/{rules[0]['id']}", method="delete").status_code == 200
    assert client.get(f"/api/board/threads/{thread_id}").json()["posts"][0]["ng"] is False
    # 他人の NG は消せない
    client.force_login(carol)
    assert _json(client, f"/api/board/ng/{rules[1]['id']}", method="delete").status_code == 404
    # 運営の書き込みは NG にできない
    assert _json(client, "/api/board/ng", {"kind": "word"}).status_code == 400


def test_thread_closes_at_1000(client):
    alice = _user()
    thread_id = _thread(client, alice)
    Thread.objects.filter(pk=thread_id).update(post_count=998)
    assert _say(client, thread_id, "999").json()["number"] == 999
    assert _say(client, thread_id, "1000").json()["number"] == 1000
    thread = Thread.objects.get()
    assert thread.closed and thread.post_count == 1000
    res = _say(client, thread_id, "1001")
    assert res.status_code == 409 and "1000" in res.json()["error"]
    page = client.get(f"/board/t/{thread_id}/").content.decode()
    assert "もう書けません" in page


def test_old_threads_fall_into_the_log(client, settings):
    alice = _user()
    chat = Board.objects.get(slug="chat")
    chat.max_threads = 3
    chat.save()
    settings.HAKONIWA_BOARD_THREADS_PER_DAY = 100
    ids = [_thread(client, alice, title=f"スレ{i}", body=f"本文{i}") for i in range(3)]
    _say(client, ids[0], "上げる")  # 0 は新しくなる
    fourth = _thread(client, alice, title="スレ3", body="本文3")
    active = [t["title"] for t in client.get("/api/board/threads?board=chat").json()["threads"]]
    assert active == ["スレ3", "スレ0", "スレ2"]
    fallen = client.get("/api/board/threads?board=chat&archived=1").json()["threads"]
    assert [t["title"] for t in fallen] == ["スレ1"] and fallen[0]["archived"]
    # 過去ログは読めるが書けない
    assert client.get(f"/api/board/threads/{ids[1]}").status_code == 200
    assert _say(client, ids[1], "x").status_code == 409
    assert "スレ1" in client.get("/board/b/chat/log/").content.decode()
    assert "スレ1" not in client.get("/board/b/chat/").content.decode()
    # 固定は落ちない
    Thread.objects.filter(pk=ids[2]).update(pinned=True)
    _thread(client, alice, title="スレ4", body="本文4")
    _thread(client, alice, title="スレ5", body="本文5")
    assert Thread.objects.get(pk=ids[2]).archived is False and fourth


def test_search_finds_titles_and_bodies_including_the_log(client):
    alice = _user()
    first = _thread(client, alice, title="貿易圏を作ろう", body="関税をなくしたい")
    _thread(client, alice, title="雑談", body="今日は関税の話", board="questions")
    Thread.objects.filter(pk=first).update(archived=True)
    data = client.get("/api/board/search?q=関税").json()
    assert data["count"] == 2
    assert client.get("/api/board/search?q=関税 なくしたい").json()["count"] == 1
    assert client.get("/api/board/search?q=貿易圏").json()["count"] == 1  # 題名
    assert client.get("/api/board/search?q=関税&board=questions").json()["count"] == 1
    assert client.get("/api/board/search").status_code == 400
    page = client.get("/board/search/?q=関税").content.decode()
    assert "貿易圏を作ろう" in page and "2 件" in page
    Post.objects.update(hidden=True)
    assert client.get("/api/board/search?q=関税").json()["count"] == 0


def test_limits(client, settings):
    alice = _user()
    settings.HAKONIWA_BOARD_NEWCOMER_MINUTES = 10
    client.force_login(alice)
    res = _json(client, "/api/board/threads", {"title": "t", "body": "b"})
    assert res.status_code == 429 and "あと" in res.json()["error"]
    User.objects.filter(pk=alice.pk).update(date_joined=timezone.now() - timedelta(minutes=11))
    thread_id = _thread(client, alice, body="最初")
    # 連投の間隔
    settings.HAKONIWA_BOARD_INTERVAL_SECONDS = 60
    res = _say(client, thread_id, "すぐ")
    assert res.status_code == 429 and "秒" in res.json()["error"]
    settings.HAKONIWA_BOARD_INTERVAL_SECONDS = 0
    # 同じ文
    assert _say(client, thread_id, "同じ").status_code == 201
    assert _say(client, thread_id, "同じ").status_code == 429
    # 24 時間の数
    settings.HAKONIWA_BOARD_POSTS_PER_DAY = 2
    assert _say(client, thread_id, "別の文").status_code == 429
    settings.HAKONIWA_BOARD_POSTS_PER_DAY = 200
    settings.HAKONIWA_BOARD_THREADS_PER_DAY = 1
    res = _json(client, "/api/board/threads", {"title": "二つ目", "body": "ちがう"})
    assert res.status_code == 429
    # 管理者は歯止めの外
    admin = _user("boss", is_staff=True)
    client.force_login(admin)
    assert _say(client, thread_id, "運営", identity="staff").status_code == 201


def test_staff_only_board_and_staff_mark(client):
    alice = _user()
    client.force_login(alice)
    res = _json(client, "/api/board/threads", {"board": "news", "title": "t", "body": "b"})
    assert res.status_code == 403
    admin = _user("boss", is_staff=True)
    thread_id = _thread(client, admin, board="news", title="お知らせ", body="メンテ")
    post = client.get(f"/api/board/threads/{thread_id}").json()["posts"][0]
    assert post["staff"] and post["name"] == "運営"
    client.force_login(alice)
    assert _say(client, thread_id, "返信").status_code == 403
    page = client.get(f"/board/t/{thread_id}/").content.decode()
    assert "★" in page and "メンテ" in page


def test_validation(client):
    alice = _user()
    client.force_login(alice)
    assert _json(client, "/api/board/threads", {"title": "", "body": "x"}).status_code == 400
    assert _json(client, "/api/board/threads", {"title": "t", "body": ""}).status_code == 400
    assert (
        _json(client, "/api/board/threads", {"title": "t", "body": "あ" * 2001}).status_code == 400
    )
    assert (
        _json(client, "/api/board/threads", {"title": "t", "body": "x\n" * 50}).status_code == 400
    )
    assert (
        _json(
            client, "/api/board/threads", {"board": "nope", "title": "t", "body": "x"}
        ).status_code
        == 404
    )
    assert (
        client.post("/api/board/threads", "[]", content_type="application/json").status_code == 400
    )
    thread_id = _thread(client, alice, title="  空白   だらけ  ", body="本文\x00です")
    data = client.get(f"/api/board/threads/{thread_id}").json()
    assert data["thread"]["title"] == "空白 だらけ" and data["posts"][0]["body"] == "本文です"
    _anon(client)
    assert _say(client, thread_id, "x").status_code == 403


def test_pages_render_and_paginate(client, settings):
    settings.HAKONIWA_BOARD_POSTS_PER_DAY = 1000
    alice = _user()
    thread_id = _thread(client, alice, body="最初")
    for url in [
        "/board/",
        "/board/b/chat/",
        "/board/b/chat/log/",
        "/board/search/",
        f"/board/t/{thread_id}/",
    ]:
        assert client.get(url).status_code == 200, url
    assert client.get("/board/b/nope/").status_code == 404
    # 古い住所は新しい住所へ
    res = client.get(f"/board/{thread_id}/")
    assert res.status_code == 301 and res["Location"] == f"/board/t/{thread_id}/"
    # 画面から立てる・書く
    res = client.post(
        "/board/b/chat/",
        {"title": "画面から", "body": "こんにちは", "identity": "anon", "name": "花子"},
    )
    new = Thread.objects.get(title="画面から")
    assert res.status_code == 302 and res["Location"] == f"/board/t/{new.id}/"
    res = client.post(
        f"/board/t/{new.id}/", {"body": ">>1 ありがとう", "identity": "anon", "sage": "on"}
    )
    assert res.status_code == 302
    assert new.posts.get(number=2).sage
    page = client.get(f"/board/t/{new.id}/?latest=50").content.decode()
    assert "ありがとう" in page and "花子" in page
    # 歯止めの文は欄に出る
    res = client.post(f"/board/t/{new.id}/", {"body": ">>1 ありがとう", "identity": "anon"})
    assert res.status_code == 200 and "同じ内容" in res.content.decode()
    # ページ分け
    Thread.objects.filter(pk=thread_id).update(post_count=0)
    thread = Thread.objects.get(pk=thread_id)
    Post.objects.bulk_create(
        Post(thread=thread, number=n, author=alice, body=f"b{n}") for n in range(2, 251)
    )
    Thread.objects.filter(pk=thread_id).update(post_count=250)
    page_two = client.get(f"/board/t/{thread_id}/?p=2").content.decode()
    assert 'id="p101"' in page_two and 'id="p100"' not in page_two
    last = client.get(f"/api/board/threads/{thread_id}?page=last").json()
    assert last["page"] == 3 and last["posts"][-1]["number"] == 250
    ranged = client.get(f"/api/board/threads/{thread_id}?from=120&to=122").json()
    assert [p["number"] for p in ranged["posts"]] == [120, 121, 122]
    tail = client.get(f"/api/board/threads/{thread_id}?latest=5").json()
    assert [p["number"] for p in tail["posts"]] == [246, 247, 248, 249, 250]
    # 別のページの返信は、そのページへ飛ぶ住所になる
    res = client.post(f"/board/t/{thread_id}/", {"body": ">>150 あれ", "identity": "anon"})
    assert res.status_code == 302, res.content.decode()[-1500:]
    assert (
        f"/board/t/{thread_id}/?p=2#p150"
        in client.get(f"/board/t/{thread_id}/?p=last").content.decode()
    )


def test_ng_page_and_unauthenticated_redirects(client):
    assert client.get("/board/ng/")["Location"].startswith("/accounts/login/")
    assert client.get("/board/notices/")["Location"].startswith("/accounts/login/")
    alice = _user()
    client.force_login(alice)
    res = client.post("/board/ng/", {"kind": "word", "word": "うるさい"})
    assert res.status_code == 302
    assert "うるさい" in client.get("/board/ng/").content.decode()
    rule = alice.board_ng_rules.get()
    client.post("/board/ng/", {"delete": rule.id})
    assert not alice.board_ng_rules.exists()
    assert "種類" in client.post("/board/ng/", {"kind": "person", "word": "x"}).content.decode()
