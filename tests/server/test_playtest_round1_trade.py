"""試し遊び1回目（ターン 50）の報告のうち、貿易の画面の直し: 契約の外貨をどこからいくら用意できたか
（2026-10-20 の決定「国の契約だけ」）と、海外の商人から買った額と輸入の数え方の説明。"""

from __future__ import annotations

import pytest

from server.web import reports
from server.web import trade as trade_views
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


def test_merchant_purchases_and_imports_are_told_apart(client, world):
    """計画経済の国の「海外の商人から買った額」は政府が払った公定価格との差を含み、輸入は含まない
    ことを、統計の名前と画面の説明に書き、差も並べて出す（試し遊び1回目）。"""
    assert "公定価格" in reports.stat_label("trade.imports")[0]
    assert "公定価格との差を含む" in reports.stat_label("trade.merchant")[0]
    for names in (trade_views.TRADE_STATS, trade_views.FX_STATS):
        assert names.index("trade.merchant_gap") == names.index("trade.merchant") + 1
    own, _, _ = _trading_world(client)
    html = client.get(f"/islands/{own.id}/trade/").content.decode()
    assert "海外の商人から買った額が輸入より大きくなることがあります" in html
    assert 'data-names="trade.merchant,trade.merchant_gap"' in html
    html = client.get(f"/islands/{own.id}/fx/").content.decode()
    assert "政府が払った差も含めて数えます" in html
    html = client.get("/help/").content.decode()
    assert "中央銀行が持っているその通貨を今のレートで先に使い" in html
    assert "差は輸入と貿易収支には数えず" in html
