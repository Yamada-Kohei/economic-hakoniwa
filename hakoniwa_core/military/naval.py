"""海の戦い・護衛・通商破壊・封鎖（フェーズ4-3 の②。設計書 15-military の MI-31〜35・MI-48・
MI-56・MI-57）。

**艦隊の務め（duty）**: 命令 fleet_duty で、艦隊に続く務めを付けます。
- 護衛（escort）: 自国の海運業の船団に付く。どこにいても（航海中でも）効く。
- 見張り（watch）: 艦隊のいる区画とその周り（`watch_radius`。潜水艦は同じ区画だけ）を見張る。
- 封鎖（blockade）: 戦争の相手の島の区画に置く。見張りも兼ねる。
見張りと封鎖は、艦隊が航海の命令を持たずにとどまっている間だけ効きます。

**海の戦い（MI-31）**: 戦争の相手の艦隊が同じ区画にいて、どちらかが相手を見つけた（vision の
見え方）とき起きます。強さは艦の種類（army.yaml の strength）× 艦の数（レーダーのある国は
`radar` 倍）。見つけた側だけが先に撃ち（見つけられた側は撃ち返せない）、潜水艦が見つからないまま
撃つと `ambush` 倍です。沈む割合は 陸の戦闘と同じ形で、相手の強さとの比の `exponent` 乗です。
沈んだ艦の乗員は亡くなり、載せていた陸の部隊もいっしょに沈みます。

**通商破壊と封鎖（MI-33・MI-34・MI-48・MI-56）**: 貿易の船（`world.ext["trade"]["shipments"]`）は
船出のターンと片道のターン数から、そのターンにいる区画が決まります（`position`）。荷が船の上に
あるのは帰りの片道だけです。見張りの区画で戦争の相手の船は、見張りの艦の数で見つかります
（1 −（1 − hunt）^隻数）。護衛の艦は、襲う艦と先に戦います。封鎖の区画では、相手の国の船は沈み、
中立の国の船は追い返されます（荷を積んだまま引き返して待つので、届くのが遅れる）。
行きで沈んだ船は船だけを失い、帰りで沈んだ船は荷も失います。
"""

from __future__ import annotations

import random
from typing import Any

from hakoniwa_core import monuments
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.diplomacy import war
from hakoniwa_core.military import army, forces, sea, vision
from hakoniwa_core.state import LogEntry, WorldState
from hakoniwa_core.units import QUANTITY_SCALE

ESCORT = "escort"
WATCH = "watch"
BLOCKADE = "blockade"
DUTIES = (ESCORT, WATCH, BLOCKADE)

SUNK = "sunk"
TURNED = "turned"
LOST_SHIP = "lost_ship"
Cell = tuple[int, int]


def cfg_of(config: GameConfig) -> dict[str, Any]:
    return config.get("military.naval")


def is_warship(spec: dict[str, Any]) -> bool:
    return spec.get("strength", 0) > 0


def _ready(fleet: dict[str, Any]) -> bool:
    return army.is_sea(fleet) and fleet["status"] == army.READY and fleet.get("ships", 0) > 0


def _active(fleet: dict[str, Any], duty: str) -> bool:
    """艦隊が務めを果たしているか（護衛はどこでも、見張りと封鎖はとどまっている間だけ）。"""
    if not _ready(fleet) or fleet.get("duty") != duty:
        return False
    return duty == ESCORT or not fleet.get("order")


# --- 見張りと封鎖 --------------------------------------------------------------------------


def hunters(world: WorldState, defs: Defs, config: GameConfig) -> list[dict[str, Any]]:
    """見張りをしている艦隊（見張りと封鎖）。"""
    cfg = cfg_of(config)
    out = []
    for fleet in army.world_state(world)["units"]:
        spec = defs.army.get(fleet["kind"], {})
        if not (_active(fleet, WATCH) or _active(fleet, BLOCKADE)) or not spec.get("hunt"):
            continue
        out.append(
            {
                "fleet": fleet,
                "hunt": spec["hunt"],
                "radius": 0 if spec.get("stealth") else cfg["watch_radius"],
            }
        )
    return out


def island_cells(world: WorldState) -> dict[Cell, int]:
    return {(world.islands[i].wx, world.islands[i].wy): i for i in world.island_ids()}


