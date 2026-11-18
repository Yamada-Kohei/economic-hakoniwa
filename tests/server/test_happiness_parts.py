"""幸福度の内訳（W-172）: 経済の画面・API・国の要約に、持ち主だけ項目ごとの点と動きを出す。"""

from __future__ import annotations

import pytest

from server.world import models as m
from tests.server.test_phase1 import _own_island
from tests.server.test_reports import _stats

pytestmark = pytest.mark.django_db

PART = "happiness_part."


def _history(island: m.Island) -> None:
    _stats(island, 1, {"happiness": 70.0, PART + "base": 10.0, PART + "clothing": 5.0})
    _stats(
        island,
        12,
        {"happiness": 68.0, PART + "base": 10.0, PART + "clothing": 4.0, PART + "food": 54.0},
    )
    _stats(
        island,
        13,
        {
            "happiness": 63.5,
            PART + "base": 10.0,
            PART + "food": 55.0,
            PART + "clothing": 1.5,
            PART + "hunger": -2.0,
            PART + "rounding": -1.0,
        },
    )


def test_owner_sees_parts_and_moves(client, world):
    island = _own_island(client)
    _history(island)
    page = client.get(f"/islands/{island.id}/economy/")
    view = page.context["happiness"]
    assert view["text"] == "63.5" and view["change_text"] == "-4.5"
    assert view["change_year_text"] == "-6.5"
    rows = {r["key"]: r for r in view["rows"]}
    assert sum(r["value"] for r in view["rows"]) == pytest.approx(63.5)
    assert rows["clothing"]["change_text"] == "-2.5" and rows["clothing"]["down"]
    assert rows["clothing"]["change_year_text"] == "-3.5"
    assert rows["hunger"]["change_text"] == "-2.0" and rows["hunger"]["limit"] == 30
    assert rows["food"]["change_text"] == "+1.0" and rows["food"]["limit"] == 25
    # いちばん下げた項目に、端数・上限の行は挙げない
    assert view["worst"]["key"] == "clothing"
    assert not view["capped"]
    html = page.content.decode()
    assert 'id="happiness-parts"' in html and "いちばん下げたのは「衣料」" in html
    data = client.get(f"/api/islands/{island.id}/economy/overview").json()
    assert data["happiness"]["rows"] == view["rows"]
    summary = client.get(f"/api/islands/{island.id}/summary").json()
    assert summary["happiness"]["worst"]["key"] == "clothing"
    assert [r["key"] for r in summary["happiness"]["moved"]][:2] == ["clothing", "hunger"]


def test_capped_island_says_so(client, world):
    island = _own_island(client)
    island.regime = {**(island.regime or {}), "politics": "totalitarian"}
    island.save()
    _stats(island, 5, {"happiness": 80.0, PART + "base": 85.0, PART + "cap": -5.0})
    view = client.get(f"/islands/{island.id}/economy/").context["happiness"]
    assert view["capped"] and view["cap_value"] == 80
    assert view["change"] is None and view["change_text"] == "—"


def test_others_do_not_see_parts(client, world):
    island = m.Island.objects.order_by("id").first()
    _history(island)
    page = client.get(f"/islands/{island.id}/economy/")
    assert page.context["happiness"] is None
    assert "島の持ち主だけ" in page.content.decode()
    data = client.get(f"/api/islands/{island.id}/economy/overview").json()
    assert data["happiness"] is None
    assert not any(c["name"].startswith(PART) for c in data["choices"])
    series = client.get(f"/api/islands/{island.id}/economy", {"names": "happiness*"}).json()
    assert set(series["series"]) == {"happiness"}
