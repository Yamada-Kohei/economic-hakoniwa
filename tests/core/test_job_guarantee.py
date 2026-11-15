"""雇用の保障（LB-46。2026-11-12、W-231 の日々の仕事の割り当て）。"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import funds, payer_of
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.modules.employment import GUARANTEED, Employment
from tests.core.test_recession_round1 import context
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_staff_plan import _planned


def _half(game, regime="planned"):
    """操業目標が半分の国営の製鉄所。町には失業者が多い。"""
    island, mill = _planned(game)
    island.regime["economy"] = regime
    mill.economy["target"] = fac.FULL // 2
    return island, mill


def test_planned_islands_place_the_jobless_in_empty_state_seats(game):
    island, mill = _half(game)
    ctx = context(game)
    full = fac.scale(fac.workers_of(game.defs, mill), fac.equipment_cap(mill))
    Employment().run_island(island, ctx)
    placed = mill.economy.get(GUARANTEED, 0)
    # 操業目標に要る人に足して、空いた席を全力の人数まで失業者で埋める
    assert placed > 0
    assert mill.workers == full
    assert island.stats["labor.guaranteed"] == placed
    # 配った人の賃金は配るときに取り置いたので、遅配にしない
    assert not mill.economy.get("unpaid")
    # 次のターンは抱えた人として雇い続ける（辞める人の分だけ減る）
    Employment().run_island(island, ctx)
    assert mill.workers >= full - full * game.config.get("planning.attrition") - 1


def test_other_regimes_do_not_place_the_jobless(game):
    island, mill = _half(game, regime="mixed")
    Employment().run_island(island, context(game))
    assert GUARANTEED not in mill.economy
    assert mill.workers < fac.workers_of(game.defs, mill)


def test_placing_stops_at_the_staff_cap_and_the_money_left(game):
    island, mill = _half(game)
    ctx = context(game)
    full = fac.workers_of(game.defs, mill)
    mill.ext[planning.STAFF_CAP] = full * 3 // 4
    Employment().run_island(island, ctx)
    assert mill.workers <= full * 3 // 4
    # 払い手の残りで払える人数までだけ配る
    island2, mill2 = _half(game)
    payer = payer_of(game.defs, game.config, mill2)
    Employment().run_island(island2, ctx)
    paid_only = mill2.workers - mill2.economy.get(GUARANTEED, 0)
    island3, mill3 = _half(game)
    funds.commit(island3, payer, funds.available(island3, payer) - 1)
    Employment().run_island(island3, ctx)
    assert mill3.economy.get(GUARANTEED, 0) == 0
    assert mill3.workers <= paid_only
