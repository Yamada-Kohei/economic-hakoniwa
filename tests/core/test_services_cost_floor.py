"""サービスの売り手の変動費下限の作り直し（下書き。掲示板 #6010、services.cost_floor は既定で切）。

サービスの変動費は賃金だけだが、商業・サービス業は作り方を持たないので、ほかの施設の
下限（economy/prices.py の unit_floor）は産出が無くて 0 になり、下限が付いてこなかった。
切り替えが入のときだけ、変動費そのもの（賃金 ÷ 1人の量、上乗せなし）を下限として、
希望価格の見直し（_update_prices）と注文の時点（_services_orders）の両方で重ねる。
前の下書き（原価＋1割）は値段が上がって約定しない量が増え、店の儲けを悪くした
（#6007・#6009 の分解）ので、上乗せを置かない形に作り直した。
切のときは今までと完全に同じでなければならない。
"""

from __future__ import annotations

from hakoniwa_core.economy import services
from hakoniwa_core.market import auction
from hakoniwa_core.modules.market import Market
from hakoniwa_core.units import to_basis
from tests.core.test_recession_round1 import context
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import home, make_island, with_economy


def _floor_on(game) -> None:
    """下書きの切り替えを入にする（game は module 共有なので、値は戻さず真偽だけ置く）。"""
    game.config.raw["services"]["cost_floor"] = True


def _floor_off(game) -> None:
    game.config.raw["services"]["cost_floor"] = False


def _shop(game, wage: int, ask: int, price: int):
    """市場の島に店を置き、賃金・希望価格・今の値段を決めた状態にする。"""
    island = make_island(game)
    island.regime["economy"] = "market"
    home(game, island, 5, 5, 3000)
    with_economy(game, island)
    f = services.prepare(island, game.defs, game.config)
    assert f is not None
    good = services.good_of(game.config)
    f.workers = 10
    f.economy[services.SEATS] = 10
    f.economy["wage"] = wage
    f.economy["ask"] = {good: ask}
    island.economy.prices[good] = price
    return island, f, good


def _placed_limit(game, island, good) -> int:
    books: dict = {}
    Market()._services_orders(island, context(game), books, {})
    sells = books[good].sells
    assert len(sells) == 1
    order, _party = sells[0]
    assert order.limit is not None
    return order.limit


def test_cost_floor_is_off_by_default(game):
    """既定は切: 賃金がいくらでも下限は 0（下限なし）。"""
    _floor_off(game)
    for wage in (1, 1000, 10000, 999999):
        assert services.cost_floor(game.config, wage) == 0


def test_cost_floor_is_variable_cost_when_on(game):
    """入のときの下限は変動費そのもの（賃金 ÷ 1人の量、上乗せなし）。"""
    _floor_on(game)
    try:
        # 1人の量 1 なら、下限は賃金そのもの
        for wage in (1000, 10000, 12345):
            assert services.cost_floor(game.config, wage) == wage
        # 原価＋1割（unit_price）とは違う物差しであること
        assert services.cost_floor(game.config, 10000) == 10000
        assert services.unit_price(game.config, 10000) == 11000
        # 賃金の記録が無いときは下限を付けない
        assert services.cost_floor(game.config, 0) == 0
    finally:
        _floor_off(game)


def test_services_orders_have_no_floor_by_default(game):
    """切: 希望価格が賃金より安くても、そのまま指値になる（今までどおり）。"""
    _floor_off(game)
    island, _f, good = _shop(game, wage=10000, ask=9500, price=10000)
    assert _placed_limit(game, island, good) == 9500


def test_services_orders_are_floored_when_on(game):
    """入: 注文の時点で下限が効く。値幅の中なら下限そのもの、上端を超えるなら上端まで
    （ほかの施設の seller_limit と同じ止まり方）。希望価格が下限より高ければ触らない。"""
    _floor_on(game)
    try:
        # 下限 10000 が値幅（9000〜11000）の中にある場面: 下限まで上がる
        island, _f, good = _shop(game, wage=10000, ask=9500, price=10000)
        assert _placed_limit(game, island, good) == 10000
        # 賃金 9500 → 下限は変動費そのもの 9500
        island, _f, good = _shop(game, wage=9500, ask=9000, price=10000)
        assert _placed_limit(game, island, good) == 9500
        # 下限が値幅の上端を超える場面: 上端で止まる
        island, _f, good = _shop(game, wage=12000, ask=9500, price=10000)
        _low, high = auction.band(10000, to_basis(game.config.get("market.band")))
        assert _placed_limit(game, island, good) == high
        # 希望価格が下限より高い場面: そのまま
        island, _f, good = _shop(game, wage=9500, ask=10800, price=10000)
        assert _placed_limit(game, island, good) == 10800
    finally:
        _floor_off(game)


def _review_ask(game, island, good, offered: int, sold: int, price: int) -> int:
    """売りに出して一部だけ売れたターンの市場の記録を作り、希望価格の見直しを回す。"""
    books: dict = {}
    Market()._services_orders(island, context(game), books, {})
    book = books[good]
    order, _party = book.sells[0]
    assert order.quantity == offered
    low, high = auction.band(price, to_basis(game.config.get("market.band")))
    result = auction.Result(
        price=price,
        volume=sold,
        demand=offered,
        supply=offered,
        low=low,
        high=high,
        bought=[],
        sold=[sold],
    )
    Market()._update_prices(island, context(game), {good: book}, {good: result})
    f = services.facility_of(island, game.config)
    assert f is not None
    return f.economy["ask"][good]


def test_price_review_drops_below_wage_cost_by_default(game):
    """切: 売れ残れば希望価格は賃金を割って下がる（今までどおり）。"""
    _floor_off(game)
    island, _f, good = _shop(game, wage=10000, ask=12000, price=12000)
    # 半分だけ売れた → 値幅いっぱいの -10% で 10800（下限なし）
    assert _review_ask(game, island, good, offered=10000, sold=5000, price=12000) == 10800


def test_price_review_stops_at_floor_when_on(game):
    """入: 希望価格の見直しで、下限（変動費＝賃金そのもの）より下には下がらない。
    下限より上にいる間は触らない（全部売れれば今までどおり上がる）。"""
    _floor_on(game)
    try:
        # 半分だけ売れた → -10% で 9900 まで下がる所を、下限 10000 で止める
        island, _f, good = _shop(game, wage=10000, ask=11000, price=11000)
        assert _review_ask(game, island, good, offered=10000, sold=5000, price=11000) == 10000
        # 全部売れた（品不足なし）なら希望価格は動かず、下限は引き下げにも働かない
        island, _f, good = _shop(game, wage=10000, ask=11000, price=11000)
        assert _review_ask(game, island, good, offered=10000, sold=10000, price=11000) == 11000
    finally:
        _floor_off(game)
