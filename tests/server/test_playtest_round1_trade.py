"""試し遊び1回目（ターン 50）の報告のうち、貿易の画面の直し: 契約の外貨をどこからいくら用意できたか
（2026-10-20 の決定「国の契約だけ」）。"""

from __future__ import annotations

import pytest

from server.world import models as m
from tests.server.test_phase1 import _login
from tests.server.test_trade import _trading_world

pytestmark = pytest.mark.django_db


def test_contract_shows_where_the_foreign_currency_came_from(client, world):
    """外貨が足りずに届かなかった契約は、買う国の契約の欄に、手持ち・中央銀行から・為替の市場で用意
    できた額と足りない額を出す（売る国には出さない）。"""
    own, second, _ = _trading_world(client)
    w = m.World.objects.get()
    [c] = w.ext["trade"]["contracts"]
    fx = {
        "turn": w.turn,
        "need": 10_000,
        "had": 0,
        "reserve": 4_000,
        "market": 2_500,
        "short": 3_500,
    }
    c.update(state="active", start=w.turn, end=w.turn + 12, short=1000, fx=fx)
    w.save()
    [row] = client.get(f"/api/islands/{own.id}/trade").json()["contracts"]
    assert row["side"] == "buy" and row["fx"] == fx
    html = client.get(f"/islands/{own.id}/trade/").content.decode()
    assert "の外貨の払い" in html and "中央銀行から" in html
    assert "4,000" in html and "3,500" in html
    assert "中央銀行が持っているその通貨から先に払い" in html  # 契約の欄の説明
    second.owner = _login(client, "other")
    second.save()
    [row] = client.get(f"/api/islands/{second.id}/trade").json()["contracts"]
    assert row["side"] == "sell" and row["fx"] is None
