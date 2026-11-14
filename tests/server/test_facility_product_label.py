"""施設の名前に作る物を添える（W-262、掲示板 #25 の 21 番）。"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client

from hakoniwa_core.founding import found_island
from server.accounts import keys
from server.game import get_game
from server.orders.models import Order
from server.web import display
from server.world import models as m
from server.world.repository import create_island
from tests.core.test_founding import plan_for
from tests.server.test_ai_players import _keyed, _tool

pytestmark = pytest.mark.django_db


def test_kind_label_adds_the_product_only_where_the_method_changes_it() -> None:
    defs = get_game().defs
    assert display.kind_label(defs, "farm", "default") == "農場（穀物）"
    assert display.kind_label(defs, "farm", "cotton_fertilized") == "農場（綿）"
    assert display.kind_label(defs, "mine", "copper_ore") == "鉱山（銅鉱石）"
    # 作り方を変えても作る物が同じ施設（食品工場）は種類の名前だけ
    assert display.kind_label(defs, "food_factory", "fish") == "食品工場"
    assert display.kind_label(defs, "farm", "no_such_recipe") == "農場"


def test_build_order_text_names_the_product() -> None:
    describe = display.describe_order
    farm = {"facility": "farm", "x": 1, "y": 2}
    assert describe("build", farm) == "施設の建設: 農場（穀物） (1, 2)"
    assert (
        describe("build", {"facility": "farm", "recipe": "fertilized", "x": 1, "y": 2})
        == "施設の建設: 農場（穀物・肥料あり） (1, 2)"
    )
    assert (
        describe("build", {"facility": "mine", "recipe": "coal", "x": 1, "y": 2})
        == "施設の建設: 鉱山（石炭） (1, 2)"
    )
    assert (
        describe("build", {"facility": "food_factory", "recipe": "fish", "x": 1, "y": 2})
        == "施設の建設: 食品工場・魚から製造 (1, 2)"
    )


@pytest.fixture
def mine_island() -> tuple[m.Island, m.Facility, Client, Client]:
    cache.clear()
    game = get_game()
    world = m.World.objects.create(turn=1, seed=1)
    state = found_island(game.defs, game.config, plan_for(game, economy="mixed"), 0, 0, 0)
    user = User.objects.create_user("label-owner", password="a-long-pass-123")
    island = create_island(world, state, owner=user)
    row = island.facilities.filter(kind="mine").order_by("number").first()
    assert row is not None
    _, token = keys.issue(user, "label-test")
    browser = Client()
    browser.force_login(user)
    return island, row, browser, _keyed(token)


def test_lists_map_panel_orders_and_mcp_show_the_product(
    mine_island: tuple[m.Island, m.Facility, Client, Client],
) -> None:
    island, row, browser, ai = mine_island
    label = display.kind_label(get_game().defs, "mine", row.recipe)
    assert label.startswith("鉱山（") and label != "鉱山"
    # 施設の一覧（画面と JSON）と地図のマスの窓
    page = browser.get(f"/islands/{island.id}/facilities/")
    assert label in page.content.decode()
    listed = ai.get(f"/api/islands/{island.id}/facilities").json()
    assert any(f["kind_name"] == label for f in listed["table"]["rows"])
    tile = ai.get(f"/api/islands/{island.id}/tiles/{row.x},{row.y}").json()
    assert tile["facility"]["kind_name"] == label
    # 施設への命令の文（命令の列・国の要約）
    Order.objects.create(
        island=island, seq=1, kind="set_priority", payload={"x": row.x, "y": row.y, "priority": 2}
    )
    queue = ai.get(f"/api/islands/{island.id}/orders").json()
    assert queue["orders"][0]["text"] == f"優先度の変更: {label} ({row.x}, {row.y}) → 2"
    summary = ai.get(f"/api/islands/{island.id}/summary").json()
    assert summary["orders"]["pending"][0]["text"] == queue["orders"][0]["text"]
    text, error = _tool(ai, "map")
    assert not error and "鉱山・" in text
