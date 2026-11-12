"""決まった売り買いの画面・JSON・MCP の単位、同等性と持ち主の境界。"""

from __future__ import annotations

import json

import pytest

from server.game import get_game
from server.orders.models import Order
from server.turns.models import TurnLog
from server.web import budget_guide, display
from server.world import models as m
from tests.server.test_ai_parity import _tool
from tests.server.test_ai_parity import played as played  # noqa: F401
from tests.server.test_phase1 import _own_island

pytestmark = pytest.mark.django_db


def setting(island, kind="reserve"):
    island.economy.setdefault("ext", {})["warehouse_standing"] = {
        "grain": {
            "kind": kind,
            "quantity": 200000,
            "limit": 10000,
            "release": 20000,
            "expires": island.world.turn + 5,
            "last": {
                "turn": island.world.turn,
                "offered": 1000,
                "filled": 500,
                "spent": 5000,
                "reason": "budget",
            },
        }
    }
    island.save(update_fields=["economy"])


def test_standing_market_orders_summary_changes_and_mcp_match(played):
    browser, ai, own, other = played
    setting(own)
    shown = display.warehouse_standing(own)
    result = json.loads(_tool(ai, "summary"))
    assert result["warehouse_standing"] == shown
    assert "warehouse_standing" not in json.loads(_tool(ai, "summary", island=other.id))
    assert json.loads(_tool(ai, "changes", since=0))["warehouse_standing"] == shown
    market = browser.get(f"/api/islands/{own.id}/market").json()
    assert json.loads(_tool(ai, "report", page="market")) == market
    grain = next(r for r in market["rows"] if r["good"] == "grain")
    assert grain["warehouse_standing"] == shown["grain"]
    detail = json.loads(_tool(ai, "report", page="market", good="grain"))
    assert detail["warehouse_standing"] == shown["grain"]
    orders = json.loads(_tool(ai, "report", page="orders"))
    assert orders["policy"]["warehouse_standing"] == shown
    html = browser.get(f"/islands/{own.id}/market/").content.decode()
    assert "決まった売り買い" in html and "出した量" in html
    assert f'href="/islands/{own.id}/market/grain/#actions"' in html


def test_standing_is_private_and_pending_buys_are_secret(client, world):
    island = _own_island(client)
    setting(island)
    for seq, kind in enumerate(("buy", "sell", "reserve")):
        Order.objects.create(
            island=island,
            seq=seq,
            kind="set_warehouse_standing",
            payload={
                "good": "grain",
                "kind": kind,
                "quantity": 100,
                "limit": 10000,
                "release": 20000,
            },
        )
    TurnLog.objects.create(island=island, turn=1, kind="standing_no_budget", args={"good": "grain"})
    client.logout()
    orders = client.get(f"/api/islands/{island.id}/orders").json()
    assert "warehouse_standing" not in orders["policy"]
    assert [r["payload"]["kind"] for r in orders["orders"]] == ["sell", "reserve"]
    market = client.get(f"/api/islands/{island.id}/market").json()
    assert all("warehouse_standing" not in row for row in market["rows"])
    visible = display.visible_logs(TurnLog.objects.filter(island=island), False)
    assert not visible.filter(kind="standing_no_budget").exists()
    assert visible.filter(kind="island_founded").exists()
    fiscal = client.get(f"/api/islands/{island.id}/fiscal").json()
    assert not any("standing_year" in row for row in fiscal["fiscal"]["budget"])


def test_standing_unit_roundtrip_budget_forecast_and_ai_orders(played):
    browser, ai, own, _ = played
    setting(own, "buy")
    payload = {
        "good": "grain",
        "kind": "reserve",
        "quantity": 0.125,
        "limit": 1000000.52,
        "release": 2000000.52,
        "turns": 3,
    }
    raw = display.order_payload("set_warehouse_standing", payload, to_game=True)
    assert raw["quantity"] != payload["quantity"]
    assert display.order_payload("set_warehouse_standing", raw, to_game=False) == payload
    check = json.loads(_tool(ai, "check_order", kind="set_warehouse_standing", payload=payload))
    assert check["ok"] and "備蓄する" in check["summary"]
    order = {"kind": "set_warehouse_standing", "payload": payload}
    # 相場から離れた指値なので確かめが出る（UI-75）。確かめた印を付けて出し直す
    asked = json.loads(_tool(ai, "set_orders", orders=[order]))
    assert asked["saved"] is False and asked["confirm"][0]["index"] == 0
    _tool(ai, "set_orders", orders=[order], confirm=True)
    assert browser.get(f"/api/islands/{own.id}/orders").json()["orders"][0]["payload"] == payload
    guide = budget_guide.guide(own)["warehouse"]
    assert guide["standing_year"] == 200 * 10000 * 12
    assert "food_refill_year" in guide
    setting(own)
    m.Stock.objects.filter(island=own, facility__isnull=True, good="grain").delete()
    m.Stock.objects.create(island=own, good="grain", quantity=100000, made_turn=0)
    assert budget_guide.guide(own)["warehouse"]["standing_year"] == 100 * 10000 * 12
    assert get_game().defs.orders["set_warehouse_standing"]["admin_cost"] == 1
