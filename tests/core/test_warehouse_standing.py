"""国の倉庫の決まった売り買い（FS-28・FS-29）の境目。"""

from __future__ import annotations

import json

import pytest

from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import Payer, funds, standing
from hakoniwa_core.economy.freight import Routes
from hakoniwa_core.market import auction
from hakoniwa_core.military import forces
from hakoniwa_core.modules.market import FACILITY, HOUSEHOLD, Market, Party
from hakoniwa_core.orders import check_order
from hakoniwa_core.state.serialize import island_from_dict, island_to_dict
from hakoniwa_core.units import money_of, to_milli
from tests.core.test_recession_round1 import context, reserve_island
from tests.core.test_rules import add, make_island, with_economy
from tests.core.test_rules import game as game  # noqa: F401


def island_for(game):
    island = with_economy(game, make_island(game))
    island.regime["economy"] = "market"
    island.policy.budget["warehouse"] = 10**9
    ledger.create(island, ledger.GOV, ledger.TREASURY, ledger.CB, 10**9, "test")
    return island


def put(game, island, good="iron_ore", kind="buy", quantity=10, limit=100, **kw):
    payload = {"good": good, "kind": kind, "quantity": quantity, "limit": limit, **kw}
    result = check_order(game.defs, game.registry, island, "set_warehouse_standing", payload)
    assert result.ok, result
    standing.execute(island, payload, context(game))
    return standing.settings(island)[good] if kind != "stop" else None


def orders(game, island, turn=1):
    standing.expire(island, turn)
    funds.fiscal_state(island)["caps"]["warehouse"] = island.policy.budget["warehouse"] // 12
    books = {}
    Market()._warehouse_orders(island, context(game, turn), books)
    return books


def clear(island):
    island.scratch.clear()


def test_standing_buys_every_turn_and_expires(game):
    island = island_for(game)
    put(game, island, turns=2)
    for turn in (1, 2):
        assert orders(game, island, turn)["iron_ore"].buys[0][0].quantity == to_milli(10)
        clear(island)
    assert "iron_ore" not in orders(game, island, 3)
    assert standing.settings(island) == {}
    assert any(e.kind == "standing_expired" for e in island.log)


def test_standing_buys_share_the_remaining_budget_equally(game):
    island = island_for(game)
    put(game, island, "iron_ore")
    put(game, island, "coal")
    island.policy.budget["warehouse"] = 1000 * 12
    book = orders(game, island)
    assert [book[g].buys[0][0].quantity for g in ("coal", "iron_ore")] == [5000, 5000]
    assert funds.available(island, Payer(ledger.GOV, "warehouse")) == 0


def test_replacing_an_expired_setting_logs_expiry_before_installing(game):
    island = island_for(game)
    put(game, island, turns=1)
    payload = {"good": "iron_ore", "kind": "buy", "quantity": 20, "limit": 100}
    standing.execute(island, payload, context(game, 2))
    assert [entry.kind for entry in island.log][-2:] == ["standing_expired", "standing_set"]


def test_grain_refill_is_reserved_before_standing_buys(game):
    island = reserve_island(game)
    put(game, island, "iron_ore", quantity=10000, limit=10000)
    grain_price = island.economy.prices["grain"]
    island.policy.budget["warehouse"] = money_of(grain_price, 1000) * 12
    book = orders(game, island)
    assert book["grain"].buys[0][0].quantity > 0
    assert "iron_ore" not in book


def test_standing_sale_keeps_military_and_queued_materials(game):
    island = island_for(game)
    stock.add(island.warehouse, "steel", to_milli(1000), 0, game.defs.goods)
    forces.state_of(island)["reserve"] = {"steel": to_milli(100)}
    site = add(island, "machinery_plant", 6, 5, built=False)
    from hakoniwa_core.autoplan import _reserved_materials

    works = _reserved_materials(island, game.defs).get("steel", 0)
    assert works > 0 and not site.built
    put(game, island, "steel", "sell", quantity=2000, limit=0)
    order = orders(game, island)["steel"].sells[0][0]
    assert order.quantity == max(0, to_milli(900) - works)


