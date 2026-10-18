"""国をまたぐ移民の段（migration。バランス調整の回3。世界の段）と、移民の政策の命令。

人は、ほかの島の暮らしの見込みが今の島よりはっきり良ければ、少しずつ移り住みます（Codex E06。
これまでは飢えても国の外へ出られず、島の中で亡くなるだけだった）。都市の発展の段の後、政情の段の
前に、貿易の仕組みを置き終えた島（港から船で渡る）のあいだで動きます。

1. 暮らしの見込み V = 実質賃金（平均賃金 ÷ 物価指数）× 仕事の見つかる割合 × 食べられた割合。
   人の組は、仕事のある一般の家計・失業している家計・技術者の家計の3つ。仕事のある家計は今の島の
   仕事の見つかる割合を掛けず（もう仕事がある）、行き先では掛ける。技術者は技術者の賃金と失業率で
   見る。今の島の V は、行き先の V の migration.floor を下回らないとする（差は対数で約 3 まで）。
2. 受け入れてくれる行き先（行き先の政策 immigration: open はだれでも・jobs は人手の足りない分
   まで・engineers は技術者だけ・refugees は飢えている家計だけ・closed は受け入れない。決めて
   いない島も closed）ごとに、良くなる分 w = (ln(V行き先 ÷ V今) − cost − distance_cost × 距離)+ ×
   （1 + ties × 行き先に住む同郷の人 ÷ 今の島の住民）を出す。費用は旅費・言葉・知り合いのいない
   不安を対数の差で表したもので、先に移った同郷の人がいるほど移りやすい（移民の鎖）。1ターンに
   移りたい人 = 組の人数 × response × いちばん大きい w で、行き先には w の比で分かれる（行き先が
   多くても、移る人は増えない）。
3. 受け入れの上限: 行き先ごとに1ターンに「住民 × 1年の上限 ÷ 12」（上限は住民の1万分の
   immigration_cap。0 は migration.default_cap）と、町の空き（住む所）まで。申し込みが多ければ
   比例して分ける。
4. 出国（元の島の政策 emigration）: free（自由）・permit（許可制。1年に住民の1万分の
   emigration_quota まで）・ban（禁止）。政治の仕組みの上限（regimes.yaml の exit_control。
   民主主義は自由だけ、権威主義は許可制まで、全体主義は禁止まで）を超えては決められない。止められた
   人のうち、国境の見張り（border × 警察の人手の足りている割合）をすり抜けた人は出ていき、残りは
   「出られない人」として政情の目標を下げる（1年に住民の 1% あたり migration.trapped、
   trapped_max まで。modules/politics の events_bonus）。
5. 持ち出す預金: 移る人は、層の1人あたりの預金を元の島の通貨のまま持っていく（行き先の家計の外貨に
   なり、次のターンの為替の市場で売る）。元の島の資本取引が禁止（closed）なら持ち出せず、国境を
   すり抜けた人の預金とともに政府のものになる（emigrant_seized）。
6. 帰る人: 移った人（行き先の島の ext["migration"]["ties"]）は、元の島の見込みの方がはっきり良く
   なると、同じ式で帰る。自分の国の人なので受け入れの政策によらず帰れる（町の空きまで。住んでいる
   島の出国の政策は効く）。
7. 送金: 移った人のうち働いている人は、賃金の migration.remittance を元の島の一般の家計へ送る
   （住んでいる島の通貨で。どちらかの島の資本取引が禁止なら送れない）。国際収支の移転に入り、
   受け取った家計の所得になる。
8. 同郷の人のつながりは、1年に migration.ties_decay ずつ薄れる（土地になじむ・世代が替わる）。

移った人は、元の島の住むマス（層ごとの見込み）から抜け、行き先の空いた町に入る。飢えの蓄えは
人数で混ぜ、一般の家計の教育水準は住民の数で混ぜる（技術者は技術者の数に入る）。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, politics
from hakoniwa_core.allocation import proportional
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import ENGINEERS, GENERAL, JOBLESS, Payer, funds, household
from hakoniwa_core.economy.households import (
    add_income,
    blend_hunger,
    move_people,
    recount_engineers,
    split_persons,
)
from hakoniwa_core.modules.education import STOCK as EDUCATION_STOCK
from hakoniwa_core.modules.labor import engineer_unemployment
from hakoniwa_core.modules.politics import police_coverage, unemployment_of
from hakoniwa_core.modules.population import (
    ENDURED,
    HUNGER,
    homes_of,
    layer_tiles,
    remove_dead,
    spread_over,
    vacancies_of,
)
from hakoniwa_core.registry import OrderFailed, OrderHandler, Registry, WorldStage
from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.trade import distance
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.trade import policy as trade_policy
from hakoniwa_core.trade import started as trade_started
from hakoniwa_core.turn.context import StageContext

KEY = "migration"  # island.ext: 同郷の人のつながり（ties）と、出られない人（trapped）
FULL = 1000

# 受け入れの政策
OPEN = "open"
JOBS = "jobs"
ENGINEERS_ONLY = "engineers"
REFUGEES = "refugees"
CLOSED = "closed"
IMMIGRATION = (OPEN, JOBS, ENGINEERS_ONLY, REFUGEES, CLOSED)
# 出国の政策（制限の弱い順）
FREE = "free"
PERMIT = "permit"
BAN = "ban"
EMIGRATION = (FREE, PERMIT, BAN)

MOVERS = (GENERAL, JOBLESS, ENGINEERS)  # 移りうる家計の層（資産を持つ層は財産があり動かない）


def state_of(island: IslandState) -> dict[str, Any]:
    return island.ext.setdefault(KEY, {})


def ties_of(island: IslandState) -> dict[str, float]:
    """島に住む、ほかの島から移ってきた人（元の島の番号の文字列 → 人数）。"""
    return state_of(island).setdefault("ties", {})


def immigration(island: IslandState) -> str:
    return island.policy.immigration or CLOSED


def emigration(island: IslandState) -> str:
    return island.policy.emigration or FREE


def exit_limit(island: IslandState, defs: Defs) -> str:
    """政治の仕組みが許す、出国の制限の強さの上限（regimes.yaml の exit_control）。"""
    value = politics.trait(island, defs, "exit_control", BAN)
    return value if value in EMIGRATION else BAN


def trapped_share(island: IslandState) -> float:
    """出たいのに出られない人の、1年あたりの住民に対する割合（政情の段が読む）。"""
    return float(island.ext.get(KEY, {}).get("trapped", 0.0))


# --- 暮らしの見込み ------------------------------------------------------------------


@dataclass
class View:
    """島の、この段の初めの暮らしの見込み。"""

    population: int
    wage: float  # 一般の実質賃金
    jobs: float  # 一般の労働者の仕事の見つかる割合
    engineer_wage: float
    engineer_jobs: float
    fed: dict[str, float]  # 層 → このターンに食べられた割合（0〜1）
    hungry: dict[str, bool]  # 層 → 飢えているか
    persons: dict[str, int]  # 層 → 人数

    def offer(self, layer: str) -> float:
        """移ってくる人から見た、この島の見込み（仕事の見つかる割合を掛ける）。"""
        if layer == ENGINEERS:
            return self.engineer_wage * self.engineer_jobs
        return self.wage * self.jobs

    def here(self, layer: str) -> float:
        """今この島に住む層 layer の人の見込み（食べられた割合を掛ける）。"""
        fed = self.fed.get(layer, 1.0)
        if layer == GENERAL:
            return self.wage * fed
        return self.offer(layer) * fed


def active(island: IslandState) -> bool:
    """移民の出入りする島か（お金の経済と貿易の仕組みがあり、住民がいる）。"""
    return (
        island.has_economy
        and trade_started(island)
        and island.economy.average_wage > 0
        and any(t.population > 0 for t in island.tiles)
    )


def view_of(island: IslandState, ctx: StageContext) -> View:
    economy = island.economy
    cpi = economy.cpi_history[-1] if economy.cpi_history else 100.0
    level = max(cpi, 1e-6) / 100
    engineer = island.scratch.get("engineer_wage") or (
        economy.average_wage * ctx.config.get("labor.engineer_wage_ratio")
    )
    households = economy.households
    return View(
        population=sum(t.population for t in island.tiles),
        wage=economy.average_wage / level,
        jobs=max(0.0, 1 - unemployment_of(island, ctx.defs)),
        engineer_wage=engineer / level,
        engineer_jobs=max(0.0, 1 - engineer_unemployment(island)),
        fed={n: h.get("fed", FULL) / FULL for n, h in households.items()},
        hungry={
            n: h.get("fed", FULL) < FULL or h.get(HUNGER, 0) > 0 for n, h in households.items()
        },
        persons={n: h["persons"] for n, h in households.items()},
    )


def gain(settings: dict[str, Any], there: float, here: float, cells: float) -> float:
    """移って良くなる分: ln(行き先 ÷ 今) − 費用（今の見込みは行き先の floor 倍を下回らない）。"""
    if there <= 0:
        return 0.0
    here = max(here, there * settings["floor"])
    return math.log(there / here) - settings["cost"] - settings["distance_cost"] * cells


# --- 段 ----------------------------------------------------------------------------


@dataclass
class Flow:
    """ある島のある層から、ある島へ移る人。"""

    origin: int
    layer: str  # 出ていく層（帰る人は一般の層）
    target: int
    persons: float
    returning: bool = False  # 元の島へ帰る人
    leaked: bool = False  # 出国を止められたが、国境をすり抜けた人
    count: int = 0  # 丸めた人数


def _accepts(mode: str, layer: str, hungry: bool) -> bool:
    if mode in (OPEN, JOBS):
        return True
    if mode == ENGINEERS_ONLY:
        return layer == ENGINEERS
    if mode == REFUGEES:
        return hungry
    return False


def _rand_round(value: float, rng: random.Random) -> int:
    base = math.floor(value)
    return base + (1 if rng.random() < value - base else 0)


class Migration(WorldStage):
    name = "migration"

    def collect(self, island: IslandState, ctx: StageContext) -> Any:
        return None

    def resolve(self, requests: dict[int, Any], ctx: StageContext) -> dict[int, Any]:
        assert ctx.before is not None
        run(ctx.before, ctx)
        return {}

    def apply(self, island: IslandState, result: Any, ctx: StageContext) -> None:
        return None


@dataclass
class Tally:
    """島ごとの、このターンの出入り（統計とターンログ）。"""

    left: int = 0
    arrived: int = 0
    returned: int = 0  # arrived のうち、元の島へ帰ってきた人
    escaped: int = 0  # left のうち、国境をすり抜けた人
    seized: int = 0
    remitted_in: int = 0
    remitted_out: int = 0


def run(world: WorldState, ctx: StageContext) -> None:
    settings = ctx.config.get("migration")
    for island in world.islands.values():
        if KEY in island.ext:
            island.ext[KEY]["trapped"] = 0.0
    ids = [i for i in world.island_ids() if active(world.islands[i])]
    if len(ids) < 2:
        return
    views = {i: view_of(world.islands[i], ctx) for i in ids}
    flows = _wanted(world, settings, ids, views)
    _admit(world, ctx, settings, ids, views, flows)
    flows = _exit(world, ctx, settings, ids, views, flows)
    rng = ctx.rng()
    for flow in flows:
        flow.count = _rand_round(flow.persons, rng)
    tally = {i: Tally() for i in ids}
    moved = [f for f in flows if f.count > 0]
    _move(world, ctx, moved, tally)
    _remit(world, ctx, settings, ids, views, tally)
    _decay(world, settings)
    _report(world, ids, moved, tally)


def _wanted(
    world: WorldState, settings: dict[str, Any], ids: list[int], views: dict[int, View]
) -> list[Flow]:
    """島の層ごと・行き先ごとの、1ターンに移りたい人（帰りたい人も）。"""
    flows: list[Flow] = []
    response = settings["response"]
    for o in ids:
        origin, home = world.islands[o], views[o]
        for layer in MOVERS:
            persons = home.persons.get(layer, 0)
            if persons <= 0:
                continue
            hungry = home.hungry.get(layer, False)
            weights: dict[int, float] = {}
            for d in ids:
                dest = world.islands[d]
                if d == o or not _accepts(immigration(dest), layer, hungry):
                    continue
                cells = distance(origin, dest)
                g = gain(settings, views[d].offer(layer), home.here(layer), cells)
                if g <= 0:
                    continue
                diaspora = ties_of(dest).get(str(o), 0.0)
                weights[d] = g * (1 + settings["ties"] * diaspora / max(1, home.population))
            if not weights:
                continue
            total = persons * response * max(weights.values())
            whole = sum(weights.values())
            for d, w in sorted(weights.items()):
                flows.append(Flow(o, layer, d, total * w / whole))
        # 帰る人: この島に住む、ほかの島から移ってきた人
        for key, people in sorted(ties_of(origin).items()):
            d = int(key)
            if d not in views or d == o or people <= 0:
                continue
            cells = distance(origin, world.islands[d])
            g = gain(settings, views[d].offer(GENERAL), home.here(GENERAL), cells)
            if g > 0:
                persons = min(people, home.persons.get(GENERAL, 0))
                flows.append(Flow(o, GENERAL, d, persons * response * g, returning=True))
    return flows


def _room(island: IslandState, ctx: StageContext) -> float:
    """町の空き（住める人数）。上限を超えて詰めて住む町（過密。回5）の分は空きから引く。"""
    return float(
        max(
            0,
            sum(
                fac.housing_capacity(ctx.defs, f) - island.tile(*pos).population
                for pos, f in homes_of(island, ctx.config).items()
            ),
        )
    )


def _admit(
    world: WorldState,
    ctx: StageContext,
    settings: dict[str, Any],
    ids: list[int],
    views: dict[int, View],
    flows: list[Flow],
) -> None:
    """行き先の受け入れの上限と町の空きで、移りたい人を減らす（帰る人は町の空きだけで、
    先に入る）。"""
    defs = ctx.defs
    for d in ids:
        island = world.islands[d]
        coming = [f for f in flows if f.target == d]
        if not coming:
            continue
        room = _room(island, ctx)
        back = [f for f in coming if f.returning]
        _scale(back, room)
        room -= sum(f.persons for f in back)
        new = [f for f in coming if not f.returning]
        if not new:
            continue
        cap = island.policy.immigration_cap or settings["default_cap"]
        limit = min(room, views[d].population * cap / 10000 / YEAR)
        if immigration(island) == JOBS:
            share = fac.labor_share(defs, island)
            missing = sum(v[2] for v in vacancies_of(defs, island, ctx.config, ctx.turn))
            limit = min(limit, missing / share if share > 0 else 0.0)
        _scale(new, limit)


def _scale(flows: list[Flow], limit: float) -> None:
    """流れの合計が limit を超えれば、比例して減らす。"""
    total = sum(f.persons for f in flows)
    if total > limit:
        for f in flows:
            f.persons *= max(0.0, limit) / total


def _exit(
    world: WorldState,
    ctx: StageContext,
    settings: dict[str, Any],
    ids: list[int],
    views: dict[int, View],
    flows: list[Flow],
) -> list[Flow]:
    """元の島の出国の政策で止め、国境をすり抜けた人を分け、出られない人を数える。"""
    out: list[Flow] = []
    for o in ids:
        island = world.islands[o]
        leaving = [f for f in flows if f.origin == o and f.persons > 0]
        if not leaving:
            continue
        mode = emigration(island)
        if mode == FREE:
            out.extend(leaving)
            continue
        total = sum(f.persons for f in leaving)
        allowed = 0.0
        if mode == PERMIT:
            quota = views[o].population * island.policy.emigration_quota / 10000 / YEAR
            allowed = min(1.0, quota / total)
        escape = max(0.0, 1 - settings["border"] * police_coverage(island, ctx))
        blocked = 0.0
        for f in leaving:
            stopped = f.persons * (1 - allowed)
            blocked += stopped
            if allowed > 0:
                out.append(Flow(f.origin, f.layer, f.target, f.persons * allowed, f.returning))
            if stopped * escape > 0:
                out.append(
                    Flow(f.origin, f.layer, f.target, stopped * escape, f.returning, leaked=True)
                )
        trapped = blocked * (1 - escape) * YEAR  # 1年あたり
        state_of(island)["trapped"] = round(trapped / max(1, views[o].population), 6)
        island.stats["migration.trapped"] = round(trapped)
    return out


# --- 人とお金を動かす ----------------------------------------------------------------


def _move(world: WorldState, ctx: StageContext, flows: list[Flow], tally: dict[int, Tally]) -> None:
    """流れの人を元の島の住むマスと層から抜き、預金を持たせて行き先の町と層に入れる。"""
    defs = ctx.defs
    groups: dict[tuple[int, str], list[Flow]] = {}
    for f in flows:
        groups.setdefault((f.origin, f.layer), []).append(f)
    touched: set[int] = set()
    arrivals: list[tuple[Flow, int, float, float]] = []  # (流れ, 持ち出す預金, 飢えの蓄え, 教育)
    for o in sorted({o for o, _ in groups}):
        island = world.islands[o]
        households = island.economy.households
        placed = layer_tiles(defs, island, households)
        controls = trade_policy.capital_controls(island, defs)
        education = island.economy.ext.get(EDUCATION_STOCK, island.economy.education / FULL)
        gone: dict[str, int] = {}
        for layer in MOVERS:
            group = groups.get((o, layer))
            if not group:
                continue
            h = households[layer]
            persons = h["persons"]
            places = placed.get(layer, {})
            leaving = _trim(group, min(persons, sum(places.values())))
            if leaving <= 0:
                continue
            for pos, n in spread_over(leaving, places).items():
                tile = island.tile(*pos)
                tile.population -= min(n, tile.population)
            entity = household(layer)
            money = ledger.money(island, entity)
            seized = 0
            for f in group:
                if f.count <= 0:
                    continue
                carried = money * f.count // persons
                if f.leaked or controls == trade_policy.CLOSED:
                    seized += carried
                    carried = 0
                body = {k: h[k] for k in (HUNGER, ENDURED) if k in h}  # 飢えの蓄えも連れて移る
                arrivals.append((f, carried, body, education))
            if seized > 0:
                ledger.pay(island, entity, ledger.GOV, seized, "emigrant_seized")
                tally[o].seized += seized
            gone[layer] = leaving
        remove_dead(island, defs, gone)
        touched.add(o)
    for f, carried, hunger, education in arrivals:
        if f.count > 0:
            _arrive(world, ctx, f, carried, hunger, education, tally)
            touched.add(f.target)
    for i in sorted(touched):
        island = world.islands[i]
        move_people(island, split_persons(island, defs, sum(t.population for t in island.tiles)))


def _trim(group: list[Flow], room: int) -> int:
    """層の住むマスの見込みより多くは出られない（丸めの端数）。出ていく人数を返す。"""
    leaving = sum(f.count for f in group)
    for f in sorted(group, key=lambda f: (-f.count, f.target)):
        if leaving <= room:
            break
        cut = min(f.count, leaving - room)
        f.count -= cut
        leaving -= cut
    return leaving


def _arrive(
    world: WorldState,
    ctx: StageContext,
    f: Flow,
    carried: int,
    hunger: dict[str, float],
    education: float,
    tally: dict[int, Tally],
) -> None:
    defs, config = ctx.defs, ctx.config
    origin, dest = world.islands[f.origin], world.islands[f.target]
    households = dest.economy.households
    layer = ENGINEERS if f.layer == ENGINEERS and ENGINEERS in households else GENERAL
    population = sum(t.population for t in dest.tiles)
    free = sorted(
        (
            (pos, max(0, fac.housing_capacity(defs, home) - dest.tile(*pos).population))
            for pos, home in homes_of(dest, config).items()
        ),
        key=lambda kv: (-kv[1], kv[0][1], kv[0][0]),
    )
    fit = proportional(min(f.count, sum(n for _, n in free)), free)
    for pos, n in fit.items():
        dest.tile(*pos).population += n
    rest = f.count - sum(fit.values())
    if rest > 0:  # 空きを丸めの端数だけ超えた分は、いちばん大きな町に（次のターンに町の中で移る）
        pos = free[0][0] if free else (dest.tiles[0].x, dest.tiles[0].y)
        dest.tile(*pos).population += rest
    h = households[layer]
    h["persons"] += f.count
    blend_hunger(hunger, h, f.count)
    if layer == ENGINEERS:
        pool = dest.economy.workers.get(fac.ENGINEER, 0)
        workers = round(f.count * defs.workers[fac.ENGINEER].get("share", 1.0))
        dest.economy.workers[fac.ENGINEER] = pool + workers
        recount_engineers(dest, pool, workers, 0)
    else:
        economy = dest.economy
        stock = economy.ext.get(EDUCATION_STOCK, economy.education / FULL)
        mixed = (stock * population + education * f.count) / (population + f.count)
        economy.ext[EDUCATION_STOCK] = mixed
        economy.education = round(mixed * FULL)
    if carried > 0:
        fxrates.ensure(world, dest, origin.id)
        value = fxrates.to_own(dest, origin.id, carried)
        fxmoney.transfer(
            world,
            origin.id,
            (origin.id, household(f.layer)),
            (dest.id, household(layer)),
            carried,
            "migrant_deposits",
            value=value,
        )
    if f.returning:  # 住んでいた島の、同郷の人から抜ける
        ties = ties_of(origin)
        ties[str(dest.id)] = max(0.0, ties.get(str(dest.id), 0.0) - f.count)
        tally[dest.id].returned += f.count
    else:
        ties = ties_of(dest)
        ties[str(origin.id)] = ties.get(str(origin.id), 0.0) + f.count
    tally[origin.id].left += f.count
    tally[dest.id].arrived += f.count
    if f.leaked:
        tally[origin.id].escaped += f.count


def _remit(
    world: WorldState,
    ctx: StageContext,
    settings: dict[str, Any],
    ids: list[int],
    views: dict[int, View],
    tally: dict[int, Tally],
) -> None:
    """移った人のうち働いている人が、賃金の一部を元の島の一般の家計へ送る。"""
    defs = ctx.defs
    entity = household(GENERAL)
    for d in ids:
        host = world.islands[d]
        if trade_policy.capital_controls(host, defs) == trade_policy.CLOSED:
            continue
        payer = Payer(entity)
        share = fac.labor_share(defs, host)
        wage = host.economy.average_wage
        for key, people in sorted(ties_of(host).items()):
            o = int(key)
            if o not in views or o == d:
                continue
            home = world.islands[o]
            if trade_policy.capital_controls(home, defs) == trade_policy.CLOSED:
                continue
            want = int(people * share * views[d].jobs * wage * settings["remittance"])
            amount = min(want, funds.available(host, payer))
            if amount <= 0:
                continue
            fxrates.ensure(world, home, d)
            value = fxrates.to_own(home, d, amount)
            fxmoney.transfer(world, d, (d, entity), (o, entity), amount, "remittance", value=value)
            add_income(host, GENERAL, -amount)
            add_income(home, GENERAL, value)
            tally[d].remitted_out += amount
            tally[o].remitted_in += value


def _decay(world: WorldState, settings: dict[str, Any]) -> None:
    keep = (1 - settings["ties_decay"]) ** (1 / YEAR)
    for island in world.islands.values():
        ties = island.ext.get(KEY, {}).get("ties")
        if not ties:
            continue
        for key in sorted(ties):
            value = ties[key] * keep
            if value < 0.5 or int(key) not in world.islands:
                del ties[key]
            else:
                ties[key] = round(value, 3)


def _report(world: WorldState, ids: list[int], flows: list[Flow], tally: dict[int, Tally]) -> None:
    abroad: dict[int, float] = {}
    for island in world.islands.values():
        for key, people in island.ext.get(KEY, {}).get("ties", {}).items():
            abroad[int(key)] = abroad.get(int(key), 0.0) + people
    went: dict[int, dict[int, int]] = {}  # 島 → 行き先 → 人数
    came: dict[int, dict[int, int]] = {}  # 島 → 元の島 → 人数（帰ってきた人を除く）
    for f in flows:
        row = went.setdefault(f.origin, {})
        row[f.target] = row.get(f.target, 0) + f.count
        if not f.returning:
            row = came.setdefault(f.target, {})
            row[f.origin] = row.get(f.origin, 0) + f.count
    for i in ids:
        island, t = world.islands[i], tally[i]
        stats = island.stats
        stats["migration.left"] = t.left
        stats["migration.arrived"] = t.arrived
        stats["migration.returned"] = t.returned
        stats["migration.escaped"] = t.escaped
        stats["migration.seized"] = t.seized
        stats["migration.remittance_in"] = t.remitted_in
        stats["migration.remittance_out"] = t.remitted_out
        stats["migration.abroad"] = round(abroad.get(i, 0.0))
        stats["migration.foreign_born"] = round(sum(ties_of(island).values()))
        stats.setdefault("migration.trapped", 0)
        if t.left:
            args = {"count": t.left, "country": world.islands[_most(went[i])].name}
            island.log.append(LogEntry("migration_left", args))
        if t.escaped:
            island.log.append(LogEntry("migration_escaped", {"count": t.escaped}))
        if t.seized:
            island.log.append(LogEntry("migration_seized", {"amount": t.seized}))
        if t.arrived - t.returned:
            args = {"count": t.arrived - t.returned, "country": world.islands[_most(came[i])].name}
            island.log.append(LogEntry("migration_arrived", args))
        if t.returned:
            island.log.append(LogEntry("migration_returned", {"count": t.returned}))


def _most(counts: dict[int, int]) -> int:
    """人数のいちばん多い相手の島（同じなら番号の小さい島）。"""
    return min(counts, key=lambda k: (-counts[k], k))


# --- 命令 --------------------------------------------------------------------------


def check_immigration(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not island.has_economy:
        raise OrderFailed("reason_no_economy")


def set_immigration(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """移民の受け入れ（だれでも・人手の足りない分まで・技術者だけ・難民だけ・受け入れない）と、
    1年の上限（住民の1万分の1。0 は game.yaml の migration.default_cap）。"""
    island.policy.immigration = payload["mode"]
    island.policy.immigration_cap = int(payload.get("cap", 0))
    cap = island.policy.immigration_cap or ctx.config.get("migration.default_cap")
    args = {"mode": f"immigration_{payload['mode']}", "cap": cap / 100}
    island.log.append(LogEntry("immigration_set", args))


def check_emigration(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not island.has_economy:
        raise OrderFailed("reason_no_economy")
    if EMIGRATION.index(payload["mode"]) > EMIGRATION.index(exit_limit(island, defs)):
        raise OrderFailed("reason_exit_control_forbidden")


def set_emigration(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """出国の扱い（自由・許可制・禁止）と、許可制の1年の枠（住民の1万分の1）。"""
    island.policy.emigration = payload["mode"]
    island.policy.emigration_quota = int(payload.get("quota", 0))
    _log_emigration(island, "emigration_set")


def _log_emigration(island: IslandState, kind: str) -> None:
    mode = emigration(island)
    if mode == PERMIT:
        args = {"mode": f"emigration_{mode}", "quota": island.policy.emigration_quota / 100}
        island.log.append(LogEntry(kind + "_quota", args))
    else:
        island.log.append(LogEntry(kind, {"mode": f"emigration_{mode}"}))


def fit_exit_policy(island: IslandState, defs: Defs) -> None:
    """政治の仕組みが変わったとき、出国の制限を新しい仕組みの上限まで弱める（憲法改正）。"""
    limit = exit_limit(island, defs)
    if EMIGRATION.index(emigration(island)) > EMIGRATION.index(limit):
        island.policy.emigration = limit
        _log_emigration(island, "emigration_relaxed")


def register(registry: Registry) -> None:
    registry.add_stage(Migration())
    registry.add_order(
        OrderHandler("set_immigration", execute=set_immigration, check=check_immigration)
    )
    registry.add_order(
        OrderHandler("set_emigration", execute=set_emigration, check=check_emigration)
    )
