"""暦（hakoniwa_core/dates.py）: ターン1 が箱庭暦1年1月、ターンが 12 の倍数のときが12月
（年の終わり）。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR, Calendar, is_year_end, month_of, world_calendar
from hakoniwa_core.game import load_game
from hakoniwa_core.log import format_log, format_turn

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def game():
    return load_game()


def test_turns_count_months_from_year_one():
    calendar = Calendar(name="箱庭暦")
    assert calendar.date(1) == (1, 1)
    assert calendar.date(12) == (1, 12)
    assert calendar.date(13) == (2, 1)
    assert calendar.date(250) == (21, 10)
    assert calendar.date(383) == (32, 11)
    assert calendar.date(0) is None  # 暦の始まる前
    assert [month_of(t) for t in range(1, 14)] == [*range(1, 13), 1]


def test_year_end_is_december():
    """年に1度の処理（ターンが 12 の倍数）は、どの年でも12月に動く。"""
    calendar = Calendar(name="箱庭暦")
    ends = [t for t in range(1, 5 * TURNS_PER_YEAR + 1) if is_year_end(t)]
    assert ends == [12, 24, 36, 48, 60]
    assert all(calendar.date(t)[1] == 12 for t in ends)
    assert all(calendar.date(t + 1) == (calendar.date(t)[0] + 1, 1) for t in ends)


def test_first_year_shifts_only_the_year():
    calendar = Calendar(name="箱庭暦", first_year=100)
    assert calendar.date(1) == (100, 1)
    assert calendar.date(383) == (131, 11)


def test_national_reckoning_counts_from_its_start_turn():
    """国ごとの数え方（フェーズ4）: 数え始めたターンの年が元年で、次の1月から2年。"""
    founded = Calendar(name="建国", start_turn=230)  # 箱庭暦20年2月
    assert founded.date(229) is None
    assert founded.date(230) == (1, 2)
    assert founded.date(240) == (1, 12)
    assert founded.date(241) == (2, 1)


def test_world_calendar_reads_game_yaml(game):
    assert game.calendar == Calendar(name="箱庭暦", first_year=1)


@pytest.mark.parametrize(
    "calendar",
    [{"first_year": 1}, {"name": "", "first_year": 1}, {"name": "箱庭暦", "first_year": 0}],
)
def test_world_calendar_rejects_bad_settings(game, calendar):
    config = GameConfig(
        **{**game.config.__dict__, "raw": {**game.config.raw, "calendar": calendar}}
    )
    with pytest.raises((ValueError, KeyError)):
        world_calendar(config)


def test_turn_text(game):
    defs, calendar = game.defs, game.calendar
    assert format_turn(defs, 383, calendar) == "箱庭暦32年11月（ターン 383）"
    assert format_turn(defs, 0, calendar) == "箱庭暦の始まる前（ターン 0）"
    assert format_turn(defs, 383) == "ターン 383"  # 暦を渡さないとき


def test_log_writes_dates_for_marked_turns(game):
    args = {"turn": 400}
    text = format_log(game.defs, "entry_block_credit_crunch", args, calendar=game.calendar)
    assert (
        text == "国債の債務不履行で損をした銀行が、箱庭暦34年4月（ターン 400）まで新しく貸しません"
    )
    reason = {"order": "change_politics", "reason": "reason_in_transition"}
    text = format_log(
        game.defs, "order_failed", {**reason, "reason_args": {"until": 395}}, calendar=game.calendar
    )
    assert "箱庭暦33年11月（ターン 395）まで体制を変えられません" in text


def test_one_definition_of_the_year():
    """「1年＝12ターン」を dates.py のほかで YEAR = 12 のように書き直さない。"""
    offenders = []
    for path in [*(ROOT / "hakoniwa_core").rglob("*.py"), *(ROOT / "server").rglob("*.py")]:
        if path.name == "dates.py" or "migrations" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and node.value.value == TURNS_PER_YEAR
                and any(isinstance(t, ast.Name) and "YEAR" in t.id for t in node.targets)
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []
