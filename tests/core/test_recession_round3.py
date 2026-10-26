"""W-186（市場経済の国の不況）の直し 回3: お金の回り道（設備投資・量的緩和 W-193・住宅）。"""

from __future__ import annotations

import copy

from hakoniwa_core import ledger
from hakoniwa_core.economy import banks, debt
from hakoniwa_core.modules.bonds import Bonds, holding_at, target_holding
from hakoniwa_core.modules.trade import check
from hakoniwa_core.units import to_basis
from tests.core.test_finance import books_ok, context, open_books
from tests.core.test_phase3_3 import base as base  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_phase3_3 import game as game  # noqa: F401


def test_banks_hold_fewer_bonds_as_the_yield_nears_the_reserve_rate():
    assets, base_yield, floor, free = 10_000, 400, 200, 0.25
    # 市場の求める利回り以上では今までどおり
    assert holding_at(assets, 450, base_yield, floor, free, 0.04) == target_holding(
        assets, 450, base_yield, free, 0.04
    )
    assert holding_at(assets, base_yield, base_yield, floor, free, 0.04) == 2500
    # 準備預金の利回りに近づくほど減り、そこで 0
    assert holding_at(assets, 300, base_yield, floor, free, 0.04) == 1250
    assert holding_at(assets, floor, base_yield, floor, free, 0.04) == 0


def _qe(game, world, island, beyond):
    stage = Bonds()
    stage.world = world
    stage.bids = {}
    if not beyond:
        stage._cb_buy = lambda *args: 0  # 前の形: 値幅の中だけで買う
    island.policy.cb_independent = False
    island.policy.cb_bond_trade = 10**13
    before = list(island.economy.finance["yields"])
    open_books(island)
    stage._secondary(island, context(game, world))
    return before, island.scratch.get("bond_stats", {}).get("cb_bought", 0)


def test_central_bank_buys_beyond_the_band_from_banks_willing_to_sell(game, base):
    """W-193: 銀行が今の利回りで持ちたい分しか売らず、値幅の中では少ししか買えなかった。値幅を
    越えて利回りを下げると、持ちたい額が減った銀行が売る。準備預金の利回りより下には下げない。"""
    world = copy.deepcopy(base)
    island = next(
        i
        for i in world.islands.values()
        if i.has_economy
        and i.regime.get("economy") != "planned"
        and any(debt.holdings_of(i, b) for b in banks.active_banks(i))
    )
    old = copy.deepcopy(world)
    _, within = _qe(game, old, old.islands[island.id], beyond=False)
    before, bought = _qe(game, world, island, beyond=True)
    assert bought > within
    band = to_basis(game.config.get("bonds.trade_band"))
    spread = to_basis(game.config.get("central_bank.reserve_spread"))
    floor = max(0, island.policy.policy_rate - spread)
    after = island.economy.finance["yields"]
    assert any(b - a > band for a, b in zip(after, before, strict=True))
    assert all(a >= min(floor, b) for a, b in zip(after, before, strict=True))
    assert check(world) == []
    books_ok(game, island)
    assert sum(debt.holdings_of(island, ledger.CB).values()) > 0


def _busy_factory(game, world):
    from hakoniwa_core.state import OWNER_PRIVATE

    for island in world.islands.values():
        if not island.has_economy or island.regime.get("economy") == "planned":
            continue
        for f in sorted(island.facilities, key=lambda x: x.id):
            if f.kind == "consumer_goods_factory" and f.owner == OWNER_PRIVATE and f.built:
                return island, f
    raise AssertionError("民営の消費財工場がない")


def test_a_factory_that_keeps_missing_sales_expands_in_place(game, base):
    """W-186 回3: 全力で動いて売り切れても売り逃しが続いた民営の工場は、採算が合えばその場で建て
    増す（新しい土地は要らない）。売り逃しがなければ建て増さず、工事中は重ねない。"""
    from hakoniwa_core import facilities as fac
    from hakoniwa_core.modules.firms import EntryExit
    from hakoniwa_core.modules.market import MISSED

    from hakoniwa_core.economy import equipment, owner_entity

    world = copy.deepcopy(base)
    island, f = _busy_factory(game, world)
    f.economy.pop("entered", None)
    for key in (equipment.EQUIPMENT, equipment.CONDITION):  # 設備は新品（直す途中ではない）
        f.economy.pop(key, None)
    # 売れ行きに追いつかず値段が上がっていて、持ち主に出資するお金がある
    island.economy.price_history["consumer_goods"] = [
        p * 3 for p in island.economy.price_history["consumer_goods"]
    ]
    ledger.pay(island, ledger.GOV, owner_entity(f), 10**10, "subsidy")
    ctx = context(game, world)
    turns = game.config.get("firms.invest_turns")
    f.economy[MISSED] = [0] * turns
    EntryExit()._invest(island, ctx)
    assert fac.pending_expansions(island, f) == 0
    f.economy[MISSED] = [fac.FULL] * turns  # 毎月、全力の分だけ売り逃した
    EntryExit()._invest(island, ctx)
    assert fac.pending_expansions(island, f) == 1
    EntryExit()._invest(island, ctx)  # 工事中は重ねない
    assert fac.pending_expansions(island, f) == 1
    assert check(world) == []
