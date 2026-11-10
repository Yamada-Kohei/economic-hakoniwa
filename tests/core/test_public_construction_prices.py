"""工事の価格による待機と、着工時の資材見積り。"""

from types import SimpleNamespace

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import Payer
from hakoniwa_core.economy.prices import material_limit, site_material_cost
from hakoniwa_core.game import load_game
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules.construction import build, start_expansion
from hakoniwa_core.modules.market import MERCHANT, SITE, Book, Market, Party
from hakoniwa_core.state.serialize import island_from_dict, island_to_dict
from hakoniwa_core.units import to_basis
from tests.core.test_rules import add, make_island, with_economy


@pytest.fixture(scope="module")
def game():
    return load_game()


def test_merchant_price_wait_counts_down_and_clears(game):
    island = with_economy(game, make_island(game))
    island.regime["economy"] = "mixed"
    f = add(island, "farm", 6, 6, built=False)
    ctx = SimpleNamespace(defs=game.defs, config=game.config)
    width = to_basis(game.config.get("market.band"))
    limit = material_limit(island, game.config, "wood", width)
    ask = limit * 2
    payer = Payer(ledger.GOV)
    book = Book(
        buys=[(Order(5000, limit, 1), Party(SITE, "site", payer, facility=f))],
        sells=[(Order(5000, ask, 1), Party(MERCHANT, "merchant", payer))],
    )
    turns = game.config.get("household.reprice_turns")
    for elapsed in range(1, turns + 1):
        Market._adapt_materials(island, ctx, {"wood": book})
        assert f.ext["material_price_wait"] == {"wood": turns - elapsed}
        assert f.ext["waiting"] == ["wood"]
    assert material_limit(island, game.config, "wood", width) == ask
    stock.add(f.stock, "wood", 5000, 0, game.defs.goods)
    Market._adapt_materials(island, ctx, {"wood": book})
    assert "material_price_wait" not in f.ext


def test_start_estimate_survives_price_and_progress_changes(game):
    island = with_economy(game, make_island(game))
    ctx = SimpleNamespace(defs=game.defs, config=game.config, turn=1)
    build(island, {"facility": "food_factory", "x": 6, "y": 6}, ctx)
    f = island.facilities[-1]
    estimate = f.ext["material_estimate"]
    assert estimate == site_material_cost(island, game.defs, game.config, f)
    f.progress = fac.FULL
    island.economy.ext["site_usual"] = {"building_materials": {"ref": estimate * 2}}
    assert site_material_cost(island, game.defs, game.config, f, remaining=True) > estimate
    assert f.ext["material_estimate"] == estimate
    saved = island_from_dict(island_to_dict(island))
    assert saved.facility(f.id).ext["material_estimate"] == estimate
    expansion = start_expansion(island, ctx, f)
    assert expansion.ext["material_estimate"] == site_material_cost(
        island, game.defs, game.config, expansion
    )
