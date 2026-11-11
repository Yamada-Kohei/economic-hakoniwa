"""解雇の決まり（LB-43）を試験の中だけで入れる部品。

game.yaml の labor.dismissal は 2026-11-11 から一時止めている（どの段も rate 1.0・手当 0 か月）。
決まりそのものの試験と、決まりのある世界の道に頼る試験は、ここの値（止める前の値）を自分で入れて
回す。決まりを戻したら、この部品は game.yaml の値と同じになるので、消してよい。
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager

from hakoniwa_core.config import GameConfig

RULES = {
    "weak": {"rate": 0.2, "months": 0},
    "normal": {"rate": 0.08, "months": 2},
    "strong": {"rate": 0.04, "months": 6},
}


@contextmanager
def dismissal_rules(config: GameConfig) -> Iterator[None]:
    """この中だけ、config の labor.dismissal を止める前の値にする（出るときに戻す）。"""
    labor = config.get("labor")
    before = labor["dismissal"]
    labor["dismissal"] = copy.deepcopy(RULES)
    try:
        yield
    finally:
        labor["dismissal"] = before
