"""命令の打ち間違いの確かめ（UI-75・W-264）: 画面・JSON API・MCP の命令の足し方で同じ確かめ。"""

from __future__ import annotations

import json

import pytest
from django.db.models import Max

from hakoniwa_core.units import BASIS
from server.economy.models import MarketRecord
from server.orders.models import Order
from server.web import order_confirm
from server.world import models as m
from tests.server.test_ai_parity import _tool
from tests.server.test_ai_parity import played as played  # noqa: F401
from tests.server.test_phase1 import _own_island

pytestmark = pytest.mark.django_db

GOOD = "coal"  # 見せる量は 1 単位の 25 倍（トン）、見せる値段は 1 単位の値段の 25 分の 1
SCALE = 25


def _market(island, kind="trade", price=2500, volume=8000, history=()):
    """直近のターンの石炭の市場の記録（値段は計算の単位。2500 は 1 トン 100）。history は前の
    ターンの (ターン, 値段, 量)。"""
    last = MarketRecord.objects.filter(island=island).aggregate(t=Max("turn"))["t"] or 0
    turn = last + 10
    for back, p, v in history:
        MarketRecord.objects.create(
            island=island, turn=turn - back, good=GOOD, price=p, volume=v, demand=v, supply=v
        )
    MarketRecord.objects.create(
        island=island,
        turn=turn,
        good=GOOD,
        price=price,
        volume=volume if kind == "trade" else 0,
        demand=volume,
        supply=volume,
        kind=kind,
    )
    return turn


def _production(island, turn, units):
    m.StatRecord.objects.create(island=island, turn=turn, name=f"production.{GOOD}", value=units)


def _put(client, island, orders, confirm=False):
    body = {"orders": [{"kind": k, "payload": p} for k, p in orders]}
    if confirm:
        body["confirm"] = True
    return client.put(f"/api/islands/{island.id}/orders", body, content_type="application/json")


def _buy(limit, quantity=10):
    return ("warehouse_trade", {"good": GOOD, "side": "buy", "quantity": quantity, "limit": limit})


def test_price_rule_uses_kind_of_market_price_and_confirm_saves(client, world):
    """買う指値が相場の 2 倍以上なら並べずに 428 と確かめの文を返し、confirm: true で並べる。
    値段の種類（約定・気配・持ち越し）を文に添え、未観測の財は値段を確かめない。"""
    island = _own_island(client)
    res = _put(client, island, [_buy(10_000)])
    assert res.status_code == 200, res.json()  # 石炭の記録がない（未観測）なら確かめない
    Order.objects.all().delete()
    _market(island, "trade")
    assert _put(client, island, [_buy(199)]).status_code == 200  # 2 倍に届かない
    res = _put(client, island, [_buy(199), _buy(200)])
    assert res.status_code == order_confirm.CONFIRM_STATUS == 428
    data = res.json()
    assert "confirm: true" in data["error"]
    [ask] = data["confirm"]
    assert ask["index"] == 1 and ask["kind"] == "warehouse_trade"
    assert "石炭" in ask["text"]
    assert "相場 ¤100（直近のターンに約定した値段）の 2 倍以上" in ask["notes"][0]
    assert Order.objects.count() == 1  # 確かめが要るときは並べない
    res = _put(client, island, [_buy(199), _buy(200)], confirm=True)
    assert res.status_code == 200 and len(res.json()["orders"]) == 2

    Order.objects.all().delete()
    _market(island, "quote", price=2500, volume=10)
    note = _put(client, island, [_buy(300)]).json()["confirm"][0]["notes"][0]
    assert "気配" in note and "約定していない" in note
    # 持ち越しは最後に約定した値段と比べる（記録の値段は前の値段のままで、当てにならない）
    _market(island, "carry", price=99_999, history=[(3, 2500, 5000)])
    note = _put(client, island, [_buy(200)]).json()["confirm"][0]["notes"][0]
    assert "持ち越し" in note and "最後に約定した" in note and "¤100" in note
    # 一度も約定していない持ち越しは、未観測と同じに値段を確かめない
    MarketRecord.objects.filter(island=island, good=GOOD, volume__gt=0).delete()
    _market(island, "carry", price=2500)
    assert _put(client, island, [_buy(10_000)]).status_code == 200


def test_sell_price_quantity_and_standing_rules(client, world):
    """売る指値は相場の半分以下、買う量は 1 ターンの生産と売買の量の多いほうより多いとき、売る量は
    倉庫の在庫より多いとき。毎ターン続く決まった売り買いは、そのことを文に添える。"""
    island = _own_island(client)
    turn = _market(island, "trade", volume=8000)  # 売買 8 単位 = 200 トン
    _production(island, turn, 10)  # 生産 10 単位 = 250 トン
    m.Stock.objects.filter(island=island, facility__isnull=True, good=GOOD).delete()
    m.Stock.objects.create(island=island, good=GOOD, quantity=4000)  # 4 単位 = 100 トン

    def notes(kind, payload):
        res = _put(client, island, [(kind, payload)])
        Order.objects.all().delete()
        return res.json()["confirm"][0]["notes"] if res.status_code == 428 else []

    sell = {"good": GOOD, "side": "sell", "quantity": 100}
    assert notes("warehouse_trade", {**sell, "limit": 51}) == []
    assert "半分以下" in notes("warehouse_trade", {**sell, "limit": 50})[0]
    assert notes("warehouse_trade", {**sell, "quantity": 101})[0].startswith(
        "石炭の売る量 101 トン は、国の倉庫の今の在庫 100 トン より多い"
    )
    buy = {"good": GOOD, "side": "buy", "limit": 1}
    assert notes("warehouse_trade", {**buy, "quantity": 250}) == []
    [qty] = notes("warehouse_trade", {**buy, "quantity": 251})
    assert "1 ターンの生産 250 トン・市場の 1 ターンの売買の量 200 トン より多い" in qty

    standing = {"good": GOOD, "kind": "buy", "quantity": 300, "limit": 100}
    found = notes("set_warehouse_standing", standing)
    assert "1 ターンに買う量" in found[0] and found[-1].endswith("止めるまで毎ターン続きます。")
    found = notes("set_warehouse_standing", {**standing, "turns": 4})
    assert found[-1].endswith("4 ターンのあいだ毎ターン続きます。")
    reserve = {"good": GOOD, "kind": "reserve", "quantity": 9999, "limit": 200, "release": 40}
    found = notes("set_warehouse_standing", reserve)
    assert "買い入れの上限の値段" in found[0] and "放出の値段" in found[1]
    assert notes("set_warehouse_standing", {**reserve, "limit": 150, "release": 60}) == []


