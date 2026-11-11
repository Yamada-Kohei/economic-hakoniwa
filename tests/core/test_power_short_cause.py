"""電力を要る分だけ買えなかった施設の「わけ」（公開世界の島2 の不具合）。

発電が需要より 2,000MWh 多く売れ残っているのに、鉱山と繊維工場が「電力不足」と出ていた。電気料金が
採算に合わずに買わなかった分と、お金が足りずに少なく注文した分まで「電力不足」にしていたため。
財の市場（_record_shortages）と同じく、高くて買わなかったのは「原料が割高（電力）」、お金が足りな
かったのは「資金不足（電力）」、発電が足りなかったときだけ「電力不足」にする。"""

from __future__ import annotations

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import payer_of
from hakoniwa_core.game import load_game
from hakoniwa_core.log import format_log
from tests.core.test_rules import add, home, make_island, run, with_economy


@pytest.fixture(scope="module")
def game():
    return load_game()


def _grid(game, owner="private"):
    """発電所 1 つと、電力をその一部だけ使う民営（独立採算）の機械工場のある市場経済の島。"""
    island = make_island(game)
    island.regime["economy"] = "market"
    home(game, island, 6, 6, 8000, level="city")
    plant = add(island, "thermal_power_plant", 5, 6, stock={"coal": 4000})
    user = add(
        island,
        "machinery_plant",
        6,
        5,
        stock={"steel": 4000},
        owner=owner,
        accounting="self_supporting",
    )
    with_economy(game, island, groundwork=True)
    run(game, island, ["employment"])
    return island, plant, user


def test_power_left_over_runs_the_factory_in_full(game):
    island, _, user = _grid(game)
    run(game, island, ["production"])
    assert (user.operating_rate, user.status) == (1000, "")
    assert island.stats["power.capacity"] > island.stats["power.demand"]


def test_factory_that_will_not_pay_the_price_is_not_short_of_power(game):
    """作った物が安く、電気料金を払うと赤字になる工場は買わない。発電は余っているので電力不足ではない。"""
    island, _, user = _grid(game)
    island.economy.prices["machinery"] = 1
    run(game, island, ["production"])
    assert (user.operating_rate, user.status) == (0, "input_price:electricity")
    args = {"facility": user.kind, "x": user.x, "y": user.y, "good": "electricity"}
    text = format_log(game.defs, "facility_stopped_input_price", args)
    assert "電力が高く採算が合わない" in text
    assert island.stats["power.capacity"] > island.stats["power.demand"]


def test_factory_short_of_money_is_not_short_of_power(game):
    island, _, user = _grid(game)
    payer = payer_of(game.defs, game.config, user)
    keep = 100_000  # 電気代の半分ほど
    ledger.pay(island, payer.entity, ledger.GOV, ledger.money(island, payer.entity) - keep, "test")
    run(game, island, ["production"])
    assert 0 < user.operating_rate < 1000
    assert user.status == "funds:electricity"


def test_outbid_when_every_plant_sold_out_is_still_short_of_power():
    """発電が全部売れたときは、値段が上がって買い負けた施設も電力不足（見直し役が挙げた場面。国庫
    直轄の施設は指値が今の電気料金なので、停電で値段が上がるといつも買い負ける）。"""
    from hakoniwa_core.market import auction
    from hakoniwa_core.market.auction import Order
    from hakoniwa_core.modules.production import Production, _Buyer

    sells = [Order(50, 95)]
    buys = [Order(50, 110), Order(50, 100)]
    result = auction.clear(buys, sells, 100)
    assert (result.volume, result.supply) == (50, 50)
    buyer = _Buyer("fac:2", None, 50)  # type: ignore[arg-type]
    assert Production._short_power(buyer, buys[1], result.bought[1], result, sells) == "supply"
    # 高い発電所の売りが残り、指値がそれより安い施設は割高
    sells = [Order(50, 95), Order(50, 120)]
    result = auction.clear(buys, sells, 100)
    assert result.volume < result.supply
    assert Production._short_power(buyer, buys[1], result.bought[1], result, sells) == "price"


def test_autoplan_looks_at_the_rate_of_factories_that_will_not_pay_for_power():
    """電気料金が合わずに止まった施設は、電力不足と同じく稼働率で見る（同じ施設を建て増さない）。"""
    from types import SimpleNamespace

    from hakoniwa_core.autoplan import _waits_for_input

    assert not _waits_for_input(SimpleNamespace(status="input_price:electricity"), "electricity")
    assert not _waits_for_input(SimpleNamespace(status="funds:electricity"), "electricity")
    assert _waits_for_input(SimpleNamespace(status="input_price:coal"), "electricity")
