"""外貨準備の端数がペッグ解除を妨げないことを、小さな板で確かめる。"""

from __future__ import annotations

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import Payer
from hakoniwa_core.market.auction import Order, band
from hakoniwa_core.state import IslandState, WorldState
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney


@pytest.mark.parametrize(
    "reserve,reference,peg,wants,remaining,broken",
    [
        pytest.param(11, 9000, 9000, (100, 20), 1, True, id="split-leaves-dust"),
        pytest.param(11, 9000, 9000, (10, 1), 1, False, id="no-pressure"),
        pytest.param(2, 3000, 3000, (100, 20), 2, True, id="dust-above-one"),
        pytest.param(1, 10000, 10000, (100, 20), 0, True, id="spendable-one-is-paid"),
        pytest.param(1, 10000, 10000, (), 1, False, id="spendable-one-is-kept"),
        pytest.param(1, 9000, 18000, (100, 20), 1, False, id="moving-up-to-peg"),
        pytest.param(100, 9000, 9000, (10, 1), 90, False, id="reserves-remain"),
        pytest.param(0, 9000, 9000, (100, 20), 0, True, id="no-reserves"),
    ],
)
def test_peg_reserve_dust(
    reserve: int,
    reference: int,
    peg: int,
    wants: tuple[int, ...],
    remaining: int,
    broken: bool,
) -> None:
    island = IslandState(id=1, name="A", wx=0, wy=0, size=1, tiles=[])
    other = IslandState(id=2, name="B", wx=1, wy=0, size=1, tiles=[])
    world = WorldState(turn=0, seed=1, islands={1: island, 2: other})
    fxrates.rates(island)["2"] = reference
    island.policy.fx_peg = {"2": peg}
    fxmoney.transfer(
        world,
        other.id,
        (other.id, ledger.CB),
        (island.id, ledger.CB),
        reserve,
        "fx_exchange",
        fxrates.to_own(island, other.id, reserve),
    )
    book = fxrates.Book()
    _, high = band(reference, 200)
    for index, quantity in enumerate(wants, start=1):
        buyer = f"bank:{index}"
        ledger.pay(island, ledger.CB, buyer, 10_000, "fx_exchange")
        book.buys.append((Order(quantity, high), buyer, Payer(buyer)))

    # 11 を (10, 1) に配ると、後者の代金は 0 になって決済されず、外貨 1 が残る。
    fxrates.clear(world, island, other.id, book, 200)

    assert fxmoney.holding(island, ledger.CB, other.id) == remaining
    assert island.policy.fx_peg == ({} if broken else {"2": peg})
    assert sum(event.kind == "fx_peg_broken" for event in island.log) == int(broken)
    assert island.stats.get("fx.peg_broken", 0) == int(broken)
    assert sum(
        fxmoney.holding(island, f"bank:{i}", other.id) for i in range(1, len(wants) + 1)
    ) == (reserve - remaining)
    assert fxmoney.reconcile(world) == []
