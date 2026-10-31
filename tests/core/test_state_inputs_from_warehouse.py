"""国営の施設は、市場で買いきれなかった原料を国の倉庫から使う（2026-10-31。公開の世界で、石炭の出ない
国の国営の火力発電所が倉庫の石炭を使えず、建国の数ターン後に電力が全く足りなくなった）。"""

from __future__ import annotations

from hakoniwa_core import stock
from hakoniwa_core.founding import found_island
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE, WorldState
from tests.core.test_founding import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_founding import plan_for


def _run(game, economy, turns=6):
    plan = plan_for(game, economy=economy, resources=["iron_ore"])  # 石炭の出ない国
    world = WorldState(
        turn=0, seed=1, islands={1: found_island(game.defs, game.config, plan, 1, 3, 4)}
    )
    island = world.islands[1]
    before = stock.total(island.warehouse, "coal")
    shortage, gaps = [], []
    for _ in range(turns):
        game.engine.run_turn(world)
        shortage.append(world.islands[1].stats.get("power.shortage", 0))
        gaps.append(abs(world.islands[1].stats.get("gdp.gap", 0)))
    # 倉庫から渡した分も、GDP の生産の側と支出の側が合う
    assert max(gaps) < 0.001
    return world.islands[1], before, shortage


def test_state_power_plant_burns_the_warehouse_coal(game):
    island, before, shortage = _run(game, "mixed")
    plant = next(f for f in island.facilities if f.kind == "thermal_power_plant")
    assert plant.owner == OWNER_STATE
    assert stock.total(island.warehouse, "coal") < before  # 倉庫の石炭を使った
    assert max(shortage[1:]) == 0  # 初めのターン（動き出し）の後は電力が足りる


def test_private_power_plant_does_not_take_from_the_warehouse(game):
    island, before, _ = _run(game, "market", turns=3)
    plant = next(f for f in island.facilities if f.kind == "thermal_power_plant")
    assert plant.owner == OWNER_PRIVATE
    assert stock.total(island.warehouse, "coal") == before  # 民営は市場で買う


def test_warehouse_keeps_building_materials_and_the_grain_reserve(game):
    """工事の資材（木材・鋼鉄など）と国の穀物の備蓄は、国営の工場の原料に回さない。"""
    from hakoniwa_core.economy import reserve
    from hakoniwa_core.modules.market import Market
    from tests.core.test_recession_round1 import context

    plan = plan_for(game, economy="planned", resources=["iron_ore"])
    island = found_island(game.defs, game.config, plan, 1, 3, 4)
    for f in island.facilities:  # 原料の手元をなくす（どの工場も倉庫から取りたくなる）
        f.stock.clear()
    held = {g: stock.total(island.warehouse, g) for g in island.warehouse}
    Market()._supply_facilities_from_warehouse(island, context(game))
    after = {g: stock.total(island.warehouse, g) for g in held}
    assert after["coal"] < held["coal"]  # 発電所の燃料は使う
    for good in ("wood", "steel", "machinery", "building_materials"):
        assert after[good] == held[good], good
    target = reserve.target(island, game.defs, game.config)
    assert after["grain"] >= min(held["grain"], target)
