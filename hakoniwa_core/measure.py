"""バランス調整の測り方（回2）: 場面ごとの一括シミュレーションと、失業の内わけ。

後の回の効き目を同じ物差しで比べるための道具です。ゲームの状態は読むだけで、変えません。

- 場面（SCENARIOS）: 標準の計画・命令なし・中央銀行を独立させた国・少ない島の世界（海外の商人が
  去った後も貿易が続くか。Codex のレビュー B03）。場面ごとに、関門に数える目安の種類を決めます
  （hakoniwa_core/gate.py の KINDS。目安の値は変えない）。
- 失業の内わけ（Codex B01）: 島×ターンごとに、働き手の行き先（仕事・村の自給・失業）、施設が人を
  雇わなかった理由（売れ行き・原料・電力・資金・働き手・技術者・止まっている）、通勤圏で求人と
  失業者が出会えていないか、需要の内わけ（家計・工事・政府・輸出入）、民営が参入しなかった理由、
  機械と鋼材を誰が買ったかを数えます。

一括シミュレーションから使います:

    python -m hakoniwa_core.simulate --scenario plan --seed 1 --turns 300 --gate \\
        --out runs/plan-s1.json --series runs/plan-s1.jsonl
    python -m hakoniwa_core.measure batch --set quick --out-dir runs/r2
    python -m hakoniwa_core.measure batch --set full --out-dir runs/r2-full
    python -m hakoniwa_core.measure compare runs/main runs/r2

quick は比べるための組（種1・2、300ターン）、full は回の終わりに1回だけ回す組（種1〜4、500ターン）。
compare は2つの結果を並べます。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import gate
from hakoniwa_core.autoplan import plan_trade, plan_turn, queue_orders
from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import books, is_stopped, is_transport
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.geo import distance
from hakoniwa_core.modules.population import subsistence_persons
from hakoniwa_core.state import IslandState, Tile, WorldState
from hakoniwa_core.units import from_milli

# 命令なしの国で関門に数える目安: 崩れないか（帳簿・値段・金利・人口・銀行の連鎖）。失業率や物価は
# 命令で整えるものなので、見るだけにする（目安の値は変えない）
STEADY_KINDS = (
    "gdp_gap",
    "price",
    "rate_high",
    "rate_low",
    "population_floor",
    "population_jump",
    "bank_cascade",
)


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    islands: int
    orders: str  # plan（標準の計画）か none（命令なし）
    # 中央銀行を独立させる命令を出す。議会で否決されたら、通るまで毎ターン出し直す（プレイヤーと
    # 同じ。民主主義の島は、建国した最初のターンは賛成がちょうど半分で否決される）
    independent: bool = False
    kinds: tuple[str, ...] | None = None  # 関門に数える目安の種類（None なら全部）
    # 初めのターンに、どの島も移民の受け入れをこれにする（回3。"" は命令を出さない）
    immigration: str = ""


SCENARIOS = {
    s.name: s
    for s in (
        Scenario("plan", "標準の計画", 50, "plan"),
        Scenario(
            "none", "命令なし（初めて遊ぶ人や放っておかれた国）", 50, "none", kinds=STEADY_KINDS
        ),
        Scenario("independent", "中央銀行を独立させた国（標準の計画）", 50, "plan", True),
        Scenario("small", "少ない島の世界（海外の商人が去った後）", 4, "plan"),
        Scenario(
            "open", "どの島も移民を受け入れる世界（標準の計画）", 50, "plan", immigration="open"
        ),
    )
}

# 一度に回す組（場面, 種, ターン数）。quick は回ごとの比べ、full は回の終わりに一度
SETS = {
    "quick": [
        ("plan", 1, 300),
        ("plan", 2, 300),
        ("none", 1, 300),
        ("independent", 1, 300),
        *[("small", seed, 300) for seed in (1, 2, 3, 4)],
        ("open", 1, 300),
    ],
    "full": [
        *[("plan", seed, 500) for seed in (1, 2, 3, 4)],
        ("none", 1, 300),
        ("none", 2, 300),
        ("independent", 1, 300),
        ("independent", 2, 300),
        *[("small", seed, 500) for seed in (1, 2, 3, 4)],
        ("open", 1, 500),
        ("open", 2, 500),
    ],
}

# 施設が全力で動かなかった理由（施設の status の「:」の前）のまとめ方
CAUSES = {
    "target": "sales",  # 売れ行きに合わせた
    "no_demand": "sales",
    "power_price": "sales",
    "cargo": "sales",  # 港・商社の扱う荷が少ない
    "business": "sales",  # 小さい銀行の預金と融資が少ない
    "input": "inputs",
    "input_price": "inputs",
    "public_price": "inputs",
    "power": "power",
    "no_grid": "power",
    "funds": "funds",
    "wages": "funds",
    "workers": "workers",
    "engineers": "engineers",
}
CAUSE_GROUPS = ("sales", "inputs", "power", "funds", "workers", "engineers", "halted")

# 誰が買ったかを数える財（設備と工事の資材。回6 の材料）
WATCH = ("machinery", "steel", "building_materials", "transport_ship", "automobile")
BUYERS = ("construction", "production", "equipment", "export", "government", "households")

# 時系列に写す統計
SERIES_STATS = (
    "population",
    "labor",
    "employed",
    "subsistence",
    "unemployment",
    "gdp",
    "gdp.spending",
    "cpi",
    "inflation",
    "wage.average",
    "wage.real",
    "happiness",
    "food.ratio",
    "politics.stability",
    "education.level",
    "engineers",
    "engineers.unemployment",
    "debt",
    "debt_ratio",
    "treasury",
    "policy_rate",
    "trade.exports",
    "trade.imports",
    "trade.merchant",
    "ships",
    "ships.busy",
    "port.handled",
    "facilities",
    "construction",
    "loans",
    "loans.arrears",
    "money.deposits",
    "power.shortage",
    "population.starved",
    "migration.left",
    "migration.arrived",
    "migration.returned",
    "migration.trapped",
    "migration.remittance_in",
    "migration.abroad",
)


def queue_scenario_orders(world: WorldState, scenario: Scenario, defs: Defs, config: GameConfig):
    """次のターンの前に、場面の命令を並べる。"""
    for island in world.islands.values():
        orders = []
        if scenario.independent and not island.policy.cb_independent:
            orders.append(("set_central_bank_independence", {"independent": True}))
        if scenario.immigration and island.policy.immigration != scenario.immigration:
            orders.append(("set_immigration", {"mode": scenario.immigration}))
        if scenario.orders == "plan":
            orders += plan_turn(island, defs, config, world.turn + 1)
        queue_orders(island, orders)
    if scenario.orders == "plan":
        for island_id, orders in plan_trade(world, defs, config).items():
            queue_orders(world.islands[island_id], orders)


# --- 島×ターンの記録 ------------------------------------------------------------------


def island_row(
    island: IslandState, defs: Defs, config: GameConfig, prices: Mapping[str, int]
) -> dict[str, Any]:
    """このターンの島の測り（ターンを回した直後に読む。状態は変えない）。

    prices は島の初めの値段。
    """
    s = island.stats
    row: dict[str, Any] = {k: s[k] for k in SERIES_STATS if k in s}
    for key, value in s.items():
        if key.startswith("spent."):
            row[key] = value
    for good in WATCH:
        for kind in ("production", "volume"):
            if f"{kind}.{good}" in s:
                row[f"{kind}.{good}"] = s[f"{kind}.{good}"]
    row["output.real"] = gate.real_output(s, prices)
    row.update(jobs(island, defs, config))
    row.update(reach(island, defs, config))
    row.update(demand(island))
    row.update(buyers(island, config))
    block = island.economy.finance.get("entry_block")
    if block:
        row["entry_block"] = f"{block['reason']}:{block['facility']}"
    row["cb_independent"] = int(island.policy.cb_independent)
    return row


def jobs(island: IslandState, defs: Defs, config: GameConfig) -> dict[str, float]:
    """施設の席: 全力の人数（capacity）・雇いたかった人数（wanted）・雇えた人数（filled）・
    埋まらなかった席（vacant）と、動いている施設が全力で動かなかった分の人数を理由ごとに（idle.*）。

    idle は「全力の人数 ×（1 − 稼働率）」で、売れ行き・原料などで人を雇わなかった席の数。
    """
    out: Counter = Counter()
    for f in island.facilities:
        if fac.is_housing(config, f):
            continue
        wanted = f.economy.get("wanted", 0)
        out["jobs.wanted"] += wanted
        out["jobs.filled"] += f.workers
        out["jobs.vacant"] += max(0, wanted - f.workers)
        if not f.built:
            out["jobs.sites"] += f.workers
            continue
        need = fac.workers_needed(defs, f.kind)
        if need <= 0 or not fac.on_map(f):
            continue
        out["jobs.capacity"] += need
        if is_stopped(f) or f.status in (fac.STATUS_STARTING, "halted", "riot", "recipe_change"):
            cause = "halted"
        else:
            cause = CAUSES.get(f.status.partition(":")[0], "halted")
        lost = need * (fac.FULL - min(fac.FULL, max(0, f.operating_rate))) / fac.FULL
        if lost > 0:
            out[f"idle.{cause}"] += lost
        out[f"kind.{f.kind}.capacity"] += need
        out[f"kind.{f.kind}.working"] += need - lost
    return dict(out)


def reach(island: IslandState, defs: Defs, config: GameConfig) -> dict[str, float]:
    """通勤圏で、失業者と埋まらなかった席が出会えているか。

    unreached.unemployed: 通勤圏に埋まらなかった席のない失業者（近くに仕事がない）。
    unreached.vacant: 通勤圏に失業者のいない、埋まらなかった席（人のいない所の求人）。
    この2つの小さい方は、通勤の範囲が広がるか人が移れば埋まる失業の上限（場所のずれ）。
    島のどこからでも雇う施設（運送業・マスを持たない施設）の席は、どの失業者にも届く。
    """
    idle = {(t.x, t.y): n for t, n in _jobless(island, defs) if n > 0}
    seats = _seats(island, defs, config)
    near = {pos for pos in idle if any(_reaches(pos, at, r) for at, r, _ in seats)}
    lonely = sum(v for at, r, v in seats if not any(_reaches(p, at, r) for p in idle))
    far = sum(n for pos, n in idle.items() if pos not in near)
    return {
        "jobless": sum(idle.values()),
        "unreached.unemployed": far,
        "unreached.vacant": lonely,
        "mismatch": min(far, lonely),
    }


def _jobless(island: IslandState, defs: Defs) -> list[tuple[Tile, int]]:
    """人の住むマスごとの失業者（働き手 − 就業者 − 村の自給。統計の失業率と同じ数え方）。"""
    share = fac.labor_share(defs, island)
    out = []
    for t in island.tiles:
        if t.population <= 0:
            continue
        free = max(0, fac.labor_force(defs, island, t) - t.ext.get("employed", 0))
        out.append((t, max(0, free - int(subsistence_persons(defs, island, t) * share))))
    return out


def _seats(island: IslandState, defs: Defs, config: GameConfig) -> list:
    """埋まらなかった席のある施設の (場所, 通勤の範囲, 埋まらなかった席)。範囲 None は島のどこから
    でも雇う施設（運送業とマスを持たない施設。雇用の段の _island_wide と同じ）。"""
    out = []
    for f in island.facilities:
        if fac.is_housing(config, f):
            continue
        vacant = max(0, f.economy.get("wanted", 0) - f.workers)
        if vacant <= 0:
            continue
        everywhere = is_transport(config, f) or not fac.on_map(f)
        radius = None if everywhere else fac.commute_radius(defs, config, f.kind)
        out.append(((f.x, f.y), radius, vacant))
    return out


def _reaches(pos: tuple[int, int], at: tuple[int, int], radius: int | None) -> bool:
    return radius is None or distance(pos, at) <= radius


def demand(island: IslandState) -> dict[str, float]:
    """このターンの GDP の支出の側の内わけ（hakoniwa_core/economy/books.py の gdp と同じ流れ）。"""
    hh = books.sector(island, books.HOUSEHOLDS)
    site = books.sector(island, books.CONSTRUCTION)
    gov = books.sector(island, books.GOVERNMENT)
    store = books.sector(island, books.WAREHOUSE)
    trade = books.sector(island, books.TRADE)
    out = {
        "demand.households": sum(hh.get(k, 0) for k in ("goods", "electricity", "freight")),
        "demand.housing": hh.get("housing", 0),
        "demand.construction": sum(site.get(k, 0) for k in ("materials", "freight", "wages")),
        "demand.government": sum(gov.get(k, 0) for k in ("wages", "goods", "freight")),
        "demand.warehouse": store.get("purchases", 0) - store.get("sales", 0),
        "demand.exports": trade.get("exports", 0),
        "demand.imports": trade.get("imports", 0),
    }
    named = sum(v for k, v in out.items() if k != "demand.imports") - out["demand.imports"]
    out["demand.inventory"] = island.stats.get("gdp.spending", 0) - named
    cpi = island.stats.get("cpi", 0)
    if cpi:
        out["demand.households.real"] = out["demand.households"] * 100 / cpi
    return out


def buyers(island: IslandState, config: GameConfig) -> dict[str, float]:
    """WATCH の財を、このターンに誰が買ったか（量）。工事・物を作る施設の原料・船とトラック（設備）
    ・輸出（商社）・政府（インフラの公共事業・建て替え・国の倉庫）・家計。"""
    by_id = {f.id: f for f in island.facilities}
    out: Counter = Counter()
    for t in island.trades:
        if t.good not in WATCH:
            continue
        if t.buyer.startswith("hh:"):
            who = "households"
        elif t.buyer.startswith("fac:"):
            who = _buyer_kind(config, by_id.get(int(t.buyer.removeprefix("fac:"))))
        else:  # gov: インフラの公共事業・建て替え・国の倉庫・契約
            who = "government"
        out[f"buy.{t.good}.{who}"] += from_milli(t.quantity)
    return dict(out)


def _buyer_kind(config: GameConfig, f) -> str:
    if f is None or not f.built:  # 工事中の施設の資材（その後に建ち終わった施設も含む）
        return "construction"
    if f.kind == config.get("trade.trader_facility"):  # 商社が輸出のために買う
        return "export"
    if not fac.on_map(f):  # 運送業のトラック・海運業の船
        return "equipment"
    return "production"


def zones(island: IslandState, defs: Defs, config: GameConfig) -> list[dict[str, Any]]:
    """町と村（人の住むマス）ごとの働き手・就業者・自給・失業者と、通勤圏に届く埋まらなかった席。"""
    share = fac.labor_share(defs, island)
    seats = _seats(island, defs, config)
    out = []
    for t, jobless in _jobless(island, defs):
        force = fac.labor_force(defs, island, t)
        employed = min(force, t.ext.get("employed", 0))
        home = island.facility_at(t.x, t.y)
        level = home.ext.get("level") if home is not None else None
        out.append(
            {
                "x": t.x,
                "y": t.y,
                "level": level or fac.settlement_of(defs, t.population),
                "labor": force,
                "employed": employed,
                "subsistence": min(
                    force - employed, int(subsistence_persons(defs, island, t) * share)
                ),
                "unemployed": jobless,
                "vacant_in_reach": sum(v for at, r, v in seats if _reaches((t.x, t.y), at, r)),
            }
        )
    return out


# --- 足し上げと結果 ------------------------------------------------------------------


class Measure:
    """ターンごとに島の測りを世界全体で足し上げ、望めば島×ターンの時系列（JSON Lines）を書く。"""

    def __init__(
        self,
        world: WorldState,
        defs: Defs,
        config: GameConfig,
        series: IO[str] | None = None,
        zones_every: int = 12,
    ) -> None:
        self.defs, self.config = defs, config
        self.prices = {
            i.id: initial_prices(defs, config, i.resources) for i in world.islands.values()
        }
        self.series = series
        self.zones_every = zones_every
        self.totals: dict[int, Counter] = {}  # ターン → 世界全体の合計
        self.fleet: dict[int, dict[int, float]] = {}  # 島 → ターン → 輸送船
        self.independent_since: dict[int, int] = {}  # 島 → 中央銀行が独立したターン

    def record(self, world: WorldState) -> None:
        total: Counter = Counter()
        for island in world.islands.values():
            if not island.has_economy:
                continue
            row = island_row(island, self.defs, self.config, self.prices.get(island.id, {}))
            for key, value in row.items():
                if isinstance(value, int | float) and key not in RATES:
                    total[key] += value
            if "entry_block" in row:
                total["entry_block." + row["entry_block"].partition(":")[0]] += 1
            total["islands"] += 1
            self.fleet.setdefault(island.id, {})[world.turn] = row.get("ships", 0)
            if row["cb_independent"]:
                self.independent_since.setdefault(island.id, world.turn)
            if self.series is not None:
                line = {"t": world.turn, "island": island.id, **row}
                if self.zones_every and world.turn % self.zones_every == 0:
                    line["zones"] = zones(island, self.defs, self.config)
                self.series.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.totals[world.turn] = total

    def window(self, start: int, end: int) -> dict[str, float]:
        """start から end までのターンを足し上げた、働き手・需要・止まった理由の割合。"""
        s: Counter = Counter()
        turns = [t for t in self.totals if start <= t <= end]
        for t in turns:
            s.update(self.totals[t])
        labor, spending = s["labor"], s["gdp.spending"]
        if not turns or labor <= 0:
            return {}

        def share(key: str, of: float = labor) -> float:
            return s[key] / of if of else 0.0

        out = {
            "labor.employed": share("employed"),
            "labor.subsistence": share("subsistence"),
            "labor.jobless": share("jobless"),
            "jobless.unreached": share("unreached.unemployed"),
            "vacant.unreached": share("unreached.vacant"),
            "jobless.mismatch": share("mismatch"),
            "jobs.capacity": share("jobs.capacity"),
            "jobs.wanted": share("jobs.wanted"),
            "jobs.filled": share("jobs.filled"),
            "jobs.vacant": share("jobs.vacant"),
            "jobs.sites": share("jobs.sites"),
        }
        out.update({f"idle.{g}": share(f"idle.{g}") for g in CAUSE_GROUPS})
        for key in sorted(k for k in s if k.startswith("demand.") and not k.endswith(".real")):
            out[f"gdp.{key}"] = share(key, spending)
        people = s["population"]
        out["households.real_per_person"] = s["demand.households.real"] / people if people else 0
        island_turns = s["islands"]
        for key in sorted(k for k in s if k.startswith("entry_block.")):
            out[key] = s[key] / island_turns
        for good in WATCH:
            bought = {who: s[f"buy.{good}.{who}"] for who in BUYERS}
            whole = sum(bought.values())
            out[f"buy.{good}.per_turn"] = whole / len(turns)
            for who, qty in bought.items():
                out[f"buy.{good}.{who}"] = qty / whole if whole else 0.0
        for key in sorted(k for k in s if k.startswith("kind.") and k.endswith(".capacity")):
            kind = key.removeprefix("kind.").removesuffix(".capacity")
            out[f"use.{kind}"] = s[f"kind.{kind}.working"] / s[key] if s[key] else 0.0
        out["output.real.per_person"] = s["output.real"] / people if people else 0.0
        # 飢えで亡くなった人と、ほかの国へ移った人（1年に住民 1000 人あたり。回3）
        for key, name in (("population.starved", "starved"), ("migration.left", "emigrated")):
            out[f"{name}.per_1000_year"] = s[key] / people * YEAR * 1000 if people else 0.0
        return out

    def ships(self, turn: int = 300, span: int = 24) -> dict[str, float]:
        """初めに配った輸送船の寿命（trade.ship_life）をまたいだときの船と運んだ量（Codex E31）。

        turn の前後 span ターンの、世界全体の港の扱った量の平均と、船の数の最小。
        """
        if max(self.totals, default=0) < turn + span:
            return {}

        def handled(a: int, b: int) -> float:
            return statistics.mean(self.totals[t]["port.handled"] for t in range(a, b))

        before = {i: by_turn.get(turn - 1, 0) for i, by_turn in self.fleet.items()}
        after = {
            i: min(by_turn.get(t, 0) for t in range(turn, turn + span))
            for i, by_turn in self.fleet.items()
        }
        return {
            "ships.before": sum(before.values()),
            "ships.after.min": sum(after.values()),
            "ships.islands_without": float(
                sum(1 for i in after if before[i] > 0 and after[i] <= 0)
            ),
            "port.handled.before": handled(turn - span, turn),
            "port.handled.after": handled(turn, turn + span),
        }

    def curve(self, every: int = 12) -> list[dict[str, float]]:
        """世界全体の合計を every ターンごとに（後でグラフにするため）。"""
        keys = (
            "population",
            "labor",
            "employed",
            "subsistence",
            "jobless",
            "jobs.capacity",
            "jobs.wanted",
            "output.real",
            "gdp",
            "demand.construction",
            "volume.machinery",
            "volume.steel",
            "production.machinery",
            "production.steel",
            "ships",
            "port.handled",
            "facilities",
        )
        out = []
        for t in sorted(self.totals):
            if t % every == 0 or t == 1:
                total = self.totals[t]
                out.append({"t": t, **{k: total.get(k, 0) for k in keys}})
        return out


# 島ごとの割合の統計は足し上げない（働き手などの数から割り直す）
RATES = frozenset(
    (
        "unemployment",
        "cpi",
        "inflation",
        "wage.average",
        "wage.real",
        "happiness",
        "food.ratio",
        "politics.stability",
        "education.level",
        "engineers.unemployment",
        "debt_ratio",
        "policy_rate",
        "power.shortage",
    )
)


def result(
    scenario: Scenario,
    seed: int,
    turns: int,
    islands: int,
    recorder: gate.Recorder,
    measure: Measure,
    seconds: float,
    digest: str,
    world: WorldState,
) -> dict[str, Any]:
    """一回の結果のまとめ（JSON に書いて compare で比べる）。

    窓は START から300ターン目までと、最後まで。
    """
    ends = sorted({min(300, turns), turns}) if turns >= gate.START else []
    windows = {}
    for end in ends:
        findings = recorder.findings(gate.START, end)
        counted = [
            line for kind, line in findings if scenario.kinds is None or kind in scenario.kinds
        ]
        windows[str(end)] = {
            "start": gate.START,
            "end": end,
            "summary": recorder.summary(gate.START, end),
            "counts": recorder.counts(gate.START, end),
            "gate": {"kinds": scenario.kinds, "failures": len(counted), "lines": counted},
            "measure": measure.window(gate.START, end),
        }
    return {
        "scenario": scenario.name,
        "title": scenario.title,
        "seed": seed,
        "islands": islands,
        "turns": turns,
        "seconds": seconds,
        "digest": digest,
        "independent": sum(1 for i in world.islands.values() if i.policy.cb_independent),
        "independent_since": sorted(measure.independent_since.values()),
        "early": recorder.early(),
        "first_year": measure.window(1, gate.EARLY_TURN),
        "ships": measure.ships(),
        "windows": windows,
        "curve": measure.curve(),
    }


# --- まとめて回す・比べる ------------------------------------------------------------


def run_name(scenario: str, seed: int, turns: int) -> str:
    return f"{scenario}-s{seed}-t{turns}"


def batch(names: Iterable[tuple[str, int, int]], out_dir: Path, jobs: int, series: bool) -> int:
    """場面・種・ターン数の組を、jobs 本ずつ別の処理で回す。結果は out_dir/<名前>.json。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    waiting = list(names)
    running: list[tuple[str, subprocess.Popen, IO[str]]] = []
    failed = 0
    while waiting or running:
        while waiting and len(running) < jobs:
            scenario, seed, turns = waiting.pop(0)
            name = run_name(scenario, seed, turns)
            command = [
                sys.executable,
                "-m",
                "hakoniwa_core.simulate",
                *("--scenario", scenario, "--seed", str(seed), "--turns", str(turns)),
                *("--gate", "--out", str(out_dir / f"{name}.json")),
            ]
            if series:
                command += ["--series", str(out_dir / f"{name}.jsonl")]
            log = open(out_dir / f"{name}.log", "w")  # 処理が終わったら閉じる
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            running.append((name, process, log))
            print(f"始めた: {name}", flush=True)
        time.sleep(1)
        for item in list(running):
            name, process, log = item
            if process.poll() is None:
                continue
            running.remove(item)
            log.close()
            ok = (out_dir / f"{name}.json").exists()
            failed += 0 if ok else 1
            print(f"{'終わった' if ok else '失敗した'}: {name}", flush=True)
    return failed


