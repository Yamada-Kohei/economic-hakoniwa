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
    # 試し遊びでは、失業手当の 60% のつもりで 0.6 と打ち、何も出ないまま 0.6% で実行された
    res = client.post(
        f"/api/islands/{island.id}/orders/check",
        {"kind": "set_labor_policy", "payload": {"unemployment_benefit": 0.6}},
        "application/json",
    )
    assert "失業手当 0.6%" in res.json()["summary"], res.json()


def test_check_summary_shows_amounts_in_the_units_sent(client, world):
    """確かめの説明は、送った見せる単位のまま（命令の列と同じ文）。量を二度直さない。"""
    from server.world.models import Stock

    island = _own_island(client)
    Stock.objects.create(island=island, facility=None, good="grain", made_turn=0, quantity=500_000)
    url = f"/api/islands/{island.id}/orders"
    payload = {"good": "grain", "side": "sell", "quantity": 5, "limit": 1000}
    check = client.post(
        f"{url}/check", {"kind": "warehouse_trade", "payload": payload}, "application/json"
    ).json()
    body = {"orders": [{"kind": "warehouse_trade", "payload": payload}]}
    listed = client.put(url, body, content_type="application/json").json()["orders"][0]["text"]
    assert check["ok"] is True, check
    assert check["summary"] == listed, listed
    assert "5 " in listed and "1,000" in listed, listed


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


def test_hk_map_shows_the_operating_rate_in_percent(capsys):
    """hk.py の地図は、稼働率を MCP の地図と同じく % で出す（試し遊びでは 1000 分の 1 のまま
    「稼働 195」と出て、HAL が 195% と読んだ）。"""
    import json
    from types import SimpleNamespace

    from tests.server.test_ai_players import _hk

    hk = _hk()
    island = {
        "size": 2,
        "terrain": ["plain"] * 4,
        "facilities": [{"kind": "mine", "x": 1, "y": 0, "built": True, "rate": 195}],
    }
    defs = {"facilities": {"mine": {"name": "鉱山"}}}

    def get(path):
        return 200, json.dumps(defs if path == "/api/defs" else island), {}

    assert hk.cmd_map(SimpleNamespace(get=get), SimpleNamespace(island=1)) == 0
    assert "(1,0) 鉱山 稼働 20%" in capsys.readouterr().out


def test_hk_summary_names_the_warehouse_and_the_turn_of_old_results(capsys):
    """hk.py の要約は、財の在庫が国の倉庫のものと書き、前に実行した命令の結果にはそのターンを
    添える（試し遊びでは、列が空なのに古い結果が今の命令に見えた）。"""
    from tests.server.test_ai_players import _hk

    hk = _hk()
    good = {"name": "穀物", "price": "1", "volume": 0, "production": 0, "stock": 100}
    assert "国の倉庫 100" in hk._good(good)
    orders = {
        "admin_power": 3,
        "pending": [],
        "last_turn": 44,
        "last_results": [{"turn": 44, "status": "done", "text": "穀物を買う", "reason": ""}],
    }
    for x in orders["last_results"]:
        assert "ターン 44 実行" in hk._result(x)


def test_help_says_where_shops_go_and_the_recipe_names(client, world):
    """手引きの施設の表に、百貨店は商業地だけ・商店は町のマスに町の大きさの数まで・生産方式の
    命令に書く名前が出る（試し遊びでは、どれも試して落ちるまで分からなかった）。"""
    page = client.get("/help/").content.decode()
    assert "商業地に指定したマスのみ" in page
    assert "町のマスに入る（1つの町に" in page
    assert "命令では wood" in page