def test_contract_price_is_compared_in_own_currency(client, world):
    """契約の値段は契約の通貨で書くので、自国通貨に直してから自国の相場と比べる。"""
    island = _own_island(client)
    partner = m.Island.objects.exclude(pk=island.id).order_by("id").first()
    _market(island, "trade")
    ext = island.ext
    ext.setdefault("trade", {})["rates"] = {str(partner.id): 4 * BASIS}  # 相手の 1 が自国の 4
    m.Island.objects.filter(pk=island.id).update(ext=ext)
    contract = {"partner": partner.id, "side": "sell", "good": GOOD, "quantity": 1, "turns": 6}
    # 相手の通貨で 1 トン 30 は、自国通貨で 120。数だけ見れば相場 100 の半分以下だが、確かめない
    foreign = {**contract, "price": 30, "currency": partner.id}
    assert _put(client, island, [("propose_contract", foreign)]).status_code == 200
    Order.objects.all().delete()
    res = _put(client, island, [("propose_contract", {**foreign, "price": 12})])
    assert res.status_code == 428
    note, last = res.json()["confirm"][0]["notes"]
    assert "自国通貨で約 ¤48" in note and "半分以下" in note
    assert last == "この契約は、6 ターンのあいだ毎ターン続きます。"
    # 自国通貨の契約は、そのまま比べる（買いは 2 倍以上）
    own = {**contract, "side": "buy", "price": 200}
    assert (
        "2 倍以上"
        in _put(client, island, [("propose_contract", own)]).json()["confirm"][0]["notes"][0]
    )
    # 条約の契約の条項も同じ（通貨を省くと売る国の通貨）
    clause = {"type": "contract", "seller": partner.id, "good": GOOD, "quantity": 1}
    treaty = {"partner": partner.id, "clauses": [{**clause, "price": 60, "turns": 6}]}
    res = _put(client, island, [("propose_treaty", treaty)])
    assert "2 倍以上" in res.json()["confirm"][0]["notes"][0]  # 相手の 60 は自国の 240


def test_reorder_does_not_ask_again_and_irreversible_orders_ask(client, world):
    """並べ替えと消すだけなら聞き直さない。取り壊しなど戻せない命令は値段がなくても聞く。"""
    island = _own_island(client)
    site = island.facilities.exclude(kind="housing").first()
    demolish = ("demolish", {"x": site.x, "y": site.y})
    rename = ("rename_island", {"name": "名前"})
    res = _put(client, island, [rename, demolish])
    assert res.status_code == 428
    [ask] = res.json()["confirm"]
    assert ask["index"] == 1 and "取り壊します" in ask["notes"][0]
    assert "戻せません" in ask["notes"][0]
    assert _put(client, island, [rename, demolish], confirm=True).status_code == 200
    assert _put(client, island, [demolish, rename]).status_code == 200  # 並べ替え
    assert _put(client, island, [demolish]).status_code == 200  # 消す
    # 同じ命令をもう 1 つ足すときは聞く
    assert _put(client, island, [demolish, demolish]).status_code == 428
    # 確かめの API も同じ文を先に返す
    check = client.post(
        f"/api/islands/{island.id}/orders/check",
        {"kind": "demolish", "payload": demolish[1]},
        content_type="application/json",
    ).json()
    assert check["confirm"] == ask["notes"]
    war = order_confirm.confirmations(
        order_confirm.Context(island, None), "declare_war", {"target": 999}
    )
    assert war and "次のターンから戦争" in war[0]
    assert order_confirm.confirmations(None, "fire_missile", {"nuclear": False}) == []


def test_mcp_set_orders_returns_confirm_and_saves_with_confirm(played):
    """AI も同じ確かめを受ける: set_orders は並べずに saved: false と確かめの文を返し、confirm=true
    で出し直すと並べる。check_order も確かめの文を返す。"""
    browser, ai, own, _ = played
    site = own.facilities.exclude(kind="housing").first()
    order = {"kind": "demolish", "payload": {"x": site.x, "y": site.y}}
    check = json.loads(_tool(ai, "check_order", **order))
    assert "取り壊します" in check["confirm"][0]
    asked = json.loads(_tool(ai, "set_orders", orders=[order]))
    assert asked["saved"] is False and asked["confirm"][0]["index"] == 0
    assert "confirm: true" in asked["next"]
    assert browser.get(f"/api/islands/{own.id}/orders").json()["orders"] == []
    saved = json.loads(_tool(ai, "set_orders", orders=[order], confirm=True))
    assert [o["kind"] for o in saved["orders"]] == ["demolish"]
