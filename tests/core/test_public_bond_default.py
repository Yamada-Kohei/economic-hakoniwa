"""中央銀行と政府を合わせた国債の残高と、不履行の持ち手ごとの扱い。"""

from hakoniwa_core import ledger
from hakoniwa_core.economy import debt, funds
from hakoniwa_core.modules.bonds import Bonds, default_premium
from hakoniwa_core.modules.records import _finances
from hakoniwa_core.state import WorldState
from hakoniwa_core.units import part, to_basis
from tests.core.test_finance import context
from tests.core.test_rules import game as game  # noqa: F401
from tests.core.test_rules import make_island


def test_central_bank_only_default_keeps_face_and_adds_no_penalty(game):
    island = make_island(game)
    world = WorldState(seed=1, turn=7, islands={island.id: island})
    ctx = context(game, world)
    bond = debt.underwrite(island, 1_000_000, 12, 200, 0)
    island.economy.finance["yields"] = [200, 300]
    cost = debt.cost_of(island, ledger.CB)
    stage = Bonds()
    for turn in range(8, 13):
        ctx.turn = turn
        stage._default(island, ctx, [bond], "bond_default_ordered")
        assert debt.outstanding(island, bond) == bond.face == 1_000_000
        assert debt.holders(island, bond) == {ledger.CB: 1_000_000}
        assert debt.cost_of(island, ledger.CB) == cost
        assert island.economy.finance["yields"] == [200, 300]
        assert not debt.market_closed(island, game.config, turn)
        assert default_premium(island, ctx) == 0
    assert "default_turn" not in island.economy.finance
    assert all(e.kind == "bond_default_ordered" and e.args["amount"] == 0 for e in island.log)


def test_default_cuts_bank_holdings_and_preserves_central_bank(game):
    island = make_island(game)
    world = WorldState(seed=1, turn=7, islands={island.id: island})
    ctx = context(game, world)
    bond = debt.underwrite(island, 1_000_000, 12, 200, 0)
    bank = "bank:1"
    ledger.transfer(island, ledger.bond_item(bond.id), ledger.GOV, ledger.CB, bank, 400_000, "test")
    debt.bought(island, bank, bond.id, 400_000)
    cut = part(400_000, to_basis(game.config.get("bonds.haircut")))
    Bonds()._default(island, ctx, [bond], "bond_default_ordered")
    assert debt.holders(island, bond) == {bank: 400_000 - cut, ledger.CB: 600_000}
    assert debt.outstanding(island, bond) == bond.face == 1_000_000 - cut
    assert debt.cost_of(island, bank) == 400_000 - cut
    assert island.economy.finance[debt.REALIZED][bank] == -cut
    assert debt.market_closed(island, game.config, ctx.turn)
    assert default_premium(island, ctx) == game.config.get("bonds.default_premium")
    assert island.log[0].args["amount"] == cut


def test_net_public_debt_deducts_all_government_deposits(game):
    island = make_island(game)
    bond = debt.underwrite(island, 1_000_000, 12, 200, 0)
    ledger.transfer(
        island, ledger.bond_item(bond.id), ledger.GOV, ledger.CB, "bank:1", 400_000, "test"
    )
    ledger.treasury_to_bank(island, "bank:1", 250_000, "test")
    stats = _finances(island)
    assert stats["bonds.central_bank"] == 600_000
    assert funds.government_money(island) == 1_000_000
    assert stats["net_public_debt"] == -600_000
