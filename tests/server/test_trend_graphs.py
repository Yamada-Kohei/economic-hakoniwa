"""小さな推移グラフ（ランキングとトップで国名の横に出す折れ線）: 線の引き方、統計からの
組み立て（1人あたり所得は層の所得と人数から）、2つの画面に出ること。"""

from __future__ import annotations

import pytest

from server.turns.services import run_turn
from server.web import trends
from server.world import models as m

pytestmark = pytest.mark.django_db


# --- 線の引き方 ---------------------------------------------------------------------------


def test_path_scales_between_min_and_max():
    parts = trends.sparkline_path([0.0, 5.0, 10.0]).split()
    assert parts[0].startswith("M") and parts[1].startswith("L")
    ys = [float(p[1:].split(",")[1]) for p in parts]
    assert ys[0] > ys[1] > ys[2]  # 小さい値ほど下、大きい値ほど上


def test_path_flat_series_is_middle_line():
    path = trends.sparkline_path([7.0, 7.0, 7.0])
    ys = {float(p[1:].split(",")[1]) for p in path.split()}
    assert ys == {trends.HEIGHT / 2}


def test_path_breaks_at_missing_turns():
    path = trends.sparkline_path([1.0, None, 2.0])
    assert path.count("M") == 2 and "L" not in path


def test_path_needs_two_points():
    assert trends.sparkline_path([]) == ""
    assert trends.sparkline_path([None, None]) == ""
    assert trends.sparkline_path([3.0]) == ""


# --- 統計からの組み立て ---------------------------------------------------------------------


def _stats(island: m.Island, turn: int, **values: float) -> None:
    m.StatRecord.objects.bulk_create(
        [
            m.StatRecord(island=island, turn=turn, name=name, value=value)
            for name, value in values.items()
        ]
    )


def _fill(island: m.Island) -> None:
    """ターン 0〜2 の人口・GDP・層の所得と人数（ターンごとに増える）。"""
    for turn in range(3):
        _stats(
            island,
            turn,
            population=100 * (turn + 1),
            gdp=1000 * (turn + 1),
            **{
                "layer.general.income": 500 * (turn + 1),
                "layer.general.persons": 100,
                "layer.owners.income": 300 * (turn + 1),
                "layer.owners.persons": 10,
            },
        )


def test_trend_data_builds_series_and_income(world):
    island = world.islands.order_by("id").first()
    _fill(island)
    world.turn = 2
    series = trends.trend_data(world, {island.id: 2.0})[island.id]
    assert series["population"] == [100.0, 200.0, 300.0]
    assert series["gdp"] == [2000.0, 4000.0, 6000.0]  # 基準の通貨に倍率 2 で直す
    # 1人あたり所得 = 全階級の所得の合計 ÷ 全階級の人数
    assert series["income_per_person"][0] == pytest.approx(800 / 110 * 2)
    assert series["income_per_person"][2] == pytest.approx(2400 / 110 * 2)
    lines = trends.trend_lines(series)
    assert [line["name"] for line in lines] == ["population", "gdp", "income_per_person"]


def test_trend_data_omits_money_without_factor(world):
    island = world.islands.order_by("id").first()
    _fill(island)
    world.turn = 2
    series = trends.trend_data(world, {})[island.id]
    assert "gdp" not in series and "income_per_person" not in series
    assert series["population"] == [100.0, 200.0, 300.0]
    assert [line["name"] for line in trends.trend_lines(series)] == ["population"]


# --- 画面 -----------------------------------------------------------------------------------


def test_rankings_and_top_show_trend_lines(client, world):
    run_turn(force=True)  # 統計が1ターン分できる
    world.refresh_from_db()
    # 線が引けるよう、もう1ターン分（今のターンの1つ前）の統計を足す
    for island in world.islands.all():
        _stats(
            island,
            world.turn - 1,
            population=1000,
            gdp=1000,
            **{"layer.general.income": 500, "layer.general.persons": 100},
        )
    for url in ("/rankings/", "/"):
        html = client.get(url).content.decode()
        assert 'class="trend-lines"' in html, url
        assert "直近2年の人口（青）・GDP（赤）・1人あたり所得（緑）の推移" in html, url
