"""為替のタブの頭の融通の残りと介入の欄の目安（UI-80）、為替の手引き（W-252。UI-91）、軽く動かすと
戻らない命令の注意（W-255。UI-92）。"""

from __future__ import annotations

import pytest

from server.web import display
from server.world import models as m
from tests.server.test_trade import _trading_world

pytestmark = pytest.mark.django_db


def test_fx_api_has_market_amounts_and_swap_room(client, world):
    own, second, _ = _trading_world(client)
    # 板寄せの後に、中央銀行の注文を除いた売りと買いの量が統計に残る（0 は書かず、記録したターン
    # には fx.book が 1）
    names = set(own.stats.values_list("name", flat=True))
    assert "fx.book" in names

    data = client.get(f"/api/islands/{own.id}/fx").json()
    [rate] = data["rates"]
    assert rate["offered"] is not None and rate["wanted"] is not None
    swaps = data["swaps"]
    assert {"limit", "lent_total", "borrowed_change", "lent_change"} <= set(swaps)
    if swaps["room"] is not None:
        assert swaps["limit"] == swaps["borrowed"] + swaps["room"]
    assert swaps["borrowed_change"] is not None  # 前のターンの記録がある
    [currency] = data["policy"]["currencies"]
    assert currency["id"] == second.id
    assert currency["offered"] == rate["offered"] and currency["wanted"] == rate["wanted"]
    if swaps["room"] is not None:
        expected = swaps["room"] * 10000 // round(rate["rate"] * 10000)
        assert abs(currency["swap_room"] - expected) <= 1

    client.logout()
    data = client.get(f"/api/islands/{own.id}/fx").json()
    assert "offered" in data["rates"][0] and "swaps" not in data


def test_fx_page_shows_swap_room_and_checks_empty_markets(client, world):
    own, second, _ = _trading_world(client)
    html = client.get(f"/islands/{own.id}/fx/").content.decode()
    assert 'id="swap-room"' in html and "借りられる限度額" in html
    assert html.index('id="swap-room"') < html.index('id="rates"')  # タブの頭
    assert "data-market-check" in html and f'value="{second.id}" data-offered=' in html
    assert "前のターンに売りに出ていた量" in html
    assert display.ORDER_CAUTIONS["set_fx_intervention"] in html
    assert 'href="/help/fx/"' in html
    # 金融のページからも融通の残りへ
    html = client.get(f"/islands/{own.id}/finance/").content.decode()
    assert f'href="/islands/{own.id}/fx/#swap-room"' in html
    client.logout()
    html = client.get(f"/islands/{own.id}/fx/").content.decode()
    assert 'id="swap-room"' not in html


def test_fx_guide_page_points_at_the_new_parts(client, world):
    """為替の手引き（/help/fx/。中身は tests/server/test_help_fx.py）が、為替の画面の頭の融通の残り
    と介入の欄の売り買いの量を指す。為替の画面の「この画面の手引き」もこのページ。"""
    html = client.get("/help/fx/").content.decode()
    assert "中央銀行どうしの融通の残り" in html and "前のターンに売りに出ていた量" in html
    island = m.Island.objects.order_by("id").first()
    assert 'href="/help/fx/"' in client.get(f"/islands/{island.id}/fx/").content.decode()


def test_careful_orders_in_order_defs_and_guide(client, world):
    # 手引きの「軽く動かさないほうがいい数値」は入門にあり、詳しい仕組みの目次からも開ける
    assert 'id="careful"' in client.get("/help/start/").content.decode()
    assert 'href="/help/start/#careful"' in client.get("/help/").content.decode()
    defs = client.get("/api/defs").json()["orders"]
    assert defs["set_labor_policy"]["caution"] == display.ORDER_CAUTIONS["set_labor_policy"]
    assert "caution" not in defs["build"]
    order_defs = client.get("/api/defs/orders").json()["orders"]
    assert "一度上がると下がらない" in order_defs["set_labor_policy"]["caution"]
