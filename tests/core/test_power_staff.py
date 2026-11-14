"""発電所の働き手は電力会社 1 社分として数える（FM-74、2026-11-14）。

前は水力 300・火力 600 人（倍 1）で、公開の世界では発電所の働き手が働く人の 4.25% になっていた
（現実の電力会社全体は 0.2〜0.3%）。水力も火力とほぼ同じ人手だった。
"""

from __future__ import annotations

import pytest

PLANTS = ("thermal_power_plant", "hydro_power_plant")


@pytest.fixture(scope="module")
def defs():
    from hakoniwa_core.game import load_game

    return load_game(people=1).defs  # 倍 1 の人数で見る


def _power(defs, kind):
    return defs.facilities[kind]["recipes"]["default"]["outputs"]["electricity"]


def test_power_plants_are_staffed_as_one_utility(defs):
    """働き手は前の約 10 分の 1 で、技術者が 3〜5 割（運転員と電気・機械の保守員が中心）。"""
    old = {"thermal_power_plant": 600, "hydro_power_plant": 300}
    for kind in PLANTS:
        fac = defs.facilities[kind]
        staff = fac["staff"]
        assert staff["general"] + staff["engineer"] == fac["workers"]
        share = staff["engineer"] / fac["workers"]
        assert 0.3 <= share <= 0.5, kind
        assert fac["workers"] == old[kind] // 10, kind


def test_hydro_needs_fewer_hands_than_thermal_for_the_same_power(defs):
    """水力は燃料も灰もなく遠隔で動かすので、同じ電気を作る人手が火力より少ない。送配電と販売の分は
    作る電気に比例するので、差は発電所の分だけで、半分を下回るほどにはならない。"""
    per = {k: defs.facilities[k]["workers"] / _power(defs, k) for k in PLANTS}
    ratio = per["hydro_power_plant"] / per["thermal_power_plant"]
    assert 0.5 < ratio < 1
