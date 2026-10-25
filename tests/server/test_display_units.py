"""量の単位を実物に近づける表示（バランス調整の締め、W-30）。

計算は縮めた量のままで、画面・API・MCP・ログで見せるときだけ goods.yaml の display_scale を
掛ける（値段は割る）。打ち込まれた量と値段は、命令の API の入り口で計算の単位に戻す。
"""

from __future__ import annotations

import math

import pytest

from hakoniwa_core.military import army
from server.game import get_game
from server.turns.services import run_turn
from server.web import display
from server.world import models as m
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


# --- 条約の中・備蓄・積み込みの単位（W-182） ---


# 量か値段（と値段の指値）の項目の名前。命令の中身にこの名前の項目があれば、見せる単位で受け取る
_UNIT_KEYS = {"quantity", "price", "limit", "fixed_price", "reserve"}
# 財の項目を持つが、量も値段も持たない（率・封鎖・財の一覧だけ）命令
_GOOD_ONLY_ORDERS = {"set_tariff", "set_subsidy", "set_embargo", "review_official_prices"}


def _walk(schema, path=""):
    """命令の中身の形（JSON Schema）の、入れ子も含む項目を (パス, 名前) で挙げる。"""
    if not isinstance(schema, dict):
        return
    for name, sub in (schema.get("properties") or {}).items():
        yield f"{path}{name}", name
        yield from _walk(sub, f"{path}{name}.")
    if "items" in schema:
        yield from _walk(schema["items"], f"{path}[].")


def test_every_order_with_a_quantity_or_a_price_is_in_the_unit_list():
    defs = get_game().defs
    missing = []
    for kind, order in defs.orders.items():
        names = {name for _, name in _walk(order["payload"])}
        has_goods = bool(names & {"good", "fixed_good"})
        if not (names & _UNIT_KEYS or has_goods):
            continue
        if kind in _GOOD_ONLY_ORDERS:
            assert not names & _UNIT_KEYS, f"{kind} は量か値段を持つので、一覧に載せる"
            continue
        if not display.order_has_units(kind):
            missing.append(kind)
    assert missing == [], (
        f"見せる単位で受け取る命令の一覧（display.ORDER_UNITS ほか）にない: {missing}"
    )


def test_treaty_contract_quantity_and_price_are_taken_in_shown_units():
    shown = {
        "partner": 2,
        "clauses": [
            {"type": "peace"},
            {
                "type": "contract",
                "seller": 1,
                "good": "grain",
                "quantity": 500,
                "price": 10.5,
                "turns": 12,
            },
            {"type": "aid", "payer": 1, "amount": 5000},
        ],
    }
    game = display.order_payload("propose_treaty", shown, to_game=True)
    peace, contract, aid = game["clauses"]
    assert contract["quantity"] == 20.0 and contract["price"] == 262  # 10.5 × 25 を整数に丸める
    assert peace == {"type": "peace"} and aid == shown["clauses"][2]  # お金の援助は直さない
    back = display.order_payload("propose_treaty", game, to_game=False)
    assert back["clauses"][1]["quantity"] == 500 and back["clauses"][1]["price"] == 10.48
    # 1 つずつ数える財と、形の違う中身はそのまま
    cars = {"type": "contract", "seller": 1, "good": "automobile", "quantity": 3, "price": 9}
    only_cars = display.order_payload("propose_treaty", {"clauses": [cars]}, to_game=True)
    assert only_cars["clauses"] == [cars]
    assert display.order_payload("propose_treaty", {"clauses": "x"}, to_game=True) == {
        "clauses": "x"
    }


def test_reserve_and_supply_loading_are_taken_in_shown_units():
    game = display.order_payload(
        "set_defense", {"annual": 100, "reserve": {"food": 100, "ammunition": 25}}, to_game=True
    )
    assert game == {"annual": 100, "reserve": {"food": 4.0, "ammunition": 1.0}}
    assert display.order_payload("set_defense", game, to_game=False)["reserve"] == {
        "food": 100,
        "ammunition": 25,
    }
    load = {"fleet": 1, "good": "fuel", "quantity": 50}
    assert display.order_payload("load_supplies", load, to_game=True)["quantity"] == 2.0


