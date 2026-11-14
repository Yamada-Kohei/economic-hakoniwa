"""入札（W-268）の画面と API: 受付中の入札は誰にでも、札は自分の札だけ見え、落とした国と値段は
誰にでも見える。命令の API で出せ、命令の説明と手引きがある。"""

from __future__ import annotations

import pytest

from server.orders.models import Order
from server.turns.services import run_turn
from server.web import display
from server.web import trade as trade_views
from server.world import models as m
from tests.server.test_phase1 import _own_island

pytestmark = pytest.mark.django_db


def _tender_world(client) -> tuple[m.Island, m.Island, m.Island, int]:
    """持ち主の島が石炭の入札を命令の API で出し、ほかの2島が札を入れた世界（締め切りの前）。"""
    own = _own_island(client)
    second, third = m.Island.objects.exclude(pk=own.pk).order_by("id")
    run_turn(force=True)  # 移し替えの段が港・海運業・商社を置く
    tender = {"good": "coal", "quantity": 2, "turns": 6, "cap": 40, "deadline": 2}
    body = {"orders": [{"kind": "open_tender", "payload": tender}]}
    res = client.put(f"/api/islands/{own.id}/orders", body, content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    run_turn(force=True)
    data = client.get(f"/api/islands/{own.id}/trade").json()
    [row] = data["tenders"]["open"]
    assert row["mine"] and row["cap"] == 40 and row["quantity"] == 2.0
    tender_id = row["id"]
    # 値段は見せる単位で書く（石炭の上限 40 は、計算の単位では 1000。札の下限は 7 割の 28）
    for island, price in ((second, 34), (third, 30)):
        payload = display.order_payload(
            "bid_tender", {"tender": tender_id, "good": "coal", "price": price}, to_game=True
        )
        Order.objects.create(island=island, seq=1, kind="bid_tender", payload=payload)
    run_turn(force=True)
    for island in (own, second, third):
        island.refresh_from_db()
    return own, second, third, tender_id


def test_bids_are_hidden_and_the_winner_is_shown_to_everyone(client, world):
    own, second, third, tender_id = _tender_world(client)
    mine = client.get(f"/api/islands/{own.id}/trade").json()["tenders"]
    [row] = mine["open"]
    assert row["id"] == tender_id and row["my_bid"] is None and row["can_withdraw"]
    assert "bids" not in row  # 出した国にも札は見せない
    theirs = trade_views.trade_data(second, True, "")["tenders"]
    [row] = theirs["open"]
    assert row["my_bid"] == 34 and row["can_bid"] and not row["mine"]
    assert row["delivery_name"] == ""  # 届いた物の行き先は出した国だけに見せる
    # 締め切りのターンに、いちばん安い札の国と結ぶ
    run_turn(force=True)
    mine = client.get(f"/api/islands/{own.id}/trade").json()
    [result] = mine["tenders"]["results"]
    assert result["winner"] == third.id and result["price"] == 30
    contract = next(c for c in mine["contracts"] if c["tender"] == tender_id)
    assert contract["partner"] == third.id and contract["side"] == "buy"
    second = m.Island.objects.get(pk=second.pk)  # 世界の状態を読み直す
    other = trade_views.trade_data(second, True, "")["tenders"]["results"]
    assert other[0]["winner_name"] == result["winner_name"]
    html = client.get(f"/islands/{own.id}/trade/contracts/").content.decode()
    assert 'id="tenders"' in html and 'data-order="open_tender"' in html
    assert f"入札 {tender_id}番で結んだ" in html


def test_describe_tender_orders(world):
    assert display.describe_order("cancel_tender", {"tender": 3}) == "入札を取り下げる: 入札 3 番"
    text = display.describe_order(
        "settle_tender_default", {"contract": 5, "choice": "forgive"}
    )
    assert "契約 5 番" in text and "許す" in text
    text = display.describe_order(
        "open_tender", {"good": "coal", "quantity": 2, "turns": 6, "cap": 40, "deadline": 2}
    )
    assert "石炭" in text and "上限" in text


def test_help_explains_tenders(client, world):
    html = client.get("/help/").content.decode()
    assert "一般競争入札" in html and "指名停止" in html
