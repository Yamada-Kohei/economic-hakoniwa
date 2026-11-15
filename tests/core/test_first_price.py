"""相場のない品の初値（MK-43。公開世界の掲示板 #25 の 64 番、倭国さんの電子機器工場）。

島で直近 market.unlisted_turns ターンに一度も約定しなかった財は、買いたい人がいるのに売りに
出なかったとき、作る施設の1単位の原価に決まった儲け（market.initial_prices.markup）を乗せた値段
（輸入が引き合う値段があれば低いほう）まで1ターンで上がる。前は1ターンに値幅ずつしか上がらず、
原料の機械が電子機器の値段に比べて高いまま、新しい工場が何ターンも「原料が割高」で止まった。
"""

from __future__ import annotations

from types import SimpleNamespace

from hakoniwa_core.economy import freight as freight_rates
from hakoniwa_core.economy.prices import input_limit, unit_floor
from hakoniwa_core.market import auction
from hakoniwa_core.modules.market import LAST_TRADE, Market
from hakoniwa_core.state import ACCOUNTING_SELF, OWNER_PRIVATE, WorldState
from hakoniwa_core.units import round_money, to_basis
from tests.core.test_finance import context
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import add, home, make_island, with_economy


def _plant_island(game):
    """町が1つ、民営の電子機器工場が1つ（建ったばかり）の市場経済の島。機械は値上がりし、一度も
    売り買いされていない電子機器の値段は古く安いまま。"""
    island = make_island(game)
    home(game, island, 9, 9, 4000)
    with_economy(game, island)
    island.regime["economy"] = "market"
    plant = add(island, "electronics_plant", 6, 6, owner=OWNER_PRIVATE)
    plant.accounting = ACCOUNTING_SELF
    prices = island.economy.prices
    prices["machinery"] *= 2
    prices["electronics"] //= 2
    return island, plant


def _ctx_of(game, island, turn=100):
    return context(game, WorldState(turn=turn, seed=1, islands={island.id: island}), "test")


def _cost_plus(game, island, plant):
    cost = unit_floor(
        game.defs,
        game.config,
        plant,
        "electronics",
        island.economy.prices,
        plant.economy.get("wage", 0),
        freight_rates.facility_rates(island, game.defs, game.config).get(plant.id, 0),
    )
    return round_money(cost * (1 + game.config.get("market.initial_prices.markup")))


def test_unlisted_good_starts_at_cost_plus_margin(game):
    """相場のない電子機器は、1ターンで工場の原価＋1割まで上がる。その値段なら工場は今の値段の
    機械を買える（原料の買いの上限が機械の値段を上回る）。"""
    island, plant = _plant_island(game)
    ctx = _ctx_of(game, island)
    prices = island.economy.prices
    low = prices["electronics"]
    wage = plant.economy.get("wage", 0)
    machine = prices["machinery"]
    assert input_limit(game.defs, game.config, plant, "machinery", prices, wage, 0) < machine
    target = _cost_plus(game, island, plant)
    assert target > auction.band(low, to_basis(game.config.get("market.band")))[1]
    island.scratch["wanted"] = {"electronics": 1000}
    Market._record(island, ctx, {}, {}, {})
    assert prices["electronics"] == target
    assert input_limit(game.defs, game.config, plant, "machinery", prices, wage, 0) > machine


def test_listed_good_still_rises_one_band_a_turn(game):
    """直近に約定した品（相場のある品）は、今までどおり1ターンに値幅ずつ（MK-13）。"""
    island, _ = _plant_island(game)
    ctx = _ctx_of(game, island)
    prices = island.economy.prices
    low = prices["electronics"]
    island.economy.ext[LAST_TRADE] = {"electronics": ctx.turn - 3}
    island.scratch["wanted"] = {"electronics": 1000}
    Market._record(island, ctx, {}, {}, {})
    assert prices["electronics"] == auction.band(low, to_basis(game.config.get("market.band")))[1]


def test_good_unsold_for_two_years_is_unlisted_again(game):
    """market.unlisted_turns ターン約定がなければ、相場のない品に戻る。"""
    island, plant = _plant_island(game)
    ctx = _ctx_of(game, island)
    island.economy.ext[LAST_TRADE] = {
        "electronics": ctx.turn - game.config.get("market.unlisted_turns")
    }
    island.scratch["wanted"] = {"electronics": 1000}
    Market._record(island, ctx, {}, {}, {})
    assert island.economy.prices["electronics"] == _cost_plus(game, island, plant)


def test_unlisted_starts_exactly_at_the_turns_line(game):
    """最後の約定から market.unlisted_turns − 1 ターンはまだ相場のある品（値幅ずつ）、ちょうどの
    ターンから相場のない品（仮の線。掲示板のレビュー #495〜）。"""
    island, _ = _plant_island(game)
    ctx = _ctx_of(game, island)
    turns = game.config.get("market.unlisted_turns")
    island.economy.ext[LAST_TRADE] = {"electronics": ctx.turn - (turns - 1)}
    assert "electronics" not in Market._unlisted(island, ctx, {})
    island.economy.ext[LAST_TRADE] = {"electronics": ctx.turn - turns}
    assert "electronics" in Market._unlisted(island, ctx, {})


