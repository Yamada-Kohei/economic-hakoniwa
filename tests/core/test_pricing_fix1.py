"""価格の決まり方の直しの第1弾（2026-11-01）: 計画経済の賃金・持ち越せない物の在庫・指値の端数。"""

from __future__ import annotations

import pytest

from hakoniwa_core.market import auction
from hakoniwa_core.modules.labor import PRODUCTIVITY_YEAR, wage_month
from hakoniwa_core.modules.market import gross_cost, inventory_turns, net_limit, seller_limit
from hakoniwa_core.state import OWNER_STATE
from hakoniwa_core.units import part
from tests.core.test_recession_round1 import context
from tests.core.test_rules import add, home, make_island, with_economy
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）


def _short_handed_mill(game):
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    mill = add(island, "steel_mill", 9, 9, owner=OWNER_STATE)
    with_economy(game, island)
    mill.workers = 1
    mill.economy["wanted"] = 50  # 人手が大きく足りない
    island.economy.cpi_history = [1000 + 20 * t for t in range(14)]  # 物価も上がっている
    return island, mill


def test_planned_wages_do_not_chase_shortages_or_prices(game):
    island, mill = _short_handed_mill(game)
    month = next(t for t in range(1, 13) if wage_month(mill, t))
    wage = mill.economy["wage"]
    update = game.engine.formulas["wage_update"]
    # 市場経済なら人手の足りなさで上がる
    island.regime["economy"] = "market"
    assert update(island, mill, context(game, month + 1)) > wage
    # 計画経済では施設の月でなければ動かない
    island.regime["economy"] = "planned"
    assert update(island, mill, context(game, month + 1)) == wage
    engineer = game.engine.formulas["engineer_wage_update"]
    if "engineer_wage" in mill.economy:
        assert engineer(island, mill, context(game, month + 1)) == mill.economy["engineer_wage"]
    # 施設の月には生産性の伸びの分だけ上がる
    mill.economy[PRODUCTIVITY_YEAR] = 1000
    mill.economy["productivity"] = 1050
    assert update(island, mill, context(game, month)) == pytest.approx(wage * 1.05, abs=1)
    # 最低賃金より下げない
    island.policy.minimum_wage = wage * 2
    assert update(island, mill, context(game, month + 1)) == wage * 2


def test_goods_that_cannot_be_carried_over_keep_no_stock(game):
    ctx = context(game)
    for good in ("services", "retail", "electricity"):
        assert inventory_turns(ctx, good) == 0
    assert inventory_turns(ctx, "fish") == pytest.approx(0.5)  # 2 ターン持つ物は半分
    assert inventory_turns(ctx, "steel") == game.config.get("market.inventory_turns")


@pytest.mark.parametrize("freight", [0, 37, 120, 333, 1250, 2999])
def test_input_bid_at_the_top_of_the_band_meets_a_seller_there(freight):
    width = 1000
    for price in range(1, 3000, 7):
        _, high = auction.band(price, width)
        usual = high + part(high, freight)  # 原料の買いのふだんの指値（運賃込み）
        bid = net_limit(usual, freight, 0)
        assert bid == high  # 上端に張り付いた売りとすれ違わない
        assert seller_limit(high, 0, high) <= bid


def test_net_limit_never_exceeds_what_can_be_paid():
    for gross in range(1, 5000, 3):
        for freight, tax in ((0, 0), (250, 0), (800, 1000), (3333, 77)):
            net = net_limit(gross, freight, tax)
            assert net + part(net, freight) + part(net, tax) <= gross
            assert net + 1 + part(net + 1, freight) + part(net + 1, tax) > gross
    # 量を掛けた取り置きも、上限から出した指値なら上限の量の額を超えない
    assert gross_cost(net_limit(1100, 250, 0), 1000, 250, 0) <= 1100
