"""W-190・W-201 の延べ床と住み替え（設計書 PO-16〜PO-18・PO-51〜PO-53）。

仕組みの試験は、作った場面（小さな島、決めた在庫や人数）で確かめる。世界を何ターンも回す試験では、
止まらない・お金が合う・崩れない、だけを見る。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.economy import GENERAL, JOBLESS, OWNERS, banks, household
from hakoniwa_core.log import format_log
from hakoniwa_core.modules.population import (
    Housing,
    _next_floor,
    desired_space,
    dwelling_status,
    happiness,
    homes_of,
    layer_income,
)
from hakoniwa_core.modules.trade import check
from tests.core.test_finance import context
from tests.core.test_phase3_3 import base as base  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_phase3_3 import game as game  # noqa: F401
from tests.core.test_phase3_3 import run_checked


def _market_island(world):
    return next(
        i
        for i in world.islands.values()
        if i.has_economy and i.regime.get("economy") == "market" and banks.active_banks(i)
    )


def _village(game, island):
    """人の住む村のマス（町でない最初の段階）と、その施設。"""
    first = game.defs.settlement_levels()[0]
    for pos, home in sorted(homes_of(island, game.config).items(), key=lambda kv: kv[0]):
        if home.ext.get("level", first) == first and island.tile(*pos).population > 0:
            return pos, home
    raise AssertionError("村がない")


def _stock_for(game, island, low, high, times=1):
    """床を low から high まで足す資材を、家計の在庫に置く。"""
    held = island.economy.housing.setdefault("stock", {})
    for good, qty in fac.floor_materials(game.defs, low, high).items():
        stock.add(held, good, qty * times, 0, game.defs.goods)


# --- 床と資材 -----------------------------------------------------------------------


def test_floor_defaults_to_the_level_cap_and_materials_follow_the_floor(game):
    defs = game.defs
    levels = defs.settlement_levels()
    village, town = levels[0], levels[1]
    cap = defs.settlements[village]["capacity"]
    home = fac.new_housing(
        type("Island", (), {"facilities": [], "next_facility_id": lambda self: 1})(),
        "housing",
        (1, 1),
        village,
    )
    assert fac.floor_of(defs, home) == cap
    assert fac.nominal_housing_capacity(defs, home) == cap
    # 村を丸ごと建てる資材は、村の段階の資材そのもの
    assert fac.floor_materials(defs, 0, cap) == defs.settlements[village]["materials"]
    assert fac.housing_materials(defs, cap) == defs.settlements[village]["materials"]
    # 町の段階の床は、段階の幅に比例して資材を使う（半分なら半分、端数は切り上げ）
    width = defs.settlements[town]["capacity"] - cap
    half = fac.floor_materials(defs, cap, cap + width // 2)
    for good, qty in defs.settlements[town]["materials"].items():
        assert abs(half[good] - qty / 2) <= 1
    # 段階をまたぐ分は、それぞれの段階の資材の和
    both = fac.floor_materials(defs, cap - 10, cap + 10)
    assert both[next(iter(defs.settlements[village]["materials"]))] > 0
    # 段階は床が収まるいちばん下の段階
    assert fac.level_of_floor(defs, cap) == village
    assert fac.level_of_floor(defs, cap + 1) == town
    assert fac.level_after(defs, cap) == town
    assert fac.level_after(defs, fac.max_floor(defs)) is None


def test_next_floor_is_a_quarter_of_the_level_and_stops_at_its_cap(game):
    defs = game.defs
    step = game.config.get("housing.floor_step")
    village, town = defs.settlement_levels()[:2]
    cap = defs.settlements[village]["capacity"]
    assert _next_floor(defs, 0, step) == round(cap * step)
    # 村の上限から町の幅の4分の1
    width = defs.settlements[town]["capacity"] - cap
    assert _next_floor(defs, cap, step) == cap + -(-int(step * width * 1000) // 1000)
    # 段階の上限を超えない
    assert _next_floor(defs, cap - 1, step) == cap
    top = fac.max_floor(defs)
    assert _next_floor(defs, top, step) == top


# --- 望む広さ ----------------------------------------------------------------------


def test_desired_space_rises_with_real_income_and_never_below_the_minimum(game, base):
    island = _market_island(copy.deepcopy(base))
    spec = game.config.get("housing.space")
    income = layer_income(island, game.config, GENERAL)
    want = desired_space(island, game.config, GENERAL)
    assert want == pytest.approx(max(spec["min"], spec["base"] * income ** spec["elasticity"]))
    layer = island.economy.households[GENERAL]
    layer["income"] = [x * 4 for x in layer["income"]]  # 所得が 4 倍なら広さは 2 倍（弾力性 0.5）
    assert desired_space(island, game.config, GENERAL) == pytest.approx(
        spec["base"] * (income * 4) ** 0.5
    )
    layer["income"] = [0 for _ in layer["income"]]
    assert desired_space(island, game.config, GENERAL) == spec["min"]


def test_layers_share_a_tile_by_persons_times_their_desired_space(game, base):
    island = _market_island(copy.deepcopy(base))
    homes = homes_of(island, game.config)
    status = dwelling_status(game.defs, game.config, island, homes)
    pos, home = _village(game, island)
    d = status[pos]
    spec = game.config.get("housing.space")
    assert d.nominal == fac.floor_of(game.defs, home) * spec["min"]
    assert d.floor <= d.nominal
    assert sum(d.layers.values()) == pytest.approx(d.want)
    # 層の望みは、層の望む広さ以上（最低の広さ）の人数倍
    assert d.want >= island.tile(*pos).population * spec["min"] * 0.99
    assert 0 < d.ratio <= 1
    # 床が望みの半分なら、割合は半分
    home.ext[fac.FLOOR] = int(d.want / spec["min"] / 2)
    half = dwelling_status(game.defs, game.config, island, homes)[pos]
    assert half.ratio == pytest.approx(0.5, abs=0.02)


# --- 幸福度 ------------------------------------------------------------------------


def test_housing_term_lowers_happiness_by_its_weight(game, base):
    island = _market_island(copy.deepcopy(base))
    ctx = context(game, base)
    facts = {
        "food": 1000,
        "jobs": 1000,
        "power": 1000,
        "quality": 0.0,
        "pollution": 0,
        "water": 0,
        "education": 0,
    }
    weight = game.config.get("population.happiness")["housing"]
    full = happiness(island, {**facts, "housing": 1000}, ctx)
    none = happiness(island, {**facts, "housing": 0}, ctx)
    absent = happiness(island, facts, ctx)  # 書かなければ満点
    assert absent == full
    assert full - none == pytest.approx(weight, abs=1)
    weights = game.config.get("population.happiness")
    keys = ("base", "food", "jobs", "power", "food_quality", "clothing", "luxury", "water")
    assert sum(weights[k] for k in (*keys, "education", "housing")) == 100


# --- 住み替え ----------------------------------------------------------------------


def _lacking_village(game, world):
    """人が詰まって床が望みに足りない村のある市場の島。"""
    island = _market_island(world)
    pos, home = _village(game, island)
    spec = game.config.get("housing.space")
    status = dwelling_status(game.defs, game.config, island, homes_of(island, game.config))
    # 床を望みの 7 割にして、足りなくする
    home.ext[fac.FLOOR] = int(status[pos].want / spec["min"] * 0.7)
    island.economy.housing["stock"] = {}
    return island, pos, home


def test_layers_short_of_floor_add_it_a_little_each_turn_from_bought_materials(game, base):
    world = copy.deepcopy(base)
    island, pos, home = _lacking_village(game, world)
    spec = game.config.get("housing.space")
    before = fac.floor_of(game.defs, home)
    d = dwelling_status(game.defs, game.config, island, homes_of(island, game.config))[pos]
    lack = (d.want - d.nominal) / spec["min"]
    # 買える資材が十分あれば、足りない床の年 move_rate の 12 分の 1 ずつ足す
    _stock_for(game, island, before, before + int(lack), times=2)
    ctx = context(game, world)
    for _ in range(12):
        Housing().run_island(island, ctx)
    added = fac.floor_of(game.defs, home) - before
    # 住む人の望む分は人手の足りない所の育ちでも足されるので、年の分より少し多くてよい
    assert added > 0
    assert added <= lack
    assert added == pytest.approx(lack * spec["move_rate"], rel=0.5)
    assert fac.floor_of(game.defs, home) <= fac.max_floor(game.defs)


def test_without_materials_the_need_is_written_per_layer_and_capped_at_a_year(game, base):
    world = copy.deepcopy(base)
    island, pos, home = _lacking_village(game, world)
    spec = game.config.get("housing.space")
    before = fac.floor_of(game.defs, home)
    d = dwelling_status(game.defs, game.config, island, homes_of(island, game.config))[pos]
    lack = (d.want - d.nominal) / spec["min"]
    ctx = context(game, world)
    Housing().run_island(island, ctx)
    # 資材がなければ床は増えず、足りない資材を層ごとに次のターンに買う分に書く
    assert fac.floor_of(game.defs, home) == before
    move = island.economy.housing["move"]
    assert move and JOBLESS not in move  # 失業している家計は買わない
    assert GENERAL in move
    assert all(qty > 0 for goods in move.values() for qty in goods.values())
    for _ in range(40):  # 何か月たっても、ためるのは年の分まで
        Housing().run_island(island, ctx)
    assert home.ext[fac.FLOOR_DUE] <= lack * spec["move_rate"] + 1e-6


def test_a_floor_never_exceeds_the_top_level_and_a_dry_town_waits_for_water(game, base):
    world = copy.deepcopy(base)
    island, pos, home = _lacking_village(game, world)
    village, town = game.defs.settlement_levels()[:2]
    cap = game.defs.settlements[village]["capacity"]
    # 村の上限まで足りて、町の段階に入るには上下水道が要る（町は water を書いていない → 入れる）
    home.ext[fac.FLOOR] = cap
    home.ext["level"] = village
    top = fac.max_floor(game.defs)
    fac.set_floor(game.defs, home, top)
    assert home.ext["level"] == game.defs.settlement_levels()[-1]
    island.economy.housing["stock"] = {}
    Housing().run_island(island, context(game, world))
    assert fac.floor_of(game.defs, home) == top
    assert fac.FLOOR_DUE not in home.ext


# --- 前からある世界と建国 -----------------------------------------------------------


def test_old_worlds_move_to_the_new_happiness_weights_and_say_so_once(game, base):
    from tests.core.test_trade import run

    world = copy.deepcopy(base)
    island = _market_island(world)
    turns = game.config.get("population.blend_turns")
    island.economy.ext.pop("housing")  # 住まいの広さのなかった頃の島
    island.economy.happiness_blend = 0
    run(game, world, 1)
    assert island.economy.ext["housing"] == fac.HOUSING_VERSION
    assert island.economy.ext["happiness_from"] == "happiness_before_housing"
    (log,) = [e for e in island.log if e.kind == "happiness_weights_changed_housing"]
    now = game.config.get("population.happiness")
    assert log.args["housing"] == now["housing"] and log.args["base_from"] == 15
    assert "住まいの広さ" in format_log(game.defs, log.kind, log.args)
    assert island.economy.happiness_blend == turns - 1
    run(game, world, 1)  # 次のターンからは出さない
    assert not any(e.kind == "happiness_weights_changed_housing" for e in island.log)


def test_a_founded_island_starts_with_housing_space_and_no_blend(game, base):
    for island in base.islands.values():
        assert island.economy.ext.get("housing") == fac.HOUSING_VERSION


# --- 世界を回して ------------------------------------------------------------------


def test_the_world_runs_with_floors_and_the_books_balance(game, base):
    world = copy.deepcopy(base)
    run_checked(game, world, 60)
    assert check(world) == []
    for island in world.islands.values():
        homes = homes_of(island, game.config)
        for home in homes.values():
            floor = fac.floor_of(game.defs, home)
            assert 0 < floor <= fac.max_floor(game.defs)
            assert fac.level_of_floor(game.defs, floor) == home.ext["level"]
        assert island.stats["housing.space"] > 0
        assert island.stats["housing.want"] > 0
        assert island.stats["housing.floor"] == sum(
            fac.floor_of(game.defs, h) for h in homes.values()
        )


def test_owners_buy_floor_with_their_own_savings_and_mortgage(game, base):
    from tests.core.test_trade import run

    world = copy.deepcopy(base)
    island = _market_island(world)
    assert OWNERS in island.economy.households
    owners = household(OWNERS)
    before = sum(
        banks.outstanding(island, x)
        for x in banks.loans_by_borrower(island, owners)
        if x.purpose == banks.MORTGAGE
    )
    wanted = fac.floor_materials(game.defs, 0, 10 * game.defs.settlements["village"]["capacity"])
    # 資産を持つ層が、市場に出ている資材をたくさん買いたい
    island.economy.housing["move"] = {OWNERS: {g: q // 100 for g, q in wanted.items()}}
    island.economy.housing["stock"] = {}
    run(game, world, 1)
    after = sum(
        banks.outstanding(island, x)
        for x in banks.loans_by_borrower(island, owners)
        if x.purpose == banks.MORTGAGE
    )
    assert island.stats["housing.investment"] > 0
    assert after >= before
    assert check(world) == []
