"""バランス調整の回2（測り方）: 関門の目安の見方の直しと、場面ごとの一括シミュレーションの道具。

- GDP の差の割合は、片方が 0 やマイナスでも暴れない（Codex のレビュー A08）
- 値段の目安は、見る期間のターンの値段だけで判定する（Codex のレビュー B02）
- 最初の1年の縮み、貿易が途絶えた島、目安の種類ごとの数え方
- 測りは状態を変えず、失業の内わけは統計の失業率と同じ数え方で合う
"""

from __future__ import annotations

import json

import pytest

from hakoniwa_core import gate, measure
from hakoniwa_core.economy import books
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.game import load_game
from hakoniwa_core.simulate import main as simulate_main
from hakoniwa_core.simulate import world_digest
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


# --- GDP の差の割合（A08） -------------------------------------------------------------


def test_gdp_gap_is_zero_when_both_sides_are_zero():
    assert books.gdp_gap(0, 0, 1000) == 0.0
    assert books.gdp_gap(0, 0, 0) == 0.0


def test_gdp_gap_uses_the_larger_side_and_a_floor():
    assert books.gdp_gap(1_000_000, 999_000, 1000) == pytest.approx(0.001)
    # 片方が 0 なら差の全部（1 倍）。前は生産の側が 0 なら差を 0 にしていた
    assert books.gdp_gap(0, 500_000, 1000) == pytest.approx(1.0)
    # どちらも小さいときは、1人の1ターンの賃金を下限に割る（小さな島のわずかな差で外れない）
    assert books.gdp_gap(10, 20, 1000) == pytest.approx(0.01)


def test_gdp_gap_with_negative_sides():
    # 符号が違っても、両方がマイナスでも、差の大きさで見る（前は分母が生産の側そのままで、
    # マイナスだと割合もマイナスになり、目安から外れなかった）
    assert books.gdp_gap(100_000, -100_000, 1000) == pytest.approx(2.0)
    assert books.gdp_gap(-50_000, -50_000, 1000) == 0.0
    assert books.gdp_gap(-50_000, -40_000, 1000) == pytest.approx(0.2)


# --- 関門の目安の見方 ---------------------------------------------------------------------


def record(turns: range, **overrides) -> gate.IslandRecord:
    """目安に収まる値で埋めた島の記録（overrides で列を差し替える）。"""
    n = len(turns)
    rec = gate.IslandRecord(regime="mixed", start_population=1000)
    rec.turns = list(turns)
    rec.unemployment = [0.05] * n
    rec.inflation = [0.02] * n
    rec.cpi = [100.0] * n
    rec.population = [1000] * n
    rec.gdp_gap = [0.0] * n
    rec.rates = [0.03] * n
    rec.low_rates = [0.01] * n
    rec.private_firms = [10] * n
    rec.reserves = [0.0] * n
    rec.employed = [500] * n
    rec.output = [1000.0] * n
    rec.trade = [1.0] * n
    for key, value in overrides.items():
        setattr(rec, key, value)
    return rec


def recorder_with(game, records: dict[int, gate.IslandRecord]) -> gate.Recorder:
    world = generate_world(1, 1, game.config, game.defs)
    recorder = gate.Recorder(world, game.defs, game.config)
    recorder.islands = records
    recorder.turns = sorted({t for rec in records.values() for t in rec.turns})
    recorder.goods_list = []
    return recorder


def kinds_of(found: list[tuple[str, str]]) -> list[str]:
    return [kind for kind, _ in found]


def test_price_check_looks_only_at_turns_in_the_window(game):
    # 売り買いのまばらな財: 期間の前（ターン 110）に外れた値段があり、期間の中は収まっている。
    # 前は売り買いのあったターンの末尾の N 個を切り出していたので、期間の外の 110 が混ざった
    rec = record(range(100, 131), price_ratio={"steel": {110: 10.0, 125: 1.0}})
    recorder = recorder_with(game, {1: rec})
    assert "price" not in kinds_of(recorder.findings(120, 130))
    assert "price" in kinds_of(recorder.findings(100, 130))
    assert recorder.summary(120, 130)["price.inside"] == 1.0
    assert recorder.summary(100, 130)["price.inside"] == 0.5
    assert recorder.prices(120, 130) == {"steel": (1.0, 1.0, 1.0)}


