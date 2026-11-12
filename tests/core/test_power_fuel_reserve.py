"""火力発電所の使う分と、余裕があるときの燃料の買い足し（FM-63）。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import (
    STATE_FACILITIES,
    WAREHOUSE,
    Payer,
    banks,
    facility_entity,
    funds,
    household,
    payer_of,
    power_fuel,
)
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.economy.freight import Routes
from hakoniwa_core.economy.start import next_payments
from hakoniwa_core.game import Game
from hakoniwa_core.market import auction
from hakoniwa_core.modules.market import (
    BIDS,
    CLEARING,
    FACILITY,
    FUEL_RESERVE_PRIORITY,
    HOUSEHOLD,
    MERCHANT,
    MERCHANT_GAP,
    STORE,
    Book,
    Market,
    Party,
    input_cost,
)
from hakoniwa_core.modules.production import Production
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
    ACCOUNTING_TREASURY,
    OWNER_PRIVATE,
    OWNER_STATE,
    Facility,
    IslandState,
)
from hakoniwa_core.units import money_of, qty_for, round_money
from tests.core.test_recession_round1 import context
from tests.core.test_rules import add, make_island, with_economy
from tests.core.test_rules import game as game  # noqa: F401


def _plant(game: Game, treasury: bool = False) -> tuple[IslandState, Facility, int]:
    island = make_island(game)
    island.regime["economy"] = "market"
    plant = add(
        island,
        "thermal_power_plant",
        6,
        5,
        owner=OWNER_STATE if treasury else OWNER_PRIVATE,
        accounting=ACCOUNTING_TREASURY if treasury else ACCOUNTING_SELF,
    )
    use = fac.recipe_of(game.defs, plant)["inputs"]["coal"]
    stock.add(plant.stock, "coal", use, 0, game.defs.goods)
    with_economy(game, island)
    plant.workers = 550
    plant.operating_rate = fac.FULL
    plant.economy.update(target=fac.FULL, wanted=550, engineers=50)
    island.economy.prices["coal"] = 100
    ledger.create(island, ledger.GOV, ledger.TREASURY, ledger.CB, 10**10, "test")
    ledger.pay(island, ledger.GOV, facility_entity(plant.id), 10**9, "test")
    return island, plant, use


def _orders(
    game: Game, island: IslandState, turn: int = 1, treasury: bool = False
) -> dict[str, Book]:
    books: dict[str, Book] = {}
    Market()._fuel_orders(island, context(game, turn), books, {}, treasury=treasury)
    return books


def _set_cash(island: IslandState, entity: str, amount: int) -> None:
    have = ledger.money(island, entity)
    if have > amount:
        ledger.pay(island, entity, ledger.GOV, have - amount, "test")
    elif have < amount:
        ledger.pay(island, ledger.GOV, entity, amount - have, "test")


def test_reserve_accumulates_by_a_fifth_and_stops_at_target(game: Game) -> None:
    """動きながら、買い足しで使った分を埋め、毎ターン使う分の2割ずつ増えて目標で止まる。

    注文の合計は min(目標 − 手元, 使う分 × 1.2)。手元が使う分以上なので使う分の注文は出ず、
    使った分の補充も買い足しの側で出る。"""
    island, plant, use = _plant(game)
    target = round_money(use * game.config.get("power.fuel_stock_turns"))
    step = round_money(use * game.config.get("power.fuel_restock"))
    stock.add(island.warehouse, "coal", target * 10, 0, game.defs.goods)
    levels = []
    for turn in range(1, 30):
        before = stock.total(plant.stock, "coal")
        book = _orders(game, island, turn).get("coal")
        want = min(use + step, target - before)
        if want <= 0:
            assert book is None
        else:
            assert sum(order.quantity for order, _ in book.buys) == want
            seller = Party(STORE, ledger.GOV, Payer(ledger.GOV, WAREHOUSE))
            book.sells.append((auction.Order(want, 100), seller))
            result = auction.clear([o for o, _ in book.buys], [o for o, _ in book.sells], 100)
            Market()._settle(
                island,
                context(game, turn),
                "coal",
                book,
                result,
                (ledger.GOV, None),
                {},
                Routes(island, game.defs),
            )
        after = stock.total(plant.stock, "coal")
        assert after <= target
        levels.append(after)
        stock.take(plant.stock, "coal", use)  # 次のターンに使う
    # 買った後の手元は 2割ずつ増え、3か月分に着いたらそのまま（使った分だけを買う）
    assert levels[0] == 2 * use + step  # 初めに1か月分ある
    assert levels[1] == levels[0] + step
    assert levels[-1] == levels[-2] == target


def test_reserve_is_separate_from_use_orders_and_their_bid_record(game: Game) -> None:
    island, plant, use = _plant(game)
    plant.stock.clear()
    ctx = context(game)
    market = Market()
    books: dict[str, Book] = {}
    normal_cost = input_cost(island, ctx, plant, 0)
    market._facility_orders(island, ctx, books, {})
    bids = deepcopy(plant.economy[BIDS])
    short = deepcopy(plant.economy.get("short", {}))
    market._fuel_orders(island, ctx, books, {})
    buys = books["coal"].buys
    assert sum(o.quantity for o, p in buys if not p.fuel_reserve) == use
    assert sum(o.quantity for o, p in buys if p.fuel_reserve) == round_money(
        use * game.config.get("power.fuel_restock")
    )
    assert plant.economy[BIDS] == bids
    assert plant.economy.get("short", {}) == short
    assert input_cost(island, ctx, plant, 0) == normal_cost


def test_reserve_at_target_replaces_what_was_burned(game: Game) -> None:
    """目標に着いた後は、使った分（使う見込み1か月分）を買い足しの側で買い直す。"""
    island, plant, use = _plant(game)
    target = round_money(use * game.config.get("power.fuel_stock_turns"))
    stock.add(
        plant.stock, "coal", target - use - stock.total(plant.stock, "coal"), 0, game.defs.goods
    )
    books: dict[str, Book] = {}
    market = Market()
    market._facility_orders(island, context(game), books, {})
    assert not books.get("coal", Book()).buys
    market._fuel_orders(island, context(game), books, {})
    reserve = sum(o.quantity for o, p in books["coal"].buys if p.fuel_reserve)
    assert reserve == use
    assert stock.total(plant.stock, "coal") + reserve == target


@pytest.mark.parametrize("stop", ["closing", "foreclosed", "receiver", "disaster", "zero", "site"])
def test_stopped_seized_and_receivership_plants_do_not_restock(game: Game, stop: str) -> None:
    island, plant, _ = _plant(game)
    if stop == "closing":
        plant.economy["closing"] = {"turn": 1}
    elif stop == "foreclosed":
        plant.economy["foreclosed"] = {"run": False}
    elif stop == "receiver":
        plant.economy["foreclosed"] = {"run": True}
    elif stop == "disaster":
        plant.ext["halted_until"] = 1
    elif stop == "zero":
        plant.economy["condition"] = 0
    else:
        plant.built = False
    assert not _orders(game, island)


@pytest.mark.parametrize(
    "recipe,good,other", [("default", "coal", "fuel"), ("fuel", "fuel", "coal")]
)
def test_only_current_recipe_restocked_and_other_fuel_is_kept(
    game: Game, recipe: str, good: str, other: str
) -> None:
    island, plant, _ = _plant(game)
    plant.recipe = recipe
    plant.stock.clear()
    use = fac.recipe_of(game.defs, plant)["inputs"][good]
    stock.add(plant.stock, good, use, 0, game.defs.goods)
    stock.add(plant.stock, other, 2000, 0, game.defs.goods)
    island.economy.prices[good] = 100
    books: dict[str, Book] = {}
    market = Market()
    market._facility_orders(island, context(game), books, {})
    market._fuel_orders(island, context(game), books, {})
    assert books[good].buys and all(p.fuel_reserve for _, p in books[good].buys)
    assert not books.get(other, Book()).sells
    assert not books.get(other, Book()).buys
    assert stock.total(plant.stock, other) == 2000


def test_restock_is_capped_at_current_price_and_has_lowest_priority(game: Game) -> None:
    island, plant, _ = _plant(game)
    plant.economy[BIDS] = {"turn": 0, "limits": {"coal": 10000}}
    plant.economy["short"] = {"coal": "price"}
    order, party = _orders(game, island)["coal"].buys[0]
    assert order.limit == 100
    assert order.priority == min(
        FUEL_RESERVE_PRIORITY, game.config.get("warehouse.standing_priority")
    )
    assert party.fuel_reserve
    assert plant.economy[BIDS]["limits"]["coal"] == 10000


def test_restock_uses_the_official_price(game: Game) -> None:
    island, _, _ = _plant(game)
    island.regime["economy"] = "planned"
    planning.state_of(island)["prices"] = {"coal": 75}
    order, _ = _orders(game, island)["coal"].buys[0]
    assert order.limit == 75


@pytest.mark.parametrize("official", [{}, {"coal": 100}])
def test_households_receive_fuel_before_reserve_orders(
    game: Game, official: dict[str, int]
) -> None:
    island, _, _ = _plant(game)
    book = _orders(game, island)["coal"]
    reserve = book.buys[0][0]
    hh = household("general")
    book.buys.insert(
        0,
        (auction.Order(reserve.quantity, 100, 0), Party(HOUSEHOLD, hh, Payer(hh), layer="general")),
    )
    bounds, allocate = Market._limits(island, context(game), "coal", book, official, {"general": 1})
    result = auction.clear(
        [o for o, _ in book.buys],
        [auction.Order(reserve.quantity, 100)],
        100,
        bounds=bounds,
        allocate_buys=allocate,
    )
    assert result.bought == [reserve.quantity, 0]


def test_failed_restock_is_excluded_from_shortages_demand_and_input_estimate(game: Game) -> None:
    island, plant, _ = _plant(game)
    before_prices = dict(island.economy.prices)
    books = _orders(game, island)
    result = auction.clear([o for o, _ in books["coal"].buys], [], 100)
    Market._record_shortages(books, {"coal": result}, {})
    Market._record(island, context(game), books, {"coal": result}, {})
    assert plant.economy.get("short", {}) == {}
    assert BIDS not in plant.economy
    assert input_cost(island, context(game), plant, 0) == 0
    assert next(m for m in island.markets if m.good == "coal").demand == 0
    assert island.economy.prices == before_prices


@pytest.mark.parametrize("reason", ["supply", "price", "funds"])
def test_existing_inventory_keeps_production_prepared_when_fuel_cannot_be_bought(
    game: Game, reason: str
) -> None:
    island, plant, _ = _plant(game)
    plant.workers = fac.workers_of(game.defs, plant)  # 技術者も入れて人手は足りている
    plant.economy["short"] = {"coal": reason}
    prepared = Production._prepare(island, plant, context(game), game.config.get("power.good"))
    assert prepared.rate == fac.FULL


def test_restock_keeps_operating_payments_and_debt_service_and_never_borrows(game: Game) -> None:
    island, plant, _ = _plant(game)
    entity = facility_entity(plant.id)
    bank = banks.active_banks(island)[0]
    banks.lend(island, bank, entity, 1000, banks.CONSTRUCTION, 10, 0, 0)
    ctx = context(game)
    keep = next_payments(island, game.defs, game.config, plant)
    keep *= game.config.get("firms.operating_turns")
    keep += banks.debt_service(island, entity, ctx.turn)
    _set_cash(island, entity, keep + 25)
    loans = deepcopy(island.loans)
    reserve = _orders(game, island)["coal"].buys
    assert 0 < sum(p.reserved for _, p in reserve) <= 25
    assert funds.available(island, payer_of(game.defs, game.config, plant)) >= keep
    assert island.loans == loans


def test_restock_does_not_draw_working_capital_when_cash_is_needed_for_payments(game: Game) -> None:
    island, plant, _ = _plant(game)
    entity = facility_entity(plant.id)
    keep = next_payments(island, game.defs, game.config, plant)
    keep *= game.config.get("firms.operating_turns")
    _set_cash(island, entity, keep)
    loans = deepcopy(island.loans)
    assert not _orders(game, island)
    assert island.loans == loans
    assert plant.economy.get("short", {}) == {}


def test_treasury_restock_uses_only_the_remaining_monthly_budget(game: Game) -> None:
    island, plant, _ = _plant(game, treasury=True)
    funds.open_treasury(island, {STATE_FACILITIES: 50}, [STATE_FACILITIES], {STATE_FACILITIES: 50})
    payer = payer_of(game.defs, game.config, plant)
    funds.pay(island, payer, ledger.GOV, 35, "sale")
    assert not _orders(game, island)
    buys = _orders(game, island, treasury=True)["coal"].buys
    assert sum(p.reserved for _, p in buys) <= 15
    assert 0 < sum(o.quantity for o, _ in buys) <= qty_for(15, 100)
    assert funds.fiscal_state(island)["used"][STATE_FACILITIES] <= 50


def test_warehouse_supplies_only_the_next_turn_use_and_keeps_reserves(game: Game) -> None:
    island, plant, use = _plant(game, treasury=True)
    stock.take(plant.stock, "coal", use)
    stock.add(island.warehouse, "coal", use * 10, 0, game.defs.goods)
    Market()._supply_facilities_from_warehouse(island, context(game))
    assert stock.total(plant.stock, "coal") == use
    assert stock.total(island.warehouse, "coal") == use * 9
    assert input_cost(island, context(game), plant, 0) == 0
    assert _orders(game, island, treasury=True)["coal"].buys


def test_warehouse_release_does_not_refill_the_fuel_reserve(game: Game) -> None:
    island, plant, use = _plant(game)
    island.policy.warehouse_release = True
    plant.economy["short"] = {"coal": "supply"}
    stock.add(island.warehouse, "coal", use * 10, 0, game.defs.goods)
    books: dict[str, Book] = {}
    Market()._warehouse_orders(island, context(game), books)
    assert not books.get("coal", Book()).sells
    assert stock.total(island.warehouse, "coal") == use * 10


def test_treasury_second_pass_does_not_buy_fuel_already_taken_by_mk38(game: Game) -> None:
    island, plant, use = _plant(game, treasury=True)
    other = add(island, "thermal_power_plant", 7, 5)
    stock.add(island.warehouse, "coal", use, 0, game.defs.goods)
    seller = Party(STORE, ledger.GOV, Payer(ledger.GOV, WAREHOUSE))
    book = Book(sells=[(auction.Order(use, 100), seller)])
    first = auction.clear([], [o for o, _ in book.sells], 100)
    ctx = context(game)
    market = Market()
    market._supply_facilities_from_warehouse(island, ctx)
    assert stock.total(island.warehouse, "coal") == 0
    assert stock.total(other.stock, "coal") == use
    before = {f.id: deepcopy(f.economy[power_fuel.BOOK]) for f in (plant, other)}
    used = dict(funds.fiscal_state(island)["used"])
    island.scratch[CLEARING] = ((ledger.GOV, None), Routes(island, game.defs))
    results = {"coal": first}
    market._treasury_fuel_reserves(island, ctx, {"coal": book}, results, {}, {}, {})
    assert results["coal"].volume == 0
    assert not island.trades
    assert funds.fiscal_state(island)["used"] == used
    assert {f.id: f.economy[power_fuel.BOOK] for f in (plant, other)} == before


def test_treasury_second_pass_caps_duplicate_clearance_sales_at_actual_stock(game: Game) -> None:
    island, plant, use = _plant(game, treasury=True)
    mine = add(island, "mine", 7, 5, recipe="coal", stock={"coal": 300})
    entity = facility_entity(mine.id)
    normal = Party(FACILITY, entity, Payer(ledger.GOV), facility=mine)
    clearance = Party(FACILITY, entity, Payer(ledger.GOV), facility=mine, clearance=True)
    book = Book(sells=[(auction.Order(1000, 100), normal), (auction.Order(800, 50), clearance)])
    results = {"coal": auction.clear([], [o for o, _ in book.sells], 100)}
    island.scratch[CLEARING] = ((ledger.GOV, None), Routes(island, game.defs))
    Market()._treasury_fuel_reserves(island, context(game), {"coal": book}, results, {}, {}, {})
    assert results["coal"].volume == 300
    assert sum(results["coal"].sold) == 300
    assert stock.total(mine.stock, "coal") == 0
    assert stock.total(plant.stock, "coal") == use + 300


def test_treasury_second_pass_reserves_merchant_gap_again_after_mk38(game: Game) -> None:
    island, plant, use = _plant(game, treasury=True)
    island.regime["economy"] = "planned"
    planning.state_of(island)["prices"] = {"coal": 100}
    quantity = round_money(use * game.config.get("power.fuel_restock"))
    cost, gap = money_of(100, quantity), money_of(50, quantity)
    funds.open_treasury(
        island,
        {STATE_FACILITIES: cost, MERCHANT_GAP.item: gap},
        [STATE_FACILITIES, MERCHANT_GAP.item],
        {STATE_FACILITIES: cost},
    )
    funds.commit(island, MERCHANT_GAP, gap)
    seller = Party(MERCHANT, MERCHANT, Payer(ledger.CB), gap=50, reserved=gap)
    # 通常の市場の取り置きは MK-38 の前に戻し、2巡目の分だけ取り直す。
    funds.release(island, MERCHANT_GAP, seller.reserved)
    seller.reserved = 0
    book = Book(sells=[(auction.Order(quantity, 100), seller)])
    results = {"coal": auction.clear([], [o for o, _ in book.sells], 100)}
    island.scratch[CLEARING] = ((ledger.GOV, None), Routes(island, game.defs))
    before = ledger.money(island, ledger.GOV)
    Market()._treasury_fuel_reserves(
        island, context(game), {"coal": book}, results, {}, {"coal": 100}, {}
    )
    assert results["coal"].volume == quantity
    assert stock.total(plant.stock, "coal") == use + quantity
    assert before - ledger.money(island, ledger.GOV) == cost + gap
    assert seller.reserved == 0
    assert island.stats["trade.merchant_gap"] == gap


@pytest.mark.parametrize("available_gap", [0, 20])
def test_treasury_second_pass_caps_imports_when_merchant_gap_budget_is_short(
    game: Game, available_gap: int
) -> None:
    island, plant, use = _plant(game, treasury=True)
    island.regime["economy"] = "planned"
    planning.state_of(island)["prices"] = {"coal": 100}
    quantity = round_money(use * game.config.get("power.fuel_restock"))
    cost = money_of(100, quantity)
    funds.open_treasury(
        island,
        {STATE_FACILITIES: cost, MERCHANT_GAP.item: available_gap},
        [STATE_FACILITIES, MERCHANT_GAP.item],
        {STATE_FACILITIES: cost, MERCHANT_GAP.item: available_gap},
    )
    seller = Party(MERCHANT, MERCHANT, Payer(ledger.CB), gap=50)
    book = Book(sells=[(auction.Order(quantity, 100), seller)])
    results = {"coal": auction.clear([], [o for o, _ in book.sells], 100)}
    island.scratch[CLEARING] = ((ledger.GOV, None), Routes(island, game.defs))
    Market()._treasury_fuel_reserves(
        island, context(game), {"coal": book}, results, {}, {"coal": 100}, {}
    )
    bought = min(quantity, qty_for(available_gap, 50))
    assert results["coal"].volume == bought
    assert stock.total(plant.stock, "coal") == use + bought
    assert island.stats.get("trade.merchant_gap", 0) == money_of(50, bought) <= available_gap
    assert seller.reserved == 0
    assert funds.available(island, MERCHANT_GAP) >= 0
