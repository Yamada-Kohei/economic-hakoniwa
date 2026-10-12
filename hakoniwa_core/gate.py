"""フェーズ2の関門の目安を、一括シミュレーションの記録で確かめる。

毎ターンの終わりに Recorder.record で島ごとの統計を記録し、最後に Recorder.failures で目安から
外れたところを並べます（設計書「関門の確かめ方」の表）。帳簿の確かめは記録の段が毎ターン行い、
合わなければその場で止まります。

目安はゲームのルールではなく確かめの基準なので、game.yaml ではなくこのファイルに置いています。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.state import WorldState
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


class Recorder:
    """島ごとの統計をターンごとに記録する。"""

    def __init__(self, world: WorldState, defs: Defs, config: GameConfig) -> None:
        self.initial = initial_prices(defs, config)
        self.islands = {
            i.id: IslandRecord(
                regime=i.regime.get("economy", ""),
                start_population=sum(t.population for t in i.tiles),
            )
            for i in world.islands.values()
        }

    def record(self, world: WorldState) -> None:
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
            for m in island.markets:
                if m.volume > 0 and self.initial.get(m.good):
                    ratio = m.price / self.initial[m.good]
                    rec.price_ratio.setdefault(m.good, []).append(ratio)

    def failures(self, start: int = START) -> list[str]:
        """目安から外れたところ（島ごと・目安ごとに1行）。"""
        out: list[str] = []
        for iid, rec in sorted(self.islands.items()):
            out.extend(f"島 {iid}（{rec.regime}）: {m}" for m in _island_failures(rec, start))
        return out

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
    return out
