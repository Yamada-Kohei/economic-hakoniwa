"""ミサイルと防空（フェーズ4-3 の③。設計書 15-military の MI-37・MI-38）。

- ミサイル基地（facilities.yaml の `military: {missile_base: 12}`）は、置けるミサイルの数が上限で、
  防空陣地（`air_defense: 12`）は迎撃ミサイルの上限。毎ターン `restock` が、国の倉庫にある分から
  上限まで基地の在庫（`facility.stock`）へ移す（足りない分は軍の段が国防費で買う。`wanted`）。
- 命令 fire_missile（`launch`）は、基地のミサイルを使って、戦争の相手の島のマスを狙う。射程は島の
  区画どうしの距離。1 つの基地が 1 ターンに撃てるのは `fire_per_turn` 発まで。撃った分は命令の
  ときに基地から出し、着弾は operations の段の終わりに決める（`run`。MI-50: 動き → 海 → 陸の戦闘 →
  占領 → ミサイル）。
- 1 発ごとに `drift` の見込みで隣のマスに落ち、着弾するマスから `shield_radius` 以内の防空陣地が
  迎撃ミサイルを 1 発ずつ使って `intercept` の見込みで落とす。当たったマスは、施設が `ruin` の
  見込みで壊れる（残りは止まる。銀行と港は止まるだけ）、住民が亡くなって逃げ、港と道路の整備度が
  下がる。住宅のあるマスを撃った国は、戦時の決まりの違反として記録される（信用の傷）。
"""

from __future__ import annotations

import random
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import infra, monuments, stock
from hakoniwa_core.diplomacy import credit, war
from hakoniwa_core.military import army, battle, nuclear
from hakoniwa_core.state import Facility, IslandState, LogEntry, WorldState
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import QUANTITY_SCALE

MISSILE = "missile"
INTERCEPTOR = "interceptor"
STRIKES = "strikes"
WARHEAD = "nuclear_warhead"
SOURCES = (("missile_base", MISSILE), ("air_defense", INTERCEPTOR), ("warhead", WARHEAD))


def strikes(world: WorldState) -> list[dict[str, Any]]:
    return army.world_state(world).setdefault(STRIKES, [])


