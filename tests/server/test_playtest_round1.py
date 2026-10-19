"""試し遊び1回目（2026-10-20、新しい世界の 50 ターン）で見つかった、API と画面の直し。"""

from __future__ import annotations

from server.game import get_game
from server.web import overview
from tests.server.test_phase1 import _own_island


def test_check_rejects_a_list_of_orders_without_crashing(client, world):
    """命令の配列を確かめに送ると、500 ではなく 400 で「1つずつ」と返す。"""
    island = _own_island(client)
    body = [{"kind": "rename_island", "payload": {"name": "島"}}]
    res = client.post(f"/api/islands/{island.id}/orders/check", body, "application/json")
    assert res.status_code == 400
    assert "1つずつ" in res.content.decode()


def test_check_says_how_the_order_was_read(client, world):
    """確かめが通ったときも、命令の列と同じ説明（率の % など）を返す。"""
    island = _own_island(client)
    payload = {"kind": "production", "good": "grain", "rate": 0.15, "turns": 12}
    res = client.post(
        f"/api/islands/{island.id}/orders/check",
        {"kind": "set_subsidy", "payload": payload},
        "application/json",
    )
    data = res.json()
    assert data["ok"] is True
    assert "0.15%" in data["summary"] and "12 ターン" in data["summary"]


def test_budget_items_are_listed_and_unknown_items_fail(client, world):
    """予算の項目は game.yaml の fiscal.budget_items と同じ名前だけ。違う名前は確かめで落ちる。"""
    island = _own_island(client)
    orders = client.get("/api/defs/orders").json()["orders"]
    names = orders["set_budget"]["payload"]["propertyNames"]["enum"]
    assert names == list(get_game().config.get("fiscal.budget_items"))
    url = f"/api/islands/{island.id}/orders/check"

    def check(payload):
        body = {"kind": "set_budget", "payload": payload}
        return client.post(url, body, "application/json").json()["ok"]

    assert check({"nonexistent": 5}) is False
    assert check({"warehouse": 5}) is True


def test_percent_fields_say_percent_in_order_defs(client, world):
    """率を打ち込む項目は、命令の一覧（AI 用の入り口にも出る）に % と書いてある。"""
    orders = client.get("/api/defs/orders").json()["orders"]
    assert "%" in orders["set_subsidy"]["payload"]["properties"]["rate"]["description"]
    assert "%" in orders["set_taxes"]["payload"]["properties"]["income_tax"]["description"]


def test_town_shops_are_listed_at_their_town(world):
    """町の中の店（施設の行の位置は負）は、工事の一覧に町のマスで出る。"""
    from server.world import models as m

    island = m.Island.objects.order_by("id").first()
    town = island.facilities.filter(kind="housing", x__gte=0).first()
    assert town is not None
    island.facilities.create(
        kind="shop",
        number=999,
        x=-11,
        y=-11,
        owner="state",
        built=False,
        ext={"site": [town.x, town.y]},
    )
    works = overview.facility_state(island)["construction"]
    assert any(w["x"] == town.x and w["y"] == town.y for w in works)
    assert not any(w["x"] < 0 for w in works)
