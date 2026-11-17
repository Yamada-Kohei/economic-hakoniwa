"""W-286 の強制移住の命令（force_relocate）: 体制の縛り、過密の分だけ、運ぶ費用、移る間、政情。"""

from __future__ import annotations

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import freight as freight_rates
from hakoniwa_core.game import load_game
from hakoniwa_core.modules.politics import events_bonus
from hakoniwa_core.modules.population import homes_of, relocation, village_size
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.state import OrderState
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.units import BASIS, part, to_basis
from tests.core.test_finance import books_ok, context, open_books


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def world(game):
    world = generate_world(3, 3, game.config, game.defs)
    game.engine.run_turn(world)
    return world


def _setup(game, world, politics="authoritarian"):
    """市場経済の島を politics の体制にし、町を 1 つ過密にする。(島, 過密の町, ほかの町, 村)。"""
    island = next(i for i in world.islands.values() if i.regime["economy"] == "market")
    island.regime["politics"] = politics
    homes = homes_of(island, game.config)
    first = game.defs.settlement_levels()[0]
    towns = sorted(p for p, h in homes.items() if h.ext.get("level", first) != first)
    villages = sorted(p for p, h in homes.items() if h.ext.get("level", first) == first)
    source, other = towns[0], towns[-1]
    cap = fac.housing_capacity(game.defs, homes[source])
    island.tile(*source).population = cap + 5000
    return island, source, other, villages[0]


def _payload(source, target, count):
    return {"x": source[0], "y": source[1], "to_x": target[0], "to_y": target[1], "count": count}


def test_only_authoritarian_and_totalitarian_regimes_can_order_it(game, world):
    island, source, other, _ = _setup(game, world, politics="democracy")
    with pytest.raises(OrderFailed) as e:
        relocation.check_force_relocate(island, _payload(source, other, 10), game.defs)
    assert e.value.reason == "reason_relocation_regime"
    for name in ("authoritarian", "totalitarian"):
        island.regime["politics"] = name
        relocation.check_force_relocate(island, _payload(source, other, 10), game.defs)


def test_moves_only_the_crowded_part_pays_the_carrier_and_arrives_at_the_population_stage(
    game, world
):
    island, source, other, _ = _setup(game, world)
    ctx = context(game, world)
    open_books(island)
    before_target = island.tile(*other).population
    steps = freight_rates.Routes(island, game.defs).between(source, other) / freight_rates.STEP
    rate = min(
        to_basis(game.config.get("freight.max_rate")),
        round(to_basis(game.config.get("freight.per_step")) * steps),
    )
    paid_before = island.economy.freight.get("paid", 0)
    # 過密の分（5000 人）より多く頼んでも、過密の分だけ移す
    relocation.force_relocate(island, _payload(source, other, 9000), ctx)
    cap = fac.housing_capacity(game.defs, homes_of(island, game.config)[source])
    assert island.tile(*source).population == cap
    assert island.tile(*other).population == before_target  # 移る月は、どのマスにもいない
    assert relocation.in_transit(island) == 5000
    cost = part(5000 * island.economy.average_wage, rate)
    assert cost > 0
    surcharge = island.economy.freight.get("surcharge", BASIS) or BASIS
    assert island.economy.freight["paid"] - paid_before == cost * surcharge // BASIS
    books_ok(game, island)
    assert relocation.arrive(island) == 5000
    assert island.tile(*other).population == before_target + 5000
    assert relocation.in_transit(island) == 0
    # もう過密でないので、次の命令は断る
    with pytest.raises(OrderFailed) as e:
        relocation.plan(game.defs, game.config, island, _payload(source, other, 1))
    assert e.value.reason == "reason_relocation_not_crowded"


