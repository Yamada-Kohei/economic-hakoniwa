"""国庫直轄の通常の買いと、採算に基づく買い上がりを分ける。"""

import pytest

from hakoniwa_core.economy import funds
from hakoniwa_core.economy.prices import input_limit
from hakoniwa_core.market import auction
from hakoniwa_core.modules.market import MERCHANT, Market, Party, net_limit
from hakoniwa_core.state import ACCOUNTING_SELF, Facility
from hakoniwa_core.units import part, to_basis
from tests.core.test_recession_round1 import context
from tests.core.test_rules import add, home, make_island, with_economy
from tests.core.test_rules import game as game  # noqa: F401


@pytest.mark.parametrize("freight", [0, 250, 3333])
def test_public_facility_alone_does_not_raise_the_price_every_turn(game, monkeypatch, freight):
    island = make_island(game)
    home(game, island, 5, 5, 6000)
    mill = add(island, "steel_mill", 9, 9)
    with_economy(game, island)
    island.regime["economy"] = "market"
    island.facilities = [mill]
    prices = island.economy.prices
    prices["coal"] = 1000
    prices["steel"] = 10**9  # 採算の余地が大きくても、自分だけでは値段をつり上げない。
    mill.economy["wage"] = 0
    monkeypatch.setattr(Market, "_input_needs", staticmethod(lambda *_: [("coal", 10_000)]))
    width = to_basis(game.config.get("market.band"))
    market = Market()
    for turn in range(1, 61):
        ctx = context(game, turn)
        assert input_limit(game.defs, game.config, mill, "coal", prices, 0, freight) > 2000
        books = {}
        market._facility_orders(island, ctx, books, {mill.id: freight})
        book = books["coal"]
        assert len(book.buys) == 1
        low, _ = auction.band(prices["coal"], width)
        merchant = Party(MERCHANT, "merchant", book.buys[0][1].payer)
        book.sells.append((auction.Order(1000, low), merchant))  # 輸入の売りが少しだけ出る。
        result = auction.clear(
            [o for o, _ in book.buys], [o for o, _ in book.sells], prices["coal"], width
        )
        assert result.volume == 1000
        assert result.price == 1000
        # 量が足りなかった場合も、通常の不足判定を通して次のターンに渡す。
        market._record_shortages(books, {"coal": result}, {})
        for _, party in book.buys:
            funds.release(island, party.payer, party.reserved)
        market._record(island, ctx, books, {"coal": result}, {})
        market._update_prices(island, ctx, books, {"coal": result})
        assert prices["coal"] == 1000


@pytest.mark.parametrize("freight", [0, 250, 3333])
def test_public_facility_buys_at_current_price_even_when_unprofitable(game, freight):
    ctx = context(game)
    f = Facility(id=1, kind="steel_mill", x=9, y=9, built=True)
    prices = {"coal": 1000, "steel": 0, "iron_ore": 1000}
    usual, most = Market._bid(ctx, f, "coal", prices, freight)
    assert net_limit(usual, freight, 0) == prices["coal"]
    assert most < usual
    assert Market._step_up(usual, most, usual * 2, 1000) == usual
    assert usual == prices["coal"] + part(prices["coal"], freight)


def test_public_facility_only_competes_up_to_break_even(game):
    ctx = context(game)
    f = Facility(id=1, kind="steel_mill", x=9, y=9, built=True)
    prices = {"coal": 1000, "steel": 100_000, "iron_ore": 1000}
    usual, most = Market._bid(ctx, f, "coal", prices, 0)
    assert usual == prices["coal"] < most
    assert Market._step_up(usual, most, most, 1000) == most
    # 独立採算の施設は既存どおり通常の値幅増しまで買える。
    f.accounting = ACCOUNTING_SELF
    private_usual, private_most = Market._bid(ctx, f, "coal", prices, 0)
    assert private_usual == auction.band(prices["coal"], 1000)[1]
    assert private_most == most


@pytest.mark.parametrize("kind", ["school", "university"])
def test_public_services_keep_buying_at_the_band_top(game, kind):
    """学校と大学は今までどおり今の値段の値幅増しで買う（教育費の予算で買う。国庫直轄の買い上がりの
    直しには含めない）。"""
    f = Facility(id=1, kind=kind, x=9, y=9, built=True)
    usual, most = Market._bid(context(game), f, "consumer_goods", {"consumer_goods": 1000}, 0)
    assert usual == most == auction.band(1000, 1000)[1]
