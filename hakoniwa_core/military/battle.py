"""陸の戦闘（フェーズ4-3 の①。設計書 15-military の MI-28〜MI-30・MI-49）。

戦争の相手の部隊が同じマスにいるとき、全島の段 `operations` が毎ターン戦わせます。動いて入った側が
攻め（role = attack）、もとからいた側が守ります（role = defend）。
守る側は地形・陣地・要塞・町で強くなり
（重ねて掛ける。上限あり）、攻める側は揚陸したターンと補給が尽きたときに弱くなります。

- 強さ = 種類の強さ × 兵の割合 × 装備のそろい × 練度 × 士気 × 弾薬 ×
  食べ物（守りの倍率はそのあとに掛ける）
- 損害は、相手との強さの比で決まる割合で兵と装備が減り（`base_loss × (相手 ÷ 自分)^exponent`、
  上限あり、
  戦う側は必ず 1 人は失う）、戦死 1 : 負傷 3 に分かれます。負傷は数ターン後に半分が戻ります。
- 崩れた部隊（士気が低い・相手の強さが何倍もある・戦い始めの兵が半分を割った）は、
  隣の味方のマスへ退き、
  退く所がなければ降伏して捕虜になります（MI-29・MI-49）。決着しないまま続いたら、攻めた側が元のマスへ
  退きます。
- 持ち主のいる島で、守る部隊がいないマスは警察が守ります（警察の人数ぶんの弱い歩兵。MI-59）。

戦死は国の歴史の「戦死」と相手の「殺した数」（平和賞）に足します。弾薬と燃料は戦ったターンに使い、
外国の島の部隊は自分の stores から、自分の島の部隊は国の倉庫から使います。
"""

from __future__ import annotations

import random
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import monuments, stock
from hakoniwa_core.diplomacy import war
from hakoniwa_core.military import army, forces
from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, to_basis, to_milli

AMMO = "ammunition"
FUEL = "fuel"
VEHICLE = "military_vehicle"
Cell = tuple[int, int]


def hostile(world: WorldState, a: int, b: int, turn: int) -> bool:
    """a と b が戦っていて、戦ってよいか（戦争中。停戦の間と布告のターンは戦わない）。"""
    return a != b and war.fighting(world, a, b, turn)


# --- 強さ ---------------------------------------------------------------------------------


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def stores_of(unit: dict[str, Any]) -> dict[str, int]:
    """外国の島の部隊が持つ補給（財 → 1000 分の 1 単位）。"""
    return unit.setdefault("stores", {})


def _have(world: WorldState, unit: dict[str, Any], good: str) -> int:
    """部隊が今使える物の量。外国の島では自分の stores、自分の島では国の倉庫。"""
    if army.abroad(unit):
        return stores_of(unit).get(good, 0)
    return stock.total(world.islands[unit["owner"]].warehouse, good)


def _use(world: WorldState, unit: dict[str, Any], good: str, qty: int) -> int:
    """部隊が物を使う。使えた量を返す。"""
    if qty <= 0:
        return 0
    if army.abroad(unit):
        have = stores_of(unit).get(good, 0)
        took = min(have, qty)
        stores_of(unit)[good] = have - took
        return took
    return stock.take(world.islands[unit["owner"]].warehouse, good, qty)


def ammo_need(ctx: StageContext, unit: dict[str, Any]) -> int:
    """部隊が戦ったターンに使う弾薬（1000 分の 1 トン）。"""
    return unit["soldiers"] * to_milli(ctx.config.get("military.combat.ammo_per_thousand")) // 1000


def fuel_need(ctx: StageContext, unit: dict[str, Any]) -> int:
    spec = ctx.defs.army[unit["kind"]]
    if not spec.get("vehicles"):
        return 0
    vehicles = unit["equipment"].get(VEHICLE, 0)
    return vehicles * to_milli(ctx.config.get("military.combat.fuel_per_vehicle"))


