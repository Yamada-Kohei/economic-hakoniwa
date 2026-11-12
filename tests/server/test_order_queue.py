"""命令の列の帯・命令のページ・1 件ずつの保存（UI-74）。画面・JSON API・MCP で同じ列を扱う。"""

from __future__ import annotations

import json

import pytest

from server.game import get_game
from server.orders.models import Order
from server.web.templatetags.order_tags import bar_text
from tests.server.test_ai_parity import _call, _tool
from tests.server.test_ai_parity import played as played  # noqa: F401
from tests.server.test_phase1 import _login, _own_island

pytestmark = pytest.mark.django_db

RENAME = {"kind": "rename_island", "payload": {"name": "新しい名前"}}
SEA_FARM = {"kind": "build", "payload": {"facility": "farm", "x": 0, "y": 0}}


def _url(island, tail=""):
    return f"/api/islands/{island.id}/orders{tail}"


def _post(client, url, body):
    return client.post(url, body, content_type="application/json")


def test_add_one_keeps_orders_added_elsewhere_and_checks_like_the_list(client, world):
    """1 件足す API は列の最後に足すので、ほかの画面で足した命令を消さない。形の誤りは 400、今は
    実行できない命令は警告、確かめの要る命令（UI-75）は 428。持ち主だけ。"""
    island = _own_island(client)
    first = _post(client, _url(island, "/items"), RENAME)
    assert first.status_code == 200, first.content.decode()
    added = first.json()["added"]
    # 別の画面（古い列を持ったまま）で足しても、先の命令は残る
    second = _post(client, _url(island, "/items"), SEA_FARM).json()
    assert [o["kind"] for o in second["orders"]] == ["rename_island", "build"]
    assert second["orders"][0]["id"] == added
    assert second["warning"]  # 海には建てられない（前の命令しだいで変わるので受け付ける）
    assert [w["index"] for w in second["warnings"]] == [1]
    assert list(island.orders.order_by("seq").values_list("seq", flat=True)) == [1, 2]

    bad = _post(client, _url(island, "/items"), {"kind": "launch_rocket", "payload": {}})
    assert bad.status_code == 400 and bad.json()["error"]
    site = island.facilities.exclude(kind="housing").first()
    demolish = {"kind": "demolish", "payload": {"x": site.x, "y": site.y}}
    asked = _post(client, _url(island, "/items"), demolish)
    assert asked.status_code == 428 and "取り壊します" in asked.json()["confirm"][0]["notes"][0]
    assert island.orders.count() == 2
    assert _post(client, _url(island, "/items"), {**demolish, "confirm": True}).status_code == 200

    limit = get_game().config.get("orders.max_queue")
    Order.objects.bulk_create(
        [Order(island=island, seq=10 + i, kind="rename_island") for i in range(limit)]
    )
    assert _post(client, _url(island, "/items"), RENAME).status_code == 400
    _login(client, "other")
    assert _post(client, _url(island, "/items"), RENAME).status_code == 403


def test_remove_and_reorder_one_at_a_time(client, world):
    """消すのは番号で 1 つ。もうなければ 404 と今の列。並べ替えは番号の順を送り、もうない番号は
    飛ばし、送らなかった命令（ほかの画面で足した）は後ろに残す。"""
    island = _own_island(client)
    ids = [
        _post(
            client, _url(island, "/items"), {"kind": "rename_island", "payload": {"name": n}}
        ).json()["added"]
        for n in ("一", "二", "三")
    ]
    res = client.delete(_url(island, f"/items/{ids[0]}"))
    assert res.status_code == 200 and [o["id"] for o in res.json()["orders"]] == ids[1:]
    gone = client.delete(_url(island, f"/items/{ids[0]}"))
    assert gone.status_code == 404 and [o["id"] for o in gone.json()["orders"]] == ids[1:]
    later = _post(client, _url(island, "/items"), RENAME).json()["added"]
    res = _post(client, _url(island, "/sequence"), {"ids": [ids[2], ids[0], ids[1]]})
    assert res.status_code == 200
    assert [o["id"] for o in res.json()["orders"]] == [ids[2], ids[1], later]
    assert _post(client, _url(island, "/sequence"), {"ids": "1,2"}).status_code == 400
    _login(client, "other")
    assert client.delete(_url(island, f"/items/{ids[1]}")).status_code == 403
    assert island.orders.count() == 3


