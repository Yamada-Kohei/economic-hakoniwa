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
    assert set(public) == {"soldiers", "budget", "stats_turn", "fleets"}
    assert row.pk


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