def load(path: Path) -> dict[tuple[str, int, int], dict[str, Any]]:
    """結果の JSON（1つのファイルか、ディレクトリの中の全部）を (場面, 種, ターン数) ごとに読む。"""
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    out = {}
    for f in files:
        data = json.loads(f.read_text())
        out[(data["scenario"], data["seed"], data["turns"])] = data
    return out


# compare で並べる値と、良くなる向き（+1 は大きいほど良い、-1 は小さいほど良い、0 は向きなし）
COMPARED = (
    ("gate", -1),
    ("summary.unemployment.mean", -1),
    ("summary.unemployment.inside", 1),
    ("summary.inflation.inside", 1),
    ("summary.price.inside", 1),
    ("summary.banks.failed", -1),
    ("measure.labor.employed", 1),
    ("measure.labor.subsistence", 0),
    ("measure.labor.jobless", -1),
    ("measure.jobs.vacant", 0),
    ("measure.jobless.mismatch", -1),
    ("measure.idle.sales", 0),
    ("measure.idle.inputs", 0),
    ("measure.idle.power", 0),
    ("measure.idle.funds", 0),
    ("measure.idle.workers", 0),
    ("measure.idle.engineers", 0),
    ("measure.households.real_per_person", 1),
    ("measure.output.real.per_person", 1),
    ("measure.starved.per_1000_year", -1),
    ("measure.emigrated.per_1000_year", 0),
)
EARLY = (
    ("early.employment.median", 1),
    ("early.employment.min", 1),
    ("early.output.median", 1),
    ("early.output.min", 1),
)


