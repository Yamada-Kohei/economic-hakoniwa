"""バランス調整の回7 前半: 家計の支え合い（W-17）と企業（W-14）。

ターンを回す試験は、記録の段が毎ターン帳簿を確かめ、trade_check の段が島をまたぐお金と運ぶ途中の荷を
確かめる（合わなければその場で止まる。tests/core/test_phase3_3.py の run_checked）。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import (
    ENGINEERS,
    GENERAL,
    OWNERS,
    arrears,
    banks,
    facility_entity,
    household,
    is_stopped,
)
from hakoniwa_core.economy import companies as co
from hakoniwa_core.economy.households import SUPPORT, jobless_layer
from hakoniwa_core.economy.start import next_payments
from hakoniwa_core.modules.companies import FirmsFunds, _carry_losses
from hakoniwa_core.modules.failures import cross_default
from hakoniwa_core.modules.labor import Payroll, food_cost
from hakoniwa_core.state import OWNER_PRIVATE
from hakoniwa_core.state.generate import generate_world
from tests.core.test_finance import books_ok, context, open_books
from tests.core.test_phase3_3 import run_checked

BASE_TURNS = 24


@pytest.fixture(scope="module")
def game():
    from hakoniwa_core.game import load_game

    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """標準の計画で BASE_TURNS ターン回した6島（市場・混合・計画経済が2つずつ）。試験ごとに写す。"""
    world = generate_world(1, 6, game.config, game.defs)
    run_checked(game, world, BASE_TURNS)
    return world


def jobless_island(world):
    for i in world.island_ids():
        island = world.islands[i]
        state = island.economy.households.get(jobless_layer(island))
        if state is not None and state["persons"] > 0 and GENERAL in island.economy.households:
            return island
    raise AssertionError("失業している家計のいる島がない")


def drain(island, layer):
    """層の預金を政府に移して 0 にする（帳簿の確かめの前に呼ぶ）。"""
    entity = household(layer)
    ledger.pay(island, entity, ledger.GOV, ledger.money(island, entity), "unemployment_benefit")


# --- 家計の支え合い（W-17） ---------------------------------------------------------


def test_working_families_send_food_money_to_jobless_without_benefit(game, base):
    """手当のない国で、失業している家計が食べ物を買えないとき、働く家計が所得の上限までの範囲で
    足りない分を送り、受けた額は市場の段で食べ物に先に使う。帳簿は合う。"""
    world = copy.deepcopy(base)
    island = jobless_island(world)
    layer = jobless_layer(island)
    ctx = context(game, world)
    drain(island, layer)
    island.economy.households[layer]["income"] = [0, 0, 0]
    open_books(island)
    income = {GENERAL: 10**9, ENGINEERS: 10**9}
    island.scratch["income"] = dict(income)
    before = ledger.money(island, household(GENERAL))
    Payroll._support(island, ctx)
    need = food_cost(island, ctx, island.economy.households[layer]["persons"])
    got = ledger.money(island, household(layer))
    assert need > 0
    assert 0 < got <= need
    assert island.scratch[SUPPORT] == {layer: got}
    assert island.scratch["income"][layer] == got
    assert ledger.money(island, household(GENERAL)) < before
    sent = sum(income[d] - island.scratch["income"][d] for d in income)
    assert sent == got
    books_ok(game, island)


def test_support_is_capped_by_working_income_share(game, base):
    """送るのは、働く家計のこのターンの可処分所得の household.support.share まで。"""
    world = copy.deepcopy(base)
    island = jobless_island(world)
    layer = jobless_layer(island)
    ctx = context(game, world)
    drain(island, layer)
    island.economy.households[layer]["income"] = [0, 0, 0]
    island.scratch["income"] = {GENERAL: 1000}
    Payroll._support(island, ctx)
    share = game.config.get("household.support.share")
    assert ledger.money(island, household(layer)) <= round(1000 * share)


def test_no_support_when_jobless_can_buy_food(game, base):
    """失業している家計が自分のお金で食べ物を買えるなら、送らない。"""
    world = copy.deepcopy(base)
    island = jobless_island(world)
    layer = jobless_layer(island)
    ctx = context(game, world)
    need = food_cost(island, ctx, island.economy.households[layer]["persons"])
    island.economy.households[layer]["income"] = [need * 10] * 3
    ledger.pay(island, ledger.GOV, household(layer), need * 10, "unemployment_benefit")
    island.scratch["income"] = {GENERAL: 10**9}
    Payroll._support(island, ctx)
    assert SUPPORT not in island.scratch


# --- 企業（W-14） ---------------------------------------------------------------


def private_members(game, island):
    """民営の企業に入った、動いていて借入の遅れも未払いもない施設（番号の順）。"""
    found = []
    for f in sorted(island.facilities, key=lambda f: f.id):
        if co.private_firm_of(island, f) is None or not f.built or is_stopped(f):
            continue
        entity = facility_entity(f.id)
        if arrears.total(island, entity) or arrears.total(island, entity, arrears.TAX_ARREARS):
            continue
        found.append(f)
    return found


def market_island(world):
    for i in world.island_ids():
        island = world.islands[i]
        if island.has_economy and island.regime.get("economy") == "market":
            return island
    raise AssertionError("市場経済の島がない")


def merge(island, a, b):
    """施設 b を a の企業に入れる（試験のため。株は動かさない）。"""
    b.economy[co.FIRM] = co.firm_of(a)


def test_private_facilities_belong_to_firms_that_hold_their_shares(game, base):
    """民営の物を作る施設はみな民営の企業に入り、企業が施設の株を、資本家階級が企業の株を持つ。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    found = 0
    for f in island.facilities:
        if f.owner != OWNER_PRIVATE or not co.eligible(game.defs, game.config, f):
            continue
        if "foreclosed" in f.economy:
            continue
        number = co.private_firm_of(island, f)
        assert number is not None, f.kind
        firm = co.entity(number)
        held = ledger.balance(island, ledger.account(firm, ledger.EQUITY, facility_entity(f.id)))
        owners = ledger.balance(island, ledger.account(household(OWNERS), ledger.EQUITY, firm))
        assert held > 0 and owners > 0
        found += 1
    assert found > 0
    assert island.stats["firms.count"] > 0


