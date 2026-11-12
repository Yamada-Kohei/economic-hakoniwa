"""火力発電所の燃料の備えと知らせの設定（FM-63・UI-87）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hakoniwa_core.config import default_config_path, load_config


def test_power_fuel_default_settings() -> None:
    config = load_config(default_config_path())
    assert config.get("power.fuel_stock_turns") == 3
    assert config.get("power.fuel_restock") == 0.2
    assert config.get("power.fuel_notice_turns") == 1.5
    assert config.get("power.notice_reset") == 0.9
    assert config.get("power.price_notice_turns") == 6


@pytest.mark.parametrize("notice", [1, 1.5])
def test_fuel_notice_at_most_half_the_target_loads(tmp_path: Path, notice: float) -> None:
    path = tmp_path / "game.yaml"
    path.write_text(
        f"power:\n  fuel_stock_turns: 3\n  fuel_notice_turns: {notice}\n", encoding="utf-8"
    )
    assert load_config(path).get("power.fuel_notice_turns") == notice


def test_fuel_notice_above_half_the_target_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "game.yaml"
    path.write_text("power:\n  fuel_stock_turns: 3\n  fuel_notice_turns: 1.6\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"power\.fuel_notice_turns.*power\.fuel_stock_turns"):
        load_config(path)


def test_price_notice_line_shorter_than_the_target_is_rejected(tmp_path: Path) -> None:
    """線が備えの月数より短いと、全力で動く発電所が在庫を積み終えても採算の注意を出せない。"""
    path = tmp_path / "game.yaml"
    path.write_text("power:\n  fuel_stock_turns: 3\n  price_notice_turns: 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"power\.price_notice_turns.*power\.fuel_stock_turns"):
        load_config(path)


def test_config_without_power_settings_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "game.yaml"
    path.write_text("stages: []\n", encoding="utf-8")
    assert load_config(path).stages == ()
