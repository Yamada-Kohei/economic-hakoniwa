from __future__ import annotations

import random

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.founding import (
    FoundingError,
    FoundingPlan,
    auto_terrain,
    found_island,
    plan_problems,
    shape_problems,
)
from hakoniwa_core.game import load_game
from hakoniwa_core.geo import components
from hakoniwa_core.naming import facility_name
from hakoniwa_core.state import ACCOUNTING_SELF, ACCOUNTING_TREASURY, OWNER_PRIVATE, OWNER_STATE
from hakoniwa_core.state.generate import generate_land, random_plan


@pytest.fixture(scope="module")
def game():
    return load_game()


def square(x0: int, y0: int, w: int, h: int) -> set[tuple[int, int]]:
    return {(x, y) for y in range(y0, y0 + h) for x in range(x0, x0 + w)}


def plan_for(game, economy="planned", seed=5, **changes) -> FoundingPlan:
    rng = random.Random(seed)
    config = game.config
    land = generate_land(
        rng, config.get("island.size"), config.get("island.land_min"), config.get("island.land_max")
    )
    plan = random_plan(rng, "テスト国", config, game.defs, land)
    plan.regime["economy"] = economy
    plan.resources = ["coal", "iron_ore"]
    for key, value in changes.items():
        setattr(plan, key, value)
    return plan


def reasons(problems):
    return [reason for reason, _ in problems]


def test_shape_rules(game):
    config = game.config
    assert shape_problems(square(8, 8, 12, 12), config) == []
    assert "founding_edge" in reasons(shape_problems(square(0, 8, 12, 12), config))
    assert "founding_too_small" in reasons(shape_problems(square(8, 8, 5, 5), config))
    assert "founding_too_large" in reasons(shape_problems(square(1, 1, 30, 30), config))
    # 離れ小島も描ける（陸地は1つにつながっていなくてよい）
    apart = square(2, 2, 10, 10) | square(18, 18, 10, 10)
    assert shape_problems(apart, config) == []


def test_auto_terrain_is_deterministic_and_keeps_shape(game):
    land = square(8, 8, 14, 14)
    a = auto_terrain(land, game.config, 42)
    assert a == auto_terrain(land, game.config, 42)
    assert a != auto_terrain(land, game.config, 43)
    on_land = {pos: t for pos, t in a.items() if pos in land}
    assert all(game.defs.terrain[t]["land"] for t in on_land.values())
    assert sum(t == "mountain" for t in on_land.values()) == round(len(land) * 0.12)
    assert sum(t == "forest" for t in on_land.values()) == round(len(land) * 0.25)
    assert a[(7, 8)] == "shallows"  # 陸地に接する海
    assert a[(0, 0)] == "sea"


def test_auto_terrain_stays_put_when_land_is_added(game):
    """陸地を数マス描き足しても、島全体の森と山は入れ替わらない。"""
    land = square(8, 8, 14, 14)
    for seed in (7, 42, 1234):
        before = auto_terrain(land, game.config, seed)
        after = auto_terrain(land | {(22, 10), (22, 11), (22, 12)}, game.config, seed)
        changed = [p for p in land if before[p] != after[p]]
        assert len(changed) <= 5, (seed, changed)


def test_plan_problems(game):
    plan = plan_for(game)
    assert plan_problems(plan, game.defs, game.config) == []
    bad = plan_for(game, resources=["coal", "iron_ore", "stone"])
    assert "founding_too_many_resources" in reasons(plan_problems(bad, game.defs, game.config))
    bad = plan_for(game, resources=["grain"])
    assert "founding_unknown_resource" in reasons(plan_problems(bad, game.defs, game.config))
    bad = plan_for(game, names={"country": "国"})
    assert "founding_name_missing" in reasons(plan_problems(bad, game.defs, game.config))
    bad = plan_for(game, regime={"politics": "democracy"})
    assert "founding_regime_missing" in reasons(plan_problems(bad, game.defs, game.config))
    sea = plan_for(game, capital=(0, 0))
    assert reasons(plan_problems(sea, game.defs, game.config)) == ["founding_capital_not_land"]
    with pytest.raises(FoundingError):
        found_island(game.defs, game.config, sea, 1, 0, 0)


