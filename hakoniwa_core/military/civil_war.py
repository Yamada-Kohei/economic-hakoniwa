"""反乱と内戦（フェーズ4-4。設計書 15-military の MI-39〜MI-41・MI-58）。

政情が反乱の前触れ（`politics.levels.rebellion` 未満）のまま `military.civil_war.trigger_turns`
ターン続くと、反乱軍が首都と陸でつながる大きな町に現れます（MI-39）。反乱軍は持ち主のいない島の中の
主体で、全島の段 `operations` が動かします（MI-40）。世界の状態 `world.ext["military"]["rebels"]` に
グループの並びで持ちます。グループ 1 つの形:

- id（部隊と同じ番号の振り出し）/ island（島の番号）/ x, y（島のマス）
- soldiers: 兵の数 / weapons: 持っている武器（組。兵より少ないと残りは猟銃や農具で戦う）
- morale: 士気（1 万分の 1）/ since: 立ち上がったターン / entered: そのマスに入ったターン
- hungry: 食べ物が足りなかったか / fought, engaged: 戦いの続いたターン数と戦い始めの兵の数

毎ターン（戦闘の後・占領の前に）、島ごとに次の順で動きます。

1. 見張り: 反乱の前触れが続けば立ち上がり、政情が落ち着いて続けば終わる（`_watch`）。
2. 寝返り: 士気が低く賃金の遅れた政府の部隊が、乱数で反乱軍に加わる（`_defect`。1917 年のロシア）。
3. 行軍と戦い: 反乱軍は首都へ向かい、政府の部隊か警察のいるマスで戦う。守りのいないマスは占領し、
   そこの住民から兵を集め、施設の在庫から食べ物を取り立てる（`_march`・`_fight`・`_hold`・`_recruit`）。
4. 首都を落とすと体制が倒れ（降伏と同じ作りの選び直し待ち）、反乱は終わる（`_overthrow`）。反乱軍が
   尽きる・政情が落ち着く・長引いて兵が逃げ尽くすと、反乱は鎮まって兵は家計に戻る。

反乱軍が占領したマスは `tile.ext["occupied"] = {"by": REBELS, ...}`（外国の占領と同じ仕組みで施設が
止まり、住民は政情の目標を下げる）。反乱軍がそのマスにいなくなっても、政府の部隊が入るまで続く。

武器は、寝返った部隊が持ってきた分と、占領した兵舎のマスで島の武器庫から奪った分だけ（何もない所
からは生まれない）。持たない兵は猟銃や農具で戦う（`improvised`。強さの下限）。外国が反乱軍や政府を
助ける形はベータ版の後。
"""

from __future__ import annotations

import random
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import monuments, politics, stock
from hakoniwa_core.military import army, battle, forces
from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, to_basis, to_milli

KEY = "rebels"
REBELS = -1  # 占領したマスの `by` に入れる、反乱軍の印（島の番号は 1 から）
STATE = "civil_war"  # 島の ext
WEAPONS = "weapons"
FOOD_GOODS = ("food", "grain")
Cell = tuple[int, int]


# --- 状態 ---------------------------------------------------------------------------------


def groups(world: WorldState) -> list[dict[str, Any]]:
    """世界の反乱軍のグループ（番号の順）。"""
    return army.world_state(world).setdefault(KEY, [])


def groups_of(world: WorldState, island_id: int) -> list[dict[str, Any]]:
    return [g for g in groups(world) if g["island"] == island_id]


def active(world: WorldState, island_id: int) -> bool:
    """島で内戦が起きているか。"""
    return any(g["island"] == island_id for g in groups(world))


def state_of(island: IslandState) -> dict[str, Any]:
    state = island.ext.setdefault(STATE, {})
    state.setdefault("alarm", 0)  # 反乱の前触れが続いているターン数
    state.setdefault("calm", 0)  # 内戦の間、政情が落ち着いて続いているターン数
    state.setdefault("cooldown", 0)  # このターンまでは新しい反乱が起きない
    return state