def equipment_ratio(ctx: StageContext, unit: dict[str, Any]) -> float:
    """装備のそろい（要る数に対して持っている数。兵が減った分は要る数も減る）。"""
    spec = ctx.defs.army[unit["kind"]]
    full = max(1, spec["soldiers"])
    men = unit["soldiers"] / full
    worst = 1.0
    for good, qty in spec["equipment"].items():
        need = qty // QUANTITY_SCALE * men
        if need > 0:
            worst = min(worst, unit["equipment"].get(good, 0) / need)
    return _clamp(worst, ctx.config.get("military.combat.equipment_floor"), 1.0)


def hungry(world: WorldState, unit: dict[str, Any]) -> bool:
    """食べ物が尽きているか（外国の島では部隊の stores、自分の島では島の兵の食べ物）。"""
    if army.abroad(unit):
        return bool(unit.get("hungry"))
    return bool(forces.state_of(world.islands[unit["owner"]]).get("hungry"))


def unit_power(
    world: WorldState, ctx: StageContext, unit: dict[str, Any], terrain: str = ""
) -> float:
    """部隊の強さ（守りの倍率と奇襲より前）。terrain は戦うマスの地形（機甲は山で弱い）。"""
    cfg = ctx.config.get("military.combat")
    spec = ctx.defs.army[unit["kind"]]
    island = world.islands[unit["owner"]]
    state = forces.state_of(island)
    men = unit["soldiers"] / max(1, spec["soldiers"])
    training = cfg["training_floor"] + (1 - cfg["training_floor"]) * state["training"] / BASIS
    start = ctx.config.get("military.morale.start")
    morale = _clamp(state["morale"] / BASIS / start, cfg["morale_floor"], 1.1)
    need = ammo_need(ctx, unit)
    frac = min(1.0, _have(world, unit, AMMO) / need) if need else 1.0
    ammo = cfg["ammo_floor"] + (1 - cfg["ammo_floor"]) * frac
    food = cfg["hungry"] if hungry(world, unit) else 1.0
    power = spec["strength"] * men * equipment_ratio(ctx, unit) * training * morale * ammo * food
    if unit.get("landed") == ctx.turn:
        power *= cfg["landing_weak"]
    if terrain == "mountain" and spec.get("vehicles"):
        power *= cfg["armor_mountain"]
    return power


def police_unit(world: WorldState, ctx: StageContext, island: IslandState) -> dict[str, Any] | None:
    """島の警察が守るときの、疑似の部隊（警察で働く人 ＝ 兵の数）。警察がいなければ None。"""
    workers = sum(
        f.workers
        for f in island.facilities
        if f.built and ctx.defs.facilities[f.kind].get("police")
    )
    if workers <= 0:
        return None
    return {
        "id": 0,
        "owner": island.id,
        "kind": "police",
        "police": True,
        "soldiers": workers,
        "equipment": {},
        "role": "defend",
        "x": -1,
        "y": -1,
    }


def police_power(ctx: StageContext, police: dict[str, Any]) -> float:
    cfg = ctx.config.get("military.combat.police")
    return cfg["per_person"] * police["soldiers"] / cfg["workers"]


def defense_factor(world: WorldState, ctx: StageContext, loc: IslandState, x: int, y: int) -> float:
    """そのマスで守る側が強くなる倍率（地形・町・陣地・要塞を重ねて掛ける。上限あり）。"""
    cfg = ctx.config.get("military.combat.defense")
    tile = army.tile_at(loc, x, y)
    if tile is None:
        return 1.0
    factor = cfg.get(tile.terrain, 1.0)
    if tile.population > 0:
        level = fac.settlement_of(ctx.defs, tile.population)
        factor *= cfg.get(level, 1.0)
    if army.has_trench(tile):
        factor *= cfg["trench"]
    for facility, _ in army.bases(loc, ctx.defs, "fortress"):
        if (facility.x, facility.y) == (x, y):
            factor *= cfg["fortress"]
    return min(factor, cfg["cap"])