def test_replacing_the_whole_list_refuses_an_old_version(client, world):
    """丸ごとの置き換え（AI の道具が使う PUT）は、読んだ版（version）が今と違えば 409 で断る。
    版を省けば今までどおり置き換える。"""
    island = _own_island(client)
    seen = client.get(_url(island)).json()
    assert seen["version"] and seen["orders"] == []
    _post(client, _url(island, "/items"), RENAME)  # 別の画面で足した
    body = {"orders": [SEA_FARM], "version": seen["version"]}
    stale = client.put(_url(island), body, content_type="application/json")
    assert stale.status_code == 409
    assert stale.json()["version"] == client.get(_url(island)).json()["version"]
    assert [o.kind for o in island.orders.all()] == ["rename_island"]
    body["version"] = stale.json()["version"]
    assert client.put(_url(island), body, content_type="application/json").status_code == 200
    plain = client.put(_url(island), {"orders": [RENAME]}, content_type="application/json")
    assert plain.status_code == 200
    # ほかの国の人には番号と版を見せない
    _login(client, "other")
    other = client.get(_url(island)).json()
    assert "version" not in other and "id" not in other["orders"][0]


def test_bar_on_owner_pages_and_the_orders_page(client, world):
    """帯は持ち主の島の画面と統計の画面の上に出て、命令のページへ飛ぶ。命令のページはほかの人も
    開ける（中身は API が公表する命令だけを返す）。"""
    island = _own_island(client)
    _post(client, _url(island, "/items"), RENAME)
    _post(client, _url(island, "/items"), SEA_FARM)
    page = f"/islands/{island.id}/orders/"
    for url in (f"/islands/{island.id}/", f"/islands/{island.id}/market/", page):
        html = client.get(url).content.decode()
        assert f'class="orders-bar" href="{page}"' in html, url
        assert "data-orders-bar-text>次のターンの命令 2 件（行政力 1／" in html, url
    html = client.get(page).content.decode()
    assert 'id="orders-page"' in html and 'aria-current="page"' in html
    _login(client, "other")
    assert client.get(page).status_code == 200
    assert "orders-bar" not in client.get(f"/islands/{island.id}/market/").content.decode()


def test_bar_text_matches_the_screen():
    """帯の文はサーバーと画面（orders.js の queueSummary）で同じ数え方。"""
    text = "次のターンの命令 2 件（行政力 4／2）・1 件は次のターン以降に回ります"
    assert bar_text([3, 1], 2) == text
    assert bar_text([], 5) == "次のターンの命令 0 件（行政力 0／5）"


def test_mcp_add_and_remove_one_order(played):
    """AI も 1 件ずつ足す・消す: add_order は added（番号）を返し、remove_order は summary の
    orders.pending の id で消す。確かめの要る命令は saved: false。"""
    browser, ai, own, _ = played
    Order.objects.filter(island=own).delete()
    added = json.loads(_tool(ai, "add_order", **RENAME))
    assert added["orders"][0]["id"] == added["added"]
    browser_added = browser.post(
        _url(own, "/items"), SEA_FARM, content_type="application/json"
    ).json()["added"]
    summary = json.loads(_tool(ai, "summary"))
    assert [o["id"] for o in summary["orders"]["pending"]] == [added["added"], browser_added]
    site = own.facilities.exclude(kind="housing").first()
    asked = json.loads(_tool(ai, "add_order", kind="demolish", payload={"x": site.x, "y": site.y}))
    assert asked["saved"] is False and "confirm: true" in asked["next"]
    left = json.loads(_tool(ai, "remove_order", id=added["added"]))
    assert [o["id"] for o in left["orders"]] == [browser_added]
    text, is_error = _call(ai, "remove_order", id=added["added"])
    assert is_error and "もうありません" in text
