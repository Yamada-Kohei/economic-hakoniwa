"""試し遊び1回目（2026-10-20、新しい世界の 50 ターン）で見つかった、計算のコアの直し。"""

from __future__ import annotations

import copy
import random
from types import SimpleNamespace

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import (
    GENERAL,
    JOBLESS,
    OWNERS,
    STATE_FACILITIES,
    Payer,
    funds,
    household,
)
from hakoniwa_core.log import format_log
from hakoniwa_core.modules.firms import privatize
from hakoniwa_core.modules.market import Market
from hakoniwa_core.modules.population import FED, HUNGER, Population
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.state import ACCOUNTING_TREASURY, OWNER_PRIVATE, OWNER_STATE, Facility
from hakoniwa_core.trade import island_state
from hakoniwa_core.trade import traders as trade_traders
from tests.core.test_finance import context, island_of, order
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401
from tests.core.test_rules import home, make_island, with_economy


def _bloc(island, price: int, on_sale: bool) -> dict:
    return {
        "name": "圏",
        "currency": island.id,
        "members": 2,
        "rep": island.id,
        "cells": 2,
        "prices": {"steel": price},
        "supply": ["steel"] if on_sale else [],
        "short": [],
    }


def test_imports_go_to_the_bloc_that_had_sellers(game, warmed):
    """2 つの圏に入った国の商社は、前のターンに売りのあった圏から輸入する（売りのない圏のほうが
    安く見えても、そこで買い続けると約定しない）。どの圏にも売りがなければ、一番安い圏で待つ。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    price = island.economy.prices["steel"]
    blocs = island_state(island)["blocs"] = {
        "1": _bloc(island, price // 4, on_sale=False),
        "2": _bloc(island, price // 2, on_sale=True),
    }
    assert trade_traders.prospects(island, game.defs, game.config)["steel"].import_bloc == 2
    blocs["2"]["supply"] = []
    assert trade_traders.prospects(island, game.defs, game.config)["steel"].import_bloc == 1


def test_privatize_that_owners_cannot_pay_keeps_the_treasury_accounting(game, warmed):
    """資本家が払えない民営化は、会計を独立採算に変えて運転資金を出す前に断る（試し遊びでは、
    断られた後も独立採算のまま残り、国の施設の資金が減っていた）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f = next(
        f
        for f in island.facilities
        if f.owner == OWNER_PRIVATE
        and f.built
        and f.kind in ("farm", "lumber_camp", "fishing_port")
        and "foreclosed" not in f.economy
        and "closing" not in f.economy
    )
    assert order(game, world, island, "nationalize", {"x": f.x, "y": f.y}).status == "done"
    f.accounting = ACCOUNTING_TREASURY
    owners = household(OWNERS)
    ledger.pay(island, owners, household(GENERAL), ledger.money(island, owners), "layer_transfer")
    capital = Payer(ledger.GOV, STATE_FACILITIES)
    before = funds.available(island, capital)
    with pytest.raises(OrderFailed) as failed:
        privatize(island, {"x": f.x, "y": f.y}, context(game, world))
    assert failed.value.reason == "reason_owners_short"
    assert f.owner == OWNER_STATE and f.accounting == ACCOUNTING_TREASURY
    assert funds.available(island, capital) == before


def test_sites_stop_waiting_once_the_materials_arrive(game):
    """市場や倉庫から次の1段分の資材が届いた工事は、そのターンのうちに「資材待ち」が消える。"""
    island = make_island(game)
    home(game, island, 5, 5, 3000)
    kind = next(
        k
        for k, d in sorted(game.defs.facilities.items())
        if len(d.get("construction", {}).get("materials", {})) >= 2 and not d.get("site")
    )
    site = Facility(id=99, kind=kind, x=9, y=9)
    island.facilities.append(site)
    need = fac.step_materials(game.defs, site)
    first, second = sorted(g for g, q in need.items() if q > 0)[:2]
    site.ext["waiting"] = [first, second]
    stock.add(site.stock, first, need[first], 0, game.defs.goods)
    ctx = SimpleNamespace(defs=game.defs)
    Market._refresh_waiting(island, ctx)
    assert site.ext["waiting"] == [second]
    stock.add(site.stock, second, need[second], 0, game.defs.goods)
    Market._refresh_waiting(island, ctx)
    assert "waiting" not in site.ext


def test_department_store_on_a_town_says_it_needs_a_commercial_zone(game):
    """百貨店を町のマスに置こうとすると、「施設がある」ではなく「商業地にだけ建てられる」と出る。"""
    island = make_island(game)
    home(game, island, 8, 8, 3000)
    problem = fac.placement_problem(game.defs, island, "department_store", 8, 8)
    assert problem[0] == "reason_zone_required"


def test_retail_policy_log_names_the_policy(game):
    """大きな店の出店の規制のログは、規制の名前（「許可制」など）で書く。"""
    text = format_log(game.defs, "retail_policy_set", {"policy": "large_store_permit"})
    assert "許可制" in text and "large_store" not in text


def test_jobless_who_flee_hunger_are_counted_in_the_log(game):
    """飢えて村へ移った人のうち、仕事のない人とその家族の人数もログに書く（村では自給の農業を
    するので失業率から外れる。試し遊びでは、失業者が急に減ったわけが分からなかった）。"""
    island = make_island(game)
    home(game, island, 9, 9, 4000)
    home(game, island, 4, 4, 1000, "village")
    home(game, island, 12, 12, 1000, "village")
    with_economy(game, island)
    jobless = island.economy.households.setdefault(JOBLESS, {})
    jobless[FED] = 0
    jobless[HUNGER] = game.config.get("population.hunger")["flee"]
    ctx = SimpleNamespace(defs=game.defs, config=game.config)
    free = {(4, 4): 1400, (12, 12): 1400}
    moved = Population._flee_famine(island, ctx, random.Random(1), free)
    assert moved > 0
    entry = island.log[-1]
    assert entry.kind == "famine_flight_jobless"
    assert entry.args["count"] == moved and 0 < entry.args["jobless"] <= moved
    text = format_log(game.defs, entry.kind, entry.args)
    assert str(entry.args["jobless"]) in text and "失業者には数えません" in text


def test_warehouse_buy_with_no_budget_is_refused_at_the_check(game):
    """国の倉庫の買い入れ費の予算が 0 のときの買い注文は、確かめで「予算が 0」と知らせる（試し遊び
    では「買い注文を出しました」と出たまま、何も買えていなかった）。"""
    from hakoniwa_core.orders import check_order

    island = make_island(game)
    home(game, island, 5, 5, 3000)
    with_economy(game, island)
    buy = {"good": "coal", "side": "buy", "quantity": 5, "limit": 10**6}
    island.policy.budget["warehouse"] = 0
    result = check_order(game.defs, game.registry, island, "warehouse_trade", buy)
    assert result.reason == "reason_warehouse_no_budget"
    island.policy.budget["warehouse"] = 10_000
    assert check_order(game.defs, game.registry, island, "warehouse_trade", buy).ok
