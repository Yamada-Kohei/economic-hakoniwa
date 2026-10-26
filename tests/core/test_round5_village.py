"""回5 後半: 住む所があふれた人を消さずに詰めて住ませ（過密）、近くの平地に村を開く（分家と開拓。
10-19 のカード「分家と開拓」）。"""

from __future__ import annotations

from hakoniwa_core import stock
from hakoniwa_core.modules.migration import _room
from hakoniwa_core.modules.population import (
    Population,
    crowding,
    happiness,
    homes_of,
    overcrowded,
    village_size,
)
from hakoniwa_core.state import WorldState
from tests.core.test_finance import context
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import home, make_island, with_economy
from tests.core.test_rules import run as run_stages


def _full_island(game):
    """村が1つと町が1つ、どちらも住める上限まで住んでいる島。"""
    island = make_island(game)
    village = game.defs.settlements["village"]["capacity"]
    town = game.defs.settlements["town"]["capacity"]
    home(game, island, 4, 4, village, "village")
    home(game, island, 9, 9, town, "town")
    island.regime["economy"] = "market"  # 計画経済は国が建てる（国の住宅は test_public_housing.py）
    with_economy(game, island)
    return island


def test_crowding_grows_without_a_ceiling():
    assert crowding(100, 100) == 0.0
    assert crowding(150, 100) == 0.5
    assert crowding(300, 100) == 2.0  # 頭打ちにしない（詰めるほど強く効いて、どこかで釣り合う）
    assert crowding(10, 0) == 1.0  # 住む所のないマス


def test_births_are_kept_when_every_home_is_full(game):
    """住む所の空きがどこにもなくても、生まれた人は消えずに詰めて住む（前は生まれなかった）。"""
    island = _full_island(game)
    island.tile(9, 9).population += 200  # 町が過密
    before = sum(t.population for t in island.tiles)
    run_stages(game, island, ["population"])  # 市場の段がないので2ターン目からは飢える
    stats = island.stats
    after = sum(t.population for t in island.tiles)
    assert stats["population.births"] > 0
    lost = stats["population.deaths"] + stats.get("population.starved", 0)
    assert after == before + stats["population.births"] - lost
    assert "population.births_unhoused" not in stats
    homes = homes_of(island, game.config)
    assert stats["population.overcrowded"] == overcrowded(game.defs, island, homes) > 0
    assert not any(e.kind == "residents_left" for e in island.log)
    # 自給の村は、人数で町と見られて自給をやめる所までは詰めない（あふれた分は町へ出る）
    assert island.tile(4, 4).population <= village_size(game.defs)


def test_overflow_beyond_a_village_goes_to_the_nearest_town(game):
    island = _full_island(game)
    homes = homes_of(island, game.config)
    island.tile(4, 4).population = village_size(game.defs) - 10
    free = {pos: 0 for pos in homes}
    Population._crowd_in(game.defs, island, homes, free, (4, 4), 50)
    assert island.tile(4, 4).population == village_size(game.defs)
    assert island.tile(9, 9).population == game.defs.settlements["town"]["capacity"] + 40


def test_overcrowded_island_opens_a_new_village_with_wood(game):
    """島の上限を超えて住む人が空きより多ければ、いちばん過密なマスの近くの平地に村を開く。資材が
    なければ開かず、次のターンに買う資材を注文する。"""
    island = _full_island(game)
    island.tile(9, 9).population += 3000  # 町が過密
    run_stages(game, island, ["housing"])
    assert not any(e.kind == "village_founded" for e in island.log)
    need = game.defs.settlements["village"]["materials"]
    assert island.economy.housing["want"] == need
    held = island.economy.housing["stock"]
    for good, qty in need.items():  # 傷んだ家の手入れに先に使う分も
        stock.add(held, good, qty * 2, 0, game.defs.goods)
    run_stages(game, island, ["housing"])
    founded = [e for e in island.log if e.kind == "village_founded"]
    assert len(founded) == 1
    x, y = founded[0].args["x"], founded[0].args["y"]
    radius = game.config.get("housing.settle_radius")
    assert max(abs(x - 9), abs(y - 9)) <= radius
    assert homes_of(island, game.config)[(x, y)].ext["level"] == "village"


def test_no_village_when_the_island_still_has_room(game):
    island = _full_island(game)
    island.tile(9, 9).population += 500
    island.tile(4, 4).population = 1000  # 村に 1,400 の空き
    held = island.economy.housing["stock"]
    for good, qty in game.defs.settlements["village"]["materials"].items():
        stock.add(held, good, qty, 0, game.defs.goods)
    run_stages(game, island, ["housing"])
    assert not any(e.kind == "village_founded" for e in island.log)


def test_crowding_lowers_happiness_and_immigration_room(game):
    island = _full_island(game)
    facts = {
        "food": 1000,
        "jobs": 1000,
        "power": 1000,
        "quality": 1.0,
        "pollution": 0,
        "water": 1000,
        "education": 1000,
    }
    ctx = context(game, WorldState(turn=0, seed=1, islands={1: island}))
    calm = happiness(island, facts, ctx)
    crowded = happiness(island, {**facts, "crowding": 1.0}, ctx)
    assert calm - crowded == game.config.get("population.happiness")["crowding"]
    # 移民の受け入れの空きは、過密の町の分を引いた島全体の空き
    island.tile(4, 4).population -= 100
    island.tile(9, 9).population += 300
    assert _room(island, ctx) == 0
