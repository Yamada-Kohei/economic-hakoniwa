"""W-271・W-272 の切り替え（既定は切。GT-44）。

W-271（発電所が増えない）: 送電網で発電能力が足りなくても、発電所には売り逃しの合図が残らず
（firms.power_invest）、建て増しの出資が足りなくても利益は配当で出ていった（firms.invest_retain）。
W-272（失業の層の行き来）: 自給の農業ができる村かをマスの人数だけで毎ターン決めていたので、
町の下限の前後のマスの働き手が自給の農家と失業者を行き来した（population.farming_by_housing）。"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.economy import (
    INVEST_SHORT,
    companies,
    equipment,
    facility_entity,
    owner_entity,
)
from hakoniwa_core.economy.books import PROFIT
from hakoniwa_core.modules.accounts import Accounts
from hakoniwa_core.modules.firms import EntryExit
from hakoniwa_core.modules.market import MISSED
from hakoniwa_core.modules.population import FARMING, farming, refresh_farming, village_size
from hakoniwa_core.state import OWNER_PRIVATE
from tests.core.test_finance import context
from tests.core.test_phase3_3 import base as base  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_phase3_3 import game as game  # noqa: F401
from tests.core.test_rules import add, home, make_island, run, with_economy


@pytest.fixture
def switch(game, monkeypatch):  # noqa: F811
    def turn_on(section: str, key: str, value=True) -> None:
        monkeypatch.setitem(game.config.get(section), key, value)

    return turn_on


# --- 発電所の売り逃し（W-271。firms.power_invest） ---------------------------------------


def _grid(game, users):  # noqa: F811
    """民営（独立採算）の火力発電所 1 つと、電力を使う民営の機械工場 users 個の市場経済の島。"""
    island = make_island(game)
    island.regime["economy"] = "market"
    home(game, island, 6, 6, 30000, level="city")
    plant = add(
        island,
        "thermal_power_plant",
        5,
        6,
        stock={"coal": 40000},
        owner="private",
        accounting="self_supporting",
    )
    spots = [(6, 5), (7, 5), (7, 6), (7, 7), (6, 7), (5, 7), (5, 5), (8, 6), (8, 5), (8, 7)]
    for x, y in spots[:users]:
        add(
            island,
            "machinery_plant",
            x,
            y,
            stock={"steel": 4000},
            owner="private",
            accounting="self_supporting",
        )
    with_economy(game, island, groundwork=True)
    run(game, island, ["employment"])
    return island, plant


def test_power_plant_records_missed_sales_only_when_the_switch_is_on(game, switch):  # noqa: F811
    island, plant = _grid(game, 10)
    run(game, island, ["production"])
    assert island.stats["power.demand"] > island.stats["power.capacity"]
    assert MISSED not in plant.economy  # 切: 今までどおり合図は残らない

    switch("firms", "power_invest")
    island, plant = _grid(game, 10)
    run(game, island, ["production"])
    need, able = island.stats["power.demand"], island.stats["power.capacity"]
    assert plant.operating_rate == fac.FULL
    # 足りなかった能力（要る量 − 全力の能力）を全力の産出に対する 1000 分の 1 で
    (missed,) = plant.economy[MISSED]
    assert abs(missed - (need - able) / able * fac.FULL) <= 1


def test_power_plant_with_spare_capacity_records_no_missed_sales(game, switch):  # noqa: F811
    switch("firms", "power_invest")
    island, plant = _grid(game, 1)
    run(game, island, ["production"])
    assert island.stats["power.capacity"] > island.stats["power.demand"]
    assert plant.economy[MISSED] == [0]


# --- 建て増しのために利益を残す（W-271。firms.invest_retain） ------------------------------


def _private(world, kinds, firm):
    """市場か混合経済の島の民営の建った施設（firm なら企業に入ったもの、でなければ入らない物）。"""
    for island in world.islands.values():
        if not island.has_economy or island.regime.get("economy") == "planned":
            continue
        for f in sorted(island.facilities, key=lambda x: x.id):
            if f.kind in kinds and f.owner == OWNER_PRIVATE and f.built:
                if (companies.private_firm_of(island, f) is not None) == firm:
                    return island, f
    raise AssertionError(f"民営の {kinds} がない")


def _short_of_owners(game, base):  # noqa: F811
    """売り逃しが続いて建て増しが採算に合うのに、持ち主（資本家階級）に出資するお金がない工場。"""
    world = copy.deepcopy(base)
    island, f = _private(world, {"consumer_goods_factory"}, firm=True)
    f.economy.pop("entered", None)
    for key in (equipment.EQUIPMENT, equipment.CONDITION):
        f.economy.pop(key, None)
    island.economy.price_history["consumer_goods"] = [
        p * 3 for p in island.economy.price_history["consumer_goods"]
    ]
    owner = owner_entity(f)
    ledger.pay(island, owner, ledger.GOV, ledger.money(island, owner), "test")
    f.economy[MISSED] = [fac.FULL] * game.config.get("firms.invest_turns")
    return world, island, f


def test_short_owners_mark_the_shortfall_only_when_retaining(game, base, switch):  # noqa: F811
    world, island, f = _short_of_owners(game, base)
    EntryExit()._invest(island, context(game, world))
    assert fac.pending_expansions(island, f) == 0
    assert INVEST_SHORT not in f.economy  # 切: 足りない分は残さない

    switch("firms", "invest_retain")
    world, island, f = _short_of_owners(game, base)
    EntryExit()._invest(island, context(game, world))
    assert fac.pending_expansions(island, f) == 0
    assert f.economy[INVEST_SHORT] > 0


def test_retained_cash_pays_the_owners_share(game, base, switch):  # noqa: F811
    """施設に残した手元で出資が足りれば、持ち主へ上げて建て増す。切なら施設の手元は使わない。"""
    world, island, f = _short_of_owners(game, base)
    ledger.pay(island, ledger.GOV, facility_entity(f.id), 10**11, "subsidy")
    EntryExit()._invest(island, context(game, world))
    assert fac.pending_expansions(island, f) == 0

    switch("firms", "invest_retain")
    world, island, f = _short_of_owners(game, base)
    ledger.pay(island, ledger.GOV, facility_entity(f.id), 10**11, "subsidy")
    EntryExit()._invest(island, context(game, world))
    assert fac.pending_expansions(island, f) == 1
    assert INVEST_SHORT not in f.economy


def _dividend(game, base, short):  # noqa: F811
    world = copy.deepcopy(base)
    island, f = _private(world, {"services", "trading", "food_factory"}, firm=False)
    ledger.pay(island, ledger.GOV, facility_entity(f.id), 10**10, "subsidy")
    f.economy[INVEST_SHORT] = short
    profit = 10**9
    _, dividend = Accounts()._settle(island, context(game, world), f, {PROFIT: profit})
    return island, dividend


def test_dividend_holds_back_only_the_shortfall(game, base, switch):  # noqa: F811
    _, full = _dividend(game, base, 10**8)
    assert full > 10**8  # 切: 足りない分があっても配当は変わらない
    switch("firms", "invest_retain")
    island, held = _dividend(game, base, 10**8)
    assert held == full - 10**8
    assert island.stats["firms.invest_retained"] == 10**8
    _, more = _dividend(game, base, 10**12)  # 配当より多く足りなければ、配当は全部残す
    assert more == 0


# --- 自給の農業ができる村か（W-272。population.farming_by_housing） -------------------------


def _village(game, population):  # noqa: F811
    island = make_island(game)
    home(game, island, 6, 6, population, level="village")
    return island, island.tile(6, 6)


def test_village_housing_keeps_farming_above_the_town_size(game, switch):  # noqa: F811
    town = village_size(game.defs) + 1
    island, tile = _village(game, town + 10)
    refresh_farming(game.defs, game.config, island)
    assert FARMING not in tile.ext
    assert not farming(game.defs, tile)  # 切: 人数だけで町になり、自給の農業をしない

    switch("population", "farming_by_housing")
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is True
    assert farming(game.defs, tile)  # 建った住宅地はまだ村の段階


def test_tile_without_housing_switches_with_a_margin(game, switch):  # noqa: F811
    switch("population", "farming_by_housing")
    town = village_size(game.defs) + 1
    back = game.config.get("population.farming_back_ratio")
    island = make_island(game)
    tile = island.tile(6, 6)
    tile.population = town
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is False  # 初めは人数で（町）
    tile.population = town - 1
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is False  # 下限をわずかに切っただけでは町のまま
    tile.population = int(town * back) - 1
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is True  # 下限の 0.9 倍を切ると村
    tile.population = town - 1
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is True  # 戻るのは町の下限から
    tile.population = town
    refresh_farming(game.defs, game.config, island)
    assert tile.ext[FARMING] is False


def test_switching_off_clears_the_marks(game, switch):  # noqa: F811
    island, tile = _village(game, 100)
    tile.ext[FARMING] = False
    refresh_farming(game.defs, game.config, island)
    assert FARMING not in tile.ext