def _pick(data: Mapping[str, Any], window: Mapping[str, Any] | None, key: str) -> float | None:
    if key.startswith("early."):
        return data.get("early", {}).get(key)
    if window is None:
        return None
    if key == "gate":
        return window["gate"]["failures"]
    part, _, name = key.partition(".")
    return window.get(part, {}).get(name)


def compare(base: Path, new: Path) -> str:
    """2つの結果（ディレクトリかファイル）を、同じ場面・種・ターン数どうしで Markdown の表に並べる。

    悪くなった値に「悪化」と書く（向きのある値だけ）。
    """
    a, b = load(base), load(new)
    lines = []
    for key in sorted(set(a) & set(b)):
        name = run_name(*key)
        lines += [
            "",
            f"### {name}（{b[key]['title']}）",
            "",
            "| 値 | 前 | 後 | 差 |",
            "|---|---|---|---|",
        ]
        rows = [(k, d, None) for k, d in EARLY]
        for end in sorted(set(a[key]["windows"]) & set(b[key]["windows"]), key=int):
            rows += [(k, d, end) for k, d in COMPARED]
            kinds = set(a[key]["windows"][end]["counts"]) | set(b[key]["windows"][end]["counts"])
            rows += [(f"counts.{k}", -1, end) for k in sorted(kinds)]
        for metric, direction, end in rows:
            wa = a[key]["windows"].get(end) if end else None
            wb = b[key]["windows"].get(end) if end else None
            if metric.startswith("counts."):
                x = wa["counts"].get(metric[7:], 0)
                y = wb["counts"].get(metric[7:], 0)
                if x == y == 0:
                    continue
            else:
                x, y = _pick(a[key], wa, metric), _pick(b[key], wb, metric)
            if x is None or y is None:
                continue
            worse = direction and (y - x) * direction < -1e-9
            label = f"{metric}（〜{end}）" if end else metric
            lines.append(
                f"| {label} | {x:.4g} | {y:.4g} | {y - x:+.4g}{' 悪化' if worse else ''} |"
            )
    missing = sorted(set(a) ^ set(b))
    if missing:
        lines += ["", "片方にしかない: " + "、".join(run_name(*k) for k in missing)]
    return "\n".join(lines).strip()


