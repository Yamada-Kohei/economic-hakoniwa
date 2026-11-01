"""価格の決まり方の直しの第2弾（2026-11-01）: 指値を出す人の損得から作る。"""

from __future__ import annotations

from hakoniwa_core.economy.prices import (
    SITE_USUAL,
    adapt_materials,
    input_limit,
    material_limit,
    variable_profit,
)
from hakoniwa_core.economy.stats import basket, update_cpi
from hakoniwa_core.state import OWNER_STATE
from hakoniwa_core.state.model import PRICE_CARRY, PRICE_QUOTE, PRICE_TRADE
from hakoniwa_core.units import to_basis
from tests.core.test_rules import add, home, make_island, with_economy
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）


def test_input_limit_reads_the_product_at_todays_price(game):
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    mill = add(island, "steel_mill", 9, 9, owner=OWNER_STATE)
    with_economy(game, island)
    defs, config = game.defs, game.config
    prices = dict(island.economy.prices)
    wage = mill.economy["wage"]
    for good in ("coal", "iron_ore"):
        limit = input_limit(defs, config, mill, good, prices, wage, 0)
        assert limit > 0
        # 上限の値段で買えば、製品を今の値段で売って変動費の利益がほぼ 0（1 割高く売れるとは見ない）
        at = variable_profit(defs, config, mill, prices | {good: limit}, wage, 0)
        above = variable_profit(defs, config, mill, prices | {good: limit + 2}, wage, 0)
        assert at >= 0 > above


def test_sites_re_estimate_to_the_cheapest_seller_after_waiting(game):
    island = make_island(game)
    with_economy(game, island)
    config = game.config
    width = to_basis(config.get("market.band"))
    good = "machinery"
    turns = config.get("household.reprice_turns")
    usual = material_limit(island, config, good, width)
    ask = usual * 13 // 10  # 作り手の指値が工事の指値より 3 割高い
    for _ in range(turns - 1):
        adapt_materials(island, config, width, {good: ask}, {})
        assert material_limit(island, config, good, width) == usual  # まだ待つ
    adapt_materials(island, config, width, {good: ask + 50}, {})
    assert material_limit(island, config, good, width) == ask  # その間の一番安い売りの指値
    # 売りのないターンは数えず、積算し直した値段のまま
    for _ in range(turns * 2):
        adapt_materials(island, config, width, {good: 0}, {})
    assert material_limit(island, config, good, width) == ask
    # 買えた後に売り手が下げたら、積算し直した値段も下げる
    lower = usual * 12 // 10
    adapt_materials(island, config, width, {good: lower}, {good: 1000})
    assert material_limit(island, config, good, width) == lower
    # 公定価格の財は積算し直さない
    island.ext.setdefault("plan", {})["prices"] = {good: usual}
    assert material_limit(island, config, good, width) < lower
    island.ext["plan"]["prices"] = {}
    # 約定が続いて値段が上がり、ふだんの指値が届いたら外す
    island.economy.prices[good] = lower
    island.economy.price_history[good] = [lower] * 12
    adapt_materials(island, config, width, {good: lower}, {good: 1000})
    assert good not in island.economy.ext.get(SITE_USUAL, {})


def test_only_cars_that_can_be_paid_for_count_as_wanted(game):
    from hakoniwa_core.modules.market import Market
    from tests.core.test_recession_round1 import context

    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    config = game.config
    car = config.get("household.cars.good")
    price = island.economy.prices[car]
    persons, income = 1000, 3.0  # 欲しい台数のある層

    def wanted_with(spend):
        island.scratch.clear()
        Market()._car_orders(
            island, context(game), {}, "general", persons, income, spend, 0, 0, {}, {}, (1, 1)
        )
        return island.scratch.get("wanted", {}).get(car, 0)

    share = config.get("household.cars.share")
    assert wanted_with(0) == 0  # 払えなければ欲しい量に数えない
    some = wanted_with(round(price * 3 / share))  # 3 台ほど払える
    assert 0 < some <= 3 * 1000
    assert wanted_with(price * 10**6) > some  # 払えれば欲しい台数まで


def test_market_records_keep_the_kind_of_price(game):
    """市場の記録に値段の種類を残す（W-227）: 売買があれば約定、両側の注文が折り合わなければ気配
    （海外の商人の買いも数える）、片側だけなら持ち越し。"""
    from types import SimpleNamespace

    from hakoniwa_core.modules.market import _kind

    def result(volume, demand, supply):
        return SimpleNamespace(volume=volume, demand=demand, supply=supply)

    assert _kind(result(5, 10, 10)) == PRICE_TRADE
    assert _kind(result(0, 10, 10)) == PRICE_QUOTE
    assert _kind(result(0, 0, 10)) == PRICE_CARRY
    assert _kind(result(0, 10, 0)) == PRICE_CARRY


def test_prices_without_a_trade_stay_out_of_the_cpi(game):
    """約定しなかった財は、最後に約定した値段で物価指数に入る（気配で物価が動かない）。"""
    island = make_island(game)
    with_economy(game, island)
    economy, defs, config = island.economy, game.defs, game.config
    goods = basket(defs)
    prices = {g: economy.prices.get(g, 100) or 100 for g in goods}
    bought = dict.fromkeys(goods, 1000)
    base = update_cpi(economy, defs, config, prices, bought, dict(prices))
    good = goods[0]
    quoted = prices | {good: prices[good] * 3}  # 取引のないまま気配が 3 倍に
    traded = {g: p for g, p in prices.items() if g != good}
    assert update_cpi(economy, defs, config, quoted, bought, traded) == base
    assert economy.price_history[good][-1] == quoted[good]  # 基準の値段の記録は今までどおり
    # 約定すると、その値段で物価に入る
    assert update_cpi(economy, defs, config, quoted, bought, dict(quoted)) > base