def alarmed(world: WorldState, island: IslandState, trigger_turns: int) -> bool:
    """島で内戦が起きているか、反乱の前触れが立ち上がりの半分のターン続いているか（標準の計画が備える）。"""
    return active(world, island.id) or state_of(island)["alarm"] >= max(1, trigger_turns // 2)


def is_rebel_tile(tile: Any) -> bool:
    held = tile.ext.get("occupied")
    return bool(held) and held.get("by") == REBELS


def near(world: WorldState, island_id: int, x: int, y: int, reach: int) -> bool:
    """マス (x, y) から reach マス以内に反乱軍がいるか（政府の部隊が隣のマスで止まる）。"""
    return any(
        g["soldiers"] > 0 and max(abs(g["x"] - x), abs(g["y"] - y)) <= reach
        for g in groups_of(world, island_id)
    )


# --- 毎ターン -----------------------------------------------------------------------------


def run(world: WorldState, ctx: StageContext) -> None:
    """全島の反乱と内戦を 1 ターン進める（島の番号の順）。"""
    for island_id in world.island_ids():
        island = world.islands[island_id]
        if not island.has_economy or island.capital is None:
            continue
        _watch(world, ctx, island)
        if not active(world, island_id):
            _release_all(world, island)
            continue
        _defect(world, ctx, island)
        for g in list(groups_of(world, island_id)):
            if g["soldiers"] <= 0 or g not in groups(world):
                continue
            _march(world, ctx, island, g)
            if g not in groups(world):
                continue
            _fight(world, ctx, island, g)
            if g not in groups(world):
                continue
            _hold(world, ctx, island, g)
            if g not in groups(world):
                break  # 首都が落ちて、反乱は終わった
            _recruit(world, ctx, island, g)
            _eat(world, ctx, island, g)
        _lift(world, ctx, island)
        if active(world, island_id):
            _drag(world, ctx, island)
        if not active(world, island_id):
            _settle(ctx, island)  # 追い払った・飢えた・逃げ尽きたときも、次の反乱まで間を空ける
            continue
        _write_stats(world, island)


def _settle(ctx: StageContext, island: IslandState) -> None:
    """最後の反乱軍がいなくなった島の後始末（鎮まった・首都が落ちたときは済んでいる）。"""
    cfg = ctx.config.get("military.civil_war")
    state = state_of(island)
    if state["cooldown"] > ctx.turn:
        return
    state["alarm"] = 0
    state["calm"] = 0
    state["cooldown"] = ctx.turn + cfg["cooldown"]
    island.log.append(LogEntry("rebellion_ended", {"why": "civil_war_end_routed", "count": 0}))


def _write_stats(world: WorldState, island: IslandState) -> None:
    found = groups_of(world, island.id)
    island.stats["civil_war.rebels"] = sum(g["soldiers"] for g in found)
    island.stats["civil_war.groups"] = len(found)
    island.stats["civil_war.held"] = sum(1 for t in island.tiles if is_rebel_tile(t))


# --- 見張りと終わり --------------------------------------------------------------------------


def _watch(world: WorldState, ctx: StageContext, island: IslandState) -> None:
    """反乱の前触れが続けば立ち上がり、内戦の間は政情が落ち着いて続けば終わらせる。"""
    cfg = ctx.config.get("military.civil_war")
    pol = politics.state_of(island)
    state = state_of(island)
    if not active(world, island.id):
        state["calm"] = 0
        if ctx.turn < state["cooldown"]:
            state["alarm"] = 0  # 鎮まった後・体制が替わった後の間は、前触れを数えない
        elif pol.get("level") == politics.REBELLION:
            state["alarm"] += 1
        else:
            state["alarm"] = 0
        if state["alarm"] >= cfg["trigger_turns"] and ctx.turn >= state["cooldown"]:
            if _spawn(world, ctx, island):
                state["alarm"] = 0
        return
    boundary = ctx.config.get("politics.levels.rebellion")
    if pol.get("stability", 0.0) >= boundary + cfg["end_margin"]:
        state["calm"] += 1
    else:
        state["calm"] = 0
    if state["calm"] >= cfg["end_calm_turns"]:
        _end(world, ctx, island, "calm")


def _end(world: WorldState, ctx: StageContext, island: IslandState, why: str) -> None:
    """反乱を終わらせる: 残った兵は家計に戻り、反乱軍の占領は解ける。"""
    cfg = ctx.config.get("military.civil_war")
    home = 0
    for g in groups_of(world, island.id):
        home += g["soldiers"]
        _vanish(world, ctx, island, g)
    _release_all(world, island)
    state = state_of(island)
    state["calm"] = 0
    state["alarm"] = 0
    state["cooldown"] = ctx.turn + cfg["cooldown"]
    island.log.append(LogEntry("rebellion_ended", {"why": f"civil_war_end_{why}", "count": home}))


def _vanish(world: WorldState, ctx: StageContext, island: IslandState, g: dict[str, Any]) -> None:
    """グループが消える: 武器は島の武器庫へ、兵は家計へ戻る。"""
    if g["weapons"] > 0:
        stock.add(
            island.warehouse, WEAPONS, g["weapons"] * QUANTITY_SCALE, ctx.turn, ctx.defs.goods
        )
    forces.return_people(island, g["soldiers"])
    groups(world).remove(g)


def _release_all(world: WorldState, island: IslandState) -> None:
    """反乱軍がいない島の、反乱軍の占領を解く。"""
    if active(world, island.id):
        return
    for tile in island.tiles:
        if is_rebel_tile(tile):
            del tile.ext["occupied"]
            tile.ext.pop("seized", None)  # 兵舎の武器庫は、次の反乱でまた奪われうる


# --- 立ち上がり -----------------------------------------------------------------------------


def _distance(a: Cell, b: Cell) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _spawn(world: WorldState, ctx: StageContext, island: IslandState) -> bool:
    """反乱軍が首都から遠い大きな町に現れる。首都と陸でつながる町がなければ立ち上がらない。"""
    cfg = ctx.config.get("military.civil_war")
    defs, config = ctx.defs, ctx.config
    capital = island.capital
    assert capital is not None
    smallest = defs.settlement_levels()[0]
    towns = [
        t
        for t in island.tiles
        if t.population > 0 and (t.x, t.y) != capital and not t.ext.get("occupied")
    ]
    big = [t for t in towns if fac.settlement_of(defs, t.population) != smallest]
    for pool in (big, towns):  # 大きな町がなければ、村でも立ち上がる
        ranked = sorted(
            pool,
            key=lambda t: (-t.population * (1 + _distance((t.x, t.y), capital)), t.y, t.x),
        )
        for tile in ranked:
            if army.path(island, defs, config, (tile.x, tile.y), capital) is None:
                continue  # 離れ小島に出て終わらないように、首都と陸でつながる町だけ
            return _start(world, ctx, island, tile, cfg)
    return False


def _take_near(island: IslandState, x: int, y: int, radius: int, count: int) -> int:
    """マス (x, y) の周りの住民から count 人を、人口に比例して抜く（各マスに keep 人は残す）。"""
    keep = 1
    pools = [
        (i, t.population - keep)
        for i, t in enumerate(island.tiles)
        if t.population > keep and _distance((t.x, t.y), (x, y)) <= radius
    ]
    room = sum(p for _, p in pools)
    count = min(count, room)
    if count <= 0:
        return 0
    given: dict[int, int] = {}
    left = count
    for i, pool in pools:
        given[i] = count * pool // room
        left -= given[i]
    for i, pool in sorted(pools, key=lambda p: (-(count * p[1] % room), p[0])):
        if left <= 0:
            break
        if given[i] < pool:
            given[i] += 1
            left -= 1
    for i, n in given.items():
        island.tiles[i].population -= n
    return count


def _start(
    world: WorldState, ctx: StageContext, island: IslandState, tile: Any, cfg: dict[str, Any]
) -> bool:
    """町 tile に反乱軍を立ち上げる。兵は失業者と所得の低い層の中から集める。"""
    from hakoniwa_core.modules.politics import unemployment_of

    whole = forces.population_of(island)
    unemployed = int(whole * unemployment_of(island, ctx.defs))
    size = max(cfg["start_min"], int(unemployed * cfg["start_share"]))
    size = min(size, int(whole * cfg["start_max_share"]))
    got = _take_near(island, tile.x, tile.y, cfg["radius"], size)
    if got <= 0:
        return False
    state = army.world_state(world)
    state["seq"] += 1
    group = {
        "id": state["seq"],
        "island": island.id,
        "x": tile.x,
        "y": tile.y,
        "soldiers": got,
        "weapons": 0,
        "morale": to_basis(cfg["morale"]["start"]),
        "since": ctx.turn,
        "entered": ctx.turn,
        "hungry": False,
    }
    groups(world).append(group)
    island.log.append(LogEntry("rebellion_started", {"x": tile.x, "y": tile.y, "count": got}))
    _hold(world, ctx, island, group)
    return True


# --- 寝返り -------------------------------------------------------------------------------


def _defect(world: WorldState, ctx: StageContext, island: IslandState) -> None:
    """士気が低く賃金の遅れた政府の部隊が、乱数で反乱軍に加わる（MI-40）。部隊の兵と武器を連れて
    いく。島の兵の数からは、亡くなったのでなく抜けた兵として引く。"""
    cfg = ctx.config.get("military.civil_war.defect")
    state = forces.state_of(island)
    if state["morale"] / BASIS >= cfg["morale"] or state["late"] < cfg["late"]:
        return
    mine = groups_of(world, island.id)
    rng = random.Random(f"{world.seed}:{ctx.turn}:defect:{island.id}")
    for unit in list(army.land_units_of(world, island.id)):
        if unit["status"] != army.READY or unit["soldiers"] <= 0 or army.abroad(unit):
            continue
        if "aboard" in unit or rng.random() >= cfg["chance"]:
            continue
        nearest = min(
            mine, key=lambda g: (_distance((g["x"], g["y"]), (unit["x"], unit["y"])), g["id"])
        )
        count = unit["soldiers"]
        nearest["morale"] = _blend(nearest["morale"], nearest["soldiers"], state["morale"], count)
        nearest["soldiers"] += count
        nearest["weapons"] += unit["equipment"].get(WEAPONS, 0)
        forces.lose(island, count)
        army.remove(world, unit)
        island.log.append(LogEntry("unit_defected", {"unit": unit["id"], "count": count}))


def _blend(old: int, old_n: int, new: int, new_n: int) -> int:
    whole = old_n + new_n
    return (old * old_n + new * new_n) // whole if whole else 0


# --- 行軍 ---------------------------------------------------------------------------------


def _defenders(
    world: WorldState, ctx: StageContext, island: IslandState, x: int, y: int
) -> list[dict[str, Any]]:
    """マス (x, y) で反乱軍と戦う政府の側（政府の部隊と、守る警察）。部隊は defend_radius マス以内に
    いれば駆けつける（兵舎のそばの部隊が、首都の守りに加わる）。"""
    reach = ctx.config.get("military.civil_war.defend_radius")
    units = [
        u
        for u in army.on_land(world, island.id)
        if u["owner"] == island.id
        and _distance((u["x"], u["y"]), (x, y)) <= reach
        and u["status"] == army.READY
        and u["soldiers"] > 0
    ]
    tile = army.tile_at(island, x, y)
    if tile is not None and tile.ext.get("police_down") != ctx.turn:
        if battle._police_holds(ctx, island, x, y):
            police = battle.police_unit(world, ctx, island)
            if police is not None:
                units.append(police)
    return units


def _march(world: WorldState, ctx: StageContext, island: IslandState, g: dict[str, Any]) -> None:
    """首都へ向かって進む。政府の側がいるマスか、まだ占領していない集落に入ったら、そこで止まる。"""
    cfg = ctx.config.get("military.civil_war")
    capital = island.capital
    assert capital is not None
    if (g["x"], g["y"]) == capital or g.get("fought"):
        return  # 首都にいる・戦っている間はその場にとどまる
    route = army.path(island, ctx.defs, ctx.config, (g["x"], g["y"]), capital)
    if not route:
        return
    left = to_milli(cfg["speed"])
    for step in route:
        if left <= 0:
            break
        tile = army.tile_at(island, *step)
        assert tile is not None
        left -= army.enter_cost(tile, ctx.defs, ctx.config) or 0
        g["x"], g["y"] = step
        g["entered"] = ctx.turn
        if _defenders(world, ctx, island, *step):
            break
        if tile.population > 0 and not is_rebel_tile(tile):
            break  # 集落を占領して、そこで兵を集める


# --- 戦い ---------------------------------------------------------------------------------


def _power(ctx: StageContext, g: dict[str, Any]) -> float:
    """反乱軍の強さ: 歩兵の強さ × 兵の割合 × 武器のそろい（猟銃や農具の下限つき）× 練度の下限 × 士気
    × 食べ物。"""
    cfg = ctx.config.get("military.civil_war")
    combat = ctx.config.get("military.combat")
    spec = ctx.defs.army["infantry"]
    men = g["soldiers"] / max(1, spec["soldiers"])
    armed = g["weapons"] / g["soldiers"] if g["soldiers"] else 0.0
    equipment = battle._clamp(armed, cfg["improvised"], 1.0)
    morale = battle._clamp(
        g["morale"] / BASIS / ctx.config.get("military.morale.start"), combat["morale_floor"], 1.1
    )
    food = combat["hungry"] if g["hungry"] else 1.0
    return spec["strength"] * men * equipment * combat["training_floor"] * morale * food


def _fight(world: WorldState, ctx: StageContext, island: IslandState, g: dict[str, Any]) -> None:
    """反乱軍のいるマスに政府の側がいれば戦う。入ってきた側が攻め、迎えた側は地形・陣地・町で強くなる。"""
    cfg = ctx.config.get("military.combat")
    side = _defenders(world, ctx, island, g["x"], g["y"])
    if not side:
        g.pop("fought", None)
        g.pop("engaged", None)
        return
    tile = army.tile_at(island, g["x"], g["y"])
    terrain = tile.terrain if tile is not None else ""
    shield = battle.defense_factor(world, ctx, island, g["x"], g["y"])
    gov_entered = any(u.get("entered") == ctx.turn for u in side if not u.get("police"))
    rebels_attack = g["entered"] == ctx.turn and not gov_entered
    gov_attack = gov_entered and g["entered"] != ctx.turn
    power_gov = sum(
        battle.police_power(ctx, u)
        if u.get("police")
        else battle.unit_power(world, ctx, u, terrain)
        for u in side
    )
    power_rebel = _power(ctx, g)
    if rebels_attack:
        power_gov *= shield
    elif gov_attack:
        power_rebel *= shield
    loss_gov = battle._loss(cfg, power_rebel, power_gov)
    loss_rebel = battle._loss(cfg, power_gov, power_rebel)
    if power_gov <= 0 or power_rebel / max(power_gov, 1e-9) > cfg["break"]["ratio"]:
        if tile is not None:
            tile.ext["police_down"] = ctx.turn  # 警察は持ちこたえられない
    g.setdefault("engaged", g["soldiers"])
    g["fought"] = g.get("fought", 0) + 1
    gov_dead = {"killed": 0, "wounded": 0}
    for part_ in (
        [
            u for u in side if not u.get("police")
        ],  # 警察の損害は住民が亡くなる（兵の数には数えない）
        [u for u in side if u.get("police")],
    ):
        if part_:
            for key, value in battle._casualties(
                world, ctx, island, island.id, part_, loss_gov
            ).items():
                gov_dead[key] += value
    lost = min(g["soldiers"], max(cfg["min_casualties"], round(g["soldiers"] * loss_rebel)))
    dead = round(lost * cfg["killed_share"])
    g["weapons"] = max(0, g["weapons"] - round(g["weapons"] * lost / max(1, g["soldiers"])))
    g["soldiers"] -= lost
    forces.return_people(island, lost - dead)  # 負傷した兵は家へ帰る
    monuments.add_dead(island, monuments.WAR_DEAD, gov_dead["killed"] + dead)
    island.stats["military.killed"] = island.stats.get("military.killed", 0) + gov_dead["killed"]
    island.stats["military.kills"] = island.stats.get("military.kills", 0) + dead
    island.stats["civil_war.dead"] = (
        island.stats.get("civil_war.dead", 0) + dead + gov_dead["killed"]
    )
    island.log.append(
        LogEntry(
            "civil_battle",
            {
                "x": g["x"],
                "y": g["y"],
                "killed": gov_dead["killed"],
                "wounded": gov_dead["wounded"],
                "enemy_killed": dead,
            },
        )
    )
    _morale(island, g, loss_gov, loss_rebel, ctx)
    _civilians(world, ctx, island, g)
    for u in [u for u in side if not u.get("police") and u["soldiers"] <= 0]:
        army.remove(world, u)
    if g["soldiers"] <= 0:
        _vanish(world, ctx, island, g)
        return
    brk = cfg["break"]
    broke = (
        g["morale"] / BASIS < brk["morale"]
        or (power_rebel > 0 and power_gov / power_rebel > brk["ratio"])
        or g["soldiers"] < g["engaged"] * brk["remaining"]
    )
    if broke:
        island.log.append(
            LogEntry("rebels_routed", {"x": g["x"], "y": g["y"], "count": g["soldiers"]})
        )
        _vanish(world, ctx, island, g)


def _morale(
    island: IslandState, g: dict[str, Any], loss_gov: float, loss_rebel: float, ctx: StageContext
) -> None:
    cfg = ctx.config.get("military.combat")["morale"]
    gov = forces.state_of(island)
    if loss_gov < loss_rebel:
        gov["morale"] = min(BASIS, gov["morale"] + to_basis(cfg["win"]))
        g["morale"] = max(0, g["morale"] - to_basis(cfg["lose"]))
    elif loss_rebel < loss_gov:
        gov["morale"] = max(0, gov["morale"] - to_basis(cfg["lose"]))
        g["morale"] = min(BASIS, g["morale"] + to_basis(cfg["win"]))


def _civilians(
    world: WorldState, ctx: StageContext, island: IslandState, g: dict[str, Any]
) -> None:
    """戦場のマスの住民の一部が亡くなり、一部は同じ島のほかの町へ逃げる（外国との戦争と同じ。MI-30）。"""
    battle._civilians(world, ctx, island, g["x"], g["y"])


# --- 占領と補給 ---------------------------------------------------------------------------


def _hold(world: WorldState, ctx: StageContext, island: IslandState, g: dict[str, Any]) -> None:
    """守りのいない集落と施設のあるマスを占領する。占領した兵舎では島の武器庫の武器を奪う。"""
    tile = army.tile_at(island, g["x"], g["y"])
    if tile is None or _defenders(world, ctx, island, g["x"], g["y"]):
        return
    held = tile.ext.get("occupied")
    if held and held.get("by") != REBELS:
        return  # 外国の軍が占領しているマスは、反乱軍が上書きしない
    if (tile.x, tile.y) == island.capital:
        _overthrow(world, ctx, island, g)
        return
    facility = island.facility_at(tile.x, tile.y)
    if tile.population <= 0 and facility is None:
        return
    if not is_rebel_tile(tile):
        tile.ext["occupied"] = {"by": REBELS, "turn": ctx.turn, "rebel": True}
        island.log.append(LogEntry("tile_rebel_held", {"x": tile.x, "y": tile.y}))
    cfg = ctx.config.get("military.civil_war")
    if facility is not None and facility.built and not tile.ext.get("seized"):
        if ctx.defs.facilities[facility.kind].get("military", {}).get("barracks"):
            tile.ext["seized"] = ctx.turn
            have = stock.total(island.warehouse, WEAPONS) // QUANTITY_SCALE
            took = int(have * cfg["seize_share"])
            if took > 0:
                stock.take(island.warehouse, WEAPONS, took * QUANTITY_SCALE)
                g["weapons"] += took
                island.log.append(LogEntry("arsenal_seized", {"count": took}))


def _recruit(world: WorldState, ctx: StageContext, island: IslandState, g: dict[str, Any]) -> None:
    """占領したマスの住民から兵を集める（兵の数は住民に対する上限まで）。"""
    cfg = ctx.config.get("military.civil_war")
    tile = army.tile_at(island, g["x"], g["y"])
    if tile is None or not is_rebel_tile(tile) or ctx.turn - g["since"] >= cfg["drag_turns"]:
        return  # 長引いた内戦では、新しく立ち上がる人はいない（疲れ）
    room = int(forces.population_of(island) * cfg["max_share"]) - sum(
        x["soldiers"] for x in groups_of(world, island.id)
    )
    want = min(room, max(0, round(tile.population * cfg["recruit"])))
    got = _take_near(island, tile.x, tile.y, 0, want)
    if got > 0:
        g["morale"] = _blend(g["morale"], g["soldiers"], to_basis(cfg["morale"]["start"]), got)
        g["soldiers"] += got


def _eat(world: WorldState, ctx: StageContext, island: IslandState, g: dict[str, Any]) -> None:
    """兵が食べる。占領したマスの施設の在庫から取り立て、足りなければ飢えて兵が逃げる。"""
    from hakoniwa_core.economy import books

    cfg = ctx.config.get("military.civil_war")
    need = g["soldiers"] * to_milli(ctx.config.get("military.food_per_thousand")) // 1000
    got = 0
    tile = army.tile_at(island, g["x"], g["y"])
    if tile is not None and is_rebel_tile(tile):
        facility = island.facility_at(tile.x, tile.y)
        if facility is not None:
            for good in FOOD_GOODS:
                taken = stock.take(facility.stock, good, need - got)
                if taken > 0:
                    books.stock_moved(island, facility.id, good, -taken)
                    got += taken
    g["hungry"] = got < need
    if not g["hungry"] or need <= 0:
        return
    lack = 1 - got / need
    gone = min(g["soldiers"], max(1, round(g["soldiers"] * cfg["hungry_desert"] * lack)))
    g["soldiers"] -= gone
    forces.return_people(island, gone)
    if g["soldiers"] <= 0:
        _vanish(world, ctx, island, g)


def _lift(world: WorldState, ctx: StageContext, island: IslandState) -> None:
    """反乱軍のいなくなったマスに政府の部隊が入ったら、反乱軍の占領を解く。"""
    here = {(g["x"], g["y"]) for g in groups_of(world, island.id)}
    for tile in island.tiles:
        if not is_rebel_tile(tile) or (tile.x, tile.y) in here:
            continue
        if any(
            u["owner"] == island.id
            and (u["x"], u["y"]) == (tile.x, tile.y)
            and u["status"] == army.READY
            and u["soldiers"] > 0
            for u in army.on_land(world, island.id)
        ):
            del tile.ext["occupied"]
            tile.ext.pop("seized", None)
            island.log.append(LogEntry("tile_rebel_lifted", {"x": tile.x, "y": tile.y}))


def _drag(world: WorldState, ctx: StageContext, island: IslandState) -> None:
    """決着せずに長引いた内戦では、反乱軍の兵が少しずつ逃げる（疲れて家へ帰る）。"""
    cfg = ctx.config.get("military.civil_war")
    for g in list(groups_of(world, island.id)):
        if ctx.turn - g["since"] < cfg["drag_turns"]:
            continue
        gone = min(g["soldiers"], max(1, round(g["soldiers"] * cfg["drag_desert"])))
        g["soldiers"] -= gone
        forces.return_people(island, gone)
        if g["soldiers"] <= 0:
            island.log.append(LogEntry("rebels_dispersed", {"x": g["x"], "y": g["y"]}))
            _vanish(world, ctx, island, g)


# --- 体制が倒れる -------------------------------------------------------------------------


def _overthrow(
    world: WorldState, ctx: StageContext, island: IslandState, g: dict[str, Any]
) -> None:
    """反乱軍が首都を落とす: 体制が倒れ、持ち主が選び直す（降伏と同じ作り。MI-41・MI-58）。"""
    from hakoniwa_core.modules import politics as politics_stage

    cfg = ctx.config.get("military.civil_war")
    island.log.append(LogEntry("revolution", {}))
    politics_stage.lose_regime(island, ctx, "revolution", 0)
    for other in groups_of(world, island.id):
        _vanish(world, ctx, island, other)
    _release_all(world, island)
    state = state_of(island)
    state["alarm"] = 0
    state["calm"] = 0
    state["cooldown"] = ctx.turn + cfg["revolution_cooldown"]