def test_refused_without_a_town_at_the_destination_or_enough_treasury(game, world, monkeypatch):
    island, source, other, _ = _setup(game, world)
    empty = next(
        (t.x, t.y)
        for t in island.tiles
        if (t.x, t.y) not in homes_of(island, game.config) and 0 < t.x and 0 < t.y
    )
    with pytest.raises(OrderFailed) as e:
        relocation.plan(game.defs, game.config, island, _payload(source, empty, 10))
    assert e.value.reason == "reason_relocation_destination"
    with pytest.raises(OrderFailed) as e:
        relocation.plan(game.defs, game.config, island, _payload(source, source, 10))
    assert e.value.reason == "reason_relocation_same"
    monkeypatch.setattr(relocation.funds, "available", lambda *a, **k: 0)
    with pytest.raises(OrderFailed) as e:
        relocation.plan(game.defs, game.config, island, _payload(source, other, 10))
    assert e.value.reason == "reason_treasury_short"


def test_a_farming_village_takes_only_up_to_the_village_size(game, world):
    island, source, _, village = _setup(game, world)
    room = village_size(game.defs) - island.tile(*village).population
    assert 0 < room < 5000
    p = relocation.plan(game.defs, game.config, island, _payload(source, village, 5000))
    assert p.count == room
    island.tile(*village).population = village_size(game.defs)
    with pytest.raises(OrderFailed) as e:
        relocation.plan(game.defs, game.config, island, _payload(source, village, 5000))
    assert e.value.reason == "reason_relocation_village_full"


# 上限より軽い場合と、上限（trapped_max）に当たる場合
@pytest.mark.parametrize("share", [0.004, 0.15])
def test_stability_drop_is_the_same_for_one_move_and_split_moves(game, world, share):
    """直近 12 ターンに移した人を、出られない人の割合に足して上限を一度だけ掛けるので、同じ人数を
    一度に移しても、12 ターンの内に何回かに分けて移しても、下がりは同じ（掲示板 #1073・#1076）。"""
    island, _, _, _ = _setup(game, world)
    island.ext.setdefault("migration", {})["trapped"] = 0.0
    ctx = context(game, world)
    people = sum(t.population for t in island.tiles)
    moved = round(people * share)
    state = relocation.state_of(island)
    state["recent"] = [[ctx.turn, moved]]
    once = events_bonus(island, ctx)["relocation"]
    third = moved // 3
    state["recent"] = [[ctx.turn - 11, third], [ctx.turn - 5, third], [ctx.turn, moved - 2 * third]]
    split = events_bonus(island, ctx)["relocation"]
    assert split == pytest.approx(once)
    m = game.config.get("migration")
    expected = -min(m["trapped_max"], m["trapped"] * moved / people * 100)
    assert once == pytest.approx(expected)
    if share > 0.1:
        assert once == -m["trapped_max"]
    # 出られない人と合わせても、上限は一度だけ
    island.ext["migration"]["trapped"] = share
    parts = events_bonus(island, ctx)
    total = parts["trapped"] + parts["relocation"]
    both = m["trapped"] * (share + moved / people) * 100
    assert total == pytest.approx(-min(m["trapped_max"], both))
    # 13 ターン前の分は数えない
    island.ext["migration"]["trapped"] = 0.0
    state["recent"] = [[ctx.turn - 12, moved]]
    assert "relocation" not in events_bonus(island, ctx)


def test_two_orders_in_one_turn_count_the_same_as_one(game, world):
    island, source, other, _ = _setup(game, world)
    ctx = context(game, world)
    relocation.force_relocate(island, _payload(source, other, 2000), ctx)
    relocation.force_relocate(island, _payload(source, other, 3000), ctx)
    assert sum(n for _, n in relocation.state_of(island)["recent"]) == 5000
    assert relocation.in_transit(island) == 5000


def test_a_turn_with_the_order_keeps_the_books_and_lowers_the_stability_target(game, world):
    island, source, other, _ = _setup(game, world)
    payload = _payload(source, other, 3000)
    island.orders = [OrderState(id=1, seq=1, kind="force_relocate", payload=payload)]
    total = sum(t.population for t in island.tiles)
    game.engine.run_turn(world)  # 記録の段が帳簿を確かめる
    assert island.orders[0].status == "done", island.orders[0].reason
    assert island.stats["population.relocated"] == 3000
    assert relocation.in_transit(island) == 0
    ctx = context(game, world)
    assert events_bonus(island, ctx)["relocation"] < 0
    assert abs(sum(t.population for t in island.tiles) - total) < total * 0.01
