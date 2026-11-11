"""火力発電所の燃料の移動平均原価、低価法と所有変更（FM-64・MK-24）。"""

from __future__ import annotations

from dataclasses import replace

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.economy import books, power_fuel
from hakoniwa_core.economy.prices import initial_prices, input_limit, unit_floor, variable_profit
from hakoniwa_core.founding import found_island
from hakoniwa_core.modules.accounts import Accounts
from hakoniwa_core.modules.firms import restart
from hakoniwa_core.modules.market import Market
from hakoniwa_core.modules.politics import _nationalize_all
from hakoniwa_core.modules.upgrade import Upgrade
from hakoniwa_core.state import ACCOUNTING_SELF, OWNER_PRIVATE, OWNER_STATE, Facility
from hakoniwa_core.state.serialize import island_from_dict, island_to_dict
from hakoniwa_core.units import money_of, to_milli
from tests.core.test_founding import plan_for
from tests.core.test_recession_round1 import context
from tests.core.test_rules import add, home, make_island, run, with_economy
from tests.core.test_rules import game as game  # noqa: F401 （試験の部品を使い回す）


def _plant(game, quantity=4000):
    f = Facility(id=1, kind="thermal_power_plant", x=5, y=5, built=True)
    stock.add(f.stock, "coal", quantity, 0, game.defs.goods)
    power_fuel.ensure_book(game.defs, f, {"coal": 100, "fuel": 200})
    return f


def test_power_fuel_follows_category_and_recipe(game):
    f = _plant(game)
    tables = dict(game.defs.tables)
    tables["facilities"] = {**game.defs.facilities, "another_plant": game.defs.facilities[f.kind]}
    defs = replace(game.defs, tables=tables)
    f.kind = "another_plant"
    assert power_fuel.is_thermal(defs, f)
    assert power_fuel.fuel_goods(defs, f) == {"coal"}
    assert power_fuel.fuel_goods(defs, f, all_recipes=True) == {"coal", "fuel"}
    assert not power_fuel.is_thermal(defs, replace(f, kind="hydro_power_plant"))
    assert not power_fuel.is_thermal(defs, replace(f, kind="steel_mill"))


def test_power_fuel_moving_average_uses_paid_price_without_freight(game):
    f = _plant(game, quantity=to_milli(1))
    power_fuel.acquire(game.defs, f, "coal", to_milli(1), 300, 1)
    stock.add(f.stock, "coal", to_milli(1), 1, game.defs.goods)
    item = f.economy[power_fuel.BOOK]["coal"]
    assert item == {"quantity": to_milli(2), "value": 400}
    assert power_fuel.consume(game.defs, f, "coal", to_milli(1.5), 1) == 300
    stock.take(f.stock, "coal", to_milli(1.5))
    assert item == {"quantity": to_milli(0.5), "value": 100}
    flow = f.economy[power_fuel.FLOW]
    assert flow["acquired"]["coal"] == to_milli(1)
    assert flow["used"]["coal"] == to_milli(1.5)
    assert flow["value"]["coal"] == 0


def test_power_floor_and_exit_profit_use_cheap_inventory_but_bids_use_current_price(game):
    defs, config = game.defs, game.config
    f = _plant(game)
    prices = {**initial_prices(defs, config), "coal": 400}
    power = config.get("power.good")
    floor = unit_floor(defs, config, f, power, prices, 0, 0)
    current = unit_floor(defs, config, f, power, prices, 0, 0, fuel_prices={})
    assert floor < current
    assert variable_profit(defs, config, f, prices, 0, 0) > variable_profit(
        defs, config, f, prices, 0, 0, fuel_prices={}
    )
    limit = input_limit(defs, config, f, "coal", prices, 0, 0)
    power_fuel.reset_book(defs, f, prices)
    assert input_limit(defs, config, f, "coal", prices, 0, 0) == limit
    assert unit_floor(defs, config, f, power, prices, 0, 0) == current