def test_window_end_leaves_out_later_turns(game):
    unemployment = [0.05] * 21 + [0.5] * 10  # ターン 121 から外れる
    rec = record(range(100, 131), unemployment=unemployment)
    recorder = recorder_with(game, {1: rec})
    assert "unemployment" not in kinds_of(recorder.findings(100, 120))
    assert "unemployment" in kinds_of(recorder.findings(100, 130))
    assert recorder.counts(100, 130)["unemployment"] == 1
    assert set(recorder.counts(100, 130)) == set(gate.KINDS)


def test_failures_can_be_limited_to_kinds(game):
    rec = record(
        range(100, 131),
        unemployment=[0.5] * 31,
        price_ratio={"steel": {t: 10.0 for t in range(100, 131)}},
    )
    recorder = recorder_with(game, {1: rec})
    everything = recorder.failures(100)
    only_prices = recorder.failures(100, kinds=["price"])
    assert len(everything) == 2
    assert len(only_prices) == 1 and "steel の値段が" in only_prices[0]


def test_trade_that_stops_for_a_year_is_flagged(game):
    trade = [1.0] * 21 + [0.0] * 30  # ターン 101 から途絶える
    stopped = record(range(80, 131), trade=trade)
    never = record(range(80, 131), trade=[0.0] * 51)  # 一度も貿易していない島は数えない
    short = record(range(80, 131), trade=[1.0] * 45 + [0.0] * 6)
    recorder = recorder_with(game, {1: stopped, 2: never, 3: short})
    lines = [line for kind, line in recorder.findings(120, 130) if kind == "trade_stopped"]
    assert lines == ["島 1（mixed）: 貿易が 30 ターン続けて途絶えた（ターン 101〜130）"]


def test_early_year_compares_turn_12_with_turn_1(game):
    shrinking = record(range(1, 13), employed=[1000] + [900] * 10 + [700])
    steady = record(range(1, 13), output=[2000.0] * 11 + [1800.0])
    recorder = recorder_with(game, {1: shrinking, 2: steady})
    early = recorder.early()
    assert early["early.employment.min"] == pytest.approx(0.7)
    assert early["early.employment.median"] == pytest.approx(0.85)
    assert early["early.output.min"] == pytest.approx(0.9)
    # 12 ターン目を記録していなければ出さない
    assert recorder_with(game, {1: record(range(1, 10))}).early() == {}


# --- 場面と測り ---------------------------------------------------------------------------


def run(game, world, scenario, turns, meter=None):
    for _ in range(turns):
        measure.queue_scenario_orders(world, scenario, game.defs, game.config)
        game.engine.run_turn(world)
        if meter is not None:
            meter.record(world)


def test_measuring_does_not_change_the_world(game):
    scenario = measure.SCENARIOS["plan"]
    plain = generate_world(7, 2, game.config, game.defs)
    measured = generate_world(7, 2, game.config, game.defs)
    run(game, plain, scenario, 8)
    meter = measure.Measure(measured, game.defs, game.config, zones_every=4)
    run(game, measured, scenario, 8, meter)
    assert world_digest(plain) == world_digest(measured)


def test_unemployment_breakdown_matches_the_statistics(game):
    world = generate_world(3, 2, game.config, game.defs)
    scenario = measure.SCENARIOS["plan"]
    for _ in range(6):
        run(game, world, scenario, 1)
        for island in world.islands.values():
            s = island.stats
            row = measure.island_row(island, game.defs, game.config, {})
            assert row["jobless"] == s["labor"] - s["employed"] - s["subsistence"]
            assert 0 <= row["mismatch"] <= min(row["unreached.unemployed"], row["jobless"])
            assert row["jobs.vacant"] >= 0
            # 家計の消費のうち財だけの分は、サービスを含む分を超えない（回4）
            goods = row.get("demand.households.goods.real", 0)
            assert 0 <= goods <= row.get("demand.households.real", 0) + 1e-6
            assert row["politics.demo"] >= row["politics.riot"] >= 0
            idle = sum(v for k, v in row.items() if k.startswith("idle."))
            assert idle <= row["jobs.capacity"] + 1e-6
            zones = measure.zones(island, game.defs, game.config)
            assert sum(z["unemployed"] for z in zones) == row["jobless"]
            assert sum(z["employed"] for z in zones) == s["employed"]