def test_reserve_buys_to_target_and_releases_without_buying(game):
    island = island_for(game)
    stock.add(island.warehouse, "coal", 3000, 0, game.defs.goods)
    item = put(game, island, "coal", "reserve", quantity=10, limit=100, release=200)
    island.economy.prices["coal"] = 100
    assert orders(game, island)["coal"].buys[0][0].quantity == 7000
    clear(island)
    island.economy.prices["coal"] = 200
    book = orders(game, island, 2)["coal"]
    assert book.buys == [] and book.sells[0][0].limit == 200
    assert item["releasing"]
    clear(island)
    island.economy.prices["coal"] = 100
    orders(game, island, 3)
    assert [e.kind for e in island.log if "release_" in e.kind] == [
        "standing_release_on",
        "standing_release_off",
    ]


def test_grain_standing_replaces_target_prices_and_rotates(game):
    island = reserve_island(game)
    item = put(game, island, "grain", "reserve", quantity=10, limit=100, release=1000)
    life = game.defs.goods["grain"]["shelf_life"]
    turn = life - game.config.get("food_reserve.rotate")
    stock.add(island.warehouse, "grain", 10000, 0, game.defs.goods)
    island.economy.prices["grain"] = 100
    book = orders(game, island, turn)["grain"]
    assert book.sells[0][0].quantity == book.buys[0][0].quantity == 10000
    assert book.sells[0][0].limit == book.buys[0][0].limit == 100
    assert item["last"].get("paused") is None


def test_manual_grain_trade_pauses_setting_but_keeps_famine_release(game):
    from hakoniwa_core.modules.population import FED

    island = reserve_island(game)
    item = put(game, island, "grain", "reserve", quantity=10, limit=100, release=1000)
    stock.add(island.warehouse, "grain", 10000, 1, game.defs.goods)
    island.economy.households["general"][FED] = 500
    island.economy.ext["warehouse_orders"] = [
        {"good": "grain", "side": "buy", "quantity": 1000, "limit": 100}
    ]
    book = orders(game, island)["grain"]
    assert item["last"]["paused"] == "manual"
    assert len(book.buys) == 1
    assert book.sells[0][0].quantity == 10000


def test_reserve_target_is_kept_from_state_inputs_and_construction(game):
    island = island_for(game)
    plant = add(island, "thermal_power_plant", 6, 5)
    plant.stock.clear()
    put(game, island, "coal", "reserve", quantity=10, limit=100, release=200)
    stock.add(island.warehouse, "coal", 10000, 0, game.defs.goods)
    Market()._supply_facilities_from_warehouse(island, context(game))
    assert stock.total(island.warehouse, "coal") == 10000
    put(game, island, "coal", "stop")
    Market()._supply_facilities_from_warehouse(island, context(game))
    assert stock.total(island.warehouse, "coal") < 10000
    put(game, island, "steel", "reserve", quantity=100, limit=100, release=200)
    stock.add(island.warehouse, "steel", 100000, 0, game.defs.goods)
    add(island, "machinery_plant", 7, 5, built=False)
    Market()._supply_sites_from_warehouse(island, context(game))
    assert stock.total(island.warehouse, "steel") == 100000
    put(game, island, "steel", "stop")
    Market()._supply_sites_from_warehouse(island, context(game))
    assert stock.total(island.warehouse, "steel") < 100000


@pytest.mark.parametrize(
    "kind,side,military,paused",
    [
        ("buy", "buy", False, True),
        ("buy", "sell", False, True),
        ("buy", "buy", True, False),
        ("sell", "buy", True, True),
    ],
)
def test_standing_pauses_for_manual_and_opposite_but_not_military_buy(
    game, kind, side, military, paused
):
    island = island_for(game)
    stock.add(island.warehouse, "coal", 100000, 0, game.defs.goods)
    item = put(game, island, "coal", kind, quantity=10, limit=100)
    island.economy.ext["warehouse_orders"] = [
        {"good": "coal", "side": side, "quantity": 1000, "limit": 100, "military": military}
    ]
    orders(game, island)
    assert ("paused" in item["last"]) == paused


