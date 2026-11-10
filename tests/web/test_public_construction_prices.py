"""工事の買い値不足と積算し直しまでの表示。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hakoniwa_core.game import load_game
from hakoniwa_core.state import Facility
from server.web import display
from server.web.display import status_text
from tests.core.test_rules import make_island, with_economy

pytestmark = pytest.mark.django_db


def test_price_wait_names_reason_and_remaining_turns():
    f = Facility(
        id=1,
        kind="farm",
        x=1,
        y=1,
        ext={"waiting": ["wood"], "material_price_wait": {"wood": 2}},
    )
    text = status_text(f)
    assert "木材の買い値が売り値に届かない" in text
    assert "あと 2 ターン" in text
    f.ext["short_funds"] = True
    assert "公共事業費が足りず" in status_text(f)
    f.ext.pop("short_funds")
    f.ext["material_price_wait"]["wood"] = 0
    assert "積算し直し済み" in status_text(f)


def test_facility_info_shows_estimate_and_unpurchased_material_cost(monkeypatch):
    game = load_game()
    island = with_economy(game, make_island(game))
    f = Facility(id=99, kind="farm", x=6, y=6, ext={"material_estimate": 123})
    island.facilities.append(f)
    monkeypatch.setattr(display, "facility_from_row", lambda row: f)
    stocks = MagicMock()
    totals = stocks.values.return_value.annotate.return_value
    totals.values_list.return_value = [("wood", 5000)]
    totals.order_by.return_value.values_list.return_value = [("wood", 5000)]
    row = SimpleNamespace(stocks=stocks, island=None, kind=f.kind)
    info = display.facility_info(row, island, mine=True)
    assert info["material_estimate"] == 123
    assert info["remaining_material_cost"] == 0  # 届いた資材をもう一度買う費用に数えない
    assert "material_estimate" not in display.facility_info(row, island, mine=False)
    f.ext["repair"] = True
    assert "material_estimate" not in display.facility_info(row, island, mine=True)
    f.ext.pop("repair")
    f.ext.pop("material_estimate")  # 古いセーブには見積りがない
    assert "material_estimate" not in display.facility_info(row, island, mine=True)
