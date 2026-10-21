"""外国の島にいる部隊の補給と飢え（フェーズ4-3 の①。設計書 15-military の MI-25・MI-64）。

外国の島の部隊は、その島の市場でも自国の国の倉庫でも買わず、食べ物・弾薬・燃料を自分の
`stores`（財 →
1000 分の 1 単位）で持ちます。持てるのは `military.supply.store_turns` ターン分までです。
補いは次の 2 つだけ
です。

- 船で運んだ補給: 輸送船団（`fleet["goods"]`）がその島の区画にいれば、1 隻あたり
  `unload_per_ship` トンを
  1 ターンに部隊へ渡せます。占領した港があれば `port_unload` 倍（港を取れば補給が楽になる）。
  積み込みは母港の区画で命令 load_supplies（国の倉庫から。MI-22 の船の重さの上限に数える）。
- 占領したマスの取り立て（徴発。命令 set_requisition で部隊ごとに切り替え。初めは取り立てる）。

補給が届かなければ持っている分を食べ尽くし、食べ物が尽きると部隊は弱くなり（battle.py の hungry）、
病気と飢えで兵が亡くなります（MI-14）。講和で戦争が終わると、相手の島の部隊は期限までに引き揚げ、
期限を過ぎてもいる部隊はその場で除隊して自国の家計に戻ります（見直し役が挙げた場面への手当て）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import monuments
from hakoniwa_core.diplomacy import war
from hakoniwa_core.military import army, battle, forces, occupation, sea
from hakoniwa_core.state import LogEntry, WorldState
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, part, to_basis, to_milli

FOOD = "food"
AMMO = "ammunition"
FUEL = "fuel"
GOODS = (FOOD, AMMO, FUEL)


def per_turn(ctx: StageContext, unit: dict[str, Any]) -> dict[str, int]:
    """部隊が 1 ターンに使う補給（食べ物は毎ターン、弾薬と燃料は戦ったターンの量）。"""
    food = unit["soldiers"] * to_milli(ctx.config.get("military.food_per_thousand")) // 1000
    return {FOOD: food, AMMO: battle.ammo_need(ctx, unit), FUEL: battle.fuel_need(ctx, unit)}


def capacity(ctx: StageContext, unit: dict[str, Any]) -> dict[str, int]:
    turns = ctx.config.get("military.supply.store_turns")
    return {good: qty * turns for good, qty in per_turn(ctx, unit).items()}


def fleet_goods(fleet: dict[str, Any]) -> dict[str, int]:
    """輸送船団が積んでいる補給（財 → 1000 分の 1 単位）。"""
    return fleet.setdefault("goods", {})


def goods_weight(fleet: dict[str, Any]) -> float:
    """積んでいる補給の重さ（トン）。"""
    return sum(fleet.get("goods", {}).values()) / QUANTITY_SCALE


def run(world: WorldState, ctx: StageContext) -> None:
    """外国の島の部隊を補給し、食べさせる。島ごとの外国にいる兵の数を数え直す。"""
    abroad_total: dict[int, int] = {}
    for island_id in world.island_ids():
        loc = world.islands[island_id]
        units = [u for u in army.on_land(world, island_id) if army.abroad(u)]
        if not units:
            continue
        pool = _fleet_pool(world, ctx, loc)
        for unit in sorted(units, key=lambda u: u["id"]):
            if unit["status"] != army.READY or unit["soldiers"] <= 0:
                continue
            _resupply(world, ctx, loc, unit, pool)
            _eat(world, ctx, unit)
            abroad_total[unit["owner"]] = abroad_total.get(unit["owner"], 0) + unit["soldiers"]
    for island_id in world.island_ids():
        island = world.islands[island_id]
        if island.ext.get(forces.KEY) is not None or abroad_total.get(island_id):
            forces.state_of(island)["abroad"] = abroad_total.get(island_id, 0)


def _fleet_pool(world: WorldState, ctx: StageContext, loc) -> dict[int, dict[str, Any]]:
    """島の区画にいる輸送船団ごとの、今ターン渡せる量の残り。"""
    cfg = ctx.config.get("military.supply")
    pools: dict[int, dict[str, Any]] = {}
    for fleet in army.world_state(world)["units"]:
        if not army.is_sea(fleet) or (fleet["x"], fleet["y"]) != (loc.wx, loc.wy):
            continue
        spec = ctx.defs.army.get(fleet["kind"], {})
        if not spec.get("capacity") or fleet["status"] != army.READY:
            continue
        rate = cfg["unload_per_ship"] * army.size(fleet)
        if occupation.holds_port(loc, ctx.config, fleet["owner"]):
            rate *= cfg["port_unload"]
        pools[fleet["id"]] = {"fleet": fleet, "left": to_milli(rate)}
    return pools


def _resupply(
    world: WorldState,
    ctx: StageContext,
    loc,
    unit: dict[str, Any],
    pool: dict[int, dict[str, Any]],
) -> None:
    stores = battle.stores_of(unit)
    cap = capacity(ctx, unit)
    tile = army.tile_at(loc, unit["x"], unit["y"])
    if tile is not None and occupation.occupier(tile) == unit["owner"]:
        if unit.get("requisition", True):
            wants = {g: max(0, cap[g] - stores.get(g, 0)) for g in GOODS}
            for good, qty in occupation.requisition(world, ctx, loc, tile, wants).items():
                stores[good] = stores.get(good, 0) + qty
    for entry in pool.values():
        if entry["fleet"]["owner"] != unit["owner"] or entry["left"] <= 0:
            continue
        goods = fleet_goods(entry["fleet"])
        for good in GOODS:
            give = min(max(0, cap[good] - stores.get(good, 0)), goods.get(good, 0), entry["left"])
            if give > 0:
                goods[good] -= give
                entry["left"] -= give
                stores[good] = stores.get(good, 0) + give


def _eat(world: WorldState, ctx: StageContext, unit: dict[str, Any]) -> None:
    """部隊が食べる。足りなければ食べ物が尽きた印（hungry）が付き、兵が亡くなる（MI-14）。"""
    need = per_turn(ctx, unit)[FOOD]
    eaten = battle._use(world, unit, FOOD, need)
    unit["hungry"] = eaten < need
    if not unit["hungry"] or need <= 0:
        return
    lack = BASIS - eaten * BASIS // need
    island = world.islands[unit["owner"]]
    dead = min(
        unit["soldiers"],
        max(
            1,
            part(unit["soldiers"], part(to_basis(ctx.config.get("military.supply.starve")), lack)),
        ),
    )
    if dead > 0:
        unit["soldiers"] -= dead
        forces.lose(island, dead)
        monuments.add_dead(island, monuments.WAR_DEAD, dead)
        island.log.append(LogEntry("military_starved", {"count": dead}))
        if unit["soldiers"] <= 0:
            army.remove(world, unit)  # 兵が 0 の部隊は消える


def recall(world: WorldState, ctx: StageContext) -> None:
    """講和で戦争が終わった相手の島の部隊のうち、引き揚げの期限を過ぎたものは、その場で除隊して
    自国の家計に戻る（船がなくて帰れない場合の手当て）。"""
    for unit in list(army.world_state(world)["units"]):
        if army.is_sea(unit) or not army.abroad(unit):
            continue
        key = war.pair(unit["owner"], army.where(unit))
        if war.open_war(world, unit["owner"], army.where(unit)) is not None:
            continue  # 同じ相手とまた戦っている間は、前の戦争の引き揚げを使わない
        for w in war.wars(world):
            if war.pair(w["attacker"], w["defender"]) != key or w["state"] != war.ENDED:
                continue
            if w["withdraw_until"] is not None and ctx.turn > w["withdraw_until"]:
                owner = world.islands[unit["owner"]]
                forces.lose(owner, unit["soldiers"])
                forces.return_people(owner, unit["soldiers"])
                owner.log.append(
                    LogEntry(
                        "unit_demobilized_abroad", {"unit": unit["id"], "count": unit["soldiers"]}
                    )
                )
                army.remove(world, unit)
                break


def landing_tons(ctx: StageContext) -> float:
    return ctx.config.get("military.combat.landing_tons")


def cargo_load(world: WorldState, ctx: StageContext, fleet: dict[str, Any]) -> float:
    """輸送船団が積んでいる重さの合計（トン。部隊と補給）。"""
    return sea.cargo_weight(world, ctx.defs, fleet) + goods_weight(fleet)
