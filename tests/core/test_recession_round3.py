"""W-186（市場経済の国の不況）の直し 回3: お金の回り道（設備投資・量的緩和 W-193・住宅）。"""

from __future__ import annotations

import copy

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import (
    GENERAL,
    JOBLESS,
    OWNERS,
    banks,
    debt,
    equipment,
    household,
    owner_entity,
)
from hakoniwa_core.economy.households import set_jobless
from hakoniwa_core.modules.banking import Banking
from hakoniwa_core.modules.bonds import Bonds, holding_at, target_holding
from hakoniwa_core.modules.firms import EntryExit
from hakoniwa_core.modules.market import MISSED
from hakoniwa_core.modules.population import Housing
from hakoniwa_core.modules.trade import check
from hakoniwa_core.units import to_basis
from tests.core.test_finance import books_ok, context, open_books
from tests.core.test_phase3_3 import base as base  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_phase3_3 import game as game  # noqa: F401
from tests.core.test_phase3_3 import run_checked


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


# --- 住宅ローンと家の傷み（A2） ----------------------------------------------------


def _market_island(world):
    return next(
        i
        for i in world.islands.values()
        if i.has_economy and i.regime.get("economy") == "market" and banks.active_banks(i)
    )


def test_mortgages_merge_per_bank_and_layer_and_blend_the_term(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    bank = banks.active_banks(island)[0]
    owners = household(OWNERS)
    for loan in banks.loans_by_borrower(island, owners):  # 人と一緒に移ってきた分を消してから試す
        banks.write_off(island, loan, banks.outstanding(island, loan))
    banks.drop_settled(island)
    open_books(island)
    first = banks.lend(island, bank, owners, 3_000_000, banks.MORTGAGE, 360, 10, 500)
    second = banks.lend(island, bank, owners, 1_000_000, banks.MORTGAGE, 360, 190, 500)
    assert first is second and len(banks.loans_by_borrower(island, owners)) == 1
    assert banks.outstanding(island, first) == 4_000_000
    # 残り 180 ターンの 300 万円と新しい 360 ターンの 100 万円を、残高の重みで混ぜる
    assert first.term == (3_000_000 * 180 + 1_000_000 * 360) // 4_000_000
    books_ok(game, island)


def _mortgage(island, layer):
    return sum(
        banks.outstanding(island, x)
        for x in banks.loans_by_borrower(island, household(layer))
        if x.purpose == banks.MORTGAGE
    )


def test_people_who_lose_their_jobs_take_their_share_of_the_mortgage(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    bank = banks.active_banks(island)[0]
    households = island.economy.households
    banks.lend(island, bank, household(GENERAL), 10_000_000, banks.MORTGAGE, 360, 10, 500)
    open_books(island)
    total = _mortgage(island, GENERAL) + _mortgage(island, JOBLESS)
    general, jobless = _mortgage(island, GENERAL), _mortgage(island, JOBLESS)
    moved = households[GENERAL]["persons"] // 10
    share = moved / households[GENERAL]["persons"]
    set_jobless(island, households[JOBLESS]["persons"] + moved)
    assert _mortgage(island, GENERAL) + _mortgage(island, JOBLESS) == total
    assert abs(_mortgage(island, JOBLESS) - jobless - general * share) <= general * 0.001
    books_ok(game, island)


def test_a_layer_that_cannot_pay_its_mortgage_is_written_off_after_the_limit(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    bank = banks.active_banks(island)[0]
    jobless = household(JOBLESS)
    loan = banks.lend(island, bank, jobless, 10_000_000, banks.MORTGAGE, 360, world.turn, 500)
    # 預金を全部ほかへ移して返せなくする
    ledger.pay(island, jobless, household(GENERAL), ledger.money(island, jobless), "layer_transfer")
    stage = Banking()
    limit = game.config.get("loans.arrears_limit")
    for _ in range(limit):
        world.turn += 1
        ctx = context(game, world)
        open_books(island)
        stage._service_loans(island, ctx, {b: [0, 0] for b in banks.bank_list(island)})
        books_ok(game, island)
    assert banks.outstanding(island, loan) == 0
    assert island.stats["loans.mortgage_write_off"] > 10_000_000


def test_houses_wear_and_lose_room_only_below_the_livable_line(game, base):
    world = copy.deepcopy(base)
    island = _market_island(world)
    home = next(f for f in island.facilities if fac.is_housing(game.config, f))
    full = fac.HOUSING_FULL
    nominal = fac.nominal_housing_capacity(game.defs, home)
    island.economy.housing["stock"] = {}  # 手入れの資材がない
    stage = Housing()
    ctx = context(game, world)
    stage.run_island(island, ctx)
    cond = home.economy[fac.HOUSING_CONDITION]
    assert cond < full and fac.housing_capacity(game.defs, home) == nominal
    assert island.economy.housing["repair"]  # 足りない手入れの資材を次に買う
    home.economy[fac.HOUSING_CONDITION] = full * 4 // 10  # 住める線（0.8）の半分
    stage.run_island(island, ctx)
    assert fac.housing_capacity(game.defs, home) < nominal * 0.51
    # 資材があれば、育つより先に直す
    for good, qty in fac.housing_materials(game.defs, nominal).items():
        stock.add(island.economy.housing["stock"], good, qty, world.turn, game.defs.goods)
    stage.run_island(island, ctx)
    assert home.economy[fac.HOUSING_CONDITION] > full * 99 // 100
    assert fac.housing_capacity(game.defs, home) == nominal


def test_households_borrow_for_new_towns_and_the_books_balance(game, base):
    world = copy.deepcopy(base)
    run_checked(game, world, 12)
    loans = [
        x
        for i in world.islands.values()
        for x in i.loans
        if x.purpose == banks.MORTGAGE and i.regime.get("economy") != "planned"
    ]
    assert loans and all(x.borrower.startswith("hh:") for x in loans)