def test_firm_sweeps_extra_cash_and_supports_short_plant(game, base):
    """企業の資金の段: 手元の多すぎる工場から企業へ上げ、足りない工場へ企業から回す。帳簿は合う。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    a, b = private_members(game, island)[:2]
    merge(island, a, b)
    ctx = context(game, world)
    open_books(island)
    ea, eb = facility_entity(a.id), facility_entity(b.id)
    ledger.pay(island, eb, ea, ledger.money(island, eb), "subsidy")
    ledger.pay(island, ledger.GOV, ea, ledger.money(island, ledger.GOV) // 2, "subsidy")
    FirmsFunds().run_island(island, ctx)
    settings = game.config.get("firms")

    def keep(f):
        entity = facility_entity(f.id)
        return next_payments(island, game.defs, game.config, f) * settings[
            "operating_turns"
        ] + banks.debt_service(island, entity, ctx.turn)

    assert ledger.money(island, ea) == keep(a)
    assert ledger.money(island, eb) >= keep(b) > 0
    rec = co.record(island, co.firm_of(a))
    assert rec[co.SUPPORTED] > 0
    assert island.stats["firms.support"] > 0
    books_ok(game, island)


def test_losses_carry_within_the_firm():
    """連結の赤字は企業で繰り越し、後の利益から古い順に引く。期限を過ぎた赤字は消える。"""
    rec = {}
    assert _carry_losses(rec, -100, 1, 60) == 0
    assert _carry_losses(rec, -50, 2, 60) == 0
    assert _carry_losses(rec, 120, 3, 60) == 0
    assert rec[co.LOSSES] == [[2, 30]]
    assert _carry_losses(rec, 50, 4, 60) == 20
    assert co.LOSSES not in rec
    rec = {co.LOSSES: [[1, 100]]}
    assert _carry_losses(rec, 40, 70, 60) == 40


def test_investor_prefers_firm_with_same_kind(game, base):
    """新しい施設の出資は、預金で出せる企業のうち同じ種類の施設を持つ企業が先に出す。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    f = private_members(game, island)[0]
    number = co.firm_of(f)
    cash = ledger.money(island, ledger.GOV) // 2
    ledger.pay(island, ledger.GOV, co.entity(number), cash, "subsidy")
    chosen = co.choose_investor(island, game.defs, f.kind, cash)
    assert chosen == (co.entity(number), number)
    assert co.choose_investor(island, game.defs, f.kind, cash * 10**6) is None


