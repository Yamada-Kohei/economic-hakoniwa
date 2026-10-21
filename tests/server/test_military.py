"""フェーズ4-2 の②のサーバー側: 兵の命令の説明と、国の要約の軍（持ち主だけ）。"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from server.turns.services import run_turn
from server.web import display
from server.world import models as m

pytestmark = pytest.mark.django_db


def _player():
    island = m.Island.objects.order_by("id").first()
    user = User.objects.create_user("soldier", password="a-long-pass-123")
    island.owner = user
    island.save()
    client = Client()
    client.force_login(user)
    return client, island


def test_summary_shows_the_army_to_its_owner_only(world):
    client, island = _player()
    body = {
        "orders": [
            {"kind": "set_defense", "payload": {"annual": 2_000_000}},
            {"kind": "recruit", "payload": {"count": 20}},
        ]
    }
    res = client.put(f"/api/islands/{island.id}/orders", body, content_type="application/json")
    assert res.status_code == 200, res.content
    run_turn(force=True)
    run_turn(force=True)
    mine = client.get(f"/api/islands/{island.id}/summary").json()["military"]
    assert mine["soldiers"] == 20 and mine["volunteers"] == 20 and mine["conscripts"] == 0
    assert mine["budget"]["personnel"] == 2_000_000 and mine["budget"]["procurement"] == 0
    assert 0 < mine["morale"] <= 1 and mine["wages_late_turns"] == 0
    public = Client().get(f"/api/islands/{island.id}/summary").json()
    # MI-55: ほかの国には兵の総数と予算の枠と見えた艦隊だけ（士気・部隊・内訳は出さない）
    assert public["military"]["soldiers"] == 20 and public["military"]["fleets"] == []
    assert not {"morale", "units", "volunteers", "conscripts"} & set(public["military"])


def test_orders_are_described():
    assert "20" in display.describe_order("recruit", {"count": 20})
    text = display.describe_order("set_defense", {"annual": 1_000_000, "personnel_share": 60})
    assert "1,000,000" in text and "60%" in text


def test_units_and_bases_show_in_the_summary_and_the_military_api(world):
    from hakoniwa_core.military import army

    client, island = _player()
    row = m.Facility.objects.create(
        island=island, number=900, kind="barracks", x=1, y=1, built=True
    )
    w = island.world
    w.ext = {
        army.KEY: {
            "seq": 1,
            "units": [
                {
                    "id": 1,
                    "owner": island.id,
                    "kind": "infantry",
                    "x": 1,
                    "y": 1,
                    "soldiers": 100,
                    "equipment": {"weapons": 100},
                    "status": "ready",
                    "order": None,
                }
            ],
        }
    }
    w.save()
    tile = island.tiles.get(x=2, y=2)
    tile.ext = {army.TRENCH: 1}
    tile.save()
    mine = client.get(f"/api/islands/{island.id}/military").json()["military"]
    assert mine["units"][0]["name"] == "歩兵中隊" and mine["units"][0]["soldiers_full"] == 100
    assert [b["kind"] for b in mine["bases"]] == ["barracks"] and mine["bases"][0][
        "barracks"
    ] == 500
    assert mine["trenches"] == [{"x": 2, "y": 2}]
    assert (
        client.get(f"/api/islands/{island.id}/summary").json()["military"]["units"] == mine["units"]
    )
    public = Client().get(f"/api/islands/{island.id}/military").json()["military"]
    assert set(public) == {"soldiers", "budget", "stats_turn", "fleets", "seen_units"}
    assert row.pk


def test_missile_bases_show_their_stock(world):
    client, island = _player()
    base = m.Facility.objects.create(
        island=island, number=901, kind="missile_base", x=1, y=1, built=True
    )
    m.Stock.objects.create(island=island, facility=base, good="missile", quantity=3_000)
    m.Stock.objects.create(island=island, facility=base, good="nuclear_warhead", quantity=1_000)
    rows = client.get(f"/api/islands/{island.id}/military").json()["military"]["bases"]
    assert [(b["kind"], b["stock"], b["warheads"]) for b in rows] == [("missile_base", 3, 1)]


# --- ③': 秘密の命令・ログの出し分け・艦隊の見え方（MI-27・MI-55・MI-26） -----------------------


def test_unit_and_fleet_orders_are_always_secret_for_others(world):
    from server.game import get_game
    from server.orders.models import Order

    defs = get_game().defs
    for kind in ("form_unit", "move_unit", "form_fleet", "sail_fleet", "load_unit", "unload_unit"):
        assert defs.orders[kind]["secret"] == "always"
    client, island = _player()
    url = f"/api/islands/{island.id}/orders"
    for seq, kind in enumerate(["move_unit", "sail_fleet"], start=1):
        Order.objects.create(island=island, seq=seq, kind=kind, payload={"x": 1, "y": 1})
    Order.objects.create(
        island=island, seq=3, kind="stop_unit", payload={"unit": 1}, status="done", executed_turn=1
    )
    assert len(client.get(url).json()["orders"]) == 2
    assert len(client.get(url).json()["recent"]) == 1
    other = Client()
    assert other.get(url).json()["orders"] == []
    assert other.get(url).json()["recent"] == []  # 実行しても公開しない


def test_military_logs_are_for_the_owner_only(world):
    from hakoniwa_core.military import PRIVATE_LOGS
    from server.turns.models import TurnLog

    client, island = _player()
    TurnLog.objects.create(island=island, turn=1, kind="fleet_ready", args={"unit": 1})
    TurnLog.objects.create(island=island, turn=1, kind="unit_arrived", args={"unit": 1})
    TurnLog.objects.create(island=island, turn=1, kind="defense_set", args={})
    assert {"fleet_ready", "unit_arrived", "defense_set"} <= set(PRIVATE_LOGS)
    url = f"/api/islands/{island.id}/log?from=1&to=1"
    mine = {e["kind"] for e in client.get(url).json()["entries"]}
    assert {"fleet_ready", "unit_arrived", "defense_set"} <= mine
    others = {e["kind"] for e in Client().get(url).json()["entries"]}
    assert not set(PRIVATE_LOGS) & others


def test_military_numbers_other_than_the_soldiers_are_hidden(world):
    from server.web import reports

    assert reports.visible("military.soldiers", mine=False)
    assert not reports.visible("military.units", mine=False)
    assert not reports.visible("military.ships", mine=False)
    assert reports.visible("military.ships", mine=True)


def test_world_fleets_show_own_and_seen_fleets_only(world):
    from hakoniwa_core.military import army

    client, island = _player()
    other = m.Island.objects.exclude(pk=island.pk).order_by("id").first()
    w = island.world
    w.ext = {
        army.KEY: {
            "seq": 2,
            "units": [
                {"id": 1, "owner": island.id, "kind": "frigate", "x": 0, "y": 0, "ships": 2}
                | {
                    "soldiers": 400,
                    "equipment": {},
                    "status": "ready",
                    "order": None,
                    "arm": "sea",
                    "cargo": [],
                },
                {"id": 2, "owner": other.id, "kind": "frigate", "x": 3, "y": 3, "ships": 1}
                | {
                    "soldiers": 200,
                    "equipment": {},
                    "status": "ready",
                    "order": None,
                    "arm": "sea",
                    "cargo": [],
                },
            ],
            "seen": {},
        }
    }
    w.save()
    data = client.get("/api/world/fleets").json()
    assert [f["id"] for f in data["mine"]] == [1] and data["seen"] == []
    w.ext[army.KEY]["seen"] = {
        str(island.id): {
            "2": {"owner": other.id, "kind": "frigate", "x": 3, "y": 3, "turn": w.turn}
        }
    }
    w.save()
    seen = client.get("/api/world/fleets").json()["seen"]
    assert [(f["id"], f["x"], f["y"], f["current"]) for f in seen] == [(2, 3, 3, True)]
    assert "soldiers" not in seen[0] and "equipment" not in seen[0]
    anonymous = Client().get("/api/world/fleets").json()
    assert anonymous["mine"] == [] and anonymous["seen"] == []


def test_map_shows_trenches_to_all_and_units_to_the_owner(world):
    from hakoniwa_core.military import army

    client, island = _player()
    plain = island.tiles.filter(terrain="plain").order_by("y", "x").first()
    plain.ext = {army.TRENCH: 1}
    plain.save()
    w = island.world
    unit = {"id": 1, "owner": island.id, "kind": "infantry", "x": plain.x, "y": plain.y}
    w.ext = {army.KEY: {"seq": 1, "units": [unit | {"soldiers": 100, "equipment": {}}]}}
    w.ext[army.KEY]["units"][0] |= {"status": "ready", "order": None}
    w.save()
    url = f"/api/islands/{island.id}/map"
    mine = client.get(url).json()
    assert mine["trench"][plain.y * island.size + plain.x] == 1 and sum(mine["trench"]) == 1
    assert mine["units"] == [
        {
            "id": 1,
            "owner": island.id,
            "army": "infantry",
            "x": plain.x,
            "y": plain.y,
            "current": True,
        }
    ]
    public = Client().get(url).json()
    assert sum(public["trench"]) == 1 and public.get("units", []) == []


def test_military_page_and_defs(world):
    client, island = _player()
    page = client.get(f"/islands/{island.id}/military/")
    assert page.status_code == 200 and "兵と国防費" in page.content.decode()
    others = Client().get(f"/islands/{island.id}/military/")
    assert others.status_code == 200
    assert "その国の持ち主だけに見えます" in others.content.decode()
    army = Client().get("/api/defs").json()["army"]
    assert army["infantry"]["arm"] == "land" and army["infantry"]["equipment"] == {"weapons": 100}
    assert army["transport"]["capacity"] == 40 and army["submarine"]["stealth"] is True
    assert army["frigate"]["name"] == "フリゲートの艦隊"


def test_a_hidden_facility_shows_only_to_its_owner_and_the_ones_who_found_it(world):
    client, island = _player()
    other = m.Island.objects.exclude(pk=island.pk).order_by("id").first()
    other_user = User.objects.create_user("spy", password="a-long-pass-123")
    other.owner = other_user
    other.save()
    spy = Client()
    spy.force_login(other_user)
    secret = m.Facility.objects.create(
        island=island, number=902, kind="missile_base", x=1, y=1, built=True, ext={"hidden": True}
    )
    m.Facility.objects.create(island=island, number=903, kind="barracks", x=2, y=2, built=True)
    url = f"/api/islands/{island.id}/map"

    def kinds(who):
        return {f["kind"] for f in who.get(url).json()["facilities"]}

    assert {"barracks", "missile_base"} <= kinds(client)
    rows = client.get(url).json()["facilities"]
    assert [f.get("hidden") for f in rows if f["kind"] == "missile_base"] == [True]
    assert "barracks" in kinds(spy) and "missile_base" not in kinds(spy)
    assert "missile_base" not in kinds(Client())
    secret.ext = {"hidden": True, "found": [other.id]}
    secret.save()
    assert "missile_base" in kinds(spy) and "missile_base" not in kinds(Client())
    # 隠した施設のマスのターンログは持ち主だけ
    from server.turns.models import TurnLog

    TurnLog.objects.create(island=island, turn=1, kind="facility_started", args={"x": 1, "y": 1})
    TurnLog.objects.create(island=island, turn=1, kind="facility_started", args={"x": 2, "y": 2})
    rows = TurnLog.objects.filter(island=island, kind="facility_started")
    seen = display.visible_logs(rows, False, island)
    assert [e.args for e in seen] == [{"x": 2, "y": 2}]
    assert display.visible_logs(rows, True, island).count() == 2


def test_map_shows_the_nuclear_fallout_to_all(world):
    _, island = _player()
    tile = island.tiles.order_by("y", "x").first()
    tile.ext = {"fallout": 5_000}
    tile.save()
    fallout = Client().get(f"/api/islands/{island.id}/map").json()["fallout"]
    assert fallout[0] == 5.0 and sum(fallout) == 5.0
