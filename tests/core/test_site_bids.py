"""民営の工事の資材を、その事業の採算が合う値段まで買い上がる（W-292）。"""

from types import SimpleNamespace

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy.prices import material_limit
from hakoniwa_core.game import load_game
from hakoniwa_core.modules import firms
from hakoniwa_core.modules.market import Market
from hakoniwa_core.state import OWNER_PRIVATE
from hakoniwa_core.units import money_of, part, to_basis
from tests.core.test_rules import add, make_island, with_economy


@pytest.fixture(scope="module")
def game():
    return load_game()


def _site(game, kind, owner=OWNER_PRIVATE, recipe="default", terrain="forest"):
    island = with_economy(game, make_island(game, terrain={(10, 10): terrain}))
    f = add(island, kind, 10, 10, built=False, owner=owner, recipe=recipe)
    ctx = SimpleNamespace(defs=game.defs, config=game.config, turn=5)
    width = to_basis(game.config.get("market.band"))
    return island, f, ctx, width


def _plan(island, game, f, width):
    return {
        g: (q, material_limit(island, game.config, g, width))
        for g, q in fac.materials_of(game.defs, f).items()
    }


def test_outbid_material_steps_up_by_band(game, monkeypatch):
    island, f, ctx, width = _site(game, "fishing_port")
    plan = _plan(island, game, f, width)
    monkeypatch.setattr(firms, "site_value", lambda *a: 10**15)  # 採算はいくらでも合う
    last = {g: lim for g, (_, lim) in plan.items()}
    out = Market._site_raise(island, ctx, f, plan, last, set(plan), 0, width)
    for g, (q, lim) in plan.items():
        assert out[g] == (q, lim + max(1, part(lim, width)))
    # 買い負けていない資材は上げない
    assert Market._site_raise(island, ctx, f, plan, last, set(), 0, width) == plan


def test_no_raise_when_the_project_cannot_pay(game, monkeypatch):
    island, f, ctx, width = _site(game, "fishing_port")
    plan = _plan(island, game, f, width)
    monkeypatch.setattr(firms, "site_value", lambda *a: 0.0)
    last = {g: lim for g, (_, lim) in plan.items()}
    assert Market._site_raise(island, ctx, f, plan, last, set(plan), 0, width) == plan


def test_two_materials_share_one_budget(game, monkeypatch):
    """資材 2 種が同じターンに全部約定しても、残りの費用が払える額を超えない。"""
    island, f, ctx, width = _site(game, "mine", recipe="coal", terrain="mountain")
    plan = _plan(island, game, f, width)
    assert len(plan) >= 2
    labor = (
        fac.construction_labor(game.defs, f)
        * fac.total_progress(game.defs, f)
        / fac.FULL
        * island.economy.average_wage
    )
    base = sum(money_of(lim, q) for _, (q, lim) in plan.items())
    value = labor + base + base * 0.03  # 上げしろは全部の資材で 3% 分だけ
    monkeypatch.setattr(firms, "site_value", lambda *a: value)
    last = {g: lim for g, (_, lim) in plan.items()}
    out = Market._site_raise(island, ctx, f, plan, last, set(plan), 0, width)
    spent = sum(money_of(lim, q) for _, (q, lim) in out.items())
    assert spent > base  # 上げた
    assert labor + spent <= value + len(plan)  # 共通の払える額に収まる（切り捨ての 1 ずつ）


def test_own_product_material_does_not_run_away(game, monkeypatch):
    """自分の産物を資材にする工事（伐採場の木材）: 払える額は発注の前の平均の値段で決まり、その
    ターンの指値の間は動かないので、上げても払える額で止まる。"""
    island, f, ctx, width = _site(game, "lumber_camp")
    plan = _plan(island, game, f, width)
    value = firms.site_value(island, ctx, f, 0)
    assert value is not None
    lim = plan["wood"][1]
    last = {"wood": lim}
    for _ in range(200):  # 何ターン買い負けても
        out = Market._site_raise(island, ctx, f, plan, last, {"wood"}, 0, width)
        last = {"wood": out["wood"][1]}
        plan = {"wood": (plan["wood"][0], out["wood"][1])}
    labor = (
        fac.construction_labor(game.defs, f)
        * fac.total_progress(game.defs, f)
        / fac.FULL
        * island.economy.average_wage
    )
    qty = fac.materials_of(game.defs, f)["wood"]
    assert labor + money_of(last["wood"], qty) <= max(value, labor + money_of(lim, qty)) + 1


def test_outbid_by_price_is_recorded_on_private_and_state_sites(game):
    """値段で買い負けた工事に印が付く（次のターンに民営だけが上げる。国営は market の発注で
    _site_raise を通らない）。"""
    from hakoniwa_core import ledger
    from hakoniwa_core.economy import Payer
    from hakoniwa_core.market import auction
    from hakoniwa_core.market.auction import Order
    from hakoniwa_core.modules.market import MERCHANT, SITE, SITE_SHORT, Book, Party

    island, f, ctx, width = _site(game, "fishing_port")
    payer = Payer(ledger.GOV)
    book = Book(
        buys=[(Order(5000, 100, 1), Party(SITE, "site", payer, facility=f))],
        sells=[(Order(5000, 200, 1), Party(MERCHANT, "m", payer))],
    )
    result = auction.clear([o for o, _ in book.buys], [o for o, _ in book.sells], 150)
    Market._record_shortages({"wood": book}, {"wood": result}, {})
    assert f.ext[SITE_SHORT] == {"wood": "price"}


def test_split_orders_get_no_bigger_share_and_small_site_still_buys(game):
    """同じ指値・同じ優先順位で並んだ買いは量に比例して分ける（W-285 と共通の by_priority）ので、
    注文を分けても取り分は増えず、小さな工事にも分け前が回る。"""
    from hakoniwa_core.market import auction
    from hakoniwa_core.market.auction import Order

    sells = [Order(1000, 100, 0)]
    whole = auction.clear([Order(9000, 120, 5), Order(1000, 120, 5)], sells, 100)
    split = auction.clear(
        [Order(4500, 120, 5), Order(4500, 120, 5), Order(1000, 120, 5)], sells, 100
    )
    assert whole.bought[0] == split.bought[0] + split.bought[1]
    assert whole.bought[1] == split.bought[2] > 0
