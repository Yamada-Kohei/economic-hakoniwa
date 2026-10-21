"""軍の動きの段（operations。フェーズ4-2 の③。世界の段）と、部隊の命令。

部隊の編成・解散・動き・陣地の命令は、命令の段で島の申し送りに積み、この段が島の番号の順に決めます
（外交・貿易と同じ形）。そのあと島ごとに、兵の合計に合わせて部隊の兵を整え、編成中の部隊に兵と装備を
集め、動きの命令の部隊を進め、陣地を掘り進めます。部隊と陣地の状態を書くのはこの段だけです（MI-54）。

置く場所は、市場の段の後・貿易の段の前です（市場で国の倉庫に届いた装備を、同じターンのうちに部隊へ）。
戦闘・揚陸・占領・ミサイルは 4-3 で、この段の中に「動き → 海の戦い → 揚陸 → 陸の戦闘 → 占領 →
ミサイル」の順で足します（MI-50）。

中身は hakoniwa_core/military/army.py、設計は docs/design/15-military.md の MI-18〜21・MI-50〜54。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import stock
from hakoniwa_core.defs import Defs
from hakoniwa_core.military import army, forces, sea, vision
from hakoniwa_core.registry import OrderFailed, OrderHandler, Registry, WorldStage
from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import QUANTITY_SCALE, to_milli

REQUESTS = "military_requests"
FUEL = "fuel"
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
        vision.update(world, ctx.defs, ctx.turn)
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
    unit = army.find(world, req.get("fleet" if kind == "sail_fleet" else "unit", -1))
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
    if army.is_sea(unit) or "aboard" in unit:
        return "reason_unit_at_sea" if army.is_sea(unit) else "reason_unit_aboard"
    if kind == "move_unit":
        return _move_order(ctx, island, unit, req)
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
    ctx: StageContext, island: IslandState, unit: dict[str, Any], req: dict[str, Any]
) -> str | None:
    if unit["status"] != army.READY:
        return "reason_unit_forming"
    x, y = req["x"], req["y"]
    if army.path(island, ctx.defs, ctx.config, (unit["x"], unit["y"]), (x, y)) is None:
        return "reason_no_path"
    unit["order"] = {"type": "move", "x": x, "y": y}
    return None


def _dig_order(ctx: StageContext, island: IslandState, unit: dict[str, Any]) -> str | None:
    cfg = ctx.config.get("military.trench")
    tile = army.tile_at(island, unit["x"], unit["y"])
    if unit["status"] != army.READY:
        return "reason_unit_forming"
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
    """陸の部隊を輸送船団に載せる・降ろす。どちらも母港の区画に着いている船団と、軍港のマスにいる部隊で
    だけ（外国の島への揚陸は 4-3）。unit は部隊の番号、fleet は船団の番号。"""
    if army.is_sea(unit):
        return "reason_unit_at_sea"
    fleet = army.find(world, req.get("fleet", -1))
    if fleet is None or fleet["owner"] != island.id or not army.is_sea(fleet):
        return "reason_no_fleet"
    spec = ctx.defs.army[fleet["kind"]]
    if not spec.get("capacity"):
        return "reason_fleet_not_transport"
    if fleet["status"] != army.READY or not sea.docked(fleet, island):
        return "reason_fleet_not_docked"
    bases = {(f.x, f.y) for f in sea.naval_bases(island, ctx.defs)}
    if load:
        if unit["status"] != army.READY or "aboard" in unit:
            return "reason_unit_cannot_board"
        if (unit["x"], unit["y"]) not in bases:
            return "reason_unit_not_at_port"
        weight = ctx.defs.army[unit["kind"]].get("weight", 0)
        if sea.cargo_weight(world, ctx.defs, fleet) + weight > sea.capacity(spec, fleet):
            return "reason_fleet_full"
        unit["aboard"] = fleet["id"]
        unit["order"] = None
        fleet["cargo"].append(unit["id"])
        return None
    if unit.get("aboard") != fleet["id"]:
        return "reason_unit_not_aboard"
    if (req["x"], req["y"]) not in bases:
        return "reason_unit_not_at_port"
    unit["x"], unit["y"] = req["x"], req["y"]
    del unit["aboard"]
    fleet["cargo"].remove(unit["id"])
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
        free -= _fill(ctx, island, unit, free)
    for unit in units:
        _act(ctx, island, unit)
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


def _act(ctx: StageContext, island: IslandState, unit: dict[str, Any]) -> None:
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
        _move(ctx, island, unit, order)


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


def _move(
    ctx: StageContext, island: IslandState, unit: dict[str, Any], order: dict[str, Any]
) -> None:
    """動きの命令の部隊を、1 ターンに進める量の分だけ進める。車両は進んだマスごとに燃料を使い、
    燃料がなければ動けない（MI-14・MI-21）。"""
    spec = ctx.defs.army[unit["kind"]]
    goal = (order["x"], order["y"])
    route = army.path(island, ctx.defs, ctx.config, (unit["x"], unit["y"]), goal)
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
        if per_tile and stock.total(island.warehouse, FUEL) < per_tile:
            island.log.append(LogEntry("unit_no_fuel", {"unit": unit["id"]}))
            break
        tile = army.tile_at(island, *step)
        assert tile is not None
        stock.take(island.warehouse, FUEL, per_tile)
        left -= army.enter_cost(tile, ctx.defs, ctx.config) or 0
        unit["x"], unit["y"] = step
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
)


def register(registry: Registry) -> None:
    registry.add_stage(Operations())
    for kind in ORDERS:
        check = {"form_unit": check_form, "form_fleet": check_form_fleet}.get(kind)
        registry.add_order(OrderHandler(kind, execute=_forward(kind), check=check))
