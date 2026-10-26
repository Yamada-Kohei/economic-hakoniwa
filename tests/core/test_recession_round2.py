"""W-186（市場経済の国の不況）の直し 回2: 値段と賃金（ふだんの在庫、品切れの財の値段、生産性と
賃金、計画経済の国営施設の人の抱え込み）。"""

from __future__ import annotations

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import plan
from hakoniwa_core.modules.employment import ATTRITION, Employment
from hakoniwa_core.modules.firms import entry_price
from hakoniwa_core.modules.labor import PRODUCTIVITY_YEAR, productivity_growth, wage_month
from hakoniwa_core.modules.market import offered_beyond_stock
from hakoniwa_core.modules.production import Production
from hakoniwa_core.state import OWNER_STATE
from tests.core.test_recession_round1 import context
from tests.core.test_rules import add, home, make_island, with_economy
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）


def test_normal_inventory_is_not_read_as_excess():
    # 全部売れたら今までどおり（品不足の分を値上げに足す）
    assert offered_beyond_stock(100, 100, 50) == 100
    # 売れ残りがふだんの在庫の目安以内なら、希望価格を動かさない
    assert offered_beyond_stock(100, 60, 50) is None
    assert offered_beyond_stock(100, 50, 50) is None
    # 目安を超えた分だけが余り（売れた 40、売れ残り 60 のうち 50 は目安、10 が余り）
    assert offered_beyond_stock(100, 40, 50) == 50
    # 売れ行きの見込みが 0 なら目安も 0 で、売れ残りは全部余り
    assert offered_beyond_stock(100, 0, 0) == 100


def test_wages_follow_productivity_once_a_year_on_the_facility_month(game):
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    mill = add(island, "steel_mill", 9, 9, owner=OWNER_STATE)
    with_economy(game, island)
    month = next(t for t in range(1, 13) if wage_month(mill, t))
    assert sum(wage_month(mill, t) for t in range(1, 25)) == 2  # 年に1回
    mill.economy[PRODUCTIVITY_YEAR] = 1000
    mill.economy["productivity"] = 1100
    assert productivity_growth(mill, month) == pytest.approx(0.1)
    assert productivity_growth(mill, month + 1) == 0.0  # 施設の月でなければ乗せない
    wage = mill.economy["wage"]
    raised = game.engine.formulas["wage_update"](island, mill, context(game, month))
    assert raised >= wage * 1.1 - 1
    # 下がった年は下げず、記録のない年（新しい施設）も乗せない
    mill.economy["productivity"] = 900
    assert productivity_growth(mill, month) == 0.0
    mill.economy.pop(PRODUCTIVITY_YEAR)
    assert productivity_growth(mill, month) == 0.0


def test_planned_state_facilities_keep_their_workers(game):
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    farm = add(island, "farm", 9, 9, owner=OWNER_STATE)
    with_economy(game, island)
    island.regime["economy"] = "planned"
    assert plan.hoards(island, game.defs, farm)
    ctx = context(game)
    full = fac.workers_of(game.defs, farm)
    farm.workers = full
    # 辞める人の分（月 planning.attrition）だけ減る。端数はためて次の月に数える
    attrition = game.config.get("planning.attrition")
    kept = Employment._kept(ctx, farm)
    assert kept == full - int(full * attrition)
    assert farm.economy[ATTRITION] == pytest.approx(full * attrition % 1)
    farm.workers = 10
    leave = 10 * attrition + farm.economy[ATTRITION]
    assert Employment._kept(ctx, farm) == 10 - int(leave)
    # 抱えた人で操業目標より多く作らない（隠れた失業）
    farm.workers = full
    farm.economy["target"] = 400
    run = Production._prepare(island, farm, ctx, game.config.get("power.good"))
    assert run.rate <= 400
    # 市場経済の島の国営施設は抱えない
    island.regime["economy"] = "market"
    assert not plan.hoards(island, game.defs, farm)


def test_missing_goods_rise_toward_the_price_a_new_maker_needs(game):
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    add(island, "fishing_port", 2, 2)
    with_economy(game, island)
    island.regime["economy"] = "market"
    # 穀物が売りに出ないまま買いたい人がいる月が続いた（参入の合図。売れると見込む割合は1）
    farm = game.defs.facilities["farm"]["recipes"]["default"]["outputs"]["grain"]
    island.economy.finance["shortages"] = {"grain": [farm * 10] * 6}
    island.economy.finance["demand"] = {"grain": [farm * 10] * 6}
    ctx = context(game)
    price = entry_price(island, ctx, "grain")
    assert price is not None and price > 0
    # 安い値段では新しい農場の見込みが届かない: 見合う値段は今の値段より高い
    island.economy.prices["grain"] = price // 4
    island.economy.price_history["grain"] = [price // 4] * 6
    assert entry_price(island, ctx, "grain") > price // 4
    # 計画経済の島には民営の参入がないので、上げる先はない
    island.regime["economy"] = "planned"
    assert entry_price(island, ctx, "grain") is None
