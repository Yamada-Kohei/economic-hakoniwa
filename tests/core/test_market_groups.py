"""市場の記録の、買い手と売り手の別ごとの量（W-72・W-258）。記録だけで計算には使わない。"""

from __future__ import annotations

import copy

from hakoniwa_core.modules.market import BUYER_GROUPS, GROUP_HOUSEHOLD, GROUP_STATE
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import run
from tests.core.test_finance import warmed as warmed  # noqa: F401


def test_groups_add_up_to_the_market_record(game, warmed):
    """別ごとの約定した量の合計は売買の量に、売りの注文の量の合計は売りたかった量に合う。家計と
    施設の買いはそれぞれの別に入る。"""
    world = copy.deepcopy(warmed)
    run(game, world, 1)
    seen, groups = 0, set()
    for island in world.islands.values():
        for r in island.markets:
            if not r.buyers and not r.sellers:
                continue
            seen += 1
            assert set(r.buyers) | set(r.sellers) <= set(BUYER_GROUPS)
            assert sum(b for _, b in r.buyers.values()) == r.volume, r.good
            assert sum(s for _, s in r.sellers.values()) == r.volume, r.good
            assert sum(o for o, _ in r.sellers.values()) == r.supply, r.good
            assert all(0 <= b <= o for o, b in r.buyers.values()), r.good
            groups |= set(r.buyers)
    assert seen > 0 and {GROUP_HOUSEHOLD, GROUP_STATE} <= groups