def stocked(
    island: IslandState, defs, key: str, good: str, turn: int | None = None
) -> list[tuple[Facility, int, int]]:
    """建ち終わった基地の (施設, 上限, 在庫の数)。turn を渡すと、その時点で止まっている基地
    （占領・ミサイル・暴動など）は除く。"""
    return [
        (f, cap, stock.total(f.stock, good) // QUANTITY_SCALE)
        for f, cap in army.bases(island, defs, key)
        if turn is None or not fac.is_halted(f, turn)
    ]


def restock(world: WorldState, ctx: StageContext) -> None:
    """基地と防空陣地の在庫を、国の倉庫から上限まで満たす。"""
    from hakoniwa_core.economy import books

    for island_id in world.island_ids():
        island = world.islands[island_id]
        for key, good in SOURCES:
            for f, cap, have in stocked(island, ctx.defs, key, good, ctx.turn):
                need = (cap - have) * QUANTITY_SCALE
                if need <= 0:
                    continue
                got = stock.take(island.warehouse, good, need)
                stock.add(f.stock, good, got, ctx.turn, ctx.defs.goods)
                books.stock_moved(island, f.id, good, got)


def wanted(island: IslandState, defs, turn: int) -> dict[str, int]:
    """基地に足りないミサイルと迎撃ミサイル（国の倉庫へ買い集めたい量。1000 分の 1 単位）。"""
    out: dict[str, int] = {}
    for key, good in SOURCES:
        if good == WARHEAD and not any(
            f.built and f.kind == "nuclear_facility" for f in island.facilities
        ):
            continue  # 核弾頭は、核施設を持つ国だけが基地に満たす
        missing = sum(cap - have for _, cap, have in stocked(island, defs, key, good, turn))
        if missing > 0:
            out[good] = missing * QUANTITY_SCALE
    return out


def distance(a: IslandState, b: IslandState) -> float:
    return ((a.wx - b.wx) ** 2 + (a.wy - b.wy) ** 2) ** 0.5


def launch(
    world: WorldState, ctx: StageContext, island: IslandState, req: dict[str, Any]
) -> str | None:
    """命令 fire_missile: 基地から count 発を、相手の島のマス (x, y) に撃つ。撃てた数は基地の在庫と
    1 ターンの上限で頭打ち。"""
    from hakoniwa_core.economy import books

    cfg = ctx.config.get("military.missiles")
    base = island.facility(req["base"])
    if (
        base is None
        or not base.built
        or not ctx.defs.facilities[base.kind].get("military", {}).get("missile_base")
    ):
        return "reason_no_missile_base"
    if fac.is_halted(base, ctx.turn):
        return "reason_base_halted"
    target = world.islands.get(req["island"])
    if target is None or target.id == island.id:
        return "reason_no_island"
    if not war.fighting(world, island.id, target.id, ctx.turn):
        return "reason_not_at_war"
    if distance(island, target) > cfg["range"]:
        return "reason_out_of_range"
    if not target.in_bounds(req["x"], req["y"]):
        return "reason_off_island"
    fired = base.ext.get("fired", {})
    used = fired.get("n", 0) if fired.get("turn") == ctx.turn else 0
    have = stock.total(base.stock, MISSILE) // QUANTITY_SCALE
    nuclear = bool(req.get("nuclear"))
    heads = stock.total(base.stock, WARHEAD) // QUANTITY_SCALE
    if nuclear:
        if heads <= 0:
            return "reason_no_warheads"
        have = min(have, heads)
    count = min(req["count"], have, cfg["fire_per_turn"] - used)
    if count <= 0:
        return "reason_no_missiles"
    books.stock_moved(
        island, base.id, MISSILE, -stock.take(base.stock, MISSILE, count * QUANTITY_SCALE)
    )
    if nuclear:
        books.stock_moved(
            island, base.id, WARHEAD, -stock.take(base.stock, WARHEAD, count * QUANTITY_SCALE)
        )
    base.ext["fired"] = {"turn": ctx.turn, "n": used + count}
    state = army.world_state(world)
    state["seq"] += 1
    strikes(world).append(
        {
            "id": state["seq"],
            "owner": island.id,
            "island": target.id,
            "x": req["x"],
            "y": req["y"],
            "count": count,
            "nuclear": nuclear,
        }
    )
    island.stats["military.missiles_fired"] = island.stats.get("military.missiles_fired", 0) + count
    island.log.append(
        LogEntry(
            "missile_fired", {"count": count, "country": target.name, "x": req["x"], "y": req["y"]}
        )
    )
    return None


def run(world: WorldState, ctx: StageContext) -> None:
    """今ターンの着弾を決める。"""
    pending = strikes(world)
    state = army.world_state(world)
    state[STRIKES] = []
    for strike in pending:
        _strike(world, ctx, strike)


def _strike(world: WorldState, ctx: StageContext, strike: dict[str, Any]) -> None:
    cfg = ctx.config.get("military.missiles")
    loc = world.islands.get(strike["island"])
    attacker = world.islands.get(strike["owner"])
    if loc is None or attacker is None:
        return
    residential = False
    nuclear = strike.get("nuclear", False)
    chance = ctx.config.get("military.nuclear.intercept") if nuclear else cfg["intercept"]
    for i in range(strike["count"]):
        rng = random.Random(f"{world.seed}:{ctx.turn}:missile:{strike['id']}:{i}")
        x, y = strike["x"], strike["y"]
        if rng.random() < cfg["drift"]:
            dx, dy = rng.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
            if loc.in_bounds(x + dx, y + dy):
                x, y = x + dx, y + dy
        if _intercepted(world, ctx, loc, chance, rng, x, y):
            loc.log.append(LogEntry("missile_intercepted", {"x": x, "y": y}))
            continue
        if nuclear:
            nuclear_hit(world, ctx, loc, x, y, attacker)
            continue
        residential |= _hit(world, ctx, loc, cfg, rng, x, y, attacker.id)
    violated = army.world_state(world).setdefault("violated", {})
    if residential and violated.get(str(attacker.id)) != ctx.turn:  # 1 ターンに 1 回だけ記録
        violated[str(attacker.id)] = ctx.turn
        credit.record(
            attacker,
            ctx.turn,
            0,
            "war_rules",
            cfg["violation_weight"],
            "missile_residential",
            ctx.config,
        )
        attacker.log.append(LogEntry("missile_violation", {"country": loc.name}))


def _intercepted(
    world: WorldState,
    ctx: StageContext,
    loc: IslandState,
    chance: float,
    rng: random.Random,
    x: int,
    y: int,
) -> bool:
    from hakoniwa_core.economy import books

    shield = ctx.config.get("military.missiles.shield_radius")

    for f, _cap, have in sorted(
        stocked(loc, ctx.defs, "air_defense", INTERCEPTOR, ctx.turn), key=lambda r: r[0].id
    ):
        if have <= 0 or max(abs(f.x - x), abs(f.y - y)) > shield:
            continue
        books.stock_moved(loc, f.id, INTERCEPTOR, -stock.take(f.stock, INTERCEPTOR, QUANTITY_SCALE))
        if rng.random() < chance:
            return True
    return False


def _hit(
    world: WorldState,
    ctx: StageContext,
    loc: IslandState,
    cfg: dict[str, Any],
    rng: random.Random,
    x: int,
    y: int,
    attacker: int,
) -> bool:
    """着弾したマスの被害。住民が被害を受けたら True（住宅を撃った）。"""
    tile = loc.tile(x, y)
    assert tile is not None
    loc.log.append(LogEntry("missile_hit", {"x": x, "y": y}))
    hurt = False
    if tile.population > 0:
        dead = min(tile.population, round(tile.population * cfg["dead"]))
        flee = min(tile.population - dead, round(tile.population * cfg["flee"]))
        tile.population -= dead + flee
        monuments.add_dead(loc, monuments.WAR_DEAD, dead)
        monuments.add_kills(world.islands[attacker], dead)
        loc.stats["military.civilian_dead"] = loc.stats.get("military.civilian_dead", 0) + dead
        battle._flee(world, loc, x, y, flee)
        hurt = True
    facility = loc.facility_at(x, y)
    if facility is not None and facility.built and not fac.is_housing(ctx.config, facility):
        safe = ctx.defs.facilities[facility.kind].get("category") in ("finance", "trade")
        if not safe and rng.random() < cfg["ruin"]:
            battle._ruin(loc, ctx, facility)
        else:
            facility.ext["halted_until"] = max(
                facility.ext.get("halted_until", 0), ctx.turn + cfg["halt_turns"]
            )
            facility.ext.setdefault("halt_reason", "missile")
    elif facility is not None and fac.is_housing(ctx.config, facility):
        hurt = True
    for kind in ctx.defs.infra_kinds("transport"):
        level = infra.level(tile, kind)
        if level:
            infra.set_level(tile, kind, round(level * cfg["infra"]))
    return hurt


def brief(island: IslandState, defs) -> dict[str, list[dict[str, Any]]]:
    """持ち主に見せる基地の在庫。"""
    out: dict[str, list[dict[str, Any]]] = {}
    for key, good in SOURCES:
        out[key] = [
            {"facility": f.id, "x": f.x, "y": f.y, "stock": have, "cap": cap}
            for f, cap, have in stocked(island, defs, key, good)
        ]
    return out


def nuclear_hit(
    world: WorldState,
    ctx: StageContext,
    loc: IslandState,
    x: int,
    y: int,
    attacker: IslandState,
) -> None:
    """核弾頭の着弾（被害は nuclear.detonate、違反は nuclear.condemn）。"""
    nuclear.detonate(world, ctx, loc, x, y, attacker)
    nuclear.condemn(world, ctx, attacker, loc)
