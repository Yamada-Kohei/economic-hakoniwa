"""火力発電所の燃料の注意と備えを、画面・JSON・MCP・国の要約にそろえる（UI-87）。"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import power_fuel
from hakoniwa_core.economy.power_fuel_notice import LOW, after_market
from hakoniwa_core.founding import found_island
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE
from server.accounts import keys
from server.game import get_game
from server.web import display, overview
from server.world import models as m
from server.world.repository import create_island
from tests.core.test_founding import plan_for
from tests.core.test_recession_round1 import context
from tests.server.test_ai_players import _keyed, _tool

pytestmark = pytest.mark.django_db


@pytest.fixture
def fuel_island() -> tuple[m.Island, m.Facility, Client, Client]:
    """1島の、石炭が1か月分に減って買い足せない場面。ターンの長回しは要らない。"""
    cache.clear()
    game = get_game()
    world = m.World.objects.create(turn=1, seed=1)
    state = found_island(game.defs, game.config, plan_for(game, economy="mixed"), 0, 0, 0)
    f = next(f for f in state.facilities if power_fuel.is_thermal(game.defs, f))
    need = fac.recipe_of(game.defs, f)["inputs"]["coal"]
    f.stock = {"coal": [[0, need]]}
    f.status, f.operating_rate = "", fac.FULL
    f.economy["input_plan"] = fac.FULL
    f.economy["short"] = {"coal": "supply"}
    f.economy[power_fuel.FLOW] = {
        "turn": 1,
        "used": {"coal": need},
        "acquired": {},
        "value": {},
    }
    after_market(state, context(game))
    assert any(e.kind == LOW for e in state.log)
    user = User.objects.create_user("power-owner", password="a-long-pass-123")
    island = create_island(world, state, owner=user)
    row = island.facilities.get(number=f.id)
    _, token = keys.issue(user, "fuel-test")
    browser = Client()
    browser.force_login(user)
    return island, row, browser, _keyed(token)


@pytest.mark.parametrize("owner", [OWNER_PRIVATE, OWNER_STATE])
def test_fuel_reserves_and_notices_match_screen_json_summary_and_mcp(
    fuel_island: tuple[m.Island, m.Facility, Client, Client], owner: str
) -> None:
    island, row, browser, ai = fuel_island
    row.owner = owner
    row.save(update_fields=["owner"])
    reserves = display.fuel_reserves(island)
    assert reserves[0]["fuels"][0]["months"] == 1
    assert reserves[0]["fuels"][0]["target_months"] == 3
    screen = browser.get(f"/islands/{island.id}/facilities/")
    assert screen.status_code == 200
    assert screen.context["fuel_reserves"] == reserves
    assert "火力発電所の燃料の備え" in screen.content.decode()
    facilities = ai.get(f"/api/islands/{island.id}/facilities").json()
    assert facilities["fuel_reserves"] == reserves
    summary = ai.get(f"/api/islands/{island.id}/summary").json()
    assert summary["fuel_reserves"] == reserves
    for tool, args, expected in (
        ("summary", {}, summary),
        ("report", {"page": "facilities"}, facilities),
    ):
        text, failed = _tool(ai, tool, island=island.id, **args)
        assert not failed, text
        assert json.loads(text)["fuel_reserves"] == expected["fuel_reserves"]
    tile = ai.get(f"/api/islands/{island.id}/tiles/{row.x},{row.y}").json()
    assert tile["facility"]["fuel_reserves"] == reserves[0]["fuels"]
    alerts, _ = overview.island_alerts(island, mine=True)
    notice = next(text for text in alerts if "燃料の在庫が残り約" in text)
    assert notice in summary["alerts"]
    text, failed = _tool(ai, "summary", island=island.id)
    assert not failed, text
    assert notice in json.loads(text)["alerts"]
    assert all(
        action in notice
        for action in ("燃料の輸入", "国の倉庫の放出", "生産補助", "公共料金の上限の見直し")
    )
    island_screen = browser.get(f"/islands/{island.id}/")
    assert notice in island_screen.context["alerts"]
    cache.clear()
