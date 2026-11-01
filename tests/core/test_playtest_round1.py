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
from hakoniwa_core.trade import island_state, traders_of
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


def test_exports_stay_in_the_bloc_where_their_cargo_is_listed(game, warmed):
    """売れ残った輸出の荷は、売りに出した圏で売り続ける（ほかの圏が高く見えても移らない）。輸入が
    売りのあった圏へ来るので、両方が前のターンの相手の圏へ移って入れ違い続けることがない。荷が
    なければ一番高く売れる圏。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    price = island.economy.prices["steel"]
    island_state(island)["blocs"] = {
        "1": _bloc(island, price * 2, on_sale=True),
        "2": _bloc(island, price * 3, on_sale=False),
    }
    assert trade_traders.prospects(island, game.defs, game.config)["steel"].export_bloc == 2
    f = traders_of(island, game.config)[0]
    item = trade_traders.entry(f, "steel")
    item["dir"], item["bx"], item["held"] = trade_traders.EXPORT, 1, [5_000, 5_000 * price]
    assert trade_traders.prospects(island, game.defs, game.config)["steel"].export_bloc == 1
    item["held"] = [0, 0]  # 売り切ったら選び直す
    assert trade_traders.prospects(island, game.defs, game.config)["steel"].export_bloc == 2


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
    for budget in (0, 11):  # 1 ターンに使えるのは年の予算の 12 分の 1
        island.policy.budget["warehouse"] = budget
        result = check_order(game.defs, game.registry, island, "warehouse_trade", buy)
        assert result.reason == "reason_warehouse_no_budget"
    island.policy.budget["warehouse"] = 10_000
    assert check_order(game.defs, game.registry, island, "warehouse_trade", buy).ok


def test_recipe_errors_list_the_names_to_write(game):
    """生産方式の名前を省いたり間違えたりしたときは、命令に書く名前と表示名を並べて知らせる。"""
    for kind in ("reason_unknown_recipe", "reason_recipe_required"):
        text = format_log(game.defs, kind, {"facility": "consumer_goods_factory"})
        assert "wood（木材から製造）" in text and "default（鋼鉄と木材から製造）" in text, text


def test_public_works_say_whether_the_budget_or_the_treasury_ran_out(game):
    """公共事業費で止まった工事は、年の予算の月割りと国庫のどちらで止まったかを書く（試し遊び
    では、国庫が足りないのに年の予算を何度も上げていた）。"""
    from hakoniwa_core.economy import CONSTRUCTION

    island = make_island(game)
    home(game, island, 5, 5, 3000)
    with_economy(game, island)
    payer = Payer(ledger.GOV, CONSTRUCTION)
    money = funds.available(island, payer, capped=False)
    assert money > 10
    funds.fiscal_state(island)["caps"][CONSTRUCTION] = 10
    assert funds.limit_of(island, payer) == funds.BUDGET_LIMIT
    funds.fiscal_state(island)["caps"][CONSTRUCTION] = money * 2
    assert funds.limit_of(island, payer) == funds.TREASURY_LIMIT
    for limit in (funds.BUDGET_LIMIT, funds.TREASURY_LIMIT):
        assert "年の予算" in format_log(game.defs, f"funds_limit_{limit}", {})


def test_subsidy_without_its_target_says_what_is_missing(game):
    """設備投資補助で財を省くと、「内容が不正」ではなく「財を書いてください」と出る。"""
    from hakoniwa_core.orders import check_order

    island = make_island(game)
    home(game, island, 5, 5, 3000)
    with_economy(game, island)
    payload = {"kind": "capital", "rate": 20, "turns": 12}
    result = check_order(game.defs, game.registry, island, "set_subsidy", payload)
    assert result.reason == "reason_subsidy_needs_good"
    assert "good" in format_log(game.defs, result.reason, {})


def test_build_check_sees_whether_the_site_reaches_a_power_plant(game):
    """建てる前に、そのマスが発電所のある送電網につながるかが分かる（試し遊びでは造船所が完成の
    翌ターンに「送電網につながっていない」で止まった）。電力を使わない施設は None。"""
    from hakoniwa_core.modules.production import reaches_power

    island = make_island(game)
    home(game, island, 3, 3, 1000)
    assert reaches_power(island, game.defs, game.config, "consumer_goods_factory", 10, 10) is False
    plant = Facility(id=50, kind="thermal_power_plant", x=10, y=11, built=True)
    island.facilities.append(plant)
    assert reaches_power(island, game.defs, game.config, "consumer_goods_factory", 10, 10) is True
    assert reaches_power(island, game.defs, game.config, "thermal_power_plant", 12, 12) is None


def test_small_hungry_layers_order_all_they_need(game):
    """飢えた家計の食料の注文は、段の量を累積の差で決めるので、人数の少ない層でも合計がちょうど要る
    量になる（段ごとに切り捨てると、要る量 8 単位の層は 3 単位しか注文せず、飢え続けた。試し遊び
    1回目の報告の 2）。お金で途中までのときも、食費の限りは超えない。"""
    from hakoniwa_core.modules.market import gross_cost, hungry_food_orders
    from hakoniwa_core.modules.population import food_short_reason
    from tests.core.test_balance_round3 import _menu

    settings = game.config.get("household.hungry")
    steps, elasticity = settings["steps"], settings["elasticity"]
    menu = _menu()
    price = 100_000  # 1 単位（1000 分の 1）が 100 になる値段
    menu.limits["grain"] = price
    anchors = {"grain": price}
    for need in (5, 8, 13):
        orders = hungry_food_orders(
            need, {"grain": need}, {"grain": need}, menu, anchors, 0, 10**9, steps, elasticity
        )
        assert sum(q for _, q, _ in orders) == need, (need, orders)
    budget = 900
    orders = hungry_food_orders(
        8, {"grain": 8}, {"grain": 8}, menu, anchors, 0, budget, steps, elasticity
    )
    assert 0 < sum(gross_cost(lim, q, menu.rate, menu.tax) for _, q, lim in orders) <= budget
    # 取り分の切り捨てで数単位欠けただけなら、「物がない」ではなく「買い負けた」
    summary = {"need": 1000, "ordered": 999, "offered": 998, "cost": 10, "budget": 1000}
    assert food_short_reason(summary) == "outbid"


def test_inputs_only_merchants_sell_are_priced_at_the_merchant_price(game, warmed, monkeypatch):
    """作り方の見直しでは、原料のうち島で作られておらず海外の商人からしか買えない財を、
    商人の売り値で見る（島の値段は、だれも買っていなければ初めの値段のままで安く見え、鋼鉄を作って
    いない国で民営の工場が鋼鉄から作る方式に替えて、翌ターン採算が合わずに止まった。報告の決め 1）。
    島で作っている財と、商社が貿易圏から輸入している財と、作る物の値段は島の値段のまま。"""
    from hakoniwa_core.modules.firms import EntryExit
    from hakoniwa_core.trade import merchant, traders

    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    made = EntryExit._made(island, context(game, world))
    absent, imported = [g for g in sorted(game.defs.goods) if g not in made][:2]
    present = next(g for g in sorted(made) if g in game.defs.goods)
    prices = {absent: 100, present: 100}
    offers = [(absent, 1000, 5_000), (present, 1000, 5_000), (imported, 1000, 5_000)]
    monkeypatch.setattr(merchant, "offers", lambda *args: offers)
    monkeypatch.setattr(traders, "imported", lambda *args: {imported})
    imports = EntryExit._import_prices(island, context(game, world))
    assert imports == {absent: 5_000}
    seen = EntryExit._costed(prices, imports, {"inputs": {absent: 1, present: 1}})
    assert seen[absent] == 5_000
    assert seen[present] == 100
    # 作る物は上げない（島で作っていない物がみな商人の値段で売れると見えないように）
    sold = EntryExit._costed(prices, imports, {"inputs": {present: 1}, "outputs": {absent: 1}})
    assert sold[absent] == 100


def test_factories_raise_bids_step_by_step_only_after_being_outbid():
    """工場の原料の指値は、前のターンに買い負けた財だけ、前の指値の market.band 増しまで上げる
    （ふだんの指値より低くせず、利益が0になる値段を超えない）。一度に利益が0になる値段まで買い
    上がると、石炭の値段が1ターンで3倍半に跳ねた（1つの島の 300 ターンの試験）。"""
    width = 1000  # 1割
    assert Market._step_up(110, 500, None, width) == 110  # 買い負けていなければふだんの指値
    assert Market._step_up(110, 500, 200, width) == 220
    assert Market._step_up(110, 500, 480, width) == 500  # 利益が0になる値段まで
    assert Market._step_up(110, 500, 50, width) == 110  # ふだんの指値より低くしない
    assert Market._step_up(1, 10, 1, width) == 2  # 値段が小さくても上がれる


def test_leftover_limit_is_capped_by_the_band_but_not_below_cost():
    """作り方を替えて残った物の売りの指値は前の値段の値幅の上端までだが、その施設で作る費用より
    安くはしない。費用のない物は seller_limit と同じ。"""
    from hakoniwa_core.modules.market import floored_limit, seller_limit

    assert floored_limit(100, 150, 0, 110) == 150
    assert floored_limit(100, 50, 0, 110) == 100
    assert floored_limit(200, 50, 0, 110) == 110  # 希望価格は上端まで
    assert floored_limit(100, 0, 500, 2) == seller_limit(100, 500, 2)
    assert floored_limit(100, 150, 500, 2) == seller_limit(150, 500, 10**9)  # 運賃の分も足す


def test_households_split_small_offers_rounding_up():
    """売りに出ている量を層の使う額の割合で分けるときは切り上げる。商社の輸入の残りのような
    1000 分の 1 単位の端数でも、どれかの層が買いに出せる（切り捨てると、どの層も 0 しか注文
    できず、売りと買いたい量があるのに市場が1年以上止まった）。"""
    from hakoniwa_core.modules.market import layer_share

    assert layer_share(1, 9, 10) == 1
    assert layer_share(10, 9, 10) == 9
    assert layer_share(1000, 1, 3) == 334
    assert layer_share(0, 9, 10) == 0
    assert layer_share(5, 0, 10) == 0


def test_leftover_crops_are_not_sold_below_what_they_cost_to_grow(game, warmed):
    """作り方を替えて残った物は、同じ施設でその物を作る費用より安くは売らない（綿から穀物に
    替えた国の農場が、残りの綿を毎ターン1割安く売り続け、綿の値段が初めの 0.04 倍まで下がった）。
    その施設で作れない物は下限なし。"""
    from dataclasses import replace

    from hakoniwa_core.economy.prices import unit_floor

    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    recipes = game.defs.facilities["farm"]["recipes"]
    farm = next(f for f in island.facilities if f.kind == "farm" and f.built)
    # 手作業の穀物の農場（機械化した農場の下限は、機械化の作り方の中で決まる。W-190）
    farm = replace(farm, recipe="default", ext={})
    other = next(
        n for n, r in sorted(recipes.items()) if n != farm.recipe and r["outputs"].get("cotton")
    )
    prices = dict(island.economy.prices)
    floor = Market._leftover_floor(game.defs, game.config, farm, "cotton", prices, 0)
    wage = farm.economy.get("wage", 0)
    cost = unit_floor(
        game.defs, game.config, replace(farm, recipe=other), "cotton", prices, wage, 0
    )
    assert floor == cost > 0
    assert Market._leftover_floor(game.defs, game.config, farm, "chemicals", prices, 0) == 0


def test_standard_plan_prepares_for_the_merchants_leaving(game, warmed, monkeypatch):
    """標準の計画は、海外の商人が持ってくる量を減らし始めたら（foreign_merchant.taper_from ターン
    を過ぎてから）、前のターンに商人から買った物のうち島で作っていない物を作る施設を建てる（1つの島の
    300 ターンの試験で、商人が去ってから機械を作る施設が建つまで機械が島から消え、値段が初めの
    4倍を超えた）。"""
    from hakoniwa_core import autoplan
    from hakoniwa_core.modules.market import MERCHANT
    from hakoniwa_core.state import TradeRecord
    from hakoniwa_core.trade import merchant

    defs, config = game.defs, game.config

    def planned(island, share: float) -> list[tuple[str, str]]:
        monkeypatch.setattr(merchant, "taper", lambda *args: share)
        return list(autoplan._candidates(island, defs, config, warmed.turn + 1))

    def outputs(f) -> set[str]:
        return set(fac.recipe_of(defs, f)["outputs"])

    # 商人が売る物のうち、作る施設を島から除くと、ほかの決まりでは作る施設が挙がらず、投入財は島で
    # 作っている物（どの島・どの物になるかは世界の流れによるので探す）
    found = None
    for island_id, good in sorted(
        (i, g) for i in warmed.island_ids() for g in config.get("foreign_merchant.goods")
    ):
        world = copy.deepcopy(warmed)
        island = world.islands[island_id]
        island.facilities = [f for f in island.facilities if good not in outputs(f)]
        making = {g for f in island.facilities for g in outputs(f)}
        before = planned(island, 1.0)
        makers = [
            c
            for c in autoplan._producers(defs, config, island, good)
            if autoplan._inputs_at_hand(defs, config, *c, making) and c not in before
        ]
        if makers:
            found = island, good, makers[0]
            break
    assert found is not None, "試せる物がない"
    island, good, maker = found
    island.trades.append(TradeRecord(good, "fac:1", MERCHANT, 10, 1000))
    assert maker not in planned(island, 1.0)  # まだ減らし始めていない
    assert maker in planned(island, 0.9)
    assert maker not in planned(island, 0.0)  # もう去った


def test_trader_export_bids_are_capped_except_at_official_prices(game, warmed, monkeypatch):
    """商社の輸出の買いは、島の市場では前の値段の market.band 増しまで（引き合う値段まで一度に買い
    上がると、島の家計より高く買って食料が1ターンで島の外へ出た）。公定価格の財は公定価格で約定し、
    家計を先にする層の順で分けるので、上限を付けない（付けると計画経済の島の銀行が中央銀行から借り
    続ける世界が増えた。quick の測り）。"""
    from hakoniwa_core.economy import entity_of
    from hakoniwa_core.modules import market as market_module
    from hakoniwa_core.trade import started

    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    assert started(island)
    f = traders_of(island, game.config)[0]
    good = "steel"
    price = island.economy.prices[good]
    bid = price * 3
    entity = entity_of(game.config, f)
    bank = island.economy.deposit_bank or ledger.CB
    ledger.create(island, entity, ledger.DEPOSIT, bank, bid * 10_000, "x")
    monkeypatch.setattr(market_module.merchant, "offers", lambda *args: [])
    monkeypatch.setattr(
        market_module.traders,
        "home_orders",
        lambda *args: [trade_traders.HomeOrder(f, good, "buy", 1_000, bid)],
    )

    def placed() -> int:
        books: dict = {}
        Market()._trade_orders(island, context(game, world), books, {}, 0)
        return max(o.limit for o, p in books[good].buys if p.facility is f)

    capped = placed()
    assert price < capped < bid
    monkeypatch.setattr(market_module.planning, "is_planned", lambda *args: True)
    monkeypatch.setattr(market_module.planning, "official", lambda *args: {good: price})
    assert placed() == bid