# --- 解決 ---------------------------------------------------------------------------------


def run(world: WorldState, ctx: StageContext) -> None:
    """全島の戦場を解決する（島の番号・マスの座標の順）。戦いが終わった部隊の印を片づける。"""
    groups: dict[tuple[int, int, int], dict[int, list[dict[str, Any]]]] = {}
    for unit in army.world_state(world)["units"]:
        if army.is_sea(unit) or "aboard" in unit or unit["status"] != army.READY:
            continue
        if unit["soldiers"] <= 0:
            continue
        key = (army.where(unit), unit["x"], unit["y"])
        groups.setdefault(key, {}).setdefault(unit["owner"], []).append(unit)
    engaged: set[int] = set()
    for key in sorted(groups):
        loc_id, x, y = key
        side = groups[key]
        loc = world.islands.get(loc_id)
        if loc is None:
            continue
        owners = sorted(side)
        if loc_id not in side:  # 守る部隊がいなければ、島の警察が守る
            if any(hostile(world, loc_id, o, ctx.turn) for o in owners):
                police = police_unit(world, ctx, loc)
                if police is not None and _police_holds(ctx, loc, x, y):
                    side[loc_id] = [police]
                    owners = sorted(side)
        for i, a in enumerate(owners):
            for b in owners[i + 1 :]:
                if hostile(world, a, b, ctx.turn):
                    live_a = [u for u in side[a] if u["soldiers"] > 0]
                    live_b = [u for u in side[b] if u["soldiers"] > 0]
                    if live_a and live_b:
                        _fight(world, ctx, loc, x, y, a, live_a, b, live_b)
                        engaged.update(u["id"] for u in live_a + live_b if not u.get("police"))
    for unit in list(army.world_state(world)["units"]):
        if unit["id"] in engaged and unit["soldiers"] <= 0:
            army.remove(world, unit)  # 兵がいなくなった部隊は消える（境目の表の「0 になる」）
        elif unit["id"] not in engaged and "role" in unit:
            for key in ("role", "fought", "engaged", "from"):
                unit.pop(key, None)  # 戦いが終わった


def _police_holds(ctx: StageContext, loc: IslandState, x: int, y: int) -> bool:
    """警察が守るのは、首都と、町（人の多い住宅地）のマス。"""
    if loc.capital == (x, y):
        return True
    tile = army.tile_at(loc, x, y)
    if tile is None or tile.population <= 0:
        return False
    return fac.settlement_of(ctx.defs, tile.population) != ctx.defs.settlement_levels()[0]


def _role(unit: dict[str, Any], turn: int) -> str:
    """部隊が攻める側か守る側か（戦い始めに決め、戦いが終わるまで変えない）。"""
    if unit.get("police"):
        return "defend"
    if "role" not in unit:
        unit["role"] = "attack" if unit.get("entered") == turn else "defend"
    return unit["role"]


def _latest_attacks(
    side_a: list[dict[str, Any]], side_b: list[dict[str, Any]], roles: dict[int, str]
) -> None:
    """同じターンに入った部隊がいない（宣戦の布告だけで上陸していた、停戦が明けたなど）ときは、
    後から入った側を攻める側にする。"""
    last_a = max((u.get("entered", -1) for u in side_a if not u.get("police")), default=-1)
    last_b = max((u.get("entered", -1) for u in side_b if not u.get("police")), default=-1)
    if last_a == last_b:
        return
    late = side_a if last_a > last_b else side_b
    for u in late:
        if not u.get("police"):
            u["role"] = roles[u["id"]] = "attack"