def test_costs_use_the_prices_before_this_turns_moves(game):
    """同じターンに原料（機械）の値段も上げたとき、製品（電子機器）の初値の原価は、上げる前の
    機械の値段で見る（つながった品を1ターンに重ねて跳ねさせない。次のターンに追う）。"""
    island, plant = _plant_island(game)
    add(island, "machinery_plant", 3, 3, owner=OWNER_PRIVATE).accounting = ACCOUNTING_SELF
    ctx = _ctx_of(game, island)
    prices = island.economy.prices
    prices["machinery"] //= 4  # 機械も古く安い値段のまま品切れ（初値で上がる）
    expected = _cost_plus(game, island, plant)
    machinery = prices["machinery"]
    island.scratch["wanted"] = {"electronics": 1000, "machinery": 1000}
    Market._record(island, ctx, {}, {}, {})
    assert prices["machinery"] > machinery  # 機械は上がった
    assert prices["electronics"] == expected  # 電子機器は上げる前の機械の値段で見た


def test_import_price_caps_the_first_price(game, monkeypatch):
    """商社の輸入が引き合う値段が原価＋1割より安ければ、そちら（輸入品より高くは売れない）。"""
    from hakoniwa_core.modules import market as market_module

    island, plant = _plant_island(game)
    ctx = _ctx_of(game, island)
    low = island.economy.prices["electronics"]
    target = _cost_plus(game, island, plant)
    imported = (low + target) // 2
    monkeypatch.setattr(market_module.traders, "traders_of", lambda *a: True)
    monkeypatch.setattr(
        market_module.traders,
        "prospects",
        lambda *a: {"electronics": SimpleNamespace(import_floor=imported, export_bloc=None)},
    )
    island.scratch["wanted"] = {"electronics": 1000}
    Market._record(island, ctx, {}, {}, {})
    assert island.economy.prices["electronics"] == imported
    # 輸入の値段まで上げても輸入されず品切れが続く（外貨・船が足りないなど）なら、次は輸入の値段で
    # 抑えず原価＋1割まで
    Market._record(island, ctx, {}, {}, {})
    assert island.economy.prices["electronics"] == target


def test_price_cap_stops_the_first_price(game):
    """混合経済で値段の上限を決めた財は、上限で止める。"""
    island, plant = _plant_island(game)
    island.regime["economy"] = "mixed"
    ctx = _ctx_of(game, island)
    low = island.economy.prices["electronics"]
    target = _cost_plus(game, island, plant)
    cap = (low + target) // 2
    island.policy.price_cap["electronics"] = cap
    island.scratch["wanted"] = {"electronics": 1000}
    Market._record(island, ctx, {}, {}, {})
    assert island.economy.prices["electronics"] == cap


def test_planned_island_keeps_its_prices(game):
    """計画経済の島は公定価格なので、初値を置かない（今までどおり）。"""
    island, _ = _plant_island(game)
    island.regime["economy"] = "planned"
    ctx = _ctx_of(game, island)
    assert Market._unlisted(island, ctx, {}) == set()


def test_no_maker_falls_back_to_one_band(game):
    """島に作る施設がない相場のない品は、今までどおり値幅ずつ（作り始める値段・参入の値段まで）。"""
    island, plant = _plant_island(game)
    plant.economy["closing"] = 1  # 閉じようとしている工場は作らない
    ctx = _ctx_of(game, island)
    assert Market._first_price(island, ctx, "electronics", island.economy.prices, {}, 0) is None


def test_trades_are_remembered_and_old_saves_are_seeded(game):
    """約定した財は最後の約定のターンを残す。記録のない前からの世界は、売り買いの跡のある財
    （家計の買った量・施設の在庫・売れた量）を、そのターンに約定したと置く。"""
    island, plant = _plant_island(game)
    ctx = _ctx_of(game, island)
    island.economy.bought_history["grain"] = [0, 5, 0]
    plant.economy["sales"] = {"electronics": [10, 4, 0]}
    island.economy.ext.pop(LAST_TRADE, None)
    traded = SimpleNamespace(volume=3)
    unlisted = Market._unlisted(island, ctx, {"fish": traded})
    table = island.economy.ext[LAST_TRADE]
    assert table["grain"] == table["electronics"] == table["fish"] == ctx.turn
    assert not {"grain", "electronics", "fish"} & unlisted
    assert unlisted  # 跡のない財は相場のない品


def test_stop_log_names_both_the_product_and_the_material(game):
    """電力でない原料が高くて止まったログは、売り値の品と原料の両方を書く（倭国さんの案 2）。
    電力の行は今までどおり。"""
    from hakoniwa_core.log import format_log
    from hakoniwa_core.modules.production import Production

    island, plant = _plant_island(game)
    plant.operating_rate = 1000
    Production._update_status(
        island, plant, 0, "input_price:machinery", ["electronics"], "electricity"
    )
    entry = island.log[-1]
    assert entry.kind == "facility_stopped_unprofitable"
    text = format_log(game.defs, entry.kind, entry.args)
    assert "電子機器の売り値" in text and "機械の値段に見合わず" in text
    plant.operating_rate = 1000
    plant.ext.pop("stop_causes", None)
    Production._update_status(
        island, plant, 0, "input_price:electricity", ["electronics"], "electricity"
    )
    assert island.log[-1].kind == "facility_stopped_input_price"