def test_power_floor_weights_only_fuel_needed_for_next_operation(game):
    f = _plant(game, quantity=2000)
    prices = {"coal": 400, "fuel": 200}
    f.economy["input_plan"] = fac.FULL // 2
    assert power_fuel.input_prices(game.defs, game.config, f, prices)["coal"] == 100
    assert power_fuel.input_prices(
        game.defs, game.config, f, prices, utilization=fac.FULL
    )["coal"] == 250
    prices["coal"] = 50
    assert power_fuel.input_prices(game.defs, game.config, f, prices)["coal"] == 50


def test_power_floor_returns_to_current_price_when_inventory_runs_out(game):
    f = _plant(game)
    prices = {**initial_prices(game.defs, game.config), "coal": 400}
    power = game.config.get("power.good")
    current = unit_floor(game.defs, game.config, f, power, prices, 0, 0, fuel_prices={})
    quantity = stock.total(f.stock, "coal")
    power_fuel.consume(game.defs, f, "coal", quantity, 1)
    stock.take(f.stock, "coal", quantity)
    assert f.economy[power_fuel.BOOK]["coal"] == {"quantity": 0, "value": 0}
    assert unit_floor(game.defs, game.config, f, power, prices, 0, 0) == current


def test_power_plant_sells_with_old_fuel_after_a_price_spike(game):
    island = make_island(game)
    island.regime["economy"] = "mixed"
    home(game, island, 6, 6, 8000, level="city")
    f = add(island, "thermal_power_plant", 5, 6, stock={"coal": 12000})
    add(island, "mine", 4, 6, recipe="coal")
    with_economy(game, island, groundwork=True)
    power = game.config.get("power.good")
    old_value = f.economy[power_fuel.BOOK]["coal"]["value"]
    island.economy.prices["coal"] *= 100
    assert unit_floor(
        game.defs,
        game.config,
        f,
        power,
        island.economy.prices,
        f.economy["wage"],
        0,
        fuel_prices={},
    ) > island.economy.prices[power]
    run(game, island, ["employment", "production"])
    assert f.operating_rate > 0
    assert f.economy["sales"][power][1] > 0
    assert f.economy[power_fuel.FLOW]["used"]["coal"] > 0
    assert f.economy[power_fuel.BOOK]["coal"]["value"] < old_value


def test_power_fuel_accounts_charge_average_cost_and_book_inventory(game):
    island = make_island(game)
    f = add(island, "thermal_power_plant", 5, 5, stock={"coal": 4000})
    with_economy(game, island)
    power_fuel.reset_book(game.defs, f, {"coal": 100, "fuel": 200})
    island.economy.prices["coal"] = 400
    power_fuel.consume(game.defs, f, "coal", 2000, 1)
    stock.take(f.stock, "coal", 2000)
    books.stock_moved(island, f.id, "coal", -2000)
    power_fuel.acquire(game.defs, f, "coal", 1000, 400, 1)
    stock.add(f.stock, "coal", 1000, 1, game.defs.goods)
    books.stock_moved(island, f.id, "coal", 1000)
    books.add(island, f.id, books.PURCHASES, 400)
    ctx = context(game)
    inventory = Accounts._book_inventory(island, ctx)[f.id]
    assert inventory == 200  # 買った400 − 使った200
    assert books.value_added({books.PURCHASES: 400, books.INVENTORY: inventory}) == -200
    Accounts().run_island(island, ctx)
    row = next(b for b in island.books if b.facility == f.id)
    assert row.inventory == 600


def test_power_fuel_warehouse_receipt_enters_average_at_price_paid(game):
    island = make_island(game)
    home(game, island, 6, 6, 2000)
    f = add(island, "thermal_power_plant", 5, 6, stock={"coal": 2000})
    with_economy(game, island)
    power_fuel.reset_book(game.defs, f, {"coal": 100, "fuel": 200})
    island.economy.prices["coal"] = 400
    stock.add(island.warehouse, "coal", 2000, 0, game.defs.goods)
    Market()._supply_facilities_from_warehouse(island, context(game))
    assert stock.total(f.stock, "coal") == 4000
    assert f.economy[power_fuel.BOOK]["coal"] == {"quantity": 4000, "value": 1000}
    assert f.economy[power_fuel.FLOW]["acquired"]["coal"] == 2000