def _fight(
    world: WorldState,
    ctx: StageContext,
    loc: IslandState,
    x: int,
    y: int,
    a: int,
    side_a: list[dict[str, Any]],
    b: int,
    side_b: list[dict[str, Any]],
) -> None:
    cfg = ctx.config.get("military.combat")
    turn = ctx.turn
    roles = {u["id"]: _role(u, turn) for u in side_a + side_b}
    if "attack" not in roles.values():
        _latest_attacks(side_a, side_b, roles)
    attack_a = all(roles[u["id"]] == "attack" for u in side_a)
    attack_b = all(roles[u["id"]] == "attack" for u in side_b)
    defend_a = not attack_a and attack_b
    defend_b = not attack_b and attack_a
    shield = defense_factor(world, ctx, loc, x, y)
    w = war.open_war(world, a, b)

    tile = army.tile_at(loc, x, y)
    terrain = tile.terrain if tile is not None else ""

    def power(units: list[dict[str, Any]], owner: int, defending: bool) -> float:
        total = 0.0
        for u in units:
            total += police_power(ctx, u) if u.get("police") else unit_power(world, ctx, u, terrain)
        if defending:
            total *= shield
        if w is not None and w["start"] == turn and w["attacker"] == owner:
            total *= cfg["surprise"]
        return total

    power_a = power(side_a, a, defend_a)
    power_b = power(side_b, b, defend_b)
    loss_a = _loss(cfg, power_b, power_a)
    loss_b = _loss(cfg, power_a, power_b)
    for units, own, foe in ((side_a, power_a, power_b), (side_b, power_b, power_a)):
        if any(u.get("police") for u in units) and (own <= 0 or foe / own > cfg["break"]["ratio"]):
            if tile is not None:
                tile.ext["police_down"] = turn  # 警察は持ちこたえられず、占領を許す
    for u in side_a + side_b:
        if "engaged" not in u and not u.get("police"):
            u["engaged"] = u["soldiers"]
        if not u.get("police"):
            u["fought"] = u.get("fought", 0) + 1
    result_a = _casualties(world, ctx, loc, a, side_a, loss_a)
    result_b = _casualties(world, ctx, loc, b, side_b, loss_b)
    _record(world, ctx, a, b, result_a, result_b)
    winner = a if loss_a < loss_b else b if loss_b < loss_a else None
    _morale(world, ctx, a, b, winner)
    _exhaust(world, ctx, side_a + side_b)
    _civilians(world, ctx, loc, x, y)
    _logs(world, ctx, loc, x, y, a, b, result_a, result_b)
    broke_a = _break_check(world, ctx, side_a, power_b, power_a)
    broke_b = _break_check(world, ctx, side_b, power_a, power_b)
    for units, broke, foe in ((side_a, broke_a, b), (side_b, broke_b, a)):
        for u in units:
            if u.get("police") or u["soldiers"] <= 0:
                continue
            stalemate = u.get("role") == "attack" and u.get("fought", 0) >= cfg["stalemate_turns"]
            if u in broke or stalemate:
                _withdraw(world, ctx, loc, u, foe, rout=u in broke)


def _loss(cfg: dict[str, Any], enemy: float, own: float) -> float:
    """1 ターンの損害の割合（強さが同じなら base_loss）。自分の強さが 0 なら上限まで。"""
    if own <= 0:
        return cfg["max_loss"]
    if enemy <= 0:
        return 0.0
    return min(cfg["max_loss"], cfg["base_loss"] * (enemy / own) ** cfg["exponent"])


def _casualties(
    world: WorldState,
    ctx: StageContext,
    loc: IslandState,
    owner: int,
    units: list[dict[str, Any]],
    fraction: float,
) -> dict[str, int]:
    """側の部隊に損害を与える。戦死と負傷の数を返す（警察の損害は住民が亡くなる）。"""
    cfg = ctx.config.get("military.combat")
    island = world.islands[owner]
    killed = wounded = 0
    for u in sorted(units, key=lambda u: u["id"]):
        if fraction <= 0:
            break
        lost = min(u["soldiers"], max(cfg["min_casualties"], round(u["soldiers"] * fraction)))
        dead = round(lost * cfg["killed_share"])
        hurt = lost - dead
        if u.get("police"):
            killed += _kill_police(loc, lost)
            continue
        share = lost / u["soldiers"]
        for good, have in list(u["equipment"].items()):
            u["equipment"][good] = have - round(have * share)
        u["soldiers"] -= lost
        killed += dead
        wounded += hurt
    gone = killed + wounded
    if gone and units and not units[0].get("police"):
        forces.lose(island, gone)
        if wounded:
            back = island.ext[forces.KEY].setdefault("wounded", [])
            back.append(
                [ctx.turn + ctx.config.get("military.combat.wounded_return.turns"), wounded]
            )
    return {"killed": killed, "wounded": wounded}


