"""占領と降伏と捕虜（フェーズ4-3 の①。設計書 15-military の MI-36・MI-49・MI-58）。

戦闘の後に、戦争の相手の部隊だけがいるマスは占領されます（ハーグ陸戦条約の軍政）。占領したマスは元の国のまま
で、住民も元の国の人です。占領されたマス（`tile.ext["occupied"] = {"by", "turn"}`）
の施設は産出が止まり
（雇用の段が働き手を雇わず、生産の段が止まる）、占領した国はそこの在庫を部隊の補給に取り立てられます
（徴発。持ち帰りはしない）。占領は、占領した国の部隊がそのマスからいなくなる・講和が効く・戦争が
終わる、のどれかで解けます。停戦の間は占領も取り立ても続きます。

首都のマスを占領されたとき、その国は 1 回だけ降伏します（1940 年のフランス）。
体制が倒れて混乱期間に入り
（`politics.transition`）、持ち主が命令 choose_regime
で全部の軸を選び直せるようになり（決めない間は今の体制の
まま）、首都を落とした相手とは停戦になります。ほかの国との戦争は続きます。混乱期間の間に降伏したときは
選び直しを省き、停戦だけにします。

捕虜は、降伏した部隊の兵です。捕らえた国の島の `ext["military"]["prisoners"]`（[ターン, 出身の島,
人数] の
並び）に持ち、捕らえた国の国防費で食べさせ、出身の島の人口には「捕虜」として数えます。講和が効いたとき、
または捕らえてから `military.occupation.prisoner_turns` ターンがたったときに、
出身の島の家計に帰します。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import stock
from hakoniwa_core.diplomacy import war
from hakoniwa_core.military import army, battle, forces
from hakoniwa_core.state import IslandState, LogEntry, Tile, WorldState
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import QUANTITY_SCALE

OCCUPIED = "occupied"
SURRENDER = -1  # 降伏による停戦の印（war["ceasefire"] に入れる。条約の番号は 1 から）


def occupier(tile: Tile) -> int | None:
    """マスを占領している国の島の番号（占領されていなければ None）。"""
    held = tile.ext.get(OCCUPIED)
    return held["by"] if held else None


def occupied_tiles(island: IslandState) -> list[Tile]:
    return [t for t in island.tiles if t.ext.get(OCCUPIED)]


def holds_port(island: IslandState, config: Any, by: int) -> bool:
    """by が占領しているマスに、建ち終わった港があるか（補給が楽になる）。"""
    port = config.get("trade.port_facility")
    for f in island.facilities:
        tile = army.tile_at(island, f.x, f.y)
        if f.built and f.kind == port and tile is not None and occupier(tile) == by:
            return True
    return False


# --- 毎ターン -----------------------------------------------------------------------------


def update(world: WorldState, ctx: StageContext) -> None:
    """占領を決め直す（戦闘の後）: 占領するマス・解けるマスを決め、占領されたマスの施設を止める。"""
    for island_id in world.island_ids():
        island = world.islands[island_id]
        here: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for u in army.on_land(world, island_id):
            if _present(u):
                here.setdefault((u["x"], u["y"]), []).append(u)
        for tile in island.tiles:
            pos = (tile.x, tile.y)
            held = tile.ext.get(OCCUPIED)
            units = here.get(pos, [])
            if held:
                _check_lift(world, ctx, island, tile, units)
            elif units:
                _check_occupy(world, ctx, island, tile, units)
        _halt_occupied(ctx, island)


def _present(unit: dict[str, Any]) -> bool:
    return unit["status"] == army.READY and unit["soldiers"] > 0


def _check_occupy(
    world: WorldState,
    ctx: StageContext,
    island: IslandState,
    tile: Tile,
    units: list[dict[str, Any]],
) -> None:
    """そのマスに戦争の相手の部隊だけがいれば占領する（島の持ち主の部隊と、守る警察はいない）。"""
    invaders = [u for u in units if battle.hostile(world, island.id, u["owner"], ctx.turn)]
    if not invaders or any(u["owner"] == island.id for u in units):
        return
    owners = sorted({u["owner"] for u in invaders})
    if len(owners) > 1 and any(
        battle.hostile(world, a, b, ctx.turn) for a in owners for b in owners if a < b
    ):
        return  # 攻める国どうしが戦っている間は、どちらのものでもない
    pos = (tile.x, tile.y)
    if tile.ext.get("police_down") != ctx.turn and battle._police_holds(ctx, island, *pos):
        police = battle.police_unit(world, ctx, island)
        if police is not None:
            return  # 警察がまだ守っている
    by = owners[0]
    tile.ext[OCCUPIED] = {"by": by, "turn": ctx.turn}
    captor = world.islands[by]
    island.log.append(LogEntry("tile_occupied", {"x": tile.x, "y": tile.y, "country": captor.name}))
    captor.log.append(
        LogEntry("tile_occupying", {"x": tile.x, "y": tile.y, "country": island.name})
    )
    if island.capital == pos:
        surrender(world, ctx, island, captor)


def _check_lift(
    world: WorldState,
    ctx: StageContext,
    island: IslandState,
    tile: Tile,
    units: list[dict[str, Any]],
) -> None:
    held = tile.ext[OCCUPIED]
    by = held["by"]
    still = any(u["owner"] == by for u in units)
    open_war = war.open_war(world, island.id, by)
    if still and open_war is not None:
        return
    del tile.ext[OCCUPIED]
    island.log.append(LogEntry("occupation_lifted", {"x": tile.x, "y": tile.y}))


def _halt_occupied(ctx: StageContext, island: IslandState) -> None:
    """占領されたマスの施設を止め（次のターンの生産と市場）、雇用の段が働き手を雇わない印を付ける。"""
    for f in island.facilities:
        tile = army.tile_at(island, f.x, f.y)
        taken = tile is not None and tile.ext.get(OCCUPIED)
        if taken and f.built:
            f.ext[OCCUPIED] = True
            f.ext["halted_until"] = max(f.ext.get("halted_until", 0), ctx.turn + 1)
            f.ext.setdefault("halt_reason", "occupied")
        elif f.ext.pop(OCCUPIED, None) and f.ext.get("halt_reason") == "occupied":
            f.ext.pop("halted_until", None)
            f.ext.pop("halt_reason", None)


def requisition(
    world: WorldState,
    ctx: StageContext,
    island: IslandState,
    tile: Tile,
    wants: dict[str, int],
) -> dict[str, int]:
    """占領したマスの施設の在庫から、wants（財 → 1000 分の 1 単位）を取り立てる。
    取り立てた量を返す。
    施設の在庫の損失として帳簿に出る（持ち帰らず、その場の部隊が使う）。"""
    from hakoniwa_core.economy import books

    facility = island.facility_at(tile.x, tile.y)
    if facility is None:
        return {}
    allowed = ctx.config.get("military.occupation.requisition")
    got: dict[str, int] = {}
    for good, qty in sorted(wants.items()):
        if qty <= 0 or good not in allowed:
            continue
        taken = stock.take(facility.stock, good, qty)
        if taken > 0:
            books.stock_moved(island, facility.id, good, -taken)
            got[good] = taken
    return got


# --- 降伏 ---------------------------------------------------------------------------------


def surrender(
    world: WorldState, ctx: StageContext, loser: IslandState, winner: IslandState
) -> None:
    """首都を占領された国が降伏する（始まったときの 1 回だけ。MI-36・MI-58）。"""
    from hakoniwa_core.modules import politics as politics_stage

    w = war.open_war(world, loser.id, winner.id)
    if w is not None and w["state"] == war.WAR:
        w["state"] = war.CEASEFIRE
        w["ceasefire"] = SURRENDER
    loser.log.append(LogEntry("surrendered", {"country": winner.name}))
    winner.log.append(LogEntry("enemy_surrendered", {"country": loser.name}))
    from hakoniwa_core import politics

    if politics.in_transition(loser, ctx.turn):
        return
    politics_stage._start_transition(loser, ctx, "surrender")
    politics.state_of(loser)["reselect"] = {"since": ctx.turn, "by": winner.id}


# --- 捕虜 ---------------------------------------------------------------------------------


def held(island: IslandState) -> int:
    """島が捕らえている捕虜の数。"""
    return sum(n for _, _, n in island.ext.get(forces.KEY, {}).get("prisoners", []))


def release(world: WorldState, ctx: StageContext) -> None:
    """捕虜を帰す: 講和が効いて戦争が終わった相手の分と、捕らえてから決めたターンがたった分。
    出身の島の住民として家計に戻す。あわせて、出身の島ごとの「捕らえられている数」を数え直す。"""
    limit = ctx.config.get("military.occupation.prisoner_turns")
    captive: dict[int, int] = {}
    for island_id in world.island_ids():
        island = world.islands[island_id]
        batches = island.ext.get(forces.KEY, {}).get("prisoners")
        if not batches:
            continue
        keep = []
        for turn, origin, count in batches:
            home = world.islands.get(origin)
            ended = _ended_with_prisoners(world, island_id, origin)
            if ended or ctx.turn - turn >= limit or home is None:
                if home is not None:
                    forces.return_people(home, count)
                    home.log.append(
                        LogEntry("prisoners_returned", {"count": count, "country": island.name})
                    )
                continue
            keep.append([turn, origin, count])
            captive[origin] = captive.get(origin, 0) + count
        island.ext[forces.KEY]["prisoners"] = keep
    for island_id in world.island_ids():
        state = world.islands[island_id].ext.get(forces.KEY)
        if state is not None or captive.get(island_id):
            forces.state_of(world.islands[island_id])["captive"] = captive.get(island_id, 0)


def _ended_with_prisoners(world: WorldState, a: int, b: int) -> bool:
    if war.open_war(world, a, b) is not None:
        return False  # また戦っている間は、前の戦争の捕虜の送還を使わない
    key = war.pair(a, b)
    return any(
        w["state"] == war.ENDED and war.pair(w["attacker"], w["defender"]) == key and w["prisoners"]
        for w in war.wars(world)
    )


def return_wounded(world: WorldState, ctx: StageContext) -> None:
    """負傷から戻る兵: 決めたターンがたったら、戻る割合は兵に戻り、残りは除隊して家計に戻る。"""
    share = ctx.config.get("military.combat.wounded_return.share")
    for island_id in world.island_ids():
        island = world.islands[island_id]
        state = island.ext.get(forces.KEY)
        if not state or not state.get("wounded"):
            continue
        keep = []
        for turn, count in state["wounded"]:
            if turn > ctx.turn:
                keep.append([turn, count])
                continue
            back = round(count * share)
            state["volunteers"] += back
            forces.return_people(island, count - back)
        state["wounded"] = keep


def total_stock(unit: dict[str, Any]) -> int:
    return sum(unit.get("stores", {}).values()) // QUANTITY_SCALE