def test_imported_sales_suspend_standing_buys_and_mk39_skips_settings(game):
    from hakoniwa_core import trade

    island = island_for(game)
    stock.add(island.warehouse, "coal", 100000, 0, game.defs.goods)
    item = put(game, island, "coal")
    trade.send_to_market(island, "coal", 1000)
    book = orders(game, island)["coal"]
    assert not book.buys and item["last"]["paused"] == "opposite"
    trade.to_market(island).clear()
    clear(island)
    plant = add(island, "thermal_power_plant", 6, 5)
    plant.economy["short"] = {"coal": "no_sellers"}
    book = orders(game, island)["coal"]
    assert not book.sells


def test_standing_is_last_at_market_upper_edge_and_planned_allocation(game):
    island = island_for(game)
    put(game, island, "coal", limit=100)
    book = orders(game, island)["coal"]
    government = book.buys[0][0]
    assert government.priority == game.config.get("warehouse.standing_priority") < 0
    buys = [auction.Order(1000), government]
    result = auction.clear(buys, [auction.Order(1000, 100)], 100, bounds=(1, 100))
    assert result.bought == [1000, 0]
    hh = Party(HOUSEHOLD, "hh:general", Payer("hh:general"), layer="general")
    state = Party("facility", "gov", Payer("gov"))
    export = Party("merchant", "foreign", Payer("foreign"))
    book.buys = [
        (auction.Order(1000), state),
        (auction.Order(1000), hh),
        (auction.Order(1000), export),
        (government, book.buys[0][1]),
    ]
    bounds, allocate = Market._limits(
        island, context(game), "coal", book, {"coal": 100}, {"general": 1}
    )
    assert bounds == (100, 100)
    assert allocate(3000, [(i, o.quantity, o.priority) for i, (o, _) in enumerate(book.buys)]) == {
        0: 1000,
        1: 1000,
        2: 1000,
        3: 0,
    }


def test_planned_reserve_releases_on_previous_shortage_at_official_price(game):
    island = island_for(game)
    item = put(game, island, "coal", "reserve", limit=100, release=200)
    island.ext["plan"] = {"prices": {"coal": 150}}
    item["unmet"] = 100
    stock.add(island.warehouse, "coal", 10000, 0, game.defs.goods)
    book = orders(game, island)["coal"]
    assert not book.buys and book.sells[0][0].limit == 150


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"good": "unknown", "kind": "buy", "quantity": 1, "limit": 100}, "reason_unknown_good"),
        (
            {"good": "electricity", "kind": "buy", "quantity": 1, "limit": 100},
            "reason_unknown_good",
        ),
        ({"good": "coal", "kind": "buy", "quantity": 1}, "reason_limit_required"),
        (
            {"good": "coal", "kind": "reserve", "quantity": 1, "limit": 100, "release": 100},
            "reason_standing_release",
        ),
        (
            {"good": "fish", "kind": "reserve", "quantity": 1, "limit": 100, "release": 200},
            "reason_standing_life",
        ),
        ({"good": "coal", "kind": "sell"}, "reason_standing_quantity"),
    ],
)
def test_standing_rejects_invalid_orders(game, payload, reason):
    result = check_order(
        game.defs, game.registry, island_for(game), "set_warehouse_standing", payload
    )
    assert not result.ok and result.reason == reason