def _kill_police(loc: IslandState, lost: int) -> int:
    """警察の損害: 警察官（住民）が亡くなる。亡くなった数を返す。"""
    taken = forces.take_people(loc, lost, 0)
    return taken


def _record(
    world: WorldState,
    ctx: StageContext,
    a: int,
    b: int,
    result_a: dict[str, int],
    result_b: dict[str, int],
) -> None:
    """戦死は国の歴史の戦死に、相手を殺した数は平和賞の記録に足す（MI-42・タブ 11）。"""
    for me, foe, mine, theirs in ((a, b, result_a, result_b), (b, a, result_b, result_a)):
        island = world.islands[me]
        monuments.add_dead(island, monuments.WAR_DEAD, mine["killed"])
        monuments.add_kills(world.islands[foe], mine["killed"])
        stats = island.stats
        stats["military.killed"] = stats.get("military.killed", 0) + mine["killed"]
        stats["military.wounded"] = stats.get("military.wounded", 0) + mine["wounded"]
        stats["military.kills"] = stats.get("military.kills", 0) + theirs["killed"]


def _morale(world: WorldState, ctx: StageContext, a: int, b: int, winner: int | None) -> None:
    cfg = ctx.config.get("military.combat")
    for owner in (a, b):
        state = forces.state_of(world.islands[owner])
        if winner == owner:
            state["morale"] = min(BASIS, state["morale"] + to_basis(cfg["morale"]["win"]))
        elif winner is not None:
            state["morale"] = max(0, state["morale"] - to_basis(cfg["morale"]["lose"]))
        state["training"] = min(BASIS, state["training"] + to_basis(cfg["training_gain"]))


def _exhaust(world: WorldState, ctx: StageContext, units: list[dict[str, Any]]) -> None:
    """戦ったターンの弾薬と燃料を使う。足りなければ使えるだけ（強さには _unit_power で効いている）
    。"""
    for u in units:
        if u.get("police"):
            continue
        _use(world, u, AMMO, ammo_need(ctx, u))
        _use(world, u, FUEL, fuel_need(ctx, u))


def _civilians(world: WorldState, ctx: StageContext, loc: IslandState, x: int, y: int) -> None:
    """戦場のマスの住民の一部が亡くなり、一部は同じ島のほかの町へ逃げる（MI-30）。施設は止まるか壊れる。"""
    cfg = ctx.config.get("military.combat")
    tile = army.tile_at(loc, x, y)
    if tile is None:
        return
    rng = random.Random(f"{world.seed}:{ctx.turn}:war:{loc.id}:{x}:{y}")
    if tile.population > 0:
        dead = min(tile.population, round(tile.population * cfg["civilians"]["dead"]))
        flee = min(tile.population - dead, round(tile.population * cfg["civilians"]["flee"]))
        tile.population -= dead + flee
        monuments.add_dead(loc, monuments.WAR_DEAD, dead)
        loc.stats["military.civilian_dead"] = loc.stats.get("military.civilian_dead", 0) + dead
        _flee(world, loc, x, y, flee, ctx.config.get("military.refugees.abroad_share"))
    facility = loc.facility_at(x, y)
    if facility is None or not facility.built or fac.is_housing(ctx.config, facility):
        return
    if ctx.defs.facilities[facility.kind].get("category") in ("finance", "trade"):
        return  # 銀行と港は、預金・貸し出し・積み荷の帳簿があるので戦場の乱数では壊さない
    if rng.random() < cfg["facilities"]["destroy"]:
        _ruin(loc, ctx, facility)
    elif rng.random() < cfg["facilities"]["halt"]:
        until = ctx.turn + cfg["facilities"]["halt_turns"]
        if until > facility.ext.get("halted_until", 0):
            facility.ext["halted_until"] = until
            facility.ext["halt_reason"] = "war"


