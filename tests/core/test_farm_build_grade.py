"""段階の作り方で建てる農場は、初めからその段階で建つ（W-257、公開の世界の不具合の報告 #7）。"""

from __future__ import annotations

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import equipment
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.game import load_game
from hakoniwa_core.state import OrderState
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    world = generate_world(3, 3, game.config, game.defs)
    game.engine.run_turn(world)
    return world


def _free_farm_site(game, world, recipe):
    for island in world.islands.values():
        for tile in island.tiles:
            if tile is None or island.facility_at(tile.x, tile.y) is not None:
                continue
            if fac.placement_problem(game.defs, island, "farm", tile.x, tile.y, recipe) is None:
                return island, tile.x, tile.y
    raise AssertionError("農場を建てられるマスがない")


def _build(game, world, recipe):
    island, x, y = _free_farm_site(game, world, recipe)
    payload = {"facility": "farm", "recipe": recipe, "x": x, "y": y}
    island.orders = [OrderState(id=1, seq=1, kind="build", payload=payload)]
    game.engine.run_turn(world)
    assert island.orders[0].status == "done", island.orders[0].reason
    return island, island.facility_at(x, y)


def test_build_spec_adds_the_grade_works(game):
    defs = game.defs
    base = defs.facilities["farm"]["construction"]
    steps = defs.facilities["farm"]["grade_steps"]
    assert fac.build_spec(defs, "farm", "default") == (base["turns"], dict(base["materials"]))
    turns, materials = fac.build_spec(defs, "farm", "default_mechanized")
    assert turns == base["turns"] + steps[0]["turns"]
    for good, qty in steps[0]["materials"].items():
        assert materials[good] == base["materials"].get(good, 0) + qty
    turns, materials = fac.build_spec(defs, "farm", "fertilized_modern")
    assert turns == base["turns"] + steps[0]["turns"] + steps[1]["turns"]
    assert fac.build_grade(defs, "farm", "fertilized_modern") == 2
    # 段階のない施設は定義のまま
    assert fac.build_grade(defs, "mine", "coal") == 0


def test_mechanized_farm_is_built_mechanized_and_pays_for_the_machines(game, world):
    island, site = _build(game, world, "default_mechanized")
    assert not site.built
    assert fac.grade_of(site) == 1  # 段階は作り方にそろう
    turns, materials = fac.build_spec(game.defs, "farm", "default_mechanized")
    assert fac.materials_of(game.defs, site) == materials
    factor = planning.build_factor(island, game.defs)
    assert fac.total_progress(game.defs, site) == max(fac.FULL, round(turns * factor * fac.FULL))
    for _ in range(40):
        if site.built:
            break
        game.engine.run_turn(world)
    assert site.built and fac.grade_of(site) == 1
    assert site.recipe == "default_mechanized"
    # 機械の傷みと補修は機械化の農場の設備で見る
    kit = equipment.kit_of(game.defs, site)
    assert kit["machinery"] > 0
    assert equipment.machine_goods(game.defs, site)


def test_manual_farm_is_built_as_before(game, world):
    _, site = _build(game, world, "default")
    assert fac.GRADE not in site.ext
    assert fac.materials_of(game.defs, site) == dict(
        game.defs.facilities["farm"]["construction"]["materials"]
    )


def test_grade_build_mark_is_dropped_when_the_farm_is_done(game, world):
    island, site = _build(game, world, "default_mechanized")
    assert site.ext[fac.GRADE_BUILD] == 1
    for _ in range(40):
        if site.built:
            break
        game.engine.run_turn(world)
    assert site.built and fac.GRADE_BUILD not in site.ext


def test_farm_under_work_before_the_fix_keeps_the_manual_materials(game, world):
    # 直しの前から工事中だった農場（移行で段階だけそろえた。目印 grade_build なし）は手作業の資材
    _, site = _build(game, world, "default_mechanized")
    site.ext.pop(fac.GRADE_BUILD)
    assert fac.grade_of(site) == 1
    assert fac.materials_of(game.defs, site) == dict(
        game.defs.facilities["farm"]["construction"]["materials"]
    )
