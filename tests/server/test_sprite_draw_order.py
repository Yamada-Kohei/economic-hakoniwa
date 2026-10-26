"""島の絵（tools/sprites/silhouette.py）の描く順が循環しないことを確かめる。

循環すると窓や屋根の縁が壁の中へ突き抜けて見える。直した絵だけを対象にしている
（古い絵には循環が残っているものがあり、見た目が崩れていないので触っていない）。
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SILHOUETTE = Path(__file__).resolve().parents[2] / "tools" / "sprites" / "silhouette.py"

CHECKED = [
    "facility/department_store",
    "facility/monument_ledger",
    "housing/city",
    "housing/metropolis",
    "housing/high_rise",
    "housing/skyscraper",
]
CHECKED_SPARE = [
    "housing/city_bands",
    "housing/city_green",
    "housing/metropolis_bands",
    "housing/metropolis_tiers",
]


@pytest.fixture(scope="module")
def silhouette():
    spec = importlib.util.spec_from_file_location("silhouette", SILHOUETTE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["silhouette"] = module  # dataclass が自分のモジュールを探す
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("silhouette", None)


@pytest.mark.parametrize("name", CHECKED)
def test_game_sprites_have_no_draw_order_cycle(silhouette, name):
    assert silhouette.order_cycles(silhouette.SHAPES[name]) == []


@pytest.mark.parametrize("name", CHECKED_SPARE)
def test_spare_sprites_have_no_draw_order_cycle(silhouette, name):
    assert silhouette.order_cycles(silhouette.ALTERNATES[name]) == []
