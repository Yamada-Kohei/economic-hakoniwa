"""畑の色を作柄で変える（グラフィック強化 第4波19a）: 島の地図の材料に、農場の
作柄（その農場の収穫に掛かる生産性の倍率）を出すこと。材料がある所（作物の育つ
農場が建っている所）だけに出し、畑でない・建っていない施設には出さない。"""

from __future__ import annotations

import pytest

from server.world import models as m
from tests.server.test_phase1 import _own_island

pytestmark = pytest.mark.django_db


def _facilities(client, island: m.Island) -> dict[int, dict]:
    data = client.get(f"/api/islands/{island.id}/map").json()
    return {f["number"]: f for f in data["facilities"]}


def test_map_carries_crop_yield_for_built_farms(client, world):
    island = _own_island(client)
    farm = island.facilities.filter(kind="farm", built=True).order_by("number").first()
    assert farm is not None
    farm.economy = {"productivity": 1350}
    farm.save()
    rows = _facilities(client, island)
    assert rows[farm.number]["crop_yield"] == pytest.approx(1.35)


def test_map_reads_missing_productivity_as_normal(client, world):
    """生産性が 1.0 ちょうどの農場は economy にキーが残らない（コアの update の仕様）。
    その場合は 1.0（ふつう）と読む。"""
    island = _own_island(client)
    farm = island.facilities.filter(kind="farm", built=True).order_by("number").first()
    assert farm is not None
    farm.economy = {}
    farm.save()
    rows = _facilities(client, island)
    assert rows[farm.number]["crop_yield"] == pytest.approx(1.0)


def test_map_omits_crop_yield_without_material(client, world):
    island = _own_island(client)
    farm = island.facilities.filter(kind="farm").order_by("number").first()
    assert farm is not None
    farm.built = False
    farm.economy = {"productivity": 1350}
    farm.save()
    other = island.facilities.filter(built=True).exclude(kind="farm").order_by("number").first()
    assert other is not None
    other.economy = {"productivity": 1350}
    other.save()
    rows = _facilities(client, island)
    assert "crop_yield" not in rows[farm.number]  # 建っていない畑には材料なし
    assert "crop_yield" not in rows[other.number]  # 畑でない施設には付けない