def test_start_island(game):
    plan = plan_for(game)
    island = found_island(game.defs, game.config, plan, 1, 3, 4)
    defs, config = game.defs, game.config
    assert island.name == "テスト国"
    assert island.names["capital"] == "テスト国市"
    assert island.capital == plan.capital
    assert island.facility_at(*plan.capital).kind == "government_office"
    homes = [f for f in island.facilities if f.kind == "housing"]
    towns = [h for h in homes if h.ext["level"] == "town"]
    assert len(towns) == 4
    assert all(island.tile(h.x, h.y).population == 3000 for h in towns)
    assert len(homes) - len(towns) == 8
    kinds = [f.kind for f in island.facilities]
    assert kinds.count("farm") == 3
    # 住民の買う消費財は、初めから木材で作る（鋼鉄がなくても動く）
    goods = [f for f in island.facilities if f.kind == "consumer_goods_factory"]
    assert [f.recipe for f in goods] == ["wood", "wood"]
    assert "mine" in kinds  # 石炭を選んだので石炭の鉱山が建つ
    assert "quarry" not in kinds  # 石材は選んでいない
    assert all(f.built for f in island.facilities)
    # 送電線で、マスを持つすべての施設と住宅が首都と同じ送電網につながっている
    placed = [f for f in island.facilities if fac.on_map(f)]
    nodes = {(f.x, f.y) for f in placed}
    nodes |= {(t.x, t.y) for t in island.tiles if t.power_line}
    grid = next(g for g in components(island, nodes) if island.capital in g)
    assert all((f.x, f.y) in grid for f in placed)
    # 倉庫の資材と、初めの施設の投入財
    assert island.warehouse["steel"][0][1] == 40_000
    plant = next(f for f in island.facilities if f.kind == "thermal_power_plant")
    assert stock.total(plant.stock, "coal") == 4_000
    assert all(fac.housing_capacity(defs, h) >= island.tile(h.x, h.y).population for h in homes)
    names = [facility_name(defs, config, island, f) for f in island.facilities if f.kind == "farm"]
    assert names[0].startswith("国営第一")


@pytest.mark.parametrize(
    ("economy", "state_kinds"),
    [
        ("planned", None),
        ("market", {"government_office"}),
        ("mixed", {"government_office", "thermal_power_plant", "mine"}),
    ],
)
def test_owner_follows_economy(game, economy, state_kinds):
    island = found_island(game.defs, game.config, plan_for(game, economy), 1, 0, 0)
    for f in island.facilities:
        if f.kind in ("housing", "bank", "transport"):  # 銀行と運送業は体制ごとの別の決まり
            continue
        public = game.defs.facilities[f.kind].get("public_service")  # 学校はいつも国営
        state = state_kinds is None or f.kind in state_kinds or public
        expected = OWNER_STATE if state else OWNER_PRIVATE
        assert f.owner == expected, f.kind
        if game.defs.facilities[f.kind].get("public_service"):
            assert f.accounting == ACCOUNTING_TREASURY  # 公務員の施設はいつも国庫直轄
        elif f.owner == OWNER_STATE:
            accounting = game.defs.regimes[economy]["state_accounting"]
            assert f.accounting == accounting
        else:
            assert f.accounting == ACCOUNTING_SELF
    # 銀行は体制の bank_owner の順に置く（混合経済は国営と民営の2つ）
    owners = game.defs.regimes[economy].get("bank_owner", OWNER_STATE)
    owners = [owners] if isinstance(owners, str) else list(owners)
    banks = [f for f in island.facilities if f.kind == "bank"]
    assert [b.owner for b in banks] == owners
    assert all(b.accounting == ACCOUNTING_SELF for b in banks)
    assert island.economy.banks == [b.id for b in banks]


def test_islet_can_be_founded(game):
    # 本島と、海を隔てた離れ小島
    plan = plan_for(game)
    islet = square(26, 26, 3, 3)
    plan.land = set(plan.land) | islet
    assert not (islet & set(generate_land(random.Random(5), 32, 120, 400)))
    island = found_island(game.defs, game.config, plan, 1, 0, 0)
    assert all(game.defs.terrain[island.tile(x, y).terrain]["land"] for x, y in islet)


def test_place_names_follow_style_and_capital(game):
    from hakoniwa_core.naming import place_of

    plan = plan_for(game, place_style="german")
    island = found_island(game.defs, game.config, plan, 1, 0, 0)
    assert island.ext["place_style"] == "german"
    # 首都のある区画の地名は首都名
    assert place_of(island, *plan.capital, game.config) == "テスト国市"
    german = game.defs.naming["ja"]["places"]["german"]
    others = [p for p in island.ext["places"] if p != "テスト国市"]
    assert others and all(any(p.startswith(a) for a in german["first"]) for p in others)
    bad = plan_for(game, place_style="martian")
    assert "founding_unknown_place_style" in reasons(plan_problems(bad, game.defs, game.config))


def test_housing_has_no_name_and_admin_has_no_place(game):
    island = found_island(game.defs, game.config, plan_for(game, "market"), 1, 0, 0)
    home = next(f for f in island.facilities if f.kind == "housing")
    level = home.ext["level"]
    assert facility_name(game.defs, game.config, island, home) == game.defs.name(
        "settlements", level
    )
    office = island.facility_at(*island.capital)
    assert facility_name(game.defs, game.config, island, office) == "中央省庁"