def test_standing_rejects_controlled_prices_and_honors_rotate_setting(game, monkeypatch):
    island = island_for(game)
    island.regime["economy"] = "mixed"
    island.policy.price_cap["coal"] = 150
    island.policy.price_floor["coal"] = 110
    payload = {"good": "coal", "kind": "reserve", "quantity": 1, "limit": 100, "release": 200}
    assert (
        check_order(game.defs, game.registry, island, "set_warehouse_standing", payload).reason
        == "reason_standing_cap"
    )
    payload["release"] = 150
    assert (
        check_order(game.defs, game.registry, island, "set_warehouse_standing", payload).reason
        == "reason_standing_floor"
    )
    sale = {"good": "coal", "kind": "sell", "quantity": 1, "limit": 200}
    assert (
        check_order(game.defs, game.registry, island, "set_warehouse_standing", sale).reason
        == "reason_standing_cap"
    )
    life = game.defs.goods["grain"]["shelf_life"]
    monkeypatch.setitem(game.config.raw["food_reserve"], "rotate", life)
    payload = {"good": "grain", "kind": "reserve", "quantity": 1, "limit": 100, "release": 200}
    assert (
        check_order(game.defs, game.registry, island, "set_warehouse_standing", payload).reason
        == "reason_standing_life"
    )


def test_standing_survives_snapshot_changes_and_stop(game):
    island = island_for(game)
    put(game, island)
    clone = island_from_dict(json.loads(json.dumps(island_to_dict(island))))
    assert standing.settings(clone) == standing.settings(island)
    put(game, clone, quantity=20)
    assert standing.settings(clone)["iron_ore"]["quantity"] == 20000
    put(game, clone, kind="stop")
    assert not standing.settings(clone)
    assert [e.kind for e in clone.log][-2:] == ["standing_changed", "standing_stopped"]


def test_zero_budget_warns_once_and_result_is_kept_without_trade_log(game):
    island = island_for(game)
    item = put(game, island)
    island.policy.budget["warehouse"] = 0
    for turn in (1, 2):
        book = orders(game, island, turn)
        Market._warehouse_results(island, context(game, turn), book, {})
        clear(island)
    assert sum(e.kind == "standing_no_budget" for e in island.log) == 1
    assert item["last"] == {"turn": 2, "offered": 0, "filled": 0, "spent": 0, "reason": "budget"}
    assert not any(e.kind == "warehouse_buy_result" for e in island.log)


def test_standing_records_actual_settlement_and_money_rounding(game):
    """2組の支払いをそれぞれ切り捨てる。合計量から代金を計算し直すと1だけ違う境目。"""
    island = island_for(game)
    item = put(game, island, "wood", quantity=0.002, limit=1000)
    books = orders(game, island)
    for x in (6, 7):
        seller = add(island, "lumber_camp", x, 5, stock={"wood": 1})
        party = Party(FACILITY, "gov", Payer("gov"), facility=seller)
        books["wood"].sells.append((auction.Order(1, 500), party))
    book = books["wood"]
    result = auction.clear(
        [o for o, _ in book.buys], [o for o, _ in book.sells], 500, bounds=(1, 1000)
    )
    assert result.volume == 2 and money_of(result.price, result.volume) == 1
    market = Market()
    market._settle(
        island, context(game), "wood", book, result, ("gov", None), {}, Routes(island, game.defs)
    )
    market._warehouse_results(island, context(game), books, {"wood": result})
    assert stock.total(island.warehouse, "wood") == 2
    assert item["last"]["filled"] == 2 and item["last"]["spent"] == 0


def test_standing_reports_treasury_and_military_cuts(game):
    island = island_for(game)
    buy = put(game, island, "coal")
    ledger.cancel(
        island, ledger.GOV, ledger.TREASURY, ledger.CB, ledger.money(island, ledger.GOV), "test"
    )
    book = orders(game, island)
    Market._warehouse_results(island, context(game), book, {})
    assert buy["last"]["reason"] == "treasury"
    clear(island)
    sell = put(game, island, "steel", "sell", quantity=20)
    stock.add(island.warehouse, "steel", 10000, 0, game.defs.goods)
    forces.state_of(island)["reserve"] = {"steel": 10000}
    book = orders(game, island)
    Market._warehouse_results(island, context(game), book, {})
    assert sell["last"]["offered"] == 0 and sell["last"]["reason"] == "military"
