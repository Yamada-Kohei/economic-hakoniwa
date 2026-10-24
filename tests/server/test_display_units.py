"""量の単位を実物に近づける表示（バランス調整の締め、W-30）。

計算は縮めた量のままで、画面・API・MCP・ログで見せるときだけ goods.yaml の display_scale を
掛ける（値段は割る）。打ち込まれた量と値段は、命令の API の入り口で計算の単位に戻す。
"""

from __future__ import annotations

import math

import pytest

from server.game import get_game
from server.web import display
from tests.server.test_phase1 import _own_island

pytestmark = pytest.mark.django_db


def test_weight_goods_are_shown_25_times_and_counted_goods_as_they_are():
    defs = get_game().defs
    for good, spec in defs.goods.items():
        if spec.get("unit") in ("ton", "cubic_meter", "megawatt_hour"):
            assert defs.display_scale(good) == 25, good
    for good in ("automobile", "auto_parts", "transport_ship", "services", "retail"):
        assert defs.display_scale(good) == 1, good
    # 1人1か月の食料 0.002 → 0.05 トン（実物の 50kg）、値段は 25 分の 1
    assert defs.shown_amount("grain", 0.002) == pytest.approx(0.05)
    assert defs.shown_price("grain", 27683) == 1107
    assert defs.shown_price("grain", 250) == 10.0
    assert defs.shown_price("automobile", 46289) == 46289
    assert defs.game_price("grain", 1107.32) == 27683
    assert defs.weight_scale() == 25


def test_order_payload_round_trips_between_shown_and_game_units():
    payload = {"good": "coal", "side": "buy", "quantity": 50, "limit": 1000}
    game = display.order_payload("warehouse_trade", payload, to_game=True)
    assert game == {"good": "coal", "side": "buy", "quantity": 2.0, "limit": 25000}
    assert display.order_payload("warehouse_trade", game, to_game=False) == payload
    # 丸めないので、読んだ列をそのまま送り返しても同じ命令になる
    stored = {"good": "grain", "price": 27683}
    shown = display.order_payload("set_price_floor", stored, to_game=False)
    assert shown == {"good": "grain", "price": 1107.32}
    assert display.order_payload("set_price_floor", shown, to_game=True) == stored
    # 1 つずつ数える財と、量や値段のない命令はそのまま
    cars = {"partner": 2, "side": "buy", "good": "automobile", "quantity": 3, "price": 9}
    assert display.order_payload("propose_contract", cars, to_game=True) == cars
    assert display.order_payload("set_taxes", {"income_tax": 10}, to_game=True) == {
        "income_tax": 10
    }
    fixed = {"bloc": 1, "fixed_good": "coal", "fixed_price": 40}
    assert display.order_payload("bloc_settings", fixed, to_game=True)["fixed_price"] == 1000
    # 値段は小数で書いても計算の単位の整数になる（倍率 1 の財でも）。数でない値は確かめに任せる
    cheap = {"good": "steel", "amount": 0.3}
    assert display.order_payload("set_export_subsidy", cheap, to_game=True)["amount"] == 8
    cars["price"] = 9.5
    assert display.order_payload("propose_contract", cars, to_game=True)["price"] == 10
    odd = {"good": "coal", "price": float("nan")}
    assert math.isnan(display.order_payload("set_price_cap", odd, to_game=True)["price"])


def test_cheap_unit_prices_keep_a_decimal():
    assert display.price_text("grain", 250, "¤") == "¤10"
    assert display.price_text("grain", 260, "¤") == "¤10.4"
    assert display.stat_text("price.grain", 260, "money", "¤") == "¤10.4"
    assert display.stat_text("price.grain", 27683, "money", "¤") == "¤1,107"


def test_orders_api_takes_and_returns_shown_units(client, world):
    island = _own_island(client)
    url = f"/api/islands/{island.id}/orders"
    payload = {"good": "coal", "side": "buy", "quantity": 50, "limit": 1000}
    check = client.post(
        f"{url}/check", {"kind": "warehouse_trade", "payload": payload}, "application/json"
    )
    assert check.status_code == 200
    body = {"orders": [{"kind": "warehouse_trade", "payload": payload}]}
    res = client.put(url, body, content_type="application/json")
    assert res.status_code == 200, res.content.decode()
    stored = island.orders.get(status="pending").payload
    assert stored["quantity"] == 2.0 and stored["limit"] == 25000
    shown = client.get(url).json()["orders"][0]
    assert shown["payload"] == payload
    assert "50 トン" in shown["text"] and "指値 1,000" in shown["text"]


def test_orders_api_rejects_non_finite_numbers_without_crashing(client, world):
    island = _own_island(client)
    body = '{"kind": "set_price_cap", "payload": {"good": "electricity", "price": NaN}}'
    res = client.post(f"/api/islands/{island.id}/orders/check", body, "application/json")
    assert res.status_code == 400  # NaN は JSON にない。入り口で断る（server/jsonbody.py）


def test_order_defs_say_prices_are_shown_units(client, world):
    orders = client.get("/api/defs/orders").json()["orders"]
    cap = orders["set_price_cap"]
    assert cap["payload"]["properties"]["price"]["type"] == "number"
    assert "見せる単位" in cap["units"]
    assert "units" not in orders["set_taxes"]


def test_help_page_shows_real_world_amounts(client, world):
    page = client.get("/help/").content.decode()
    assert "1人が1か月に食べる食料は 0.05 トン" in page
    assert "1隻で1航海に" in page