def _flee(
    world: WorldState,
    loc: IslandState,
    x: int,
    y: int,
    count: int,
    abroad: float = 0.0,
    avoid: set[tuple[int, int]] | None = None,
) -> None:
    """逃げる人を、同じ島のほかの住宅のマス（戦場と戦場の隣を除く）へ人口に比例して移す。
    逃げる先がなければ、その場に残る。"""
    if count <= 0:
        return
    if abroad > 0:  # 国外へ逃げたい人（abroad は割合）。移民の段が受け入れ先へ送る（MI-61）
        pool = loc.ext.setdefault("migration", {})
        pool["war"] = pool.get("war", 0) + count * abroad
    busy = {
        (u["x"], u["y"])
        for u in army.on_land(world, loc.id)
        if u["status"] == army.READY and u["soldiers"] > 0
    }
    pools = [
        (i, t.population)
        for i, t in enumerate(loc.tiles)
        if t.population > 0
        and (t.x, t.y) not in busy
        and (t.x, t.y) != (x, y)
        and (t.x, t.y) not in (avoid or ())
    ]
    whole = sum(p for _, p in pools)
    if whole <= 0:
        loc.tile(x, y).population += count
        return
    left = count
    given = {}
    for i, pool in pools:
        given[i] = count * pool // whole
        left -= given[i]
    for i, _ in sorted(pools, key=lambda p: (-(count * p[1] % whole), p[0])):
        if left <= 0:
            break
        given[i] += 1
        left -= 1
    for i, n in given.items():
        loc.tiles[i].population += n
    loc.stats["military.refugees"] = loc.stats.get("military.refugees", 0) + count


def _ruin(loc: IslandState, ctx: StageContext, facility) -> None:
    """戦場で施設が壊れる: 工事中に戻し、国の工事として直す（暴動の damage と同じ形。在庫は失う）
    。"""
    from hakoniwa_core.economy import books

    total = fac.total_progress(ctx.defs, facility)
    facility.built = False
    facility.progress = max(0, total - round(total * 0.5))
    facility.workers = 0
    facility.status = ""
    facility.ext["repair"] = True
    facility.ext.pop("halted_until", None)
    facility.ext.pop("halt_reason", None)
    for good, qty in sorted(stock.totals(facility.stock).items()):
        taken = stock.take(facility.stock, good, qty)
        books.stock_moved(loc, facility.id, good, -taken)
    loc.log.append(
        LogEntry(
            "war_facility_ruined", {"facility": facility.kind, "x": facility.x, "y": facility.y}
        )
    )


# --- 崩れと退き ----------------------------------------------------------------------------


def _break_check(
    world: WorldState,
    ctx: StageContext,
    units: list[dict[str, Any]],
    enemy: float,
    own: float,
) -> list[dict[str, Any]]:
    """崩れた部隊（士気が低い・相手が何倍も強い・戦い始めの兵が半分を割った）。"""
    cfg = ctx.config.get("military.combat.break")
    out = []
    for u in units:
        if u.get("police") or u["soldiers"] <= 0:
            continue
        state = forces.state_of(world.islands[u["owner"]])
        low = state["morale"] / BASIS < cfg["morale"]
        hopeless = own > 0 and enemy / own > cfg["ratio"] or own <= 0
        thin = u["soldiers"] < u.get("engaged", u["soldiers"]) * cfg["remaining"]
        if low or hopeless or thin:
            out.append(u)
    return out


