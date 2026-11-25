"""小さな推移グラフ（ランキングとトップで国名の横に出す折れ線）の土台。

時間軸（直近 TREND_TURNS ターン）・物差し（線ごとに、その国の期間内の最大と最小に合わせて
伸縮）・色の並びはここに寄せて、人口・GDP・1人あたり所得の線が同じ形で育つようにする
（掲示板 #5807 の条件: 11 推移グラフと C1 #455 所得のグラフは 1 つのグラフ基盤に寄せる）。
色は報告書の大きなグラフ（static/js/chart/line.js の COLORS）と同じ並び。

値は保存した統計（StatRecord）を読むだけで、計算はしない。お金の線は順位の値と同じく
基準の通貨に今のレートで直す（形を見るための小さな線なので、昔のレートでは直さない）。
1人あたり所得は、報告書の階級ごとの所得のグラフと同じく層の所得と人数から作る
（全階級の所得の合計 ÷ 全階級の人数）。
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q

from server.world import models as m

TREND_TURNS = 24  # 直近2年（報告書の期間の切り替えの最小と同じ）
WIDTH = 84
HEIGHT = 20
PAD = 1.5

# 線の並び: 統計の名前と色。income_per_person は統計に直接はなく、層の所得と人数から作る
SERIES = [
    ("population", "#2f6db5"),
    ("gdp", "#d1495b"),
    ("income_per_person", "#2e8b57"),
]
MONEY = {"gdp", "income_per_person"}


def trend_data(
    world: m.World, factors: dict[int, float | None]
) -> dict[int, dict[str, list[float | None]]]:
    """島ごと・線ごとの、窓のターンにそろえた値の並び（記録のないターンは None）。

    factors は島の通貨を基準の通貨に直す倍率（rankings._factors と同じ物）。直せない島は
    お金の線を出さない（人口の線だけ出す）。窓は世界の今のターンから TREND_TURNS ターン。
    """
    end = world.turn
    start = max(0, end - TREND_TURNS + 1)
    turns = list(range(start, end + 1))
    rows = m.StatRecord.objects.filter(
        Q(island__world=world, turn__gte=start)
        & (Q(name__in=["population", "gdp"]) | Q(name__startswith="layer."))
    ).values_list("island_id", "turn", "name", "value")
    raw: dict[int, dict[str, dict[int, float]]] = {}
    for island_id, turn, name, value in rows:
        raw.setdefault(island_id, {}).setdefault(name, {})[turn] = value
    out: dict[int, dict[str, list[float | None]]] = {}
    for island_id, table in raw.items():
        factor = factors.get(island_id)
        series: dict[str, list[float | None]] = {}
        for name, _color in SERIES:
            values = (
                _income_per_person(table) if name == "income_per_person" else table.get(name, {})
            )
            if name in MONEY:
                if factor is None:
                    continue
                values = {t: v * factor for t, v in values.items()}
            series[name] = [values.get(t) for t in turns]
        out[island_id] = series
    return out


def _income_per_person(table: dict[str, dict[int, float]]) -> dict[int, float]:
    """層ごとの所得と人数の統計から、全階級の1人あたり所得（ターンごと）を作る。"""
    layers = sorted(
        {name[6:-7] for name in table if name.startswith("layer.") and name.endswith(".income")}
    )
    turns = {t for values in table.values() for t in values}
    out = {}
    for turn in turns:
        income = sum(table.get(f"layer.{layer}.income", {}).get(turn, 0.0) for layer in layers)
        persons = sum(table.get(f"layer.{layer}.persons", {}).get(turn, 0.0) for layer in layers)
        if persons > 0:
            out[turn] = income / persons
    return out


def trend_lines(series: dict[str, list[float | None]] | None) -> list[dict[str, Any]]:
    """線の並び（名前・色・SVG のパス）。パスにできない線（点が1つ以下）は入れない。"""
    if not series:
        return []
    out = []
    for name, color in SERIES:
        path = sparkline_path(series.get(name) or [])
        if path:
            out.append({"name": name, "color": color, "path": path})
    return out


def sparkline_path(values: list[float | None]) -> str:
    """値の並びを SVG のパスにする。ない所は線を切り、全部同じ値なら真ん中の横線にする。

    横は並びの位置（窓のターン）で等間隔、縦は並びの中の最大と最小に合わせる。
    """
    points = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(points) < 2:
        return ""
    lo = min(v for _, v in points)
    hi = max(v for _, v in points)
    span = max(1, len(values) - 1)

    def xy(i: int, v: float) -> str:
        x = PAD + (WIDTH - 2 * PAD) * i / span
        y = HEIGHT / 2 if hi == lo else PAD + (HEIGHT - 2 * PAD) * (1 - (v - lo) / (hi - lo))
        return f"{x:.1f},{y:.1f}"

    parts = []
    prev = None
    for i, v in points:
        parts.append(f"{'M' if prev is None or i != prev + 1 else 'L'}{xy(i, v)}")
        prev = i
    return " ".join(parts)
