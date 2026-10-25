"""崩れの見張り（やり直しの報告の I1・I2・I5）: 1ターン目から崩れを数え、目安とは別に並べる。

- 飢饉・人口の大きな減り・品切れ・取引が成り立たない・作り方の群れ・国庫の手元が 0・理由の出ない
  停止を、作った場面で数える（世界は回さず、記録だけを書き換えて Recorder に読ませる）
- 一度も売りに出ていない財（作る島がない財）は品切れに数えない
- measure compare は崩れが増えたら「増えた」と書き、新しく崩れた島に写しの命令を付ける。目安は種の
  平均で並べる
- 写しを書いて読み直して続けても、続けて回したのと同じ世界になる
- 夜の点検のまとめに崩れが「崩れ」として出る
"""

from __future__ import annotations

import copy
import dataclasses
import json

import pytest

from hakoniwa_core import gate, ledger, measure, nightly
from hakoniwa_core.economy import arrears
from hakoniwa_core.game import load_game
from hakoniwa_core.simulate import load_snapshot, save_snapshot, world_digest
from hakoniwa_core.state import MarketRecord
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def scene(game):
    """1ターン回した 1 島の世界と、その記録。step で統計と市場を差し替えて1ターン分を記録する。"""
    world = generate_world(3, 1, game.config, game.defs)
    game.engine.run_turn(world)
    island = next(iter(world.islands.values()))
    recorder = gate.Recorder(world, game.defs, game.config)

    def step(markets=(), **stats):
        world.turn += 1
        island.stats = {"population": 1000, "treasury": 10_000} | stats
        island.markets = list(markets)
        recorder.record(world)

    return world, island, recorder, step


def kinds(recorder) -> list[str]:
    return [c["kind"] for c in recorder.collapses()]


def test_famine_and_population_drop_need_a_year(scene):
    _, _, recorder, step = scene
    for n in range(gate.COLLAPSE_SPAN):
        step(population=1000 - 15 * n, **{"population.starved": 2})
    assert kinds(recorder) == []  # 1年たつまでは見ない
    step(population=850, **{"population.starved": 2})
    # 1年で 26 人が飢えで亡くなり（初めの 1000 人の 1% 以上）、人口が 15% 減った
    assert kinds(recorder) == ["famine", "population_drop"]
    first = recorder.collapses()[0]
    assert first["turns"] == 1 and "飢饉" in first["line"]
    assert first["first"] == 2  # 当たった1年の初めのターン（1ターン目は scene が回した）


def test_people_who_moved_abroad_are_not_a_population_drop(scene):
    _, _, recorder, step = scene
    for n in range(gate.COLLAPSE_SPAN + 1):
        step(population=1000 - 15 * n, **{"migration.left": 15})
    assert kinds(recorder) == []


def test_stockout_counts_only_goods_the_island_made(scene):
    _, _, recorder, step = scene
    # 食料は島で作って売っていた。自動車は初めの在庫を一度売っただけで、島では作れない
    step(
        [MarketRecord("food", 100, 50, 60, 70), MarketRecord("automobile", 100, 1, 5, 1)],
        **{"production.food": 70},
    )
    gone = [MarketRecord("food", 100, 0, 60, 0), MarketRecord("automobile", 100, 0, 5, 0)]
    for _ in range(gate.STOCKOUT_MISSING - 1):
        step(gone)
    step([MarketRecord("food", 100, 2, 60, 2)])  # 1ターンだけ少し売りに出ても続いていると見る
    assert kinds(recorder) == []
    step(gone)
    found = recorder.collapses()
    assert [(c["kind"], c["what"], c["first"]) for c in found] == [("stockout", ["food"], 3)]


def test_no_trade_needs_a_year_in_a_row_on_a_good_that_traded_before(scene):
    _, _, recorder, step = scene
    for _ in range(gate.NO_TRADE_TURNS):
        step([MarketRecord("coal", 100, 0, 10, 10)])  # 一度も約定していない財は数えない
    step([MarketRecord("fuel", 100, 5, 10, 10)])
    for _ in range(gate.NO_TRADE_TURNS - 1):
        step([MarketRecord("fuel", 100, 0, 10, 10)])
    step([MarketRecord("fuel", 100, 5, 10, 10)])  # 1ターン約定したら数え直す
    for _ in range(gate.NO_TRADE_TURNS - 1):
        step([MarketRecord("fuel", 100, 0, 10, 10)])
    assert kinds(recorder) == []
    step([MarketRecord("fuel", 100, 0, 10, 10)])
    assert [(c["kind"], c["what"]) for c in recorder.collapses()] == [("no_trade", ["fuel"])]