def test_independent_scenario_retries_until_parliament_agrees(game):
    world = generate_world(1, 4, game.config, game.defs)
    run(game, world, measure.SCENARIOS["independent"], 10)
    # 計画経済の国は独立させられないので除く（回5 のカード「両方」）
    for island in world.islands.values():
        assert island.policy.cb_independent == (not planning.is_planned(island, game.defs))
    assert any(i.policy.cb_independent for i in world.islands.values())


def test_simulate_writes_a_result_and_a_series(game, tmp_path):
    out, series = tmp_path / "small.json", tmp_path / "small.jsonl"
    code = simulate_main(
        [
            *("--scenario", "small", "--islands", "2", "--turns", "13", "--seed", "1"),
            *("--out", str(out), "--series", str(series), "--zones-every", "6"),
        ]
    )
    assert code == 0  # 目安を見始めるターンより前なので、外れたところはない
    data = json.loads(out.read_text())
    assert (data["scenario"], data["seed"], data["turns"], data["islands"]) == ("small", 1, 13, 2)
    assert data["windows"] == {}
    assert "early.employment.median" in data["early"]
    assert data["first_year"]["labor.employed"] > 0
    lines = [json.loads(line) for line in series.read_text().splitlines()]
    assert len(lines) == 2 * 13
    assert {line["t"] for line in lines} == set(range(1, 14))
    assert all("zones" in line for line in lines if line["t"] % 6 == 0)
    # 比べと要点（同じ結果どうしなら悪化はない）
    table = measure.compare(out, out)
    assert "small-s1-t13" in table and "悪化" not in table
    assert "最初の1年" in measure.show(out)


def test_result_has_windows_after_the_start_turn(game):
    world = generate_world(2, 2, game.config, game.defs)
    scenario = measure.SCENARIOS["small"]
    recorder = gate.Recorder(world, game.defs, game.config)
    meter = measure.Measure(world, game.defs, game.config)
    for _ in range(gate.START + 2):
        run(game, world, scenario, 1)
        recorder.record(world)
        meter.record(world)
    data = measure.result(scenario, 2, gate.START + 2, 2, recorder, meter, 0.0, "x", world)
    window = data["windows"][str(gate.START + 2)]
    assert window["start"] == gate.START
    assert window["gate"]["failures"] == len(window["gate"]["lines"])
    labor = window["measure"]
    shares = labor["labor.employed"] + labor["labor.subsistence"] + labor["labor.jobless"]
    assert shares == pytest.approx(1.0)


def test_compare_reads_goods_only_values_from_rounds_without_services(tmp_path):
    """回3 までの結果（サービスがない）は、家計の消費と生産の全部を財として比べる。前の回にない値は
    「—」で並べる。"""

    def write(name, measured):
        data = {
            "scenario": "plan",
            "seed": 1,
            "turns": 300,
            "title": "標準の計画",
            "windows": {"300": {"gate": {"failures": 0}, "counts": {}, "measure": measured}},
        }
        path = tmp_path / name
        path.mkdir()
        (path / "plan-s1-t300.json").write_text(json.dumps(data))
        return path

    old = write("old", {"households.real_per_person": 400.0, "output.real.per_person": 450.0})
    new = write(
        "new",
        {
            "households.real_per_person": 560.0,
            "households.real_goods_per_person": 420.0,
            "output.real.per_person": 620.0,
            "output.real.goods_per_person": 470.0,
            "labor.services": 0.3,
            "politics.demo": 0.1,
        },
    )
    table = measure.compare(old, new)
    assert "| measure.households.real_goods_per_person（〜300） | 400 | 420 | +20 |" in table
    assert "| measure.output.real.goods_per_person（〜300） | 450 | 470 | +20 |" in table
    assert "| measure.politics.demo（〜300） | — | 0.1 | |" in table
    # サービスのある回どうしでは、財だけの値がない結果を全部の値で読まない
    newer = write("newer", {"households.real_per_person": 560.0, "labor.services": 0.3})
    assert "real_goods_per_person" not in measure.compare(newer, newer)
