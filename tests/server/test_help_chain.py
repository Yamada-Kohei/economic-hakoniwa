"""手引き「産業のつながり」（/help/chain/。UI-61）。"""

from __future__ import annotations

import json
import re

import pytest

from server.game import get_game
from server.web import chain_map


def test_every_good_made_or_used_by_a_facility_has_a_column():
    """作り方に出る品目はみな図の列に並ぶ（新しい品目を足したら chain_map.COLUMNS にも足す）。"""
    data = chain_map.chain_data()
    placed = {g for col in data["cols"] for g in col}
    used = {g for f in data["facs"] for r in f["r"] for g, _ in (*r["in"], *r["out"])}
    assert used <= placed, sorted(used - placed)
    assert all(g in get_game().defs.goods for g in placed)


def test_amounts_are_the_shown_amounts_from_the_recipes():
    """量は施設 1 つの全力の 1 ターン分を、画面と同じ単位で（例: 製鉄所の鋼鉄）。"""
    defs = get_game().defs
    mill = next(f for f in chain_map.chain_data()["facs"] if f["id"] == "steel_mill")
    default = next(r for r in mill["r"] if r["id"] == "default")
    milli = defs.facilities["steel_mill"]["recipes"]["default"]["outputs"]["steel"]
    assert default["out"] == [["steel", round(milli / 1000 * defs.display_scale("steel"), 1)]]
    inputs = defs.facilities["steel_mill"]["recipes"]["default"]["inputs"]
    assert {g for g, _ in default["in"]} == set(inputs)


@pytest.mark.django_db
def test_chain_guide_page_carries_the_data_and_is_linked(client, world):
    page = client.get("/help/chain/").content.decode()
    assert "手引き：産業のつながり" in page
    found = re.search(r'<script id="chain-data" type="application/json">(.*?)</script>', page, re.S)
    data = json.loads(found.group(1))
    assert {"goods", "facs", "cols", "sinks"} <= set(data)
    assert "js/help-chain.js" in page
    assert "{%" not in page and "{{" not in page
    assert "/help/chain/" in client.get("/help/").content.decode()