def blockades(
    world: WorldState, defs: Defs, config: GameConfig, turn: int
) -> dict[Cell, dict[int, int]]:
    """封鎖している区画ごとの、封鎖する国 → 軍艦の数。艦隊の区画にある島が、その国と戦争中のとき
    だけ封鎖になる。"""
    cells = island_cells(world)
    out: dict[Cell, dict[int, int]] = {}
    for fleet in army.world_state(world)["units"]:
        if not _active(fleet, BLOCKADE) or not is_warship(defs.army.get(fleet["kind"], {})):
            continue
        cell = (fleet["x"], fleet["y"])
        target = cells.get(cell)
        if target is None or not war.fighting(world, fleet["owner"], target, turn):
            continue
        row = out.setdefault(cell, {})
        row[fleet["owner"]] = row.get(fleet["owner"], 0) + fleet["ships"]
    return out


def blockade_share(config: GameConfig, ships: int) -> float:
    """封鎖で止める船の割合（軍艦が blockade_full 隻で全部、1 隻なら半分）。"""
    return min(1.0, ships / max(1, cfg_of(config)["blockade_full"]))


def blocked(
    world: WorldState,
    defs: Defs,
    config: GameConfig,
    turn: int,
    buyer: int,
    seller: int,
    key: str,
) -> bool:
    """買い手の船が seller の荷を運ぶ船出が、封鎖に止められるか（どちらかの島が封鎖されている。
    封鎖する国の船は止めない）。"""
    groups = blockades(world, defs, config, turn)
    if not groups:
        return False
    for island_id in (buyer, seller):
        island = world.islands.get(island_id)
        if island is None:
            continue
        for owner, ships in sorted(groups.get((island.wx, island.wy), {}).items()):
            if owner == buyer:
                continue
            rng = random.Random(f"{world.seed}:{turn}:blockade:{buyer}:{seller}:{key}")
            if rng.random() < blockade_share(config, ships):
                return True
    return False


# --- 貿易の船の位置と、襲われるか --------------------------------------------------------


def position(world: WorldState, s: dict[str, Any], turn: int) -> tuple[Cell, bool] | None:
    """貿易の船が turn にいる区画と、荷が船の上にあるか（帰りの片道だけ。MI-56）。"""
    seller = world.islands.get(s["from"])
    buyer = world.islands.get(s["to"])
    if seller is None or buyer is None or not s.get("ship"):
        return None
    start = (buyer.wx, buyer.wy)
    path = [start, *sea.line(start, (seller.wx, seller.wy))]
    last = len(path) - 1
    oneway = max(1, s["oneway"])
    k = turn - s["loaded"] - s.get("delay", 0)
    if k <= oneway:
        return path[round(max(0, k) / oneway * last)], False
    back = min(k - oneway, oneway)
    return path[last - round(back / oneway * last)], True


def route_cells(world: WorldState, buyer: int, seller: int) -> list[Cell]:
    a, b = world.islands.get(buyer), world.islands.get(seller)
    if a is None or b is None:
        return []
    start = (a.wx, a.wy)
    return [start, *sea.line(start, (b.wx, b.wy))]


def _covers(h: dict[str, Any], cell: Cell) -> bool:
    fleet = h["fleet"]
    return max(abs(fleet["x"] - cell[0]), abs(fleet["y"] - cell[1])) <= h["radius"]


def _found(attackers: list[dict[str, Any]]) -> float:
    """見張りの艦隊が、商船 1 隻を 1 ターンに見つける見込み（1 −（1 − hunt）^隻数）。"""
    miss = 1.0
    for h in attackers:
        miss *= (1 - h["hunt"]) ** h["fleet"]["ships"]
    return 1 - miss


def escort_ships(world: WorldState, defs: Defs, owner: int) -> int:
    """護衛についている軍艦の数。"""
    total = 0
    for fleet in army.fleets_of(world, owner):
        spec = defs.army.get(fleet["kind"], {})
        if _active(fleet, ESCORT) and spec.get("detect", 0) > 0:
            total += fleet["ships"]
    return total


def risk(
    world: WorldState,
    defs: Defs,
    config: GameConfig,
    turn: int,
    victim: int,
    cells: list[Cell],
    watchers: list[dict[str, Any]] | None = None,
) -> float:
    """victim の船が cells をたどる航海で沈む見込み（戦争の危険の上乗せに使う。護衛は見込みに
    入れる）。"""
    cfg = cfg_of(config)
    watchers = hunters(world, defs, config) if watchers is None else watchers
    enemies = [h for h in watchers if war.fighting(world, h["fleet"]["owner"], victim, turn)]
    groups = blockades(world, defs, config, turn)
    survive = 1.0
    for cell in cells:
        near = [h for h in enemies if _covers(h, cell)]
        p = _found(near) if near else 0.0
        escorts = escort_ships(world, defs, victim)
        if near and escorts:
            ships = sum(h["fleet"]["ships"] for h in near)
            p *= max(0.0, 1 - min(1.0, escorts * cfg["escort_find"] / max(1, ships)))
        for owner, n in groups.get(cell, {}).items():
            if war.fighting(world, owner, victim, turn):
                p = 1 - (1 - p) * (1 - blockade_share(config, n))
        survive *= 1 - p
    return min(cfg["risk_cap"], 1 - survive)


