"""取り付け（BK-08）: 預金者が逃げるのは、銀行の体力（自己資本比率）が規制を下回ったときだけ。"""

from __future__ import annotations

import copy

from hakoniwa_core import ledger
from hakoniwa_core.economy import OWNERS, banks, household
from hakoniwa_core.modules.banking import Banking
from tests.core.test_finance import context, island_of
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401


def _household_deposits(island, bank):
    return sum(
        ledger.deposits_by_bank(island, entity).get(bank, 0)
        for entity in banks.household_entities(island)
    )


def _two_banks(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "mixed")  # 銀行が2つある島
    bank, _ = banks.active_banks(island)[:2]
    return world, island, context(game, world), bank


def test_borrowing_from_the_central_bank_does_not_start_a_run(game, warmed):
    """体力のある銀行が中央銀行から借りても、預金者には見えないので取り付けは起きない（日銀や
    FRB の貸し先は公表されないか、ずっと後に出る）。動くのはふだんの乗り換えの分まで。"""
    world, island, ctx, bank = _two_banks(game, warmed)
    config = game.config
    ledger.create(island, ledger.CB, ledger.CB_LOAN, bank, 10_000_000, "cb_loan")
    ledger.pay(island, ledger.CB, bank, 10_000_000, "cb_loan")
    assert banks.capital_ratio(island, config, bank, world.turn) >= banks.regulatory(config)
    before = _household_deposits(island, bank)
    total = sum(
        sum(ledger.deposits_by_bank(island, e).values()) for e in banks.household_entities(island)
    )
    Banking()._switch_deposits(island, ctx)
    lost = before - _household_deposits(island, bank)
    assert lost <= total * config.get("banking.switch_share") + len(
        banks.household_entities(island)
    )


def test_a_bank_below_the_regulatory_ratio_still_sees_a_run(game, warmed):
    """体力が規制を下回った銀行からは、これまでどおり預金の banking.run_share が流れ出す。"""
    world, island, ctx, bank = _two_banks(game, warmed)
    config = game.config
    worth = banks.net_worth(island, config, bank, world.turn)
    ledger.pay(island, bank, household(OWNERS), worth - 1, "dividend")  # 自己資本をほぼ 0 に
    assert banks.capital_ratio(island, config, bank, world.turn) < banks.regulatory(config)
    before = _household_deposits(island, bank)
    Banking()._switch_deposits(island, ctx)
    lost = before - _household_deposits(island, bank)
    assert lost >= before * config.get("banking.run_share") * 0.9
