"""節目（グラフィック強化 案27）: 建国記念日と、人口・GDP の節目。

- 記念日は建国から満1年と10年刻みの年の、ちょうど満○年のターンにだけ残る
- 人口は10万人刻み、GDP（1ターン）は桁上がりの節目を初めて越えたときにだけ残る
  （記録は island.ext["milestones"]。記録が無い島は今の到達点を黙って記録する）
- 国のページの印（anniversary_years）は、満○年を迎えた後の1年間だけ出る
"""

from __future__ import annotations

import pytest

from hakoniwa_core import milestones
from hakoniwa_core.game import load_game
from hakoniwa_core.log import format_log
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture
def island(game):
    world = generate_world(7, 2, game.config, game.defs)
    island = next(iter(world.islands.values()))
    island.ext["founded_turn"] = 1
    island.log.clear()
    return island


def kinds(island):
    return [entry.kind for entry in island.log]


# --- 建国記念日 -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("turn", "years"),
    [(13, 1), (121, 10), (241, 20), (1201, 100)],
)
def test_anniversary_is_logged_on_the_day(island, turn, years):
    milestones.celebrate(island, turn)
    assert kinds(island) == ["anniversary_founding"]
    assert island.log[0].args == {"years": years}


@pytest.mark.parametrize("turn", [1, 12, 14, 25, 61, 131])
def test_anniversary_is_not_logged_on_other_turns(island, turn):
    # ターン1は建国そのもの、25は満2年（節目でない年）、61は満5年、131は満10年+11か月
    milestones.celebrate(island, turn)
    assert "anniversary_founding" not in kinds(island)


@pytest.mark.parametrize(
    ("founded", "turn", "years"),
    [
        (1, 12, None),  # まだ満1年になっていない
        (1, 13, 1),  # 満1年を迎えた直後
        (1, 24, 1),  # 満1年の年の終わり
        (1, 25, None),  # 満2年の年は節目でない
        (5, 125, 10),  # 満10年の年
        (5, 137, None),  # 満11年の年
    ],
)
def test_anniversary_years_for_the_country_page(founded, turn, years):
    assert milestones.anniversary_years(founded, turn) == years


# --- 人口と GDP の節目 -------------------------------------------------------------------------


def test_population_milestone_is_logged_once_at_each_step(island):
    island.stats = {"population": 480_000, "gdp": 0}
    milestones.celebrate(island, 2)  # 最初の記録は黙って置く
    assert kinds(island) == []
    assert island.ext["milestones"]["population"] == 400_000

    island.stats = {"population": 505_000, "gdp": 0}
    milestones.celebrate(island, 3)
    assert kinds(island) == ["milestone_population"]
    assert island.log[0].args == {"count": 500_000}

    island.log.clear()
    milestones.celebrate(island, 4)  # 同じ節目は二度祝わない
    assert kinds(island) == []

    island.stats = {"population": 300_000, "gdp": 0}  # 減っても記録は戻らない
    milestones.celebrate(island, 5)
    island.stats = {"population": 450_000, "gdp": 0}
    milestones.celebrate(island, 6)
    assert kinds(island) == []


def test_population_below_the_first_step_is_quiet(island):
    island.stats = {"population": 99_999, "gdp": 0}
    milestones.celebrate(island, 2)
    assert kinds(island) == []
    island.stats = {"population": 100_000, "gdp": 0}
    milestones.celebrate(island, 3)
    assert kinds(island) == ["milestone_population"]


def test_gdp_milestone_is_logged_at_each_new_digit(island):
    island.stats = {"population": 0, "gdp": 150_000_000}
    milestones.celebrate(island, 2)  # 最初の記録は黙って置く（1億まで）
    assert kinds(island) == []
    assert island.ext["milestones"]["gdp"] == 100_000_000

    island.stats = {"population": 0, "gdp": 999_999_999}
    milestones.celebrate(island, 3)  # 同じ桁のうちは祝わない
    assert kinds(island) == []

    island.stats = {"population": 0, "gdp": 1_200_000_000}
    milestones.celebrate(island, 4)
    assert kinds(island) == ["milestone_gdp"]
    assert island.log[0].args == {"amount": 1_000_000_000}


def test_gdp_below_the_minimum_is_quiet(island):
    island.stats = {"population": 0, "gdp": 99_999}
    milestones.celebrate(island, 2)
    island.stats = {"population": 0, "gdp": 100_000}
    milestones.celebrate(island, 3)
    assert kinds(island) == ["milestone_gdp"]
    assert island.log[0].args == {"amount": 100_000}


# --- 文面と、ターンを回したときの記録 ------------------------------------------------------------


def test_messages_render(game):
    defs = game.defs
    assert format_log(defs, "anniversary_founding", {"years": 10}) == "建国10年の記念日を迎えました"
    assert (
        format_log(defs, "milestone_population", {"count": 500_000})
        == "人口が500,000人に達しました"
    )
    assert (
        format_log(defs, "milestone_gdp", {"amount": 1_000_000_000})
        == "GDP（1ターン）が1,000,000,000に達しました"
    )


def test_records_stage_logs_the_anniversary(game):
    world = generate_world(7, 2, game.config, game.defs)
    island = next(iter(world.islands.values()))
    island.ext["founded_turn"] = 1
    world.turn = 12  # 次に処理するターン13で、建国から満1年
    game.engine.run_turn(world)
    found = [e for e in island.log if e.kind == "anniversary_founding"]
    assert len(found) == 1 and found[0].args == {"years": 1}
    # 人口と GDP の到達点の記録も、このターンで黙って置かれている
    assert "population" in island.ext["milestones"]
    assert island.ext["milestones"]["gdp"] > 0
