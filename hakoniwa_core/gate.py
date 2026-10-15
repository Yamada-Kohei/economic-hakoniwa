"""フェーズ2・3の関門の目安を、一括シミュレーションの記録で確かめる。

毎ターンの終わりに Recorder.record で島ごとの統計を記録し、最後に Recorder.failures で目安から
外れたところを並べます（設計書「関門の確かめ方」の表）。帳簿の確かめは記録の段が毎ターン行い、
島をまたぐお金と運ぶ途中の荷の突き合わせは trade_check の段が毎ターン行い、合わなければその場で
止まります。

フェーズ3-1 から、為替の年の動き（FX_YEAR ターン前のレートからの変わり方の絶対値の平均）と、
石炭の出ない島の失業率の平均（貿易で機械を買えるようになって下がるかを 2-3 と比べる）も見ます。

フェーズ3-2 から、世界全体で、どの財も目安を見始めるターンからどこかの島で作られ、売り買いされて
いるかを見ます（設計書の 3-2 の関門「新しい品目がどれもどこかで作られ売買される」）。財ごとの、
作った島と売り買いのあった島の数の平均は Recorder.goods で出します。

フェーズ3-3 から、得意産業が島ごとに分かれるか（最後のターンの称号＝各産業の経験の累積の1位を、1つの
島が TITLE_SHARE より多く持たない）と、外国の資産（外貨準備と外国の国債。政府・中央銀行・市中銀行）
が限りなく増え続けないか（年の GDP に対する割合が RESERVE_CAP を超えない。最後の RESERVE_WINDOW
ターンで、割合が RESERVE_RISE より多く増えない）を見ます。

目安はゲームのルールではなく確かめの基準なので、game.yaml ではなくこのファイルに置いています。
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import debt, experience
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.state import OWNER_PRIVATE, WorldState
from hakoniwa_core.units import BASIS

START = 120  # 目安を見始めるターン
PRICE_RANGE = (0.25, 4.0)  # 売り買いのあった財の値段 ÷ 初めの値段
INFLATION_RANGE = (-0.05, 0.15)  # 物価上昇率（年率）
CPI_WINDOW = 100  # 物価指数の振れ幅を見るターン数（最後から）
CPI_SPREAD = 0.15  # 物価指数の標準偏差 ÷ 平均の上限
UNEMPLOYMENT_RANGE = (0.02, 0.25)
RATE_RANGE = (0.0, 0.20)  # 政策金利と国債の利回り
POPULATION_FLOOR = 0.5  # 初めの人口に対する下限（フェーズ1と同じ）
POPULATION_CAP = 15  # 初めの人口に対する上限
POPULATION_JUMP = 0.05  # 1ターンの人口の変化の上限
GDP_GAP = 0.001  # 生産の側と支出の側の差
FIRMS_WINDOW = 100  # 民営工場の数を見るターン数（最後から。市場経済の島）
FIRMS_SPREAD = 0.5  # 民営工場の数の、その間の平均からの振れ幅の上限
CASCADE_TURNS = 24  # 同じ島で銀行の破綻がこのターン数のうちに続いたら連鎖と見る
FX_YEAR = 12  # 為替の年の動きを見るターン数
FX_MOVE = 0.30  # 為替の年の動きの平均の上限（島ごと、相手の通貨ごと）
NO_COAL = "coal"  # この資源の出ない島の失業率を別に出す
TITLE_SHARE = 1 / 3  # 1つの島が持つ称号（産業ごとの経験の1位）の、称号のある産業に対する割合の上限
RESERVE_CAP = 2.0  # 外国の資産 ÷ 年の GDP の上限
RESERVE_WINDOW = 120  # 外国の資産の増え方を見るターン数（最後から）
RESERVE_RISE = 0.5  # その間に外国の資産 ÷ 年の GDP が増えてよい幅


@dataclass
class IslandRecord:
    regime: str
    start_population: int
    turns: list[int] = field(default_factory=list)
    unemployment: list[float] = field(default_factory=list)
    inflation: list[float] = field(default_factory=list)
    cpi: list[float] = field(default_factory=list)
    population: list[int] = field(default_factory=list)
    gdp_gap: list[float] = field(default_factory=list)
    rates: list[float] = field(default_factory=list)  # 政策金利と国債の利回りの高い方
    low_rates: list[float] = field(default_factory=list)  # 政策金利と国債の利回りの低い方
    price_ratio: dict[str, list[float]] = field(
        default_factory=dict
    )  # 財 → 売り買いのあったターンの比
    private_firms: list[int] = field(default_factory=list)  # 動いている民営工場の数
    bank_failures: list[int] = field(default_factory=list)  # 銀行が破綻したターン（1行1件）
    resources: list[str] = field(default_factory=list)  # 島の鉱業資源
    fx: dict[str, dict[int, float]] = field(default_factory=dict)  # 相手の通貨 → ターン → レート
    reserves: list[float] = field(default_factory=list)  # 外国の資産 ÷ 年の GDP（フェーズ3-3）


class Recorder:
    """島ごとの統計をターンごとに記録する。"""

    def __init__(self, world: WorldState, defs: Defs, config: GameConfig) -> None:
        self.defs = defs
        self.initial = {  # 島ごとの初めの値段（島で使える作り方で解いた値段）
            i.id: initial_prices(defs, config, i.resources) for i in world.islands.values()
        }
        self.islands = {
            i.id: IslandRecord(
                regime=i.regime.get("economy", ""),
                start_population=sum(t.population for t in i.tiles),
                resources=list(i.resources),
            )
            for i in world.islands.values()
        }
        self.goods_list = list(defs.goods)
        self.turns: list[int] = []  # 記録したターン（世界全体）
        self.made: dict[str, dict[int, int]] = {}  # 財 → ターン → 作った島の数
        self.traded: dict[str, dict[int, int]] = {}  # 財 → ターン → 売り買いのあった島の数
        self.titles: dict[str, int] = {}  # 最後に記録したターンの称号（産業 → 島。フェーズ3-3）

    def record(self, world: WorldState) -> None:
        self.turns.append(world.turn)
        self.titles = dict(world.ext.get(experience.RANKINGS, {}).get("titles", {}))
        for island in world.islands.values():
            rec = self.islands.get(island.id)
            if rec is None or not island.has_economy:
                continue
            s = island.stats
            rec.turns.append(world.turn)
            rec.unemployment.append(s.get("unemployment", 0.0))
            rec.inflation.append(s.get("inflation", 0.0))
            rec.cpi.append(s.get("cpi", 0.0))
            rec.population.append(s.get("population", 0))
            rec.gdp_gap.append(s.get("gdp.gap", 0.0))
            rates = [island.policy.policy_rate] + [b.rate for b in island.bonds]
            rec.rates.append(max(rates) / BASIS)
            rec.low_rates.append(min(rates) / BASIS)
            rec.private_firms.append(
                sum(
                    1
                    for f in island.facilities
                    if f.built
                    and f.owner == OWNER_PRIVATE
                    and self.defs.facilities[f.kind].get("category") not in ("finance", "trade")
                )
            )
            rec.bank_failures += [world.turn] * int(s.get("banks.failed", 0))
            gdp = debt.annual_gdp(island.economy)
            foreign = s.get("fx.reserves", 0) + s.get("foreign_bonds.banks", 0)
            rec.reserves.append(foreign / gdp if gdp > 0 else 0.0)
            for key, value in s.items():
                currency = key.removeprefix("fx.")
                if currency != key and currency.isdigit() and value > 0:
                    rec.fx.setdefault(currency, {})[world.turn] = value
            for key, value in s.items():
                good = key.removeprefix("production.")
                if good != key and value > 0:
                    made = self.made.setdefault(good, {})
                    made[world.turn] = made.get(world.turn, 0) + 1
            initial = self.initial.get(island.id, {})
            for m in island.markets:
                if m.volume > 0:
                    traded = self.traded.setdefault(m.good, {})
                    traded[world.turn] = traded.get(world.turn, 0) + 1
                if m.volume > 0 and initial.get(m.good):
                    ratio = m.price / initial[m.good]
                    rec.price_ratio.setdefault(m.good, []).append(ratio)

    def failures(self, start: int = START) -> list[str]:
        """目安から外れたところ（島ごと・目安ごとに1行）。"""
        out: list[str] = []
        for good, (made, traded) in self.goods(start).items():
            if made <= 0:
                out.append(f"世界: {good} がどの島でも作られていない")
            if traded <= 0:
                out.append(f"世界: {good} がどの島でも売り買いされていない")
        if self.titles:
            held = Counter(self.titles.values())
            island_id, count = min(held.items(), key=lambda kv: (-kv[1], kv[0]))
            if count > len(self.titles) * TITLE_SHARE:
                out.append(
                    f"世界: 島 {island_id} が称号を {count} 個持っている"
                    f"（称号のある産業 {len(self.titles)}）"
                )
        for iid, rec in sorted(self.islands.items()):
            out.extend(f"島 {iid}（{rec.regime}）: {m}" for m in _island_failures(rec, start))
        return out

    def goods(self, start: int = START) -> dict[str, tuple[float, float]]:
        """財ごとの、作った島の数と売り買いのあった島の数の、start からの1ターンあたりの平均。

        start から後のターンを記録していなければ空。
        """
        window = [t for t in self.turns if t >= start]
        if not window:
            return {}

        def mean(by_turn: dict[int, int]) -> float:
            return sum(n for t, n in by_turn.items() if t >= start) / len(window)

        return {
            good: (mean(self.made.get(good, {})), mean(self.traded.get(good, {})))
            for good in self.goods_list
        }

    def summary(self, start: int = START) -> dict[str, float]:
        """全部の島をまとめた値（目安と見比べるため）。"""
        unemployment, inflation, spreads, ratios = [], [], [], []
        for rec in self.islands.values():
            window = _window(rec, start)
            unemployment += [rec.unemployment[i] for i in window]
            inflation += [rec.inflation[i] for i in window]
            spreads.append(_cpi_spread(rec))
            for values in rec.price_ratio.values():
                ratios += values[-len(window) :]
        if not unemployment:
            return {}
        no_coal = [
            rec.unemployment[i]
            for rec in self.islands.values()
            if NO_COAL not in rec.resources
            for i in _window(rec, start)
        ]
        moves = [m for rec in self.islands.values() for m in _fx_moves(rec, start).values()]
        firm_spreads = [
            max(abs(n - statistics.mean(tail)) for n in tail) / statistics.mean(tail)
            for rec in self.islands.values()
            if rec.regime == "market"
            and (tail := rec.private_firms[-FIRMS_WINDOW:])
            and statistics.mean(tail) > 0
        ]
        return {
            "unemployment.min": min(unemployment),
            "unemployment.max": max(unemployment),
            "unemployment.mean": statistics.mean(unemployment),
            "unemployment.inside": _inside_share(unemployment, *UNEMPLOYMENT_RANGE),
            "inflation.min": min(inflation),
            "inflation.max": max(inflation),
            "inflation.mean": statistics.mean(inflation),
            "inflation.inside": _inside_share(inflation, *INFLATION_RANGE),
            "cpi.spread.max": max(spreads),
            "price.ratio.min": min(ratios) if ratios else 1.0,
            "price.ratio.max": max(ratios) if ratios else 1.0,
            "firms.spread.max": max(firm_spreads, default=0.0),
            "banks.failed": float(sum(len(rec.bank_failures) for rec in self.islands.values())),
            "unemployment.no_coal.mean": statistics.mean(no_coal) if no_coal else 0.0,
            "fx.move.mean": statistics.mean(moves) if moves else 0.0,
            "fx.move.max": max(moves, default=0.0),
            "titles.holders": float(len(set(self.titles.values()))),
            "titles.max": float(max(Counter(self.titles.values()).values(), default=0)),
            "reserves.max": max(
                (
                    max(rec.reserves[i] for i in _window(rec, start))
                    for rec in self.islands.values()
                    if _window(rec, start)
                ),
                default=0.0,
            ),
        }

    def prices(self, start: int = START) -> dict[str, tuple[float, float, float]]:
        """財ごとの「値段 ÷ 初めの値段」の最小・中央・最大。

        全部の島の、売り買いのあったターンで見る。
        """
        ratios: dict[str, list[float]] = {}
        for rec in self.islands.values():
            count = len(_window(rec, start))
            for good, values in rec.price_ratio.items():
                ratios.setdefault(good, []).extend(values[-count:] if count else [])
        return {
            good: (min(values), statistics.median(values), max(values))
            for good, values in sorted(ratios.items())
            if values
        }


def _inside_share(values: list[float], low: float, high: float) -> float:
    return sum(1 for v in values if low <= v <= high) / len(values) if values else 1.0


def _window(rec: IslandRecord, start: int) -> list[int]:
    return [i for i, t in enumerate(rec.turns) if t >= start]


def _cpi_spread(rec: IslandRecord) -> float:
    tail = rec.cpi[-CPI_WINDOW:]
    if len(tail) < 2 or statistics.mean(tail) <= 0:
        return 0.0
    return statistics.pstdev(tail) / statistics.mean(tail)


def _fx_moves(rec: IslandRecord, start: int) -> dict[str, float]:
    """相手の通貨ごとの、為替の年の動き（FX_YEAR ターン前のレートからの変わり方の絶対値）の平均。"""
    out = {}
    for currency, by_turn in rec.fx.items():
        moves = [
            abs(rate / by_turn[turn - FX_YEAR] - 1)
            for turn, rate in by_turn.items()
            if turn >= start and by_turn.get(turn - FX_YEAR, 0) > 0
        ]
        if moves:
            out[currency] = statistics.mean(moves)
    return out


def _outside(values: list[float], low: float, high: float) -> tuple[int, float, float]:
    bad = [v for v in values if v < low or v > high]
    return len(bad), min(values, default=0.0), max(values, default=0.0)


def _island_failures(rec: IslandRecord, start: int) -> list[str]:
    out = []
    window = _window(rec, start)
    if not window:
        return out
    checks = [
        ("失業率", [rec.unemployment[i] for i in window], UNEMPLOYMENT_RANGE),
        ("物価上昇率", [rec.inflation[i] for i in window], INFLATION_RANGE),
        ("金利の上限", [rec.rates[i] for i in window], RATE_RANGE),
        ("金利の下限", [rec.low_rates[i] for i in window], RATE_RANGE),
    ]
    for name, values, (low, high) in checks:
        count, lo, hi = _outside(values, low, high)
        if count:
            out.append(f"{name}が {count} ターン外れた（{lo:.3f}〜{hi:.3f}）")
    for good, values in sorted(rec.price_ratio.items()):
        count, lo, hi = _outside(values[-len(window) :], *PRICE_RANGE)
        if count:
            out.append(f"{good} の値段が {count} ターン外れた（初めの {lo:.2f}〜{hi:.2f} 倍）")
    spread = _cpi_spread(rec)
    if spread > CPI_SPREAD:
        out.append(f"物価指数の振れ幅 {spread:.3f}")
    gap = max(rec.gdp_gap[i] for i in window)
    if gap > GDP_GAP:
        out.append(f"GDP の差 {gap:.4f}")
    people = rec.population
    if min(people) < rec.start_population * POPULATION_FLOOR:
        out.append(f"人口が {min(people)} まで減った（初め {rec.start_population}）")
    if people[-1] > rec.start_population * POPULATION_CAP:
        out.append(f"人口が {people[-1]} まで増えた（初め {rec.start_population}）")
    jumps = [abs(b - a) / a for a, b in zip(people, people[1:], strict=False) if a]
    if jumps and max(jumps) >= POPULATION_JUMP:
        out.append(f"人口が1ターンで {max(jumps):.1%} 変わった")
    firms = rec.private_firms[-FIRMS_WINDOW:]
    if rec.regime == "market" and firms:
        mean = statistics.mean(firms)
        low, high = (1 - FIRMS_SPREAD) * mean, (1 + FIRMS_SPREAD) * mean
        if min(firms) < low or max(firms) > high:
            out.append(f"民営工場の数が {min(firms)}〜{max(firms)}（平均 {mean:.1f}）")
    failures = rec.bank_failures
    chained = sum(1 for a, b in zip(failures, failures[1:], strict=False) if b - a <= CASCADE_TURNS)
    if chained:
        out.append(f"銀行の破綻が {CASCADE_TURNS} ターンのうちに続いた（{chained} 回）")
    for currency, move in sorted(_fx_moves(rec, start).items(), key=lambda kv: int(kv[0])):
        if move > FX_MOVE:
            out.append(f"島 {currency} の通貨との為替の年の動きが平均 {move:.1%}")
    reserves = [rec.reserves[i] for i in window]
    if max(reserves) > RESERVE_CAP:
        out.append(f"外国の資産が年の GDP の {max(reserves):.2f} 倍まで増えた")
    tail = rec.reserves[-RESERVE_WINDOW:]
    if len(tail) == RESERVE_WINDOW and tail[-1] - tail[0] > RESERVE_RISE:
        out.append(
            f"外国の資産が増え続けている（年の GDP の {tail[0]:.2f} 倍 → {tail[-1]:.2f} 倍）"
        )
    return out
