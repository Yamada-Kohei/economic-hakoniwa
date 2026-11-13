"""品目のページ（UI-72・UI-73・W-247・W-263）: 1 品目に 1 ページ、上に見る所・下に打てる手の箱。"""

from __future__ import annotations

import pytest

from server.orders.models import Order
from tests.server.test_phase1 import _login, _own_island
from tests.server.test_reports import _market

pytestmark = pytest.mark.django_db


def test_every_good_has_a_page_and_unknown_goods_are_404(client, world):
    island = _own_island(client)
    _market(island)
    page = client.get(f"/islands/{island.id}/market/food/")
    assert page.status_code == 200
    html = page.content.decode()
    assert 'id="good-now"' in html and 'id="market-detail"' in html
    assert f'data-url="/api/islands/{island.id}/market/food"' in html
    # 記録の無い品目もページを持ち、無い欄は出さない
    empty = client.get(f"/islands/{island.id}/market/uranium_ore/").content.decode()
    assert "市場の記録・生産・在庫がありません" in empty
    assert client.get(f"/islands/{island.id}/market/no_such_good/").status_code == 404


def test_the_action_box_shows_orders_for_this_good_only(client, world):
    island = _own_island(client)
    _market(island)
    for seq, good in enumerate(("food", "grain")):
        Order.objects.create(
            island=island,
            seq=seq,
            kind="warehouse_trade",
            payload={"good": good, "side": "buy", "quantity": 1000, "price": 100},
        )
    Order.objects.create(
        island=island,
        seq=5,
        kind="warehouse_trade",
        payload={"good": "food", "side": "sell", "quantity": 1000, "price": 100},
        status="failed",
        executed_turn=3,
        reason="reason_unknown_order",
    )
    page = client.get(f"/islands/{island.id}/market/food/")
    orders = page.context["item"]["orders"]
    assert len(orders["pending"]) == 1 and "食品" in orders["pending"][0]
    assert [o["status"] for o in orders["recent"]] == ["failed"]
    assert orders["recent_turn"] == 3
    html = page.content.decode()
    assert 'class="panel action-box" id="actions"' in html
    assert "前のターンの結果" in html


def test_others_see_the_page_without_the_action_box_or_trades(client, world):
    island = _own_island(client)
    _market(island)
    _login(client, "other")
    page = client.get(f"/islands/{island.id}/market/food/")
    assert page.status_code == 200
    html = page.content.decode()
    assert 'id="actions"' not in html and 'data-slot="trades"' not in html
    assert "orders" not in page.context["item"]


def test_the_market_list_links_each_good_and_lists_unsettled_prices_apart(client, world):
    """W-263: 値段の種類（気配・持ち越し・未観測）は表の中から外し、表の下の別の欄に出す。"""
    island = _own_island(client)
    _market(island)
    page = client.get(f"/islands/{island.id}/market/")
    html = page.content.decode()
    assert f'href="/islands/{island.id}/market/food/"' in html
    assert 'id="market-detail"' not in html  # 推移は品目のページへ
    kinds = {r["good"]: r["price_kind"] for r in page.context["rows"]}
    table, below = html.split('id="unsettled"')
    listed = {g for g in kinds if f'<li data-good="{g}">' in below}
    assert listed == {g for g, k in kinds.items() if k != "trade"}
    assert "最後の約定" not in table


def test_links_from_chain_plan_and_trade_go_to_the_good_page(client, world):
    island = _own_island(client)
    _market(island)
    chain = client.get(f"/islands/{island.id}/chain/").content.decode()
    assert f'/islands/{island.id}/market/food/"' in chain
    assert "?good=" not in chain


def test_buyer_and_seller_groups_show_who_could_not_buy(client, world):
    """市場の記録の買い手と売り手の別（W-72・W-258）を、品目のページの持ち主の欄と市場の API に
    出す。買えなかった量は注文した量から買えた量を引いた分。ほかの国には出さない。"""
    from server.economy.models import MarketRecord

    island = _own_island(client)
    _market(island)
    MarketRecord.objects.filter(island=island, turn=4).update(
        buyers={"household": [2000, 1500], "state": [1000, 500]},
        sellers={"private": [2000, 2000]},
    )
    data = client.get(f"/api/islands/{island.id}/market/food").json()
    rows = {r["group"]: r for r in data["groups"]}
    assert [r["group"] for r in data["groups"]] == ["state", "private", "household"]
    assert rows["state"]["name"] == "国営の施設"
    assert rows["state"]["unfilled"] == rows["state"]["buy"] - rows["state"]["bought"] > 0
    assert rows["private"]["sell"] == rows["private"]["sold"] > 0
    assert rows["private"]["buy"] == 0
    html = client.get(f"/islands/{island.id}/market/food/").content.decode()
    assert 'data-slot="groups"' in html
    _login(client, "other")
    assert "groups" not in client.get(f"/api/islands/{island.id}/market/food").json()
    html = client.get(f"/islands/{island.id}/market/food/").content.decode()
    assert 'data-slot="groups"' not in html
