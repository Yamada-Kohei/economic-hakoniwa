"""段7 最終調整の夜の点検（長い年月・でたらめな命令）で見つかった「ターンが止まる不具合」の直し。

どれも世界の状態をその場で作り、止まっていた呼び出しを直に確かめる。
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.economy import Payer, banks, books, entity_of, funds, is_bank
from hakoniwa_core.economy import public_housing as pub
from hakoniwa_core.modules.accounts import Accounts
from hakoniwa_core.modules.construction import demolish
from hakoniwa_core.modules.population import (
    Housing,
    build_housing,
    dwelling_status,
    homes_of,
)
from tests.core.test_balance_round7 import base as base  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_balance_round7 import game as game  # noqa: F401
from tests.core.test_balance_round7 import market_island, town_of
from tests.core.test_finance import context


def test_demolishing_a_town_takes_its_shops_with_it(game, base):
    """町を取り壊しても店だけが残ると、属する町のない店の位置で送電網の引きが KeyError で落ちた
    （でたらめな命令の夜の点検。取り壊しの命令は人手で出せる）。"""
    from hakoniwa_core.modules.production import Production

    world = copy.deepcopy(base)
    island = market_island(world)
    town = town_of(game, island)
    x, y = fac.position(town)
    shop = next(f for f in island.facilities if f.kind == "shop")
    shop.ext[fac.SITE] = [x, y]
    if shop not in island.facilities:
        island.facilities.append(shop)
    ctx = SimpleNamespace(config=game.config, defs=game.defs, turn=world.turn + 1)
    demolish(island, {"x": x, "y": y}, ctx)
    assert town not in island.facilities
    assert not [f for f in island.facilities if f.ext.get(fac.SITE) == [x, y]]
    # 施設の位置を送電網に引ける（店の属する町のマスが送電網にある）
    grid_of = Production._grids(island, game.defs)
    assert all(fac.position(f) in grid_of for f in island.facilities if f.ext.get(fac.SITE))


def test_a_founded_town_has_a_dwelling_status_even_when_materials_are_short(game, base):
    """命令 build_housing で町を新しく置いたのに資材が足りず床が増えないターンも、町の状態を
    数えておく（町が育つかの見立てが町のマスの状態を引いて KeyError で落ちた）。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    kind = fac.housing_kind(game.config)
    site = Housing._empty_site(island, game.defs, kind, [(t.x, t.y) for t in island.tiles])
    assert site is not None
    ctx = context(game, world)
    build_housing(island, {"x": site[0], "y": site[1], "floors": 300}, ctx)
    homes = homes_of(island, game.config)
    status = dwelling_status(game.defs, game.config, island, homes)
    assert site not in homes
    Housing()._run_works(
        island, game.defs, game.config, game.config.get("housing"), homes, status, set(), set()
    )
    assert site in homes
    assert site in status
    assert pub.works_pending(island)  # 資材が足りないので工事は残る


def test_a_bank_pays_no_more_dividend_than_it_holds_in_cash(game, base, monkeypatch):
    """銀行の配当は自己資本の余裕（貸出や債券の値打ちを含む）から出すが、払えるのは払える額まで
    （funds.available）。長い年月の夜の点検で、お金が桁あふれに近い島の銀行の配当が払える額を
    超えて FundsError で落ちた（払える額の上限は 64 ビットの整数の大きさ）。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    ctx = context(game, world)
    bank = next(f for f in island.facilities if is_bank(ctx.config, f))
    payer = Payer(entity_of(ctx.config, bank))
    cash = funds.available(island, payer)
    monkeypatch.setattr(banks, "net_worth", lambda *a, **k: 10**19)
    monkeypatch.setattr(banks, "capital_target", lambda *a, **k: 0)
    before = ledger.money(island, payer.entity)
    tax, dividend = Accounts()._settle(island, ctx, bank, {books.PROFIT: 4 * cash})
    assert dividend <= cash
    assert before - ledger.money(island, payer.entity) <= cash  # 税と配当で現金を使い切らない