def test_default_of_one_plant_bankrupts_the_firms_borrowers(game, base):
    """企業の1つの工場の返済の遅れが限度に届くと、同じ企業の借入のある工場もみな倒産する
    （クロスデフォルト）。帳簿は合う。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    borrowers = [
        f
        for f in private_members(game, island)
        if banks.loans_by_borrower(island, facility_entity(f.id))
    ]
    a, b = borrowers[:2]
    merge(island, a, b)
    ctx = context(game, world)
    open_books(island)
    loan = banks.loans_by_borrower(island, facility_entity(a.id))[0]
    loan.arrears = game.config.get("loans.arrears_limit")
    cross_default(island, ctx, a)
    assert "foreclosed" in a.economy and "foreclosed" in b.economy
    assert co.firm_of(a) is None and co.firm_of(b) is None
    assert any(e.kind == "firm_bankrupt" for e in island.log)
    assert island.stats["firms.cascade"] == 2
    books_ok(game, island)


def test_nationalize_and_privatize_whole_firm(game, base):
    """企業の国有化: 政府が企業の預金と施設の純資産の和を資本家階級に払い、施設は国営企業に入る。
    国営企業の民営化: 資本家階級が純資産を払い、施設は1つの新しい民営の企業に入る。帳簿は合う。"""
    from hakoniwa_core.modules.firms import (
        check_nationalize_firm,
        check_privatize_firm,
        nationalize_firm,
        privatize_firm,
    )

    world = copy.deepcopy(base)
    island = market_island(world)
    f = private_members(game, island)[0]
    number = co.firm_of(f)
    ctx = context(game, world)
    open_books(island)
    check_nationalize_firm(island, {"firm": number}, game.defs)
    owners_before = ledger.money(island, household(OWNERS))
    nationalize_firm(island, {"firm": number}, ctx)
    assert str(number) not in island.economy.firms
    assert f.owner == "state"
    state = co.firm_of(f)
    assert co.record(island, state)[co.OWNER] == "state"
    assert ledger.money(island, household(OWNERS)) >= owners_before
    assert ledger.money(island, co.entity(number)) == 0
    entry = next(e for e in island.log if e.kind == "firm_nationalized_into")
    assert "国営" in entry.args["into"] and f"（企業 {state}）" in entry.args["into"], entry.args
    books_ok(game, island)

    open_books(island)
    check_privatize_firm(island, {"firm": state}, game.defs)
    privatize_firm(island, {"firm": state}, ctx)
    assert f.owner == OWNER_PRIVATE
    new = co.private_firm_of(island, f)
    assert new is not None and new != number
    held = ledger.balance(
        island, ledger.account(co.entity(new), ledger.EQUITY, facility_entity(f.id))
    )
    assert held > 0
    books_ok(game, island)


# --- 店と小売り（W-15・W-22） --------------------------------------------------------------


def test_households_buy_retail_from_shops_then_direct(game, base, monkeypatch):
    """家計の要る小売りは、商店・百貨店で買えた分と、小さな店・屋台から割高に買った分を足したもの。

    屋台の分は払えるだけ買う。使う額をすべて品物に充てた層（失業者など）は小売りの代金が残らない
    ことがあり、そのときは要る量に届かない（どの島でもそうならないかは、世界の歩みしだい）。"""
    from hakoniwa_core.economy import funds, retail
    from hakoniwa_core.modules import market as mk
    from hakoniwa_core.units import QUANTITY_SCALE

    world = copy.deepcopy(base)
    seen = {}
    direct_of = mk.Market._direct

    def spy(island, ctx, wanted, got):
        good = retail.good_of(ctx.config)
        unit = mk.Market._direct_unit(island, ctx.config)
        tax = island.policy.consumption_tax
        short = {layer: qty - got.get(layer, {}).get(good, 0) for layer, qty in wanted.items()}
        broke = any(
            mk.gross_cost(unit, qty, 0, tax) > funds.available(island, mk.Payer(household(layer)))
            for layer, qty in short.items()
            if qty > 0
        )
        seen[island.id] = (sum(max(0, qty) for qty in short.values()) / QUANTITY_SCALE, broke)
        direct_of(island, ctx, wanted, got)

    monkeypatch.setattr(mk.Market, "_direct", staticmethod(spy))
    run_checked(game, world, 1)
    sold_somewhere = False
    for i in world.island_ids():
        stats = world.islands[i].stats
        need, sold, direct = stats["retail.need"], stats["retail.sold"], stats["retail.direct"]
        short, broke = seen[i]
        assert need > 0
        assert sold + short == pytest.approx(need, abs=1)  # 店が売った分を家計が受け取った
        if broke:
            assert direct <= short + 1
        else:
            assert direct == pytest.approx(short, abs=1)
        assert stats["retail.direct_sales"] > 0
        sold_somewhere = sold_somewhere or sold > 0
    assert sold_somewhere, "どの島でも店が売れていない"


def town_of(game, island):
    from hakoniwa_core import facilities as fac

    housing = fac.housing_kind(game.config)
    return next(f for f in island.facilities if f.kind == housing and f.built)


def test_shop_goes_into_town_slots_only(game, base):
    from hakoniwa_core import facilities as fac

    island = copy.deepcopy(market_island(base))
    town = town_of(game, island)
    x, y = fac.position(town)
    empty = next(
        (t.x, t.y)
        for t in island.tiles
        if island.facility_at(t.x, t.y) is None and game.defs.terrain[t.terrain].get("land")
    )
    assert fac.placement_problem(game.defs, island, "shop", *empty)[0] == "reason_shop_needs_town"
    slots = fac.shop_slots(game.defs, town)
    for f in [f for f in island.facilities if f.ext.get(fac.SITE) == [x, y]]:
        island.facilities.remove(f)
    assert fac.placement_problem(game.defs, island, "shop", x, y) is None
    for _ in range(slots):
        shop = copy.deepcopy(next(f for f in market_island(base).facilities if f.kind == "shop"))
        shop.ext[fac.SITE] = [x, y]
        island.facilities.append(shop)
    assert fac.placement_problem(game.defs, island, "shop", x, y)[0] == "reason_shop_slots"


def test_department_store_needs_commercial_zone(game, base):
    from hakoniwa_core import facilities as fac
    from hakoniwa_core.state import ZONE_COMMERCIAL

    island = copy.deepcopy(market_island(base))
    tile = next(
        t for t in island.tiles if t.terrain == "plain" and island.facility_at(t.x, t.y) is None
    )
    problem = fac.placement_problem(game.defs, island, "department_store", tile.x, tile.y)
    assert problem[0] == "reason_zone_required"
    tile.zone = ZONE_COMMERCIAL
    assert fac.placement_problem(game.defs, island, "department_store", tile.x, tile.y) is None
    # 商業地には工場は建たない
    assert fac.placement_problem(game.defs, island, "farm", tile.x, tile.y)[0] in (
        "reason_commercial_zone",
        "reason_wrong_terrain",
    )


def test_large_store_ban_and_permit(game, base):
    from hakoniwa_core.economy import retail
    from hakoniwa_core.modules.firms import set_retail_policy

    island = copy.deepcopy(market_island(base))
    defs, config = game.defs, game.config
    pos, turn = (3, 3), base.turn
    assert retail.entry_block(island, defs, config, "shop", pos, turn) is None
    assert retail.entry_block(island, defs, config, "department_store", pos, turn) is None
    island.policy.large_store = retail.BAN
    assert retail.entry_block(island, defs, config, "department_store", pos, turn) == (
        "large_store_banned"
    )
    island.policy.large_store = retail.PERMIT
    # 許可制: 企業が申請を出し、許すまで（申請の期限のあいだ）は建たない
    assert retail.entry_block(island, defs, config, "department_store", pos, turn) == (
        "large_store_waiting"
    )
    assert retail.application(island)["facility"] == "department_store"
    assert any(e.kind == "store_application" for e in island.log)
    assert retail.entry_block(island, defs, config, "department_store", pos, turn + 1) == (
        "large_store_waiting"
    )
    set_retail_policy(island, {"approve": True}, context(game, base))
    assert retail.entry_block(island, defs, config, "department_store", pos, turn + 2) is None
    assert retail.application(island) is None  # 許した申請は使った
    # 許可制をやめると、出ている申請は消える
    retail.entry_block(island, defs, config, "department_store", pos, turn + 3)
    set_retail_policy(island, {"large_store": retail.NONE}, context(game, base))
    assert retail.application(island) is None


def test_department_store_reaches_car_owners_island_wide(game, base):
    from hakoniwa_core.economy import retail

    island = market_island(base)
    shop = next(f for f in island.facilities if f.kind == "shop")
    store = copy.deepcopy(shop)
    store.kind = "department_store"
    near = retail.near_share(island, game.config, retail.position(shop))
    assert retail.reach(island, game.defs, game.config, shop) == pytest.approx(near)
    cars = retail.car_share(island, game.config)
    assert retail.reach(island, game.defs, game.config, store) == pytest.approx(
        min(1.0, cars + (1 - cars) * near)
    )


# --- 畑と村（W-20・W-21・台帳 2026-10-18 06:33） -------------------------------------------


def a_farm(world):
    for i in world.island_ids():
        for f in world.islands[i].facilities:
            if f.kind == "farm" and f.built:
                return world.islands[i], f
    raise AssertionError("農場がない")


def test_fields_without_fertilizer_fall_back_instead_of_stopping_the_farm(game, base):
    from hakoniwa_core import facilities as fac

    _, farm = a_farm(base)
    f = copy.deepcopy(farm)
    power = game.config.get("power.good")
    f.recipe = "cotton"
    f.economy[fac.FIELDS] = {"fertilized": 600, "cotton": 400}
    f.stock = {}
    assert fac.input_ratio(game.defs, f, power)[0] == 0  # 前は農場ごと止まっていた
    fac.fit_fields(game.defs, f, power)
    assert f.economy[fac.FIELDS] == {"default": 600, "cotton": 400}
    assert fac.input_ratio(game.defs, f, power)[0] == fac.FULL
    # 化学品が手元にあれば、肥料の畑のまま
    g = copy.deepcopy(farm)
    g.recipe = "cotton"
    g.economy[fac.FIELDS] = {"fertilized": 600, "cotton": 400}
    g.stock = {"chemicals": [[0, 10_000]]}
    fac.fit_fields(game.defs, g, power)
    assert g.economy[fac.FIELDS] == {"fertilized": 600, "cotton": 400}


def test_only_grain_farm_does_not_switch_crops(game, base):
    from hakoniwa_core.modules.firms import _sole_food

    island, farm = a_farm(base)
    island = copy.deepcopy(island)
    farm = next(f for f in island.facilities if f.id == farm.id)
    farm.recipe = "default"
    farm.economy.pop("fields", None)
    others = [
        f
        for f in island.facilities
        if f is not farm
        and "grain" in game.defs.facilities[f.kind]["recipes"].get(f.recipe, {}).get("outputs", {})
    ]
    for f in others:
        island.facilities.remove(f)
    assert _sole_food(island, game.defs, farm) == {"grain"}
    built = [f for f in others if f.built and f.x >= 0]  # 工事中の農場は数えない
    if built:
        island.facilities.append(built[0])
        assert _sole_food(island, game.defs, farm) == set()


def test_people_who_fled_to_villages_return_when_towns_need_workers(game, base):
    import random

    from hakoniwa_core.modules.population import FED, FLED, Population, _farming

    island = copy.deepcopy(market_island(base))
    people = [t for t in island.tiles if t.population > 0]
    village = next(t for t in people if _farming(game.defs, t))
    town = next(t for t in people if not _farming(game.defs, t))
    fled = village.population // 2
    village.ext[FLED] = fled
    for h in island.economy.households.values():
        h[FED] = 1000
    pos = (town.x, town.y)

    def run():
        free, pull = {pos: 10_000}, {pos: 10_000}
        return Population._return_to_towns(
            island, context(game, base), random.Random(1), free, pull, 10_000
        )

    before = town.population
    back = run()
    # 1ターンに逃れた人の return_share の割合ずつ戻る
    share = game.config.get("population.hunger")["return_share"]
    assert back == pytest.approx(fled * share, abs=1)
    assert town.population == before + back
    assert village.ext[FLED] == fled - back
    # 町で食べ物が買えなければ戻らない
    for h in island.economy.households.values():
        h[FED] = 0
    assert run() == 0


def test_services_band_widens_with_income():
    from hakoniwa_core import gate

    assert gate.services_band(1.0) == pytest.approx(gate.SERVICES_POOR)
    assert gate.services_band(gate.RICH_INCOME * 2) == pytest.approx(gate.SERVICES_RICH)
    low, high = gate.services_band(2.0)
    assert gate.SERVICES_POOR[0] < low < gate.SERVICES_RICH[0]
    assert gate.SERVICES_POOR[1] < high < gate.SERVICES_RICH[1]


def test_firm_cascade_watch_counts_falls_per_firm(game, base):
    from hakoniwa_core import gate

    island = copy.deepcopy(market_island(base))
    members = [
        f for f in island.facilities if co.private_firm_of(island, f) is not None and f.built
    ]
    if len(members) < gate.CASCADE_MIN:
        pytest.skip("民営の企業の施設が少ない")
    firm = co.private_firm_of(island, members[0])
    for f in members:
        f.economy[co.FIRM] = firm
    w = gate.Watch()
    gate.Recorder._watch_firms(island, 10, w)
    for f in members[: gate.CASCADE_MIN]:
        f.economy["foreclosed"] = 10
    gate.Recorder._watch_firms(island, 11, w)
    assert "firm_cascade" in w.hits


def test_firm_cascade_watch_skips_own_closings(game, base):
    """企業が自分で閉じた施設は、倒産の連鎖に数えない。"""
    from hakoniwa_core import gate

    island = copy.deepcopy(market_island(base))
    members = [
        f for f in island.facilities if co.private_firm_of(island, f) is not None and f.built
    ]
    if len(members) < gate.CASCADE_MIN:
        pytest.skip("民営の企業の施設が少ない")
    firm = co.private_firm_of(island, members[0])
    for f in members:
        f.economy[co.FIRM] = firm
    w = gate.Watch()
    gate.Recorder._watch_firms(island, 10, w)
    for f in members[: gate.CASCADE_MIN]:
        f.economy["closing"] = 10
    gate.Recorder._watch_firms(island, 11, w)
    assert "firm_cascade" not in w.hits