def _free_neighbor(
    world: WorldState, ctx: StageContext, loc: IslandState, unit: dict[str, Any]
) -> Cell | None:
    """隣の味方のマス（戦争の相手の部隊がいない陸のマス）。味方の部隊のいるマスを先に、なければ座標の順。"""
    x, y = unit["x"], unit["y"]
    options = []
    for nx, ny in ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)):
        tile = army.tile_at(loc, nx, ny)
        if tile is None or army.enter_cost(tile, ctx.defs, ctx.config) is None:
            continue
        here = [u for u in army.on_land(world, loc.id) if (u["x"], u["y"]) == (nx, ny)]
        if any(hostile(world, unit["owner"], u["owner"], ctx.turn) for u in here):
            continue
        if tile.ext.get("occupied") and tile.ext["occupied"]["by"] != unit["owner"]:
            continue
        mine = any(u["owner"] == unit["owner"] for u in here)
        options.append((0 if mine else 1, ny, nx))
    if not options:
        return None
    _, ny, nx = min(options)
    return (nx, ny)


def _withdraw(
    world: WorldState,
    ctx: StageContext,
    loc: IslandState,
    unit: dict[str, Any],
    foe: int,
    rout: bool,
) -> None:
    """崩れた部隊・決着しない攻めの部隊が退く。退く所がなければ（崩れたときだけ）降伏する。"""
    cfg = ctx.config.get("military.combat")
    target: Cell | None = None
    back = unit.get("from")
    if back is not None and not rout:
        target = (back[0], back[1])
    if target is None:
        target = _free_neighbor(world, ctx, loc, unit)
    owner = world.islands[unit["owner"]]
    if target is None:
        if rout:
            _surrender(world, ctx, unit, foe)
        return
    unit["x"], unit["y"] = target
    unit["order"] = None
    for key in ("role", "fought", "engaged", "from"):
        unit.pop(key, None)
    unit["entered"] = ctx.turn
    if rout:
        state = forces.state_of(owner)
        state["morale"] = max(0, state["morale"] - to_basis(cfg["morale"]["rout"]))
    owner.log.append(
        LogEntry(
            "unit_retreated",
            {"unit": unit["id"], "x": target[0], "y": target[1], "rout": rout},
        )
    )


def _surrender(world: WorldState, ctx: StageContext, unit: dict[str, Any], foe: int) -> None:
    """退く所がない部隊が降伏する: 残りの兵は捕虜になり、
    装備は勝った側が手に入れる（MI-29・MI-49）。"""
    owner = world.islands[unit["owner"]]
    captor = world.islands[foe]
    count = unit["soldiers"]
    forces.lose(owner, count)
    held = forces.state_of(captor).setdefault("prisoners", [])
    held.append([ctx.turn, owner.id, count])
    for good, qty in unit["equipment"].items():
        if qty > 0:
            stock.add(captor.warehouse, good, qty * QUANTITY_SCALE, ctx.turn, ctx.defs.goods)
    owner.log.append(
        LogEntry("unit_surrendered", {"unit": unit["id"], "count": count, "country": captor.name})
    )
    captor.log.append(LogEntry("prisoners_taken", {"count": count, "country": owner.name}))
    army.remove(world, unit)


def _logs(
    world: WorldState,
    ctx: StageContext,
    loc: IslandState,
    x: int,
    y: int,
    a: int,
    b: int,
    result_a: dict[str, int],
    result_b: dict[str, int],
) -> None:
    for me, foe, mine, theirs in ((a, b, result_a, result_b), (b, a, result_b, result_a)):
        world.islands[me].log.append(
            LogEntry(
                "battle",
                {
                    "x": x,
                    "y": y,
                    "island": loc.name,
                    "country": world.islands[foe].name,
                    "killed": mine["killed"],
                    "wounded": mine["wounded"],
                    "enemy_killed": theirs["killed"],
                },
            )
        )
    if loc.id not in (a, b):  # 第三国の島での戦いも、その島の持ち主に知らせる
        loc.log.append(
            LogEntry(
                "battle_on_land",
                {"x": x, "y": y, "a": world.islands[a].name, "b": world.islands[b].name},
            )
        )
