"""止まった理由の表（hakoniwa_core/stops.py）の行と、画面・ログ・手・入門の文面が 1 対 1 か。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hakoniwa_core import stops
from hakoniwa_core.state import Facility
from server.game import get_game
from server.web import display
from server.web.views import STATUS_HELP

MESSAGES = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "packs/base/locale/ja/messages.yaml").read_text(
        encoding="utf-8"
    )
)
ROWS = [*stops.REASONS.values(), *stops.POWER_ROWS.values()]
# 表の外の状態（工事中・修理・工業団地の指定待ち。施設の ext で出す）
OUTSIDE = {"construction", "repair", "no_zone", "stop_target"}


def test_every_row_has_its_texts():
    for row in ROWS:
        assert row.text_key in MESSAGES, row.key
        assert row.hint_key in display.STATUS_HINTS, row.key
    for key in stops.REASONS:
        if key != stops.STARTING:  # 動き出す前は止まったログを出さない
            assert f"facility_stopped_{key}" in MESSAGES, key
    assert stops.UNKNOWN.text_key in MESSAGES


def test_hints_and_help_have_no_rows_outside_the_table():
    keys = {row.key for row in ROWS}
    assert set(display.STATUS_HINTS) - keys <= OUTSIDE
    helped = {key for key, _text in STATUS_HELP}
    assert keys - helped == set(), keys - helped
    assert helped - keys <= OUTSIDE


def _facility(status: str, rate: int = 0, **ext) -> Facility:
    return Facility(
        id=1, kind="steel_mill", x=1, y=1, built=True, status=status, operating_rate=rate, ext=ext
    )


@pytest.mark.django_db
def test_power_rows_and_unknown_names_read_plainly(world):
    power = get_game().config.get("power.good")
    assert display.status_text(_facility(f"funds:{power}")) == "電力を買うお金が足りない"
    assert display.status_text(_facility(f"input_price:{power}")) == "電気料金が高く採算が合わない"
    assert display.status_text(_facility("mystery")) == "その他"
    assert "（" in display.status_text(_facility("funds:coal"))  # 電力でない財は財の名前を付ける
    war = _facility(stops.HALTED, halt_reason="occupied")
    assert display.status_text(war) == display.message("halt_reason_occupied")
    assert "停戦" in display.status_hint(war)


def test_stop_marks_skip_starting_and_work_that_follows_orders():
    config = get_game().config
    assert stops.stopped_mark(config, stops.TARGET, 0)
    assert stops.stopped_mark(config, stops.EQUIPMENT, 0)
    assert not stops.stopped_mark(config, stops.TARGET, 400)
    for status in (stops.STARTING, stops.CARGO, stops.BUSINESS, stops.ORDERS, ""):
        assert not stops.stopped_mark(config, status, 0), status
