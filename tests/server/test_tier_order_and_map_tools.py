"""品目を段の順にそろえ、表を並べ替えられるようにし、地図に目盛りと座標を出す（UI-85・UI-86）。"""

from __future__ import annotations

import pytest
from django.test import Client

from server.game import get_game
from server.web import display

pytestmark = pytest.mark.django_db


def _tier_index(goods: list[str]) -> list[tuple[int, int]]:
    defs = get_game().defs
    order = list(defs.goods)
    return [(defs.goods[g].get("tier") or 99, order.index(g)) for g in goods]


def test_goods_are_listed_by_tier_then_definition_order() -> None:
    goods = display.goods_in_order()
    assert sorted(goods) == sorted(get_game().defs.goods)
    keys = _tier_index(goods)
    assert keys == sorted(keys)
    defs = get_game().defs
    assert defs.goods[goods[0]]["tier"] == 1 and defs.goods[goods[-1]]["tier"] == 3


def test_defs_api_and_market_table_follow_the_tier_order(world) -> None:
    client = Client()
    goods = list(client.get("/api/defs").json()["goods"])
    assert goods == display.goods_in_order()
    island = world.islands.order_by("id").first()
    rows = client.get(f"/api/islands/{island.id}/market").json()["rows"]
    listed = [r["good"] for r in rows]
    assert len(listed) > 3
    assert _tier_index(listed) == sorted(_tier_index(listed))


def test_every_page_loads_the_table_sorter_and_the_map_has_a_coordinate_box(world) -> None:
    island = world.islands.order_by("id").first()
    page = Client().get(f"/islands/{island.id}/").content.decode()
    assert "js/table-sort.js" in page
    assert 'id="map-coord"' in page
    top = Client().get("/").content.decode()
    # トップの表も同じ部品で並べ替える（table-sort.js を先に読む）
    assert top.index("js/table-sort.js") < top.index("js/top-table.js")
