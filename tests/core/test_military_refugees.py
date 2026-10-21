"""戦争の難民（フェーズ4-3 の③c。設計書 15-military の MI-61）。"""

from __future__ import annotations

import copy

from hakoniwa_core.military import battle, naval
from hakoniwa_core.modules import migration
from tests.core.test_migration import _ctx, _pair
from tests.core.test_migration import base as base  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_phase3_3 import game as game  # noqa: F401


def flee_from(world, island, count, share=1.0):
    """island の戦場から count 人が逃げ、そのうち share が国外へ逃げたい。"""
    tile = next(t for t in island.tiles if t.population > 0)
    battle._flee(world, island, tile.x, tile.y, count, share)


def pool(game, world):
    ctx = _ctx(game, world)
    ids = [i for i in world.island_ids() if migration.active(world.islands[i])]
    views = {i: migration.view_of(world.islands[i], ctx) for i in ids}
    return ctx, ids, views


def test_fleeing_people_who_want_to_leave_the_country_are_counted(game, base):
    world = copy.deepcopy(base)
    poor, _ = _pair(world)
    flee_from(world, poor, 300, 0.5)
    flee_from(world, poor, 100, 0.0)  # 島の中だけで逃げる
    assert poor.ext["migration"]["war"] == 150


def test_war_refugees_go_only_to_islands_that_take_them(game, base):
    world = copy.deepcopy(base)
    poor, rich = _pair(world)
    flee_from(world, poor, 300)
    ctx, ids, views = pool(game, world)
    assert migration._war_refugees(world, ctx, ids, views) == []  # 受け入れる島がない
    assert "war" not in poor.ext["migration"]  # 使い終わった分は消える
    flee_from(world, poor, 300)
    rich.policy.immigration = migration.REFUGEES  # 飢えていなくても、戦争の難民は受け入れる
    flows = migration._war_refugees(world, ctx, ids, views)
    assert [(f.origin, f.target, f.war) for f in flows] == [(poor.id, rich.id, True)]
    assert abs(flows[0].persons - 300) < 1e-6


def test_the_exit_ban_does_not_stop_war_refugees(game, base):
    world = copy.deepcopy(base)
    poor, rich = _pair(world)
    poor.policy.emigration = migration.BAN
    rich.policy.immigration = migration.OPEN
    flee_from(world, poor, 300)
    ctx, ids, views = pool(game, world)
    flows = migration._war_refugees(world, ctx, ids, views)
    kept = migration._exit(world, ctx, ctx.config.get("migration"), ids, views, flows)
    assert [f.persons for f in kept] == [f.persons for f in flows] and flows


def test_a_blockaded_island_cannot_let_its_refugees_out(game, base, monkeypatch):
    world = copy.deepcopy(base)
    poor, rich = _pair(world)
    rich.policy.immigration = migration.OPEN
    monkeypatch.setattr(naval, "blockades", lambda *a, **k: {(poor.wx, poor.wy): {rich.id: 99}})
    flee_from(world, poor, 300)
    ctx, ids, views = pool(game, world)
    assert migration._war_refugees(world, ctx, ids, views) == []