def shipment_outcome(
    world: WorldState,
    defs: Defs,
    config: GameConfig,
    turn: int,
    s: dict[str, Any],
    watchers: list[dict[str, Any]],
    groups: dict[Cell, dict[int, int]],
) -> str | None:
    """貿易の船がこのターンの区画で、沈む・追い返される・何もないのどれか。沈む（SUNK）のは、荷が
    船の上にあれば荷ごと、なければ船だけ（結果に aboard は別に持つ）。"""
    where = position(world, s, turn)
    if where is None:
        return None
    cell, _ = where
    victim = s["to"]
    rng = random.Random(f"{world.seed}:{turn}:convoy:{s['id']}")
    for owner, ships in sorted(groups.get(cell, {}).items()):
        if owner == victim:
            continue
        if rng.random() < blockade_share(config, ships):
            return SUNK if war.fighting(world, owner, victim, turn) else TURNED
    near = [h for h in watchers if war.fighting(world, h["fleet"]["owner"], victim, turn)]
    near = [h for h in near if _covers(h, cell)]
    if not near:
        return None
    escorts = escort_ships(world, defs, victim)
    cfg = cfg_of(config)
    for _ in range(escorts):  # 護衛は襲う艦 1 隻ずつと先に戦う（MI-32）
        pool = [h for h in near if h["fleet"]["ships"] > 0]
        if not pool:
            return None
        if rng.random() < cfg["escort_find"]:
            biggest = max(pool, key=lambda h: (h["fleet"]["ships"], -h["fleet"]["id"]))
            sink_ships(world, defs, biggest["fleet"], 1, turn)
    near = [h for h in near if h["fleet"]["ships"] > 0 and army.find(world, h["fleet"]["id"])]
    if near and rng.random() < _found(near):
        return SUNK
    return None


# --- 艦が沈む ----------------------------------------------------------------------------