def show(path: Path) -> str:
    """結果の要点を並べる（Markdown）。"""
    lines = []
    for key, data in sorted(load(path).items()):
        lines += ["", f"### {run_name(*key)}（{data['title']}、{data['islands']} 島）", ""]
        early = data.get("early", {})
        if early:
            e = {k.removeprefix("early."): v for k, v in early.items()}
            lines.append(
                "最初の1年（12ターン目 ÷ 1ターン目、島の中央値と最小）: 就業者 "
                f"{e.get('employment.median', 0):.2f}・{e.get('employment.min', 0):.2f}、"
                f"実質の生産 {e.get('output.median', 0):.2f}・{e.get('output.min', 0):.2f}"
            )
        for end, w in sorted(data["windows"].items(), key=lambda kv: int(kv[0])):
            m, s = w["measure"], w["summary"]
            counts = "、".join(f"{k} {v}" for k, v in w["counts"].items() if v) or "なし"
            failures = w["gate"]["failures"]
            lines.append(
                f"- ターン {w['start']}〜{end}: 関門 {failures} 件（{counts}）。"
                f"失業率の平均 {s.get('unemployment.mean', 0):.3f}。働き手のうち仕事 "
                f"{m.get('labor.employed', 0):.1%}・自給 {m.get('labor.subsistence', 0):.1%}・"
                f"失業 {m.get('labor.jobless', 0):.1%}"
                f"（埋まらなかった席 {m.get('jobs.vacant', 0):.1%}、"
                f"そのうち場所のずれ {m.get('jobless.mismatch', 0):.1%}）。全力で動かなかった席: "
                + "・".join(f"{g} {m.get(f'idle.{g}', 0):.1%}" for g in CAUSE_GROUPS)
            )
        if data.get("ships"):
            sh = data["ships"]
            lines.append(
                f"- 300ターン目の船: {sh['ships.before']:.0f} 隻 → "
                f"最少 {sh['ships.after.min']:.0f} 隻"
                f"（船がなくなった島 {sh['ships.islands_without']:.0f}）、港の扱った量 "
                f"{sh['port.handled.before']:.0f} → {sh['port.handled.after']:.0f}"
            )
    return "\n".join(lines).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("batch", help="場面・種・ターン数の組をまとめて回す")
    run.add_argument("--set", choices=sorted(SETS), default="quick")
    run.add_argument("--out-dir", required=True)
    run.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    run.add_argument("--series", action="store_true", help="島×ターンの時系列も書く（大きい）")
    run.add_argument("--only", help="場面の名前をカンマで区切って、その場面だけ回す")
    cmp = sub.add_parser("compare", help="2つの結果を比べる")
    cmp.add_argument("base")
    cmp.add_argument("new")
    see = sub.add_parser("show", help="結果の要点を並べる")
    see.add_argument("path")
    args = parser.parse_args(argv)
    if args.command == "batch":
        names = SETS[args.set]
        if args.only:
            keep = set(args.only.split(","))
            names = [n for n in names if n[0] in keep]
        failed = batch(names, Path(args.out_dir), args.jobs, args.series)
        print(show(Path(args.out_dir)))
        return 1 if failed else 0
    if args.command == "compare":
        print(compare(Path(args.base), Path(args.new)))
        return 0
    print(show(Path(args.path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
