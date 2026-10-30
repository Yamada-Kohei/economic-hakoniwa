"""世界と島を作る。"""

from __future__ import annotations

import random

from django.db import transaction

from hakoniwa_core import scale
from hakoniwa_core.founding import FoundingError, FoundingPlan, found_island, open_for_business
from hakoniwa_core.nature import zone_of
from hakoniwa_core.state.generate import generate_island, world_grid_size
from server.game import get_game
from server.world import models as m
from server.world.repository import create_island


def get_or_create_world(seed: int | None = None) -> m.World:
    world = m.World.objects.order_by("id").first()
    if world is None:
        world = m.World.objects.create(
            seed=seed if seed is not None else random.SystemRandom().randrange(2**62),
            packs=get_game().defs.pack_versions(),
            ext={scale.WORLD_KEY: scale.people(get_game().config)},  # この世界の倍（段6 A3）
        )
    return world


def grid_size(world: m.World) -> int:
    """世界マップの1辺の区画数。島が増えたら広げる。"""
    config = get_game().config
    islands = max(config.get("world.default_islands"), world.islands.count() + 1)
    return world_grid_size(islands, config)


def free_cells(world: m.World) -> list[tuple[int, int]]:
    grid = grid_size(world)
    used = set(world.islands.values_list("wx", "wy"))
    return [(x, y) for y in range(grid) for x in range(grid) if (x, y) not in used]


@transaction.atomic
def create_sample_island(world: m.World, name: str, rng: random.Random, owner=None) -> m.Island:
    """空いている区画に、ランダムな形・資源・体制で建国した島を作る（開発・試験用）。"""
    game = get_game()
    cells = free_cells(world)
    if not cells:
        raise ValueError("世界マップに空いている区画がありません")
    wx, wy = rng.choice(cells)
    climate = zone_of(wy, grid_size(world), game.config)
    island = generate_island(rng, 0, name, wx, wy, game.config, game.defs, climate=climate)
    island.ext["founded_turn"] = world.turn
    open_for_business(island, game.defs, game.config, world.turn)
    return create_island(world, island, owner=owner)


@transaction.atomic
def found_for_user(world: m.World, user, plan: FoundingPlan, wx: int, wy: int) -> m.Island:
    """プレイヤーの建国。問題があれば FoundingError（理由は messages の名前）。"""
    game = get_game()
    world = m.World.objects.select_for_update().get(pk=world.pk)
    if m.Island.objects.filter(owner=user).exists():
        raise FoundingError([("founding_already", {})])
    # 問題はまとめて返す（区画の問題と、形や名前の問題を一度に直せるように）
    problems = [] if (wx, wy) in free_cells(world) else [("founding_no_cell", {})]
    try:
        climate = zone_of(wy, grid_size(world), game.config)
        island = found_island(game.defs, game.config, plan, 0, wx, wy, world.turn, climate)
    except FoundingError as e:
        raise FoundingError(problems + list(e.problems)) from e
    if problems:
        raise FoundingError(problems)
    island.ext["founded_turn"] = world.turn
    open_for_business(island, game.defs, game.config, world.turn)
    return create_island(world, island, owner=user)


def release_problem(island: m.Island, user) -> str:
    """持ち主が自分で国を手放せないわけ（手放せるなら空）。

    戦争の最中（宣戦から講和まで）は手放せない: 相手の国が戦う先を失い、賠償も取れなくなるため。
    手放したあと次に手放せるのは accounts.release_wait_turns ターン後（無人の島が地図を
    埋めないように）。
    """
    world = island.world
    wars = ((world.ext or {}).get("diplomacy") or {}).get("wars", [])
    for w in wars:
        if w.get("state") in ("declared", "war", "ceasefire") and island.id in (
            w.get("attacker"),
            w.get("defender"),
        ):
            return "戦争の最中（宣戦から講和まで）は国を手放せません。"
    wait = get_game().config.get("accounts.release_wait_turns")
    last = (
        m.Island.objects.filter(ext__released_by=user.pk)
        .values_list("ext__released_turn", flat=True)
        .order_by("-ext__released_turn")
        .first()
    )
    if last is not None and world.turn < last + wait:
        return f"国を手放したあと、次に手放せるのは {last + wait - world.turn} ターン後です。"
    return ""


def release_by_owner(island: m.Island, user) -> None:
    """持ち主が自分の国を手放す。島は無人の島として残る（管理画面の「無人の島にする」と同じ）。
    そのアカウントは、続けて新しい国を建てられる。"""
    island.ext = {**(island.ext or {}), "released_by": user.pk, "released_turn": island.world.turn}
    island.owner = None
    island.save(update_fields=["owner", "ext"])
