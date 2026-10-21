"""軍の動きの段（operations。フェーズ4-2 の③。世界の段）と、部隊の命令。

部隊の編成・解散・動き・陣地の命令は、命令の段で島の申し送りに積み、この段が島の番号の順に決めます
（外交・貿易と同じ形）。そのあと島ごとに、兵の合計に合わせて部隊の兵を整え、編成中の部隊に兵と装備を
集め、動きの命令の部隊を進め、陣地を掘り進めます。部隊と陣地の状態を書くのはこの段だけです（MI-54）。

置く場所は、市場の段の後・貿易の段の前です（市場で国の倉庫に届いた装備を、同じターンのうちに部隊へ）。
この段の中の順は「命令（揚陸もここ）→ 動き → 陸の戦闘 → 占領 → 外国の島の部隊の補給 → 捕虜と負傷者 →
見える範囲」です（MI-50。海の戦いとミサイルは 4-3 の②・③で足します）。

中身は hakoniwa_core/military/army.py、設計は docs/design/15-military.md の MI-18〜21・MI-50〜54。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.diplomacy import war
from hakoniwa_core.military import army, battle, forces, occupation, sea, supply, vision
from hakoniwa_core.registry import OrderFailed, OrderHandler, Registry, WorldStage
from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import QUANTITY_SCALE, to_milli

REQUESTS = "military_requests"
FUEL = "fuel"
AMMO = "ammunition"
VEHICLE = "military_vehicle"


def request(island: IslandState, kind: str, payload: dict[str, Any]) -> None:
    """命令の段から、この段への申し送りを積む。"""
    island.scratch.setdefault(REQUESTS, []).append({"kind": kind, **payload})


class Operations(WorldStage):
    name = "operations"

    def collect(self, island: IslandState, ctx: StageContext) -> Any:
        return None

    def resolve(self, requests: dict[int, Any], ctx: StageContext) -> dict[int, Any]:
        assert ctx.before is not None
        world = ctx.before
        army.world_state(world)
        for i in world.island_ids():
            island = world.islands[i]
            for req in island.scratch.pop(REQUESTS, []):
                reason = _handle(world, ctx, island, req)
                if reason is not None:
                    island.log.append(
                        LogEntry("unit_order_failed", {"order": req["kind"], "reason": reason})
                    )
        for i in world.island_ids():
            _run_island(world, ctx, world.islands[i])
        battle.run(world, ctx)
        occupation.update(world, ctx)
        supply.run(world, ctx)
        supply.recall(world, ctx)
        occupation.release(world, ctx)
        occupation.return_wounded(world, ctx)
        vision.update(world, ctx.defs, ctx.turn, ctx.config.get("military.vision.land"))
        return {}

    def apply(self, island: IslandState, result: Any, ctx: StageContext) -> None:
        return None


# --- 申し送りの処理 -----------------------------------------------------------------------


def _handle(
    world: WorldState, ctx: StageContext, island: IslandState, req: dict[str, Any]
) -> str | None:
    kind = req["kind"]
    if kind == "form_unit":
        return _form(world, ctx, island, req)
    if kind == "form_fleet":
        return _form_fleet(world, ctx, island, req)
    unit = army.find(
        world, req.get("fleet" if kind in ("sail_fleet", "load_supplies") else "unit", -1)
    )
    if kind == "load_supplies" and unit is not None and unit["owner"] == island.id:
        return _load_supplies(world, ctx, island, unit, req)
    if unit is None or unit["owner"] != island.id:
        return "reason_no_unit"
    if kind == "disband_unit":
        return _disband(world, ctx, island, unit)
    if kind == "stop_unit":
        unit["order"] = None
        return None
    if kind == "sail_fleet":
        return _sail_order(world, ctx, island, unit, req)
    if kind in ("load_unit", "unload_unit"):
        return _cargo_order(world, ctx, island, unit, req, load=kind == "load_unit")
    if kind == "set_requisition":
        unit["requisition"] = bool(req["enabled"])
        return None
    if army.is_sea(unit) or "aboard" in unit:
        return "reason_unit_at_sea" if army.is_sea(unit) else "reason_unit_aboard"
    if kind == "move_unit":
        return _move_order(world, ctx, island, unit, req)
    if kind == "dig_trench":
        return _dig_order(ctx, island, unit)
    return "reason_unknown_order"


def _form(
    world: WorldState, ctx: StageContext, island: IslandState, req: dict[str, Any]
) -> str | None:
    kind, x, y = req["army"], req["x"], req["y"]
    spec = ctx.defs.army.get(kind)
    if spec is None or spec["arm"] != "land":
        return "reason_unit_kind"
    if not island.has_economy:
        return "reason_no_economy"
    capacity = army.barracks_at(island, ctx.defs, x, y)
    if capacity <= 0:
        return "reason_no_barracks"
    if army.housed(world, ctx.defs, island.id, x, y) + spec["soldiers"] > capacity:
        return "reason_barracks_full"
    unit = army.new_unit(world, island.id, kind, x, y)
    island.log.append(LogEntry("unit_forming", {"army": kind, "unit": unit["id"], "x": x, "y": y}))
    return None


def _disband(
    world: WorldState, ctx: StageContext, island: IslandState, unit: dict[str, Any]
) -> str | None:
    if army.is_sea(unit):
        if not sea.docked(unit, island):
            return "reason_fleet_at_sea"
        if unit["cargo"]:
            return "reason_fleet_cargo"
    if "aboard" in unit:
        return "reason_unit_aboard"
    for good, count in unit["equipment"].items():
        stock.add(island.warehouse, good, count * QUANTITY_SCALE, ctx.turn, ctx.defs.goods)
    island.log.append(
        LogEntry(
            "unit_disbanded", {"army": unit["kind"], "unit": unit["id"], "count": unit["soldiers"]}
        )
    )
    army.remove(world, unit)
    return None


def _move_order(
    world: WorldState,
    ctx: StageContext,
    island: IslandState,
    unit: dict[str, Any],
    req: dict[str, Any],
) -> str | None:
    if unit["status"] != army.READY:
        return "reason_unit_forming"
    x, y = req["x"], req["y"]
    loc = world.islands[army.where(unit)]  # 外国の島に揚陸した部隊は、その島の中を動く
    if army.path(loc, ctx.defs, ctx.config, (unit["x"], unit["y"]), (x, y)) is None:
        return "reason_no_path"
    unit["order"] = {"type": "move", "x": x, "y": y}
    return None


def _dig_order(ctx: StageContext, island: IslandState, unit: dict[str, Any]) -> str | None:
    cfg = ctx.config.get("military.trench")
    tile = army.tile_at(island, unit["x"], unit["y"])
    if unit["status"] != army.READY:
        return "reason_unit_forming"
    if army.abroad(unit):
        return "reason_unit_abroad"
    if tile is None or army.has_trench(tile):
        return "reason_trench_exists"
    if unit["soldiers"] < cfg["soldiers"]:
        return "reason_trench_soldiers"
    if unit["order"] and unit["order"]["type"] == "dig":
        return None  # もう掘っている
    unit["order"] = {"type": "dig", "left": cfg["turns"]}
    return None


def _form_fleet(
    world: WorldState, ctx: StageContext, island: IslandState, req: dict[str, Any]
) -> str | None:
    kind, ships = req["army"], req["ships"]
    spec = ctx.defs.army.get(kind)
    if spec is None or spec["arm"] != "sea":
        return "reason_fleet_kind"
    if not island.has_economy:
        return "reason_no_economy"
    if not sea.naval_bases(island, ctx.defs):
        return "reason_no_naval_base"
    if not 1 <= ships <= ctx.config.get("military.sea.max_ships"):
        return "reason_fleet_ships"
    x, y = sea.home(island)
    fleet = army.new_unit(world, island.id, kind, x, y, ships=ships)
    island.log.append(
        LogEntry("fleet_forming", {"army": kind, "unit": fleet["id"], "count": ships})
    )
    return None


def _sail_order(
    world: WorldState,
    ctx: StageContext,
    island: IslandState,
    fleet: dict[str, Any],
    req: dict[str, Any],
) -> str | None:
    if not army.is_sea(fleet):
        return "reason_not_fleet"
    if fleet["status"] != army.READY:
        return "reason_unit_forming"
    x, y = req["x"], req["y"]
    grid = sea.grid(world, ctx.config)
    if not (0 <= x < grid and 0 <= y < grid):
        return "reason_off_map"
    route = sea.line((fleet["x"], fleet["y"]), (x, y))
    if not route:
        fleet["order"] = None
        return None
    fleet["order"] = {"type": sea.SAIL, "x": x, "y": y, "route": [list(c) for c in route]}
    return None


def _cargo_order(
    world: WorldState,
    ctx: StageContext,
    island: IslandState,
    unit: dict[str, Any],
    req: dict[str, Any],
    load: bool,
) -> str | None:
    """陸の部隊を輸送船団に載せる・降ろす。自分の島では母港の区画に着いている船団と軍港のマスでだけ。
    戦争の相手の島には、船団がその島の区画にいれば、港か海に接する平地のマスに揚陸する（MI-24・MI-51）。
    揚陸した部隊は、その島の海に接する平地・港のマスからも船に載せて引き揚げられる。
    unit は部隊の番号、fleet は船団の番号。"""
    if army.is_sea(unit):
        return "reason_unit_at_sea"
    fleet = army.find(world, req.get("fleet", -1))
    if fleet is None or fleet["owner"] != island.id or not army.is_sea(fleet):
        return "reason_no_fleet"
    spec = ctx.defs.army[fleet["kind"]]
    if not spec.get("capacity"):
        return "reason_fleet_not_transport"
    if fleet["status"] != army.READY:
        return "reason_fleet_not_docked"
    if load:
        return _load(world, ctx, island, unit, fleet, spec)
    return _unload(world, ctx, island, unit, fleet, req)


def _coastal(loc: IslandState, ctx: StageContext, x: int, y: int) -> bool:
    """島のマスが海に接する平地か（揚陸と引き揚げの場所）。"""
    tile = army.tile_at(loc, x, y)
    if tile is None or tile.terrain != "plain":
        return False
    for nx, ny in ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)):
        near = army.tile_at(loc, nx, ny)
        if near is None or not ctx.defs.terrain[near.terrain]["land"]:
            return True
    return False


def _has_port(loc: IslandState, ctx: StageContext, x: int, y: int) -> bool:
    return any(
        f.built and f.kind == ctx.config.get("trade.port_facility") and (f.x, f.y) == (x, y)
        for f in loc.facilities
    )


def _shore(loc: IslandState, ctx: StageContext, x: int, y: int) -> bool:
    return _has_port(loc, ctx, x, y) or _coastal(loc, ctx, x, y)


def _load(
    world: WorldState,
    ctx: StageContext,
    island: IslandState,
    unit: dict[str, Any],
    fleet: dict[str, Any],
    spec: dict[str, Any],
) -> str | None:
    if unit["status"] != army.READY or "aboard" in unit:
        return "reason_unit_cannot_board"
    loc_id = army.where(unit)
    pos = (unit["x"], unit["y"])
    if loc_id == island.id:
        if not sea.docked(fleet, island):
            return "reason_fleet_not_docked"
        if pos not in {(f.x, f.y) for f in sea.naval_bases(island, ctx.defs)}:
            return "reason_unit_not_at_port"
    else:
        loc = world.islands[loc_id]
        if (fleet["x"], fleet["y"]) != (loc.wx, loc.wy):
            return "reason_fleet_not_there"
        if not _shore(loc, ctx, *pos):
            return "reason_unit_not_at_shore"
    weight = ctx.defs.army[unit["kind"]].get("weight", 0)
    if supply.cargo_load(world, ctx, fleet) + weight > sea.capacity(spec, fleet):
        return "reason_fleet_full"
    unit["aboard"] = fleet["id"]
    unit["order"] = None
    for key in ("role", "fought", "engaged", "from"):
        unit.pop(key, None)
    fleet["cargo"].append(unit["id"])
    if loc_id == island.id:  # 外国の島で使う補給を、国の倉庫から持たせる
        _take_stores(ctx, island, unit)
    return None


def _take_stores(ctx: StageContext, island: IslandState, unit: dict[str, Any]) -> None:
    stores = battle.stores_of(unit)
    for good, cap in supply.capacity(ctx, unit).items():
        got = stock.take(island.warehouse, good, max(0, cap - stores.get(good, 0)))
        if got:
            stores[good] = stores.get(good, 0) + got


def _unload(
    world: WorldState,
    ctx: StageContext,
    island: IslandState,
    unit: dict[str, Any],
    fleet: dict[str, Any],
    req: dict[str, Any],
) -> str | None:
    if unit.get("aboard") != fleet["id"]:
        return "reason_unit_not_aboard"
    target_id = req.get("island", island.id)
    x, y = req["x"], req["y"]
    if target_id == island.id:
        if not sea.docked(fleet, island):
            return "reason_fleet_not_docked"
        if (x, y) not in {(f.x, f.y) for f in sea.naval_bases(island, ctx.defs)}:
            return "reason_unit_not_at_port"
        landed_on = island
    else:
        weak = False
        landed_on = world.islands.get(target_id)
        if landed_on is None:
            return "reason_no_island"
        if (fleet["x"], fleet["y"]) != (landed_on.wx, landed_on.wy):
            return "reason_fleet_not_there"
        if war.status(world, island.id, target_id, ctx.turn) not in (war.WAR, war.DECLARED):
            return "reason_not_at_war"
        if not _shore(landed_on, ctx, x, y):
            return "reason_not_landing_site"
        port = _has_port(landed_on, ctx, x, y)
        weak = not port
        if not port:  # 港でない所は、1 ターンに下ろせる重さが決まっている
            tons = fleet.get("landed", {}).get(str(ctx.turn), 0)
            weight = ctx.defs.army[unit["kind"]].get("weight", 0)
            if tons + weight > supply.landing_tons(ctx) * army.size(fleet) and tons > 0:
                return "reason_landing_limit"
            fleet["landed"] = {str(ctx.turn): tons + weight}
    del unit["aboard"]
    fleet["cargo"].remove(unit["id"])
    unit["x"], unit["y"] = x, y
    unit.pop("landed", None)
    if target_id == island.id:
        unit.pop("at", None)
    else:
        unit["at"] = target_id
        unit["entered"] = ctx.turn
        if weak:
            unit["landed"] = ctx.turn
        unit.pop("from", None)
        island.log.append(
            LogEntry("unit_landed", {"unit": unit["id"], "x": x, "y": y, "country": landed_on.name})
        )
    return None


def _load_supplies(
    world: WorldState,
    ctx: StageContext,
    island: IslandState,
    fleet: dict[str, Any],
    req: dict[str, Any],
) -> str | None:
    """国の倉庫の食べ物・弾薬・燃料を輸送船団に積む（母港の区画につないだ船団だけ）。積めるのは
    船の運べる重さの残りまで。"""
    if not army.is_sea(fleet):
        return "reason_not_fleet"
    spec = ctx.defs.army[fleet["kind"]]
    if not spec.get("capacity"):
        return "reason_fleet_not_transport"
    if fleet["status"] != army.READY or not sea.docked(fleet, island):
        return "reason_fleet_not_docked"
    room = sea.capacity(spec, fleet) - supply.cargo_load(world, ctx, fleet)
    want = min(to_milli(req["quantity"]), int(room * QUANTITY_SCALE))
    got = stock.take(island.warehouse, req["good"], max(0, want))
    if got <= 0:
        return "reason_fleet_full" if room <= 0 else "reason_no_supplies"
    goods = supply.fleet_goods(fleet)
    goods[req["good"]] = goods.get(req["good"], 0) + got
    return None


# --- 島ごとの毎ターンの処理 -------------------------------------------------------------------


def _run_island(world: WorldState, ctx: StageContext, island: IslandState) -> None:
    units = [u for u in army.units_of(world, island.id) if u["kind"] in ctx.defs.army]
    if not units and army.KEY not in island.ext:
        return
    state = forces.state_of(island)
    _reconcile(island, units)
    free = forces.soldiers(island) - sum(u["soldiers"] for u in units)
    for unit in units:
        if not army.abroad(unit):  # 外国の島の部隊は、島の余りの兵も国の倉庫の装備も受け取れない
            free -= _fill(ctx, island, unit, free)
    for unit in units:
        _act(world, ctx, island, unit)
    state["assigned"] = sum(u["soldiers"] for u in units)
    state["wanted"] = _wanted(ctx, island, units)
    land = [u for u in units if not army.is_sea(u)]
    fleets = [u for u in units if army.is_sea(u)]
    island.stats["military.units"] = len(land)
    island.stats["military.units_ready"] = sum(1 for u in land if u["status"] == army.READY)
    island.stats["military.fleets"] = len(fleets)
    island.stats["military.ships"] = sum(army.size(u) for u in fleets)


def _reconcile(island: IslandState, units: list[dict[str, Any]]) -> None:
    """兵の合計が減った（亡くなった・逃げた）とき、部隊の兵をいちばん多い部隊から減らして合わせる。"""
    excess = sum(u["soldiers"] for u in units) - forces.soldiers(island)
    while excess > 0:
        biggest = max(units, key=lambda u: (u["soldiers"], -u["id"]))
        biggest["soldiers"] -= 1
        excess -= 1


def _fill(ctx: StageContext, island: IslandState, unit: dict[str, Any], free: int) -> int:
    """部隊に、島の余っている兵 free 人までと、国の倉庫の装備を集める。そろったら ready にする。
    集めた兵の数を返す。"""
    spec = ctx.defs.army[unit["kind"]]
    crew = sea.need_soldiers(spec, unit)
    took = max(0, min(crew - unit["soldiers"], free))
    unit["soldiers"] += took
    complete = unit["soldiers"] >= crew
    for good, count in sea.need_equipment(spec, unit).items():
        have = unit["equipment"].get(good, 0)
        got = max(0, min(count - have, stock.total(island.warehouse, good) // QUANTITY_SCALE))
        if got > 0:
            stock.take(island.warehouse, good, got * QUANTITY_SCALE)
            unit["equipment"][good] = have + got
        complete = complete and have + got >= count
    if complete and unit["status"] == army.FORMING:
        unit["status"] = army.READY
        kind = "fleet_ready" if army.is_sea(unit) else "unit_ready"
        island.log.append(LogEntry(kind, {"army": unit["kind"], "unit": unit["id"]}))
    return took


def _act(world: WorldState, ctx: StageContext, island: IslandState, unit: dict[str, Any]) -> None:
    if army.is_sea(unit):
        if unit["status"] == army.READY:
            _act_fleet(ctx, island, unit)
        return
    order = unit["order"]
    if not order or unit["status"] != army.READY or "aboard" in unit:
        return
    if order["type"] == "dig":
        order["left"] = max(0, order["left"] - 1)
        if order["left"] == 0:
            _finish_trench(ctx, island, unit)
    elif order["type"] == "move":
        _move(world, ctx, island, unit, order)


def _act_fleet(ctx: StageContext, island: IslandState, fleet: dict[str, Any]) -> None:
    """艦隊の 1 ターン: 燃料を使い（港では port_fuel 倍）、航海の命令なら区画を進める。
    燃料が足りないと動けない（MI-14）。"""
    spec = ctx.defs.army[fleet["kind"]]
    order = fleet["order"]
    sailing = bool(order) and order["type"] == sea.SAIL
    need = sea.fuel_need(spec, fleet, ctx.config, port=sea.docked(fleet, island) and not sailing)
    have = stock.total(island.warehouse, FUEL)
    if sailing and have < need:
        island.log.append(LogEntry("fleet_no_fuel", {"unit": fleet["id"]}))
        return
    stock.take(island.warehouse, FUEL, min(need, have))
    if not sailing:
        return
    route = order["route"]
    for _ in range(min(sea.speed(spec), len(route))):
        fleet["x"], fleet["y"] = route.pop(0)
    if not route:
        fleet["order"] = None
        island.log.append(
            LogEntry("fleet_arrived", {"unit": fleet["id"], "x": fleet["x"], "y": fleet["y"]})
        )


def _finish_trench(ctx: StageContext, island: IslandState, unit: dict[str, Any]) -> None:
    """掘り終わったとき、兵が足りていて国の倉庫に建材があれば陣地ができる（建材はここで使う）。
    足りなければ、そろうまで待つ。"""
    cfg = ctx.config.get("military.trench")
    tile = army.tile_at(island, unit["x"], unit["y"])
    if tile is None or army.has_trench(tile):
        unit["order"] = None  # 同じマスの別の部隊が先に掘った
        return
    materials = {good: to_milli(qty) for good, qty in cfg["materials"].items()}
    if unit["soldiers"] < cfg["soldiers"] or any(
        stock.total(island.warehouse, good) < qty for good, qty in materials.items()
    ):
        return
    for good, qty in materials.items():
        stock.take(island.warehouse, good, qty)
    tile.ext[army.TRENCH] = 1
    unit["order"] = None
    island.log.append(
        LogEntry("trench_built", {"unit": unit["id"], "x": unit["x"], "y": unit["y"]})
    )


def _hostile_near(
    world: WorldState, ctx: StageContext, unit: dict[str, Any], x: int, y: int, reach: int
) -> bool:
    """マス (x, y) から reach マス以内に、戦争の相手の部隊がいるか（隣のマスに入ったら止まる）。"""
    for other in army.on_land(world, army.where(unit)):
        if other["status"] != army.READY or other["soldiers"] <= 0:
            continue
        if max(abs(other["x"] - x), abs(other["y"] - y)) <= reach and battle.hostile(
            world, unit["owner"], other["owner"], ctx.turn
        ):
            return True
    return False


def _move(
    world: WorldState,
    ctx: StageContext,
    island: IslandState,
    unit: dict[str, Any],
    order: dict[str, Any],
) -> None:
    """動きの命令の部隊を、1 ターンに進める量の分だけ進める。車両は進んだマスごとに燃料を使い、
    燃料がなければ動けない（MI-14・MI-21）。外国の島では、その島のマスを進み、燃料は部隊の stores
    から使う。
    戦争の相手の部隊のいるマス、またはその隣のマスに入ったら、そのターンはそこで止まる。"""
    spec = ctx.defs.army[unit["kind"]]
    loc = world.islands[army.where(unit)]
    goal = (order["x"], order["y"])
    route = army.path(loc, ctx.defs, ctx.config, (unit["x"], unit["y"]), goal)
    if route is None:
        unit["order"] = None
        island.log.append(LogEntry("unit_blocked", {"unit": unit["id"]}))
        return
    left = to_milli(spec["speed"])
    vehicles = unit["equipment"].get(VEHICLE, 0) if spec.get("vehicles") else 0
    per_tile = vehicles * to_milli(ctx.config.get("military.move.fuel_per_tile"))
    for step in route:
        if left <= 0:
            break
        if per_tile and battle._have(world, unit, FUEL) < per_tile:
            island.log.append(LogEntry("unit_no_fuel", {"unit": unit["id"]}))
            break
        tile = army.tile_at(loc, *step)
        assert tile is not None
        battle._use(world, unit, FUEL, per_tile)
        left -= army.enter_cost(tile, ctx.defs, ctx.config) or 0
        previous = (unit["x"], unit["y"])
        unit["x"], unit["y"] = step
        unit["entered"] = ctx.turn
        if _hostile_near(world, ctx, unit, step[0], step[1], 1):
            unit["from"] = list(previous)
            break
        unit.pop("from", None)
    if (unit["x"], unit["y"]) == goal:
        unit["order"] = None
        island.log.append(
            LogEntry("unit_arrived", {"unit": unit["id"], "x": goal[0], "y": goal[1]})
        )


def _wanted(ctx: StageContext, island: IslandState, units: list[dict[str, Any]]) -> dict[str, int]:
    """次のターンに国の倉庫へ買い集めたい物（財 → 1000 分の 1 単位）。編成中の部隊に足りない装備と、
    動きの命令の車両・艦が 1 ターンに使う燃料。軍の段が国防費（装備の買い入れ）で市場から買う。"""
    wanted: dict[str, int] = {}
    for unit in units:
        spec = ctx.defs.army[unit["kind"]]
        if army.abroad(unit):  # 外国の島の部隊は、船で運んだ補給でだけ補う（MI-25）
            continue
        if "role" in unit:  # 戦っている部隊は、次のターンの戦いの弾薬と燃料を見込んで買う
            wanted[AMMO] = wanted.get(AMMO, 0) + battle.ammo_need(ctx, unit) * 2
            wanted[FUEL] = wanted.get(FUEL, 0) + battle.fuel_need(ctx, unit)
        for good, count in sea.need_equipment(spec, unit).items():
            missing = count - unit["equipment"].get(good, 0)
            if missing > 0:
                wanted[good] = wanted.get(good, 0) + missing * QUANTITY_SCALE
        if army.is_sea(unit):
            if unit["status"] == army.READY:
                sailing = bool(unit["order"]) and unit["order"]["type"] == sea.SAIL
                port = sea.docked(unit, island) and not sailing
                fuel = sea.fuel_need(spec, unit, ctx.config, port)
                wanted[FUEL] = wanted.get(FUEL, 0) + fuel
            continue
        order = unit["order"]
        if order and order["type"] == "move" and spec.get("vehicles"):
            vehicles = unit["equipment"].get(VEHICLE, 0)
            per_tile = to_milli(ctx.config.get("military.move.fuel_per_tile"))
            wanted[FUEL] = wanted.get(FUEL, 0) + vehicles * per_tile * int(spec["speed"])
    return wanted


# --- 命令 -------------------------------------------------------------------------------


def _forward(kind: str):
    def execute(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
        request(island, kind, dict(payload))

    return execute


def check_form(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    spec = defs.army.get(payload.get("army", ""))
    if spec is None or spec["arm"] != "land":
        raise OrderFailed("reason_unit_kind")
    if not island.has_economy:
        raise OrderFailed("reason_no_economy")


def check_form_fleet(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    spec = defs.army.get(payload.get("army", ""))
    if spec is None or spec["arm"] != "sea":
        raise OrderFailed("reason_fleet_kind")
    if not island.has_economy:
        raise OrderFailed("reason_no_economy")


ORDERS = (
    "form_unit",
    "disband_unit",
    "move_unit",
    "dig_trench",
    "stop_unit",
    "form_fleet",
    "sail_fleet",
    "load_unit",
    "unload_unit",
    "load_supplies",
    "set_requisition",
)


def register(registry: Registry) -> None:
    registry.add_stage(Operations())
    for kind in ORDERS:
        check = {"form_unit": check_form, "form_fleet": check_form_fleet}.get(kind)
        registry.add_order(OrderHandler(kind, execute=_forward(kind), check=check))
