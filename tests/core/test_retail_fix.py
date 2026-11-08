"""FM-38・FM-58・FM-59: 量で数える小売り、店の消耗品、店の建設費。"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.economy import GENERAL, household, retail
from hakoniwa_core.economy.funds import Payer
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.modules import firms
from hakoniwa_core.modules.firms import EntryExit
from hakoniwa_core.modules.market import HOUSEHOLD, Market, Party
from hakoniwa_core.modules.production import Production
from hakoniwa_core.state import Facility
from hakoniwa_core.units import money_of
from tests.core.test_balance_round7 import base as base
from tests.core.test_balance_round7 import game as game
from tests.core.test_balance_round7 import market_island
from tests.core.test_finance import context


@pytest.fixture
def sample(game, base):
    world = copy.deepcopy(base)
    return market_island(world), context(game, world)


def entry_setup(monkeypatch, game, sample):
    """出店の合図と候補選びを単独で確かめる。資金・場所の審査は通す。"""
    island, ctx = sample
    island.facilities = [f for f in island.facilities if f.kind not in ("shop", "department_store")]
    island.economy.finance["shortages"] = {}
    island.economy.finance["demand"] = {}
    monkeypatch.setattr(firms, "average_prices", lambda *a: {})
    monkeypatch.setattr(EntryExit, "_threshold", lambda *a: 0)
    monkeypatch.setattr(EntryExit, "_buy_foreclosed", lambda *a: False)
    monkeypatch.setattr(EntryExit, "_site", lambda *a: ((3, 3), 0))
    monkeypatch.setattr(EntryExit, "_supplied", lambda *a: set(game.defs.goods))
    monkeypatch.setitem(ctx.formulas, "entry_return", lambda *a: 10)
    built = []

    def build(self, island, ctx, kind, recipe, pos, *args):
        f = Facility(id=island.next_facility_id(), kind=kind, recipe=recipe, x=-30, y=-30)
        island.facilities.append(f)
        built.append(kind)
        return None

    monkeypatch.setattr(EntryExit, "_build", build)
    return island, ctx, built


@pytest.mark.parametrize("kind", ["shop", "department_store"])
@pytest.mark.parametrize("have", [0, 5, 1000])
def test_optional_packaging_does_not_limit_shop_production(monkeypatch, game, sample, kind, have):
    island, ctx = sample
    shop = copy.deepcopy(next(f for f in island.facilities if f.kind == "shop" and f.built))
    shop.kind = kind
    shop.workers = fac.workers_needed(game.defs, kind)
    shop.economy = {"target": fac.FULL}
    shop.stock = {"consumer_goods": [[ctx.turn, have]]} if have else {}
    island.facilities = [shop]
    power = game.config.get("power.good")
    assert fac.input_ratio(game.defs, shop, power) == (fac.FULL, None)
    assert fac.labor_demand(game.defs, shop, ctx.turn, power) == shop.workers
    # 電力の確保を通し、消耗品の有無による産出と在庫の減りを確かめる。
    monkeypatch.setattr(Production, "_power_market", lambda *a: {})
    Production().run_island(island, ctx)
    need = fac.recipe_of(game.defs, shop)["inputs"]["consumer_goods"]
    assert shop.operating_rate == fac.FULL
    assert stock.total(shop.stock, "consumer_goods") == max(0, have - need)
    full = fac.recipe_of(game.defs, shop)["outputs"]["retail"]
    assert stock.total(shop.stock, "retail") >= full * fac.productivity_of(shop) // fac.FULL


def test_packaging_shortage_does_not_block_department_store_entry(monkeypatch, game, sample):
    island, ctx, built = entry_setup(monkeypatch, game, sample)
    island.economy.finance["shortages"] = {"retail": [10**9] * 6, "consumer_goods": [10**9] * 6}
    monkeypatch.setattr(
        EntryExit, "_supplied", lambda *a: set(game.defs.goods) - {"consumer_goods"}
    )
    recipe = game.defs.facilities["department_store"]["recipes"]["default"]
    assert (
        EntryExit._inputs_short(
            recipe, island.economy.finance["shortages"], game.config.get("firms")
        )
        is None
    )
    assert EntryExit._inputs_capped(island, ctx, recipe, {"electricity": 10**9}, {}, {}, {}) is None
    # 百貨店の利益率を高くして、候補として採用されることまで確かめる。
    monkeypatch.setitem(
        ctx.formulas, "entry_return", lambda *a: 100 if a[2] == "department_store" else 1
    )
    blocks = []
    assert EntryExit()._try_enter(island, ctx, blocks)
    assert built == ["department_store"]
    assert not any(b[2] in ("inputs_missing", "inputs_short", "inputs_capped") for b in blocks)


def test_retail_quantity_ignores_food_price_and_doubles_with_volume(game, sample):
    island, ctx = sample
    buyer = Party(HOUSEHOLD, household(GENERAL), Payer(household(GENERAL)), 0, 0)
    buyer.layer = GENERAL
    island.scratch[retail.UNRETAILED] = ("services", "retail")

    def buy(price, qty, cpi):
        island.scratch.pop(retail.SPENT, None)
        island.economy.prices["grain"] = price
        island.economy.cpi_history = [cpi]
        Market._give_to_buyer(
            island, "grain", buyer, [[ctx.turn, qty]], money_of(price, qty), 0, 0, {}, ctx
        )
        return retail.units_for(island, game.config, island.scratch[retail.SPENT][GENERAL])

    assert buy(100, 10_000, 100) == buy(500, 10_000, 180)
    assert buy(500, 20_000, 180) == 2 * buy(500, 10_000, 180)
    prices = initial_prices(game.defs, game.config, island.resources)
    assert island.scratch[retail.SPENT][GENERAL] == money_of(prices["grain"], 10_000)


def test_initial_retail_reserve_uses_quantity_basis(game, sample):
    island, ctx = sample
    island.economy.households[GENERAL].pop(retail.SHARE, None)
    before = Market._retail_keep(island, ctx, GENERAL, 100_000, 0)
    island.economy.cpi_history = [250]
    assert Market._retail_keep(island, ctx, GENERAL, 100_000, 0) == before
    assert before > 0


def test_retail_uses_default_when_base_price_is_missing(monkeypatch, game, sample):
    island, ctx = sample
    buyer = Party(HOUSEHOLD, household(GENERAL), Payer(household(GENERAL)), layer=GENERAL)
    monkeypatch.setattr(retail, "base_prices", lambda *a: {})
    Market._give_to_buyer(island, "grain", buyer, [[ctx.turn, 1000]], 999999, 0, 0, {}, ctx)
    assert island.scratch[retail.SPENT][GENERAL] == game.config.get("market.initial_prices.default")


def test_shops_are_lighter_than_factories_per_worker(game):
    """店の建設費は働く人1人あたりで製造業より軽い（FM-59。上場企業の労働装備率の中央値で小売業は
    鉄鋼の 0.41 倍）。商店は製鉄所の半分より軽く、百貨店も製鉄所より軽い。"""
    from hakoniwa_core.economy.prices import construction_cost

    prices = initial_prices(game.defs, game.config, [])

    def per_worker(kind):
        cost = construction_cost(game.defs, kind, prices, 1000)
        return cost / fac.workers_needed(game.defs, kind)

    steel = per_worker("steel_mill")
    assert per_worker("shop") < steel / 2
    assert per_worker("shop") < per_worker("department_store") < steel