def sink_ships(
    world: WorldState, defs: Defs, fleet: dict[str, Any], count: int, turn: int, killer: int = 0
) -> int:
    """艦隊の艦が count 隻沈む。乗員は亡くなり、載せていた陸の部隊と補給も（重さが船の容量を
    超えた分）沈む。艦隊の艦が 0 になれば艦隊は消える。亡くなった人数を返す。"""
    spec = defs.army[fleet["kind"]]
    owner = world.islands[fleet["owner"]]
    count = min(count, fleet["ships"])
    if count <= 0:
        return 0
    before = fleet["ships"]
    dead = min(fleet["soldiers"], count * spec["soldiers"])
    fleet["soldiers"] -= dead
    for good, qty in spec["equipment"].items():
        have = fleet["equipment"].get(good, 0)
        fleet["equipment"][good] = max(0, have - count * (qty // QUANTITY_SCALE))
    fleet["ships"] -= count
    goods = fleet.get("goods", {})
    for good in list(goods):
        goods[good] = goods[good] * fleet["ships"] // before
    if fleet["ships"] <= 0:
        dead += _drown_cargo(world, defs, fleet, everything=True)
        army.remove(world, fleet)
        owner.log.append(LogEntry("fleet_sunk", {"unit": fleet["id"], "army": fleet["kind"]}))
    else:
        dead += _drown_cargo(world, defs, fleet, everything=False)
    forces.lose(owner, dead)
    monuments.add_dead(owner, monuments.WAR_DEAD, dead)
    owner.stats["military.ships_sunk"] = owner.stats.get("military.ships_sunk", 0) + count
    if killer and killer in world.islands:
        monuments.add_kills(world.islands[killer], dead)
    return dead


def _drown_cargo(world: WorldState, defs: Defs, fleet: dict[str, Any], everything: bool) -> int:
    """艦隊が載せていた陸の部隊のうち、重さが残りの船の容量を超えた分が沈む。亡くなった数を返す。"""
    dead = 0
    spec = defs.army[fleet["kind"]]
    while fleet.get("cargo"):
        if not everything and sea.cargo_weight(world, defs, fleet) <= sea.capacity(spec, fleet):
            break
        unit = army.find(world, fleet["cargo"].pop())
        if unit is None:
            continue
        dead += unit["soldiers"]
        army.remove(world, unit)
    return dead


# --- 艦隊どうしの戦い ----------------------------------------------------------------------


def power(world: WorldState, defs: Defs, config: GameConfig, fleets: list[dict[str, Any]]) -> float:
    cfg = cfg_of(config)
    total = 0.0
    for fleet in fleets:
        spec = defs.army[fleet["kind"]]
        total += spec.get("strength", 0) * fleet["ships"]
    if total and fleets and _has_radar(world, defs, fleets[0]["owner"]):
        total *= cfg["radar"]
    return total


def _has_radar(world: WorldState, defs: Defs, owner: int) -> bool:
    return bool(army.bases(world.islands[owner], defs, "radar"))


def _share(cfg: dict[str, Any], enemy: float, own: float) -> float:
    if own <= 0:
        return cfg["max_loss"] if enemy > 0 else 0.0
    if enemy <= 0:
        return 0.0
    return min(cfg["max_loss"], cfg["base_loss"] * (enemy / own) ** cfg["exponent"])


def battle(world: WorldState, defs: Defs, config: GameConfig, turn: int) -> None:
    """同じ区画にいる、戦争の相手の艦隊どうしの戦い（MI-31）。"""
    cfg = cfg_of(config)
    by_cell: dict[Cell, dict[int, list[dict[str, Any]]]] = {}
    for fleet in sorted(army.world_state(world)["units"], key=lambda u: u["id"]):
        if _ready(fleet):
            by_cell.setdefault((fleet["x"], fleet["y"]), {}).setdefault(fleet["owner"], []).append(
                fleet
            )
    for cell in sorted(by_cell):
        owners = sorted(by_cell[cell])
        for i, a in enumerate(owners):
            for b in owners[i + 1 :]:
                if war.fighting(world, a, b, turn):
                    _engage(world, defs, config, cfg, turn, cell, a, b, by_cell[cell])


def _sees(world: WorldState, defs: Defs, viewer: int, fleets: list[dict[str, Any]]) -> bool:
    sight = vision.eyes(world, defs, viewer)
    return any(vision.can_see(world, defs, viewer, f, sight) for f in fleets)


def _engage(
    world: WorldState,
    defs: Defs,
    config: GameConfig,
    cfg: dict[str, Any],
    turn: int,
    cell: Cell,
    a: int,
    b: int,
    groups: dict[int, list[dict[str, Any]]],
) -> None:
    side_a = [f for f in groups[a] if f["ships"] > 0]
    side_b = [f for f in groups[b] if f["ships"] > 0]
    if not side_a or not side_b:
        return
    a_sees = _sees(world, defs, a, side_b)
    b_sees = _sees(world, defs, b, side_a)
    if not (a_sees or b_sees):
        return
    pa, pb = power(world, defs, config, side_a), power(world, defs, config, side_b)
    shots = {
        a: pa * (cfg["ambush"] if not b_sees and _stealthy(defs, side_a) else 1) if a_sees else 0.0,
        b: pb * (cfg["ambush"] if not a_sees and _stealthy(defs, side_b) else 1) if b_sees else 0.0,
    }
    rng = random.Random(f"{world.seed}:{turn}:sea:{cell[0]}:{cell[1]}:{a}:{b}")
    sunk_a = _sink_share(world, defs, side_a, _share(cfg, shots[b], pa), rng, b, turn)
    sunk_b = _sink_share(world, defs, side_b, _share(cfg, shots[a], pb), rng, a, turn)
    for me, foe, mine, theirs in ((a, b, sunk_a, sunk_b), (b, a, sunk_b, sunk_a)):
        world.islands[me].log.append(
            LogEntry(
                "sea_battle",
                {
                    "x": cell[0],
                    "y": cell[1],
                    "country": world.islands[foe].name,
                    "lost": mine,
                    "sunk": theirs,
                    "seen": (a_sees if me == a else b_sees),
                },
            )
        )


def _stealthy(defs: Defs, fleets: list[dict[str, Any]]) -> bool:
    return all(defs.army[f["kind"]].get("stealth") for f in fleets)


def _sink_share(
    world: WorldState,
    defs: Defs,
    fleets: list[dict[str, Any]],
    share: float,
    rng: random.Random,
    killer: int,
    turn: int,
) -> int:
    """側の艦隊の艦を share の割合だけ沈める（端数は乱数で丸める）。沈んだ艦の数を返す。"""
    total = 0
    for fleet in list(fleets):
        want = fleet["ships"] * share
        count = int(want) + (1 if rng.random() < want - int(want) else 0)
        total += min(count, fleet["ships"])
        sink_ships(world, defs, fleet, count, turn, killer)
    return total
