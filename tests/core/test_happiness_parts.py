"""幸福度の内訳（W-172）: 項目ごとの点を記録しても、幸福度の値は前と同じ。内訳を足すと幸福度。"""

from __future__ import annotations

import copy
import random

from hakoniwa_core import facilities as fac
from hakoniwa_core import politics
from hakoniwa_core.economy.stats import inflation, real_wage_change
from hakoniwa_core.modules.population import (
    HAPPINESS_PARTS,
    HUNGER,
    SETTLE_PARTS,
    _add_parts,
    happiness,
    happiness_parts,
    happiness_weights,
    settle_detail,
)
from tests.core.test_finance import context, island_of, run
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401


def _before_w172(island, facts, ctx) -> int:
    """W-172 の前の幸福度の式（そのまま写した物）。"""
    w = happiness_weights(island, ctx)
    value = (
        w["base"]
        + w["food"] * facts["food"] / fac.FULL
        + w["jobs"] * facts["jobs"] / fac.FULL
        + w["power"] * facts["power"] / fac.FULL
        + w["food_quality"] * facts["quality"]
        + w.get("consumer_goods", 0) * facts.get("consumer_goods", 0) / fac.FULL
        + w.get("clothing", 0) * facts.get("clothing", 0) / fac.FULL
        + w.get("luxury", 0) * facts.get("luxury", 0) / fac.FULL
        + w.get("water", 0) * facts.get("water", 0) / fac.FULL
        + w.get("education", 0) * facts.get("education", 0) / fac.FULL
        + w.get("housing", 0) * facts.get("housing", fac.FULL) / fac.FULL
        - w["pollution_per_point"] * facts["pollution"]
    )
    fall = -real_wage_change(island.economy) * 100
    if fall > 0:
        value -= min(w["real_wage_max"], w["real_wage_per_point"] * fall)
    over = (inflation(island.economy) - w["inflation_threshold"]) * 100
    if over > 0:
        value -= min(w["inflation_max"], w["inflation_per_point"] * over)
    value -= min(w.get("hunger_max", 0), w.get("hunger", 0) * facts.get(HUNGER, 0.0))
    value -= w.get("arrears", 0) * facts.get("arrears", 0.0)
    value -= min(w.get("crowding_max", 0), w.get("crowding", 0) * facts.get("crowding", 0.0))
    return max(0, min(politics.happiness_cap(island, ctx.defs), round(value)))


def _facts(rng: random.Random) -> dict:
    return {
        "food": rng.randint(0, fac.FULL),
        "jobs": rng.randint(0, fac.FULL),
        "power": rng.randint(0, fac.FULL),
        "quality": rng.random(),
        "consumer_goods": rng.randint(0, fac.FULL),
        "clothing": rng.randint(0, fac.FULL),
        "luxury": rng.randint(0, fac.FULL),
        "water": rng.randint(0, fac.FULL),
        "education": rng.randint(0, fac.FULL),
        "housing": rng.randint(0, fac.FULL),
        "pollution": rng.choice([0, rng.randint(0, 40), rng.random() * 40]),
        HUNGER: rng.choice([0.0, rng.random() * 4]),
        "arrears": rng.choice([0.0, rng.random()]),
        "crowding": rng.choice([0.0, rng.random() * 3]),
    }


def test_parts_give_the_same_happiness_as_before(game, warmed):
    world = copy.deepcopy(warmed)
    ctx = context(game, world, "population")
    rng = random.Random(172)
    for economy in ("market", "mixed", "planned"):
        island = island_of(world, economy)
        for regime in (island.regime.get("politics"), "totalitarian"):
            island.regime["politics"] = regime
            for _ in range(700):
                facts = _facts(rng)
                parts = happiness_parts(island, facts, ctx)
                assert len(parts) == len(HAPPINESS_PARTS)
                assert happiness(island, facts, ctx) == _before_w172(island, facts, ctx)


def test_settle_detail_splits_cap_floor_and_rounding(game, warmed):
    world = copy.deepcopy(warmed)
    ctx = context(game, world, "population")
    island = island_of(world, "planned")
    island.regime["politics"] = "totalitarian"  # 上限 80
    zero = (0.0,) * (len(HAPPINESS_PARTS) - 2)
    value, (cap, floor, rounding) = settle_detail(island, (60.0, 25.4, *zero), ctx)
    assert value == 80 and cap == -5 and floor == 0 and abs(rounding + 0.4) < 1e-9
    value, (cap, floor, rounding) = settle_detail(island, (10.0, -13.4, *zero), ctx)
    assert value == 0 and cap == 0 and floor == 3 and abs(rounding - 0.4) < 1e-9
    # 丸めの境目: 52.5 は偶数の 52 に丸まる（round）。端数は -0.5
    value, (cap, floor, rounding) = settle_detail(island, (50.0, 2.5, *zero), ctx)
    assert value == 52 and cap == 0 and floor == 0 and rounding == -0.5
    # 上限のマスと下限のマスが混ざっても、収めた分は打ち消し合わずに別々に残る
    sums: dict[str, list[float]] = {}
    for parts in ((60.0, 25.0, *zero), (10.0, -15.0, *zero)):
        value, extra = settle_detail(island, parts, ctx)
        _add_parts(sums, "general", (*parts, *extra), 1)
    keys = (*HAPPINESS_PARTS, *SETTLE_PARTS)
    total = dict(zip(keys, sums["general"], strict=True))
    assert total["cap"] == -5 and total["floor"] == 5
    assert sum(sums["general"]) == 80 + 0


def test_recorded_parts_add_up_to_happiness(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "planned")
    island.regime["politics"] = "totalitarian"
    run(game, world, 2)
    for island in world.islands.values():
        stats = island.stats
        parts = {k: v for k, v in stats.items() if k.startswith("happiness_part.")}
        assert parts, island.id
        assert abs(sum(parts.values()) - stats["happiness"]) < 1e-6
        known = {f"happiness_part.{k}" for k in (*HAPPINESS_PARTS, *SETTLE_PARTS)}
        assert set(parts) <= known
