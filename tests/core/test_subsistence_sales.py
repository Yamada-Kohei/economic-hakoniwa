"""村の自給の農家が、食べる分と最低限の買い物の分だけ作って売る（PO-63。population.subsistence_sales）。

会議 #3309・#3324: 小農は作れるだけ作って売るのではなく、要る分が満ちると働く量を減らす。作れる量は
農場の既定の作り方 × 肥沃度 × 気候で土地で頭打ち、売る量は食べ物以外の最低限（衣料の買い替え）の
代金の分。
"""

from __future__ import annotations

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import nature
from hakoniwa_core.modules.population import (
    FARM_CAPACITY,
    settle_farm_capacity,
    subsistence_offer,
    subsistence_persons,
    village_size,
)
from hakoniwa_core.units import to_milli
from tests.core.test_phase3_3 import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import home, make_island


@pytest.fixture
def sales(game, monkeypatch):  # noqa: F811
    monkeypatch.setitem(game.config.get("population"), "subsistence_sales", True)


def _village(game):  # noqa: F811
    island = make_island(game)
    island.economy.started = 0  # お金の経済が始まった島
    home(game, island, 6, 6, village_size(game.defs) // 2, level="village")
    island.economy.prices["grain"] = 20_000
    island.economy.prices["clothing"] = 300_000
    return island, island.tile(6, 6)


def test_no_sales_when_off(game):  # noqa: F811
    island, tile = _village(game)
    tile.ext[FARM_CAPACITY] = 999
    settle_farm_capacity(game.defs, game.config, island)
    assert FARM_CAPACITY not in tile.ext
    assert subsistence_offer(game.defs, game.config, island) == 0


def test_a_village_grows_up_to_one_farm(game, sales):  # noqa: F811
    """村の作れる量は、農場の既定の作り方 × 肥沃度 × 気候 × min(1, 働き手 ÷ 農場の働き手)。"""
    island, tile = _village(game)
    recipe = game.defs.facilities["farm"]["recipes"]["default"]
    out = recipe["outputs"]["grain"] * nature.recipe_multiplier(
        game.defs, game.config, island, recipe
    )
    workers = fac.workers_needed(game.defs, "farm")
    share = fac.labor_share(game.defs, island)
    settle_farm_capacity(game.defs, game.config, island)
    farmers = int(subsistence_persons(game.defs, island, tile, share) * share)
    fert = nature.fertility(tile) / fac.FULL
    assert tile.ext[FARM_CAPACITY] == int(out * min(1.0, farmers / workers) * fert)
    tile.ext["employed"] = fac.labor_force(game.defs, island, tile, share) - workers // 2
    settle_farm_capacity(game.defs, game.config, island)
    assert tile.ext[FARM_CAPACITY] == int(out * 0.5 * fert)  # 働き手が農場の半分なら半分


def test_towns_do_not_farm(game, sales):  # noqa: F811
    """町のマスには作れる量を書かない（町の畑 PO-62 は採らなかった）。"""
    island = make_island(game)
    island.economy.started = 0
    home(game, island, 6, 6, village_size(game.defs) + 10_000, level="town")
    settle_farm_capacity(game.defs, game.config, island)
    assert FARM_CAPACITY not in island.tile(6, 6).ext


def test_farmers_sell_only_what_buys_the_minimum(game, sales):  # noqa: F811
    """売る量 = min(作れる量 − 食べる量, 人数 × 衣料の買い替えの代金 ÷ 穀物の値段)。"""
    island, tile = _village(game)
    share = fac.labor_share(game.defs, island)
    persons = subsistence_persons(game.defs, island, tile, share)
    hh = game.config.get("household")
    clothes = hh["targets"]["clothing"] * hh["clothing"]["wear"] / 12
    cash = to_milli(persons * clothes) * 300_000 // 20_000
    need = to_milli(persons * game.config.get("population.food_per_person"))
    tile.ext[FARM_CAPACITY] = need + 10 * cash  # 余りは十分
    assert subsistence_offer(game.defs, game.config, island) == cash
    tile.ext[FARM_CAPACITY] = need + cash // 2  # 余りが少なければ余りだけ
    assert subsistence_offer(game.defs, game.config, island) == cash // 2
    tile.ext[FARM_CAPACITY] = need // 2  # 食べる量に届かなければ売らない
    assert subsistence_offer(game.defs, game.config, island) == 0
    tile.ext[FARM_CAPACITY] = need + cash
    island.economy.prices.pop("clothing")
    assert subsistence_offer(game.defs, game.config, island) == 0  # 値段がなければ売らない


def test_the_world_runs_with_sales(game, sales):  # noqa: F811
    """入れて回しても止まらず（帳簿は記録の段が確かめる）、売れた量は出した量以下で、GDP の生産の
    側と支出の側が合う（家計が作って売った分を生産の側に数える）。"""
    from hakoniwa_core.state.generate import generate_world
    from tests.core.test_finance import run

    world = generate_world(1, 3, game.config, game.defs)
    offered = sold = 0.0
    for _ in range(4):
        run(game, world, 1)
        for island in world.islands.values():
            offered += island.stats.get("subsistence.offered", 0)
            sold += island.stats.get("subsistence.sold", 0) / 1000
            if island.has_economy:
                assert island.stats["gdp"] == island.stats["gdp.spending"]
    assert offered > 0 and 0 < sold <= offered + 1