def test_many_facilities_switching_at_once_is_herding(scene, game):
    _, island, recorder, step = scene
    step()
    farms = [f for f in island.facilities if f.kind == "farm" and f.built]
    # 建国の農場の数は食べる量に合わせる（W-186 回1）ので、群れになる数まで同じ農場を足す
    next_id = max(f.id for f in island.facilities) + 1
    while len(farms) < gate.HERD_MIN:
        farms.append(dataclasses.replace(copy.deepcopy(farms[0]), id=next_id))
        island.facilities.append(farms[-1])
        next_id += 1
    used = {f.recipe for f in farms}
    first, then = [r for r in game.defs.facilities["farm"]["recipes"] if r not in used][:2]
    farms[0].recipe = first
    step()
    assert kinds(recorder) == []  # 1つだけ替えたのは群れではない
    for f in farms:
        f.recipe = then
    step()
    assert kinds(recorder) == ["herding"]


def test_unpaid_wages_for_half_a_year_and_a_silent_stop(scene):
    _, island, recorder, step = scene
    owed = ledger.account("households:workers", arrears.WAGE_ARREARS, ledger.GOV)
    island.accounts[owed] = 100
    for _ in range(gate.LONG_TURNS - 1):
        step()
    assert kinds(recorder) == []
    farm = next(f for f in island.facilities if f.kind == "farm" and f.built)
    farm.operating_rate, farm.status = 0, ""
    step()
    assert kinds(recorder) == ["unpaid_wages"]  # 理由の出ない停止は2ターン続いてから
    step()
    assert kinds(recorder) == ["unpaid_wages", "silent_stop"]
    farm.status = "workers"  # 理由があれば数えない
    del island.accounts[owed]
    step()
    found = [(c["first"], c["turns"], c["what"]) for c in recorder.collapses()]
    assert found == [(2, 2, []), (7, 1, ["farm"])]


def test_compare_marks_more_collapses_and_gives_the_snapshot_command(tmp_path):
    def write(name, found):
        counts = {k: sum(1 for c in found if c["kind"] == k) for k in gate.COLLAPSE_KINDS}
        for seed in (1, 2):
            data = {
                "scenario": "plan",
                "seed": seed,
                "turns": 300,
                "title": "標準の計画",
                "collapse": {"start": 1, "counts": counts, "found": found},
                "windows": {"300": {"gate": {"failures": seed * len(found)}, "counts": {}}},
            }
            path = tmp_path / name
            path.mkdir(exist_ok=True)
            (path / f"plan-s{seed}-t300.json").write_text(json.dumps(data), encoding="utf-8")
        return tmp_path / name

    famine = {
        "kind": "famine",
        "island": 7,
        "first": 140,
        "line": "島 7（market）: 飢饉",
        "snapshot": measure.snapshot_command("plan", 1, 140),
    }
    table = measure.compare(write("main", []), write("new", [famine]))
    assert "| plan-s1-t300 | famine | 0 | 1 | +1 増えた |" in table
    assert "--turns 137 --save plan-s1-t137.json" in table
    # 目安は種1・2の平均（0 → 1.5）
    assert "| plan | 300 | 2 | 120〜300 | 0.0 | 1.5 | +1.5 |" in table


def test_a_snapshot_continues_the_same_world(game, tmp_path):
    scenario = measure.SCENARIOS["plan"]

    def run(world, turns):
        for _ in range(turns):
            measure.queue_scenario_orders(world, scenario, game.defs, game.config)
            game.engine.run_turn(world)

    straight = generate_world(4, 2, game.config, game.defs)
    run(straight, 20)
    saved = generate_world(4, 2, game.config, game.defs)
    run(saved, 10)
    save_snapshot(saved, str(tmp_path / "w.json"))
    loaded = load_snapshot(str(tmp_path / "w.json"))
    run(loaded, 10)
    assert world_digest(loaded) == world_digest(straight)


def test_nightly_summary_lists_collapses(tmp_path):
    out = tmp_path / "night"
    out.mkdir()
    found = [
        {"kind": "stockout", "first": 50, "line": "島 2: 品切れ", "snapshot": "python x"},
        {"kind": "stockout", "first": 40, "line": "島 3: 品切れ", "snapshot": "python y"},
    ]
    run = {
        "kind": "long",
        "scenario": "small",
        "seed": 11,
        "turns": 1200,
        "collapse": {"start": 1, "counts": {"stockout": 1}, "found": found},
    }
    (out / "long-small-s11-t1200.json").write_text(json.dumps(run), encoding="utf-8")
    (out / "long-small-s11-t1200.log").write_text("", encoding="utf-8")
    summary = nightly.summarize(out, tmp_path / "known.json")
    assert [f["signature"] for f in summary["findings"]] == ["collapse small stockout 2+"]
    text = (out / "summary.md").read_text(encoding="utf-8")
    assert "【新】崩れ（1ターン目から）: collapse small stockout 2+" in text
    assert "島の数: 2。いちばん早いもの: 島 3: 品切れ" in text and "`python y`" in text
    # 崩れた島が5つに増えたら、また新しいものとして印を付ける
    run["collapse"]["found"] = found * 3
    (out / "long-small-s11-t1200.json").write_text(json.dumps(run), encoding="utf-8")
    assert nightly.summarize(out, tmp_path / "known.json")["new"] == 1
