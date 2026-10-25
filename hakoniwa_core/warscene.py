"""測りの戦争の場面（段6 A2。台帳 W-111、設計書 15 軍事と戦争 MI-67）。

標準の計画（autoplan）は自分から宣戦しないので、ふつうの測りは戦闘・占領・補給を通りません。この
場面は、測りの側で数組の島に宣戦させ、戦争が経済にどれだけ響くかを同じ物差しで測ります。

組は島の番号の小さい順に、世界マップで一番近い島と組みます（`pairs`）。攻める側（番号の小さい島）は
プレイヤーと同じ命令で次の順に動き、守る側は標準の計画（plan_war）のままです。

1. 備え（WAR_FROM の WAR_PREP ターン前から）: 兵舎を建て、国防費を決め、志願の兵を COMPANIES 中隊
   ぶん集めて歩兵を編成する（autoplan.defense_order。プレイヤーと同じ命令）。
2. 宣戦（WAR_FROM ターン）: declare_war を出す。議会が通さなければ通るまで毎ターン出し直す。
3. 上陸（戦争が始まった次のターン）: そろった歩兵を、守る側の島の海に接する平地に降ろす。
4. 攻め: 上陸した部隊は守る側の首都へ向かう（move_unit）。守る側の警察は首都と町を、部隊は兵舎の
   マスを守る。首都が占領されれば守る側は降伏する。
5. 講和（上陸から PEACE_AFTER ターン後）: 攻める側が講和を提案し、守る側の標準の計画が署名する。

仮（何の代わりか・いつ外すか）:
- 海を渡る輸送の省略: 輸送船団の編成・積み込み・航海・揚陸（命令 form_fleet・load_unit・sail_fleet・
  unload_unit）の代わりに、測りが部隊を守る側の島に置き、攻める側の国の倉庫から補給を持たせ、
  補給の船団（輸送船 SEALIFT_SHIPS 隻。毎ターン国の倉庫から食べ物と弾薬を積む）を島の区画に置く。
  標準の計画が輸送船を買って揚陸できるようになったら外す。
- 武器と弾薬の贈与: 武器は貿易で売り買いできず、標準の計画は武器工場を原料の手に入る島にしか建て
  ない。弾薬は戦う兵 1000 人が 1 ターンに 36 トンを使い、島の経済で賄える量ではない。攻める側には、
  備えの初めに武器と弾薬を国の倉庫に足す（同盟国から買った形）。食べ物は足さず、国防費の備蓄の目標
  （命令 set_defense の reserve）で市場から買わせる。

場面は世界の状態だけから決まるので、写し（--save・--load）から続けても同じに動きます。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from hakoniwa_core import autoplan, stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.diplomacy import war
from hakoniwa_core.military import army, forces, supply
from hakoniwa_core.modules.operations import _coastal, _has_port
from hakoniwa_core.state import ORDER_PENDING, IslandState, WorldState
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import QUANTITY_SCALE

KEY = "warscene"  # 世界の状態（ext）に残す、戦争をする組
WAR_PAIRS = 3  # 戦争をする組の数
WAR_FROM = 72  # 宣戦するターン（国が育って、標準の計画の警察と兵舎が建った後）
WAR_PREP = 24  # 宣戦の何ターン前から攻める側が備えるか
COMPANIES = 5  # 攻める側の歩兵の中隊の数（兵舎 1 つの定員 500 人）
PEACE_AFTER = 48  # 上陸から何ターン後に講和を提案するか
SEALIFT_SHIPS = 2  # 補給の船団の輸送船の数
AMMUNITION = 0.24  # 備えの初めに国の倉庫へ足す弾薬（兵 1 人あたりのトン）。武器は兵の数だけ
RESERVE = {"food": 12}  # 外国へ送る補給の備蓄の目標（トン。国防費の装備の分で市場から買う）


def pairs(world: WorldState, defs: Defs, config: GameConfig) -> list[tuple[int, int]]:
    """(攻める側, 守る側) の島の番号の組。備えの初めのターンに決めて、世界の状態（ext）に残す。"""
    saved = world.ext.get(KEY)
    if saved is None:
        if world.turn + 1 < WAR_FROM - WAR_PREP:
            return []
        chosen = [list(p) for p in _choose(world, defs, config)]
        if not chosen:
            return []  # 兵の食べ物が手に入る島ができるまで、組は決めない
        saved = world.ext[KEY] = chosen
    return [(a, d) for a, d in saved]


def _choose(world: WorldState, defs: Defs, config: GameConfig) -> list[tuple[int, int]]:
    """小さい番号の島から順に、まだ組でない一番近い島と組む。

    攻める側は、兵の食べ物（food）が手に入る島だけ（食べ物のない島の兵は飢えて逃げる）。
    """
    ids = [i for i in world.island_ids() if world.islands[i].has_economy]
    used: set[int] = set()
    out: list[tuple[int, int]] = []
    for a in ids:
        here = world.islands[a]
        if a in used or not autoplan.fed(here, defs, config):
            continue
        near = [
            (abs(here.wx - world.islands[b].wx) + abs(here.wy - world.islands[b].wy), b)
            for b in ids
            if b != a and b not in used
        ]
        if not near:
            break
        b = min(near)[1]
        out.append((a, b))
        used |= {a, b}
        if len(out) >= WAR_PAIRS:
            break
    return out


def orders(
    world: WorldState, defs: Defs, config: GameConfig
) -> tuple[dict[int, list[tuple[str, dict[str, Any]]]], set[int]]:
    """次のターンの前に、場面が足す命令（島ごと）と、備えの間は標準の計画の守りを止める島の番号。
    世界の状態も直接足す（上述の仮の 2 つ）。"""
    turn = world.turn + 1
    plans: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    preparing: set[int] = set()
    if turn < WAR_FROM - WAR_PREP:
        return plans, preparing
    ctx = StageContext(
        defs=defs,
        config=config,
        registry=None,  # type: ignore[arg-type]
        formulas={},
        turn=turn,
        seed=world.seed,
        stage="operations",
        before=None,
    )
    for attacker_id, defender_id in pairs(world, defs, config):
        attacker, defender = world.islands[attacker_id], world.islands[defender_id]
        key = war.pair(attacker_id, defender_id)
        fought = [w for w in war.wars(world) if war.pair(w["attacker"], w["defender"]) == key]
        if not fought:  # まだ宣戦していない
            if turn == WAR_FROM - WAR_PREP:
                _grant(world, defs, attacker)
            if turn < WAR_FROM:
                preparing.add(attacker_id)
                _prepare(world, defs, config, attacker, plans)
            else:
                plans.setdefault(attacker_id, []).append(("declare_war", {"target": defender_id}))
            continue
        found = war.open_war(world, attacker_id, defender_id)
        if found is None:
            continue  # 講和が済んだ
        landed = [u for u in army.on_land(world, defender_id) if u["owner"] == attacker_id]
        if not landed and turn == found["start"]:
            landed = _land(world, ctx, attacker, defender)
        if landed:
            _advance(world, attacker, defender, landed, plans)
        if turn >= found["start"] + PEACE_AFTER and turn % 3 == 0:
            _propose_peace(world, attacker_id, defender_id, plans)
    return plans, preparing


def _grant(world: WorldState, defs: Defs, island: IslandState) -> None:
    soldiers = _soldiers(defs)
    grant = {"weapons": soldiers, "ammunition": AMMUNITION * soldiers}
    for good, tons in grant.items():
        stock.add(island.warehouse, good, round(tons * QUANTITY_SCALE), world.turn, defs.goods)


def _soldiers(defs: Defs) -> int:
    """攻める側の歩兵の兵の数（COMPANIES 部隊ぶん）。"""
    return COMPANIES * defs.army["infantry"]["soldiers"]


def _prepare(
    world: WorldState,
    defs: Defs,
    config: GameConfig,
    attacker: IslandState,
    plans: dict[int, list[tuple[str, dict[str, Any]]]],
) -> None:
    """攻める側の備え: 兵舎・国防費・志願の兵・歩兵の編成を、1 ターンに 1 つずつ。"""
    if any(o.status == ORDER_PENDING for o in attacker.orders if o.kind in autoplan.WAR_ORDERS):
        return
    if not forces.state_of(attacker, config).get("reserve"):
        plans.setdefault(attacker.id, []).append(("set_defense", {"reserve": RESERVE}))
        return
    order = autoplan.defense_order(attacker, world, defs, config, _soldiers(defs))
    if order is not None:
        plans.setdefault(attacker.id, []).append(order)


def _land(
    world: WorldState, ctx: StageContext, attacker: IslandState, defender: IslandState
) -> list[dict[str, Any]]:
    """そろった歩兵を、守る側の海に接する平地（港があれば港）に降ろす。補給の船団も置く。"""
    ready = [
        u
        for u in army.land_units_of(world, attacker.id)
        if u["status"] == army.READY and u["soldiers"] > 0 and not army.abroad(u)
    ]
    site = _landing_site(defender, ctx)
    if not ready or site is None:
        return []
    port = _has_port(defender, ctx, *site)
    for unit in ready:
        unit["at"] = defender.id
        unit["x"], unit["y"] = site
        unit["entered"] = ctx.turn
        if not port:
            unit["landed"] = ctx.turn  # 港でない所に降ろしたターンは弱い
        unit["order"] = None
        stores = unit.setdefault("stores", {})
        for good, cap in supply.capacity(ctx, unit).items():
            got = stock.take(attacker.warehouse, good, max(0, cap - stores.get(good, 0)))
            if got:
                stores[good] = stores.get(good, 0) + got
    fleet = army.new_unit(
        world, attacker.id, "transport", defender.wx, defender.wy, ships=SEALIFT_SHIPS
    )
    fleet["status"] = army.READY
    crew = ctx.defs.army["transport"]["soldiers"] * SEALIFT_SHIPS
    fleet["soldiers"], fleet["equipment"] = crew, {"transport_ship": SEALIFT_SHIPS}
    forces.state_of(attacker)["volunteers"] += crew
    return ready


def _landing_site(island: IslandState, ctx: StageContext) -> tuple[int, int] | None:
    """海に接する平地のうち、港があればそこ、なければ首都に一番近いマス。"""
    if island.capital is None:
        return None
    cx, cy = island.capital
    best = None
    for tile in island.tiles:
        if not _coastal(island, ctx, tile.x, tile.y):
            continue
        key = (
            not _has_port(island, ctx, tile.x, tile.y),
            abs(tile.x - cx) + abs(tile.y - cy),
            tile.x,
            tile.y,
        )
        if best is None or key < best[0]:
            best = (key, (tile.x, tile.y))
    return best[1] if best else None


def _advance(
    world: WorldState,
    attacker: IslandState,
    defender: IslandState,
    landed: list[dict[str, Any]],
    plans: dict[int, list[tuple[str, dict[str, Any]]]],
) -> None:
    """上陸した部隊を首都へ進め、補給の船団に国の倉庫の食べ物と弾薬を積む。"""
    if defender.capital is None:
        return
    goal = defender.capital
    for unit in landed:
        if unit["status"] != army.READY or unit.get("order") or (unit["x"], unit["y"]) == goal:
            continue
        plans.setdefault(attacker.id, []).append(
            ("move_unit", {"unit": unit["id"], "x": goal[0], "y": goal[1]})
        )
    for fleet in army.fleets_of(world, attacker.id):
        if (fleet["x"], fleet["y"]) != (defender.wx, defender.wy):
            continue
        goods = supply.fleet_goods(fleet)
        for good in (supply.FOOD, supply.AMMO):
            want = 8 * QUANTITY_SCALE * army.size(fleet) - goods.get(good, 0)
            got = stock.take(attacker.warehouse, good, max(0, want))
            if got:
                goods[good] = goods.get(good, 0) + got


def _propose_peace(
    world: WorldState,
    attacker_id: int,
    defender_id: int,
    plans: dict[int, list[tuple[str, dict[str, Any]]]],
) -> None:
    from hakoniwa_core.diplomacy import treaties

    for t in treaties.treaties(world):
        if t["state"] == treaties.PROPOSED and {attacker_id, defender_id} == set(t["parties"]):
            return  # 返事待ちの提案がある
    plans.setdefault(attacker_id, []).append(
        (
            "propose_treaty",
            {"partner": defender_id, "clauses": [{"type": "peace", "withdraw_turns": 4}]},
        )
    )


# --- 測る ------------------------------------------------------------------------------

# 数える出来事（ターンログの種類）と、表に出す名前
TITLES = {
    "battle": "戦闘",
    "tile_occupied": "占領されたマス",
    "occupation_lifted": "占領が解けたマス",
    "surrendered": "降伏",
    "unit_surrendered": "部隊の降伏",
    "prisoners_taken": "捕虜を取った",
    "war_facility_ruined": "戦場で壊れた施設",
    "unit_landed": "上陸",
    "military_starved": "兵の飢え",
    "military_deserted": "兵の脱走",
    "military_wages_late": "兵の賃金の遅れ",
    "rebellion_started": "反乱の始まり",
    "rebellion_ended": "反乱の終わり（鎮まった・追い払った）",
    "revolution": "革命",
    "civil_battle": "内戦の戦い",
    "rebels_routed": "反乱軍を追い払った",
    "rebels_dispersed": "反乱軍が散った",
    "tile_rebel_held": "反乱軍に占領されたマス",
    "unit_defected": "部隊の寝返り",
    "arsenal_seized": "武器庫の略奪",
    "riot_halted": "暴動で止まった施設の知らせ",
    "riot_halted_police": "うち警察が抑えた知らせ",
}
EVENTS = tuple(TITLES)
# 戦っている島の時系列に写す統計
STATS = (
    "population",
    "unemployment",
    "gdp",
    "food.ratio",
    "politics.stability",
    "military.soldiers",
    "military.units_ready",
    "military.abroad",
)
EVERY = 6  # 時系列を写す間隔（ターン）
WAR_KEYS = ("declared", "start", "state", "ended", "ended_by", "ceasefire")


class Watch:
    """戦争と反乱の出来事を数える（ターンを回した直後に読む。世界の状態は変えない）。

    war=True の場面では、戦争をする組の島の時系列（人口・失業率・生産・食べ物・政情・兵）も写す。
    """

    def __init__(self, war: bool, defs: Defs, config: GameConfig) -> None:
        self.war = war
        self.defs, self.config = defs, config
        self.events: Counter = Counter()
        self.by_island: dict[int, Counter] = {}
        self.first: dict[str, dict[int, int]] = {}  # 出来事 → 島 → 最初のターン
        self.killed: Counter = Counter()  # 島 → 戦死（battle のログ）
        self.series: dict[int, dict[int, dict[str, float]]] = {}  # 島 → ターン → 統計
        self.pairs: list[tuple[int, int]] = []
        self.wars: dict[str, dict[str, Any]] = {}  # 戦争の組（攻める島-守る島）→ 状態
        self.rebel_turns: Counter = Counter()  # 島 → 反乱軍のいたターン数

    def record(self, world: WorldState) -> None:
        from hakoniwa_core.military import civil_war

        if self.war and not self.pairs and world.turn >= WAR_FROM - WAR_PREP:
            self.pairs = pairs(world, self.defs, self.config)
        involved = {i for p in self.pairs for i in p}
        for island in world.islands.values():
            for entry in island.log:
                if entry.kind not in TITLES:
                    continue
                self.events[entry.kind] += 1
                self.by_island.setdefault(island.id, Counter())[entry.kind] += 1
                self.first.setdefault(entry.kind, {}).setdefault(island.id, world.turn)
                if entry.kind == "battle":
                    self.killed[island.id] += entry.args.get("killed", 0)
            if civil_war.active(world, island.id):
                self.rebel_turns[island.id] += 1
            if island.id in involved and world.turn % EVERY == 0:
                self.series.setdefault(island.id, {})[world.turn] = {
                    k: island.stats.get(k, 0) for k in STATS
                }
        for w in war.wars(world):
            self.wars[f"{w['attacker']}-{w['defender']}"] = {k: w.get(k) for k in WAR_KEYS}

    def result(self) -> dict[str, Any]:
        return {
            "pairs": [list(p) for p in self.pairs],
            "wars": self.wars,
            "events": dict(self.events),
            "by_island": {str(i): dict(c) for i, c in sorted(self.by_island.items())},
            "first": {k: {str(i): t for i, t in v.items()} for k, v in self.first.items()},
            "killed": {str(i): n for i, n in sorted(self.killed.items())},
            "rebel_turns": {str(i): n for i, n in sorted(self.rebel_turns.items())},
            "series": {
                str(i): {str(t): row for t, row in sorted(rows.items())}
                for i, rows in sorted(self.series.items())
            },
        }


def pair_line(result: Any, attacker: int, defender: int) -> str:
    """戦争をした組の流れを 1 行に（結果の JSON の war から）。"""
    first = result.get("first", {})
    by = result.get("by_island", {})
    a, d = str(attacker), str(defender)
    wars = result.get("wars", {}).get(f"{attacker}-{defender}")
    parts = []
    if wars:
        parts.append(f"宣戦 {wars['declared']}")
        if wars.get("start"):  # 上陸は場面が部隊を直接置くので、ログが出ない。宣戦の次のターン
            parts.append(f"上陸（仮） {wars['start']}")
    killed = result.get("killed", {})
    parts.append(
        f"戦闘 {by.get(d, {}).get('battle', 0)} 回（戦死 攻め {killed.get(a, 0)}・"
        f"守り {killed.get(d, 0)}）"
    )
    parts.append(f"占領されたマス {by.get(d, {}).get('tile_occupied', 0)}")
    fell = first.get("surrendered", {}).get(d)
    parts.append(f"首都陥落（降伏）{fell}" if fell else "首都は持ちこたえた")
    if wars and wars.get("ended") is not None:
        parts.append(f"講和 {wars['ended']}")
    elif wars and wars.get("state") == "ceasefire":
        parts.append("停戦のまま")
    series = result.get("series", {}).get(d, {})
    if series:
        turns = sorted(series, key=int)
        pop = [series[t]["population"] for t in turns]
        gdp = [series[t]["gdp"] for t in turns]
        jobless = [series[t]["unemployment"] for t in turns]
        before = turns.index(next((t for t in turns if int(t) >= WAR_FROM), turns[0]))
        parts.append(
            f"守りの島の人口 {pop[before]:.0f}→{pop[-1]:.0f}（最小 {min(pop[before:]):.0f}）、"
            f"生産 {gdp[before]:.0f}→最小 {min(gdp[before:]):.0f}→{gdp[-1]:.0f}、"
            f"失業率の最大 {max(jobless[before:]):.3f}"
        )
    return "、".join(parts)