def test_power_fuel_accounts_include_inventory_loss_without_calling_it_burning(game):
    island = make_island(game)
    f = add(island, "thermal_power_plant", 5, 5, stock={"coal": 4000})
    with_economy(game, island)
    power_fuel.reset_book(game.defs, f, {"coal": 100, "fuel": 200})
    island.economy.prices["coal"] = 400
    power_fuel.consume(game.defs, f, "coal", 1000, 1)
    stock.take(f.stock, "coal", 1000)
    books.stock_moved(island, f.id, "coal", -1000)
    lost = stock.take(f.stock, "coal", 3000)  # 戦災と同じく、残った在庫を失う
    books.stock_moved(island, f.id, "coal", -lost)
    assert Accounts._book_inventory(island, context(game))[f.id] == -400
    flow = f.economy[power_fuel.FLOW]
    assert flow["used"]["coal"] == 1000
    assert flow["acquired"] == {}
    assert f.economy[power_fuel.BOOK]["coal"] == {"quantity": 0, "value": 0}


def test_power_fuel_reconciles_stock_before_next_purchase(game):
    f = _plant(game, quantity=1000)
    stock.add(f.stock, "coal", 1000, 0, game.defs.goods)
    power_fuel.acquire(game.defs, f, "coal", 1000, 400, 1)
    stock.add(f.stock, "coal", 1000, 1, game.defs.goods)
    assert f.economy[power_fuel.BOOK]["coal"] == {"quantity": 3000, "value": 900}
    assert f.economy[power_fuel.FLOW]["acquired"]["coal"] == 1000


def test_power_fuel_founding_and_legacy_upgrade_start_at_the_right_price(game):
    island = found_island(game.defs, game.config, plan_for(game), 1, 3, 4)
    f = next(f for f in island.facilities if power_fuel.is_thermal(game.defs, f))
    initial = initial_prices(game.defs, game.config, island.resources)
    quantity = stock.total(f.stock, "coal")
    assert quantity > 0
    assert f.economy[power_fuel.BOOK]["coal"]["value"] == money_of(initial["coal"], quantity)
    f.economy.pop(power_fuel.BOOK)
    island.economy.prices["coal"] = 400
    loaded = island_from_dict(island_to_dict(island))
    Upgrade().run_island(loaded, context(game))
    restored = loaded.facility(f.id)
    assert restored.economy[power_fuel.BOOK]["coal"] == {
        "quantity": quantity,
        "value": money_of(400, quantity),
    }


def test_power_fuel_sale_revalues_even_when_receiver_keeps_operating(game):
    island = make_island(game)
    f = add(island, "thermal_power_plant", 5, 5, stock={"coal": 4000})
    with_economy(game, island)
    power_fuel.reset_book(game.defs, f, {"coal": 100, "fuel": 200})
    island.economy.prices["coal"] = 400
    restart(island, context(game), f, OWNER_PRIVATE, 1000, running=True)
    assert f.economy[power_fuel.BOOK]["coal"] == {"quantity": 4000, "value": 1600}


def test_power_fuel_regime_nationalization_revalues_existing_stock(game):
    island = make_island(game)
    island.regime["economy"] = "mixed"
    home(game, island, 6, 6, 2000)
    f = add(
        island,
        "thermal_power_plant",
        5,
        6,
        stock={"coal": 4000},
        owner=OWNER_PRIVATE,
        accounting=ACCOUNTING_SELF,
    )
    with_economy(game, island)
    power_fuel.reset_book(game.defs, f, {"coal": 100, "fuel": 200})
    island.economy.prices["coal"] = 400
    _nationalize_all(island, context(game))
    assert f.owner == OWNER_STATE
    assert f.economy[power_fuel.BOOK]["coal"] == {"quantity": 4000, "value": 1600}
