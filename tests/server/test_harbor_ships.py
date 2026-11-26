"""港の商船（案6）と母港の艦隊（W-100）の、地図の API が渡す材料。

port_ships: 動いている港の位置と、港に停泊中の商船の隻数（海運業の船団の総数から
航海中の分を引いた実数）。harbor_fleets: 母港に繋留中の艦隊で、持ち主には艦種と隻数、
ほかの国には見えている艦種だけ、ログインしていない人には何も出さない。
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db.models import Max
from django.urls import reverse

from server.game import get_game
from server.world.models import Facility, Island

pytestmark = pytest.mark.django_db


def _map(client, island: Island) -> dict:
    return client.get(reverse("api_island_map", args=[island.id])).json()


def _ensure(island: Island, kind: str, **fields) -> Facility:
    row = island.facilities.filter(kind=kind).order_by("number").first()
    if row is not None:
        for key, value in fields.items():
            setattr(row, key, value)
        row.save()
        return row
    number = (island.facilities.aggregate(Max("number"))["number__max"] or 0) + 1
    taken = {(f.x, f.y) for f in island.facilities.all()}
    x, y = -1, -1
    while (x, y) in taken:
        y -= 1
    return Facility.objects.create(island=island, number=number, kind=kind, x=x, y=y, **fields)


def _port_and_shipping(island: Island) -> tuple[Facility, Facility]:
    config = get_game().config
    port = _ensure(island, config.get("trade.port_facility"), built=True, economy={})
    shipper = _ensure(island, config.get("trade.shipping_facility"), economy={})
    return port, shipper


def test_port_ships_counts_ships_in_port(client, world):
    island = Island.objects.order_by("id").first()
    port, shipper = _port_and_shipping(island)
    _map(client, island)  # 先に 1 回読んで、世界の倍率込みの設定を読み込ませる
    capacity = get_game().config.get("trade.ship_capacity")
    # 船団 5 隻のうち、2 隻分が航海中 → 港には 3 隻。
    # busy は使っている積載量（1000 分の 1 トン）なので、2 隻分は 2 × 1000 × 1 隻の積載量
    shipper.economy = {"fleet": [[1, 5000]], "busy": {"1": capacity * 2000}}
    shipper.save(update_fields=["economy"])
    data = _map(client, island)
    assert data["port_ships"] == {"x": port.x, "y": port.y, "count": 3}


def test_port_ships_none_without_port_or_shipping(client, world):
    island = Island.objects.order_by("id").first()
    config = get_game().config
    island.facilities.filter(
        kind__in=[
            config.get("trade.port_facility"),
            config.get("trade.shipping_facility"),
        ]
    ).delete()
    assert _map(client, island)["port_ships"] is None


def _docked_unit(island: Island, **over) -> dict:
    unit = {
        "id": 1,
        "owner": island.id,
        "kind": "frigate",
        "arm": "sea",
        "ships": 3,
        "x": island.wx,
        "y": island.wy,
        "soldiers": 0,
        "equipment": {},
        "status": "ready",
        "order": None,
        "cargo": [],
    }
    unit.update(over)
    return unit


def _own_island(client, world) -> Island:
    user = User.objects.create_user("owner", password="<redacted>")
    client.force_login(user)
    island = Island.objects.order_by("id").first()
    island.owner = user
    island.save(update_fields=["owner"])
    return island


def test_harbor_fleets_for_owner_only_when_docked(client, world):
    island = _own_island(client, world)
    world.ext = {
        "military": {
            "units": [
                _docked_unit(island),
                _docked_unit(island, id=2, kind="corvette", x=island.wx + 1),  # 航海中
            ]
        }
    }
    world.save(update_fields=["ext"])
    data = _map(client, island)
    assert data["harbor_fleets"] == [{"army": "frigate", "ships": 3, "mine": True}]


def test_harbor_fleets_hidden_from_anonymous_and_strangers(client, world):
    island = _own_island(client, world)
    world.ext = {"military": {"units": [_docked_unit(island)]}}
    world.save(update_fields=["ext"])

    client.logout()
    assert _map(client, island)["harbor_fleets"] == []

    other = User.objects.create_user("other", password="<redacted>")
    client.force_login(other)
    # 見える範囲の記録がなければ、ほかの国には艦種も出ない
    assert _map(client, island)["harbor_fleets"] == []