def test_treaty_clause_summary_shows_shown_units():
    clause = {
        "type": "contract",
        "seller": 1,
        "good": "grain",
        "quantity": 20.0,
        "price": 262,
        "turns": 12,
    }
    text = display.clause_summary(clause)
    assert "500 トン" in text and "1トン 10.48" in text
    summary = display.describe_order("propose_treaty", {"partner": 2, "clauses": [clause]})
    assert "500 トン" in summary
    assert display.shown_clause(clause)["quantity"] == 500


def test_treaty_proposal_through_the_api_keeps_the_quantity_the_player_typed(client, world):
    island = _own_island(client)
    other = m.Island.objects.exclude(pk=island.pk).order_by("id").first()
    clause = {
        "type": "contract",
        "seller": island.id,
        "good": "grain",
        "quantity": 500,
        "price": 10,
        "turns": 12,
    }
    payload = {"partner": other.id, "clauses": [clause]}
    url = f"/api/islands/{island.id}/orders"
    res = client.put(
        url, {"orders": [{"kind": "propose_treaty", "payload": payload}]}, "application/json"
    )
    assert res.status_code == 200, res.content.decode()
    stored = island.orders.get(status="pending").payload
    assert stored["clauses"][0]["quantity"] == 20.0 and stored["clauses"][0]["price"] == 250
    shown = client.get(url).json()["orders"][0]
    assert shown["payload"]["clauses"][0]["quantity"] == 500
    assert "500 トン" in shown["text"]
    orders = client.get("/api/defs/orders").json()["orders"]
    price = orders["propose_treaty"]["payload"]["properties"]["clauses"]["items"]["properties"][
        "price"
    ]
    assert price["type"] == "number" and "units" in orders["propose_treaty"]
    assert "units" in orders["set_defense"] and "units" in orders["load_supplies"]


def test_military_api_shows_reserve_cargo_and_capacity_in_shown_units(client, world):
    island = _own_island(client)
    url = f"/api/islands/{island.id}/orders"
    body = {"orders": [{"kind": "set_defense", "payload": {"reserve": {"food": 100}}}]}
    assert client.put(url, body, content_type="application/json").status_code == 200
    assert island.orders.get(status="pending").payload["reserve"] == {"food": 4.0}
    run_turn(force=True)
    w = island.world
    w.refresh_from_db()
    w.ext = {
        army.KEY: {
            "seq": 1,
            "units": [
                {"id": 1, "owner": island.id, "kind": "transport", "x": 0, "y": 0, "ships": 2}
                | {
                    "soldiers": 0,
                    "equipment": {},
                    "status": "ready",
                    "order": None,
                    "arm": "sea",
                    "cargo": [],
                    "goods": {"food": 4000},
                },
            ],
        }
    }
    w.save()
    mine = client.get(f"/api/islands/{island.id}/military").json()["military"]
    assert mine["reserve"]["food"] == 100
    fleet = mine["fleets"][0]
    assert fleet["goods"] == {"food": 100} and fleet["capacity"] == 2 * 40 * 25


def test_an_order_refused_in_a_later_stage_shows_as_failed_with_the_reason(client, world):
    island = _own_island(client)
    url = f"/api/islands/{island.id}/orders"
    body = {"orders": [{"kind": "denounce_treaty", "payload": {"treaty": 999}}]}
    assert client.put(url, body, content_type="application/json").status_code == 200
    run_turn(force=True)
    saved = island.orders.get()
    assert saved.status == "failed" and saved.reason == "reason_treaty_unknown"
    results = client.get(f"/api/islands/{island.id}/summary").json()["orders"]["last_results"]
    assert [(r["status"], r["reason"]) for r in results] == [
        ("failed", display.message("reason_treaty_unknown"))
    ]
