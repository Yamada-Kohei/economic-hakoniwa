"""バランス調整の回7 後半: 企業の中のお金の融通・休業・値上がりへの慣れ（W-77）・自動車部品
（W-76）・組の参入・銀行の経費の切り詰めと合併。

ターンを回す試験は、記録の段が毎ターン帳簿を確かめる（tests/core/test_phase3_3.py の run_checked）。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import autoplan, ledger
from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import OWNERS, banks, facility_entity, household
from hakoniwa_core.economy import companies as co
from hakoniwa_core.economy.prices import USUAL, adapt_usual, usual_price
from hakoniwa_core.economy.start import invest
from hakoniwa_core.modules.companies import FirmsFunds
from hakoniwa_core.modules.failures import merge
from hakoniwa_core.modules.firms import EntryExit, open_bank
from hakoniwa_core.modules.labor import furlough_share
from hakoniwa_core.modules.upgrade import fix_recipes
from hakoniwa_core.state import OWNER_PRIVATE, Facility, MarketRecord
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.units import BASIS
from tests.core.test_balance_round7 import market_island, private_members
from tests.core.test_balance_round7 import merge as join
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


# --- 企業の中のお金の融通 -------------------------------------------------------------


def two_plants(game, world):
    """同じ企業に入れた2つの施設と、企業の預金を 0 にした島。"""
    island = market_island(world)
    a, b = private_members(game, island)[:2]
    join(island, a, b)
    firm = co.entity(co.firm_of(a))
    ledger.pay(island, firm, ledger.GOV, ledger.money(island, firm), "subsidy")
    return island, a, b


def test_short_plant_draws_on_sibling_cash_when_the_firm_has_none(game, base):
    """企業の預金が 0 でも、返済に足りない施設には、同じ企業のほかの施設の手元の余りから回す。
    回す施設には次の支払いの firms.pool_floor_turns ターン分を残す。帳簿は合う。"""
    world = copy.deepcopy(base)
    island, a, b = two_plants(game, world)
    ctx = context(game, world)
    open_books(island)
    ea = facility_entity(a.id)
    ledger.pay(island, ledger.GOV, ea, ledger.money(island, ledger.GOV) // 2, "subsidy")
    before = ledger.money(island, ea)
    pooled = island.stats.get("firms.pooled", 0)  # 準備の世界のそのターンに回した分
    moved = co.draw(island, b, 1000, ctx)
    assert moved == 1000
    assert ledger.money(island, ea) == before - 1000
    assert b.economy[co.POOLED] == 1
    assert island.stats["firms.pooled"] - pooled == 1000
    # 運転資金の分（ctx なし）は企業の預金だけ
    assert co.draw(island, b, 1000) == 0
    books_ok(game, island)


def test_sibling_keeps_its_own_next_payments(game, base):
    """ほかの施設の手元は、次の支払いの分を超えた分しか回さない。"""
    world = copy.deepcopy(base)
    island, a, b = two_plants(game, world)
    ctx = context(game, world)
    for m in co.members(island, co.firm_of(a)):
        if m.id != b.id:
            entity = facility_entity(m.id)
            ledger.pay(island, entity, ledger.GOV, ledger.money(island, entity), "subsidy")
    assert co.draw(island, b, 1000, ctx) == 0
    assert co.POOLED not in b.economy


def test_plant_supported_by_siblings_too_long_is_closed(game, base):
    """ほかの施設の手元から firms.restructure_turns ターン続けて回してもらった施設は、企業が
    閉める。"""
    world = copy.deepcopy(base)
    island, a, b = two_plants(game, world)
    ctx = context(game, world)
    b.economy[co.POOLED] = game.config.get("firms.restructure_turns")
    b.economy[co.POOLED_AT] = ctx.turn
    FirmsFunds().run_island(island, ctx)
    assert "closing" in b.economy
    assert "closing" not in a.economy


def test_pool_count_resets_when_not_supported(game, base):
    world = copy.deepcopy(base)
    island, a, b = two_plants(game, world)
    ctx = context(game, world)
    b.economy[co.POOLED] = 5
    b.economy[co.POOLED_AT] = ctx.turn - 1
    FirmsFunds().run_island(island, ctx)
    assert co.POOLED not in b.economy


# --- 休業 -------------------------------------------------------------------------


def test_furlough_pays_part_of_the_wage_only_for_power_and_input_shortages(game, base):
    """電気・送電網・原料が足りずに動けなかった分の働き手には、賃金の labor.furlough_pay だけ払う。
    原料が高い・買うお金がないのは施設の都合なので満額。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    f = private_members(game, island)[0]
    able = fac.labor_ratio(game.defs, f)
    f.operating_rate = 0
    pay = round(game.config.get("labor.furlough_pay") * BASIS)
    for status in ("power", "no_grid", "input:steel"):
        f.status = status
        assert furlough_share(game.defs, game.config, f) == pay
    f.operating_rate = able // 2
    f.status = "power"
    half = furlough_share(game.defs, game.config, f)
    assert pay < half < BASIS
    for status in ("input_price:steel", "funds", ""):
        f.status = status
        assert furlough_share(game.defs, game.config, f) == BASIS


def test_furloughed_workers_stay_and_books_balance(game, base):
    """停電の島でターンを回しても帳簿が合い、休んだ人を数える。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    for f in island.facilities:
        if f.built and f.kind.endswith("power_plant"):
            f.ext["halted_until"] = world.turn + 3
    run_checked(game, world, 2)
    assert island.stats.get("labor.furloughed", 0) > 0


# --- 値上がりへの慣れ（W-77） --------------------------------------------------------------


def test_households_get_used_to_a_higher_price_halfway(game, base):
    """売りに出ていたのに家計が買わないターンが household.reprice_turns 続くと、いつもの値段を
    その間の一番安い売りの値段との半ばまで上げる。買えば、買った値段の平均が届くまで残る。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    economy = island.economy
    good = next(g for g, h in sorted(economy.bought_history.items()) if any(h))
    turns = game.config.get("household.usual_price_turns")
    ask = usual_price(island, good, turns, 1) * 2
    for _ in range(game.config.get("household.reprice_turns")):
        economy.price_history[good].append(economy.prices[good])
        economy.bought_history[good].append(0)
        adapt_usual(island, game.config, {good: ask})
    plain = usual_price(island, good, turns)  # 慣れを見ない呼び方は買った月の平均のまま
    raised = usual_price(island, good, turns, 1)
    assert raised == plain + (ask - plain + 1) // 2
    # 売りのないターンは数えない
    economy.price_history[good].append(economy.prices[good])
    economy.bought_history[good].append(0)
    adapt_usual(island, game.config, {good: 0})
    assert usual_price(island, good, turns, 1) == raised
    # 慣れた値段より高い値段で買い続ければ、慣れた値段は要らなくなる
    for _ in range(turns):
        economy.price_history[good].append(ask)
        economy.bought_history[good].append(1000)
    adapt_usual(island, game.config, {good: ask})
    assert good not in economy.ext.get(USUAL, {})


# --- 自動車部品と組の参入（W-76） ----------------------------------------------------------


def test_parts_plant_and_assembly_share_the_experience_of_one_car(game):
    facilities = game.defs.facilities
    shares = [
        facilities[k]["recipes"]["default"]["experience_share"]
        for k in ("auto_parts_plant", "automobile_plant")
    ]
    assert sum(shares) == 1
    parts = facilities["auto_parts_plant"]["staff"]
    cars = facilities["automobile_plant"]["staff"]
    assert parts["general"] > cars["general"]  # 部品のほうが人が多い


def test_maker_of_a_missing_input_is_found_for_a_bundle_entry(game, base):
    """原料が島で作られていなくても、島にある原料で作れる施設があれば、その施設を先に建てる（組の
    参入）。作り方は原料を多く使う方（アルミのある島ならアルミの作り方）。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    ctx = context(game, world)
    every = set(game.defs.goods)
    makers = EntryExit._makers(island, ctx, ["auto_parts"], every, set())
    assert makers == {"auto_parts": ("auto_parts_plant", "aluminium")}
    without = every - {"aluminium"}
    assert EntryExit._makers(island, ctx, ["auto_parts"], without, set())["auto_parts"] == (
        "auto_parts_plant",
        "default",
    )
    # 作るのに要る原料が島にない
    assert EntryExit._makers(island, ctx, ["auto_parts"], every - {"steel"}, set()) is None
    # 作る工場が建っている途中
    assert EntryExit._makers(island, ctx, ["auto_parts"], every, {"auto_parts"}) is None


def test_assembler_invests_in_its_parts_supplier_first(game, base):
    """部品工場の出資は、売り先（自動車工場）を持つ企業が、原料を作る企業より先に出す（系列）。"""
    assert co._relation(game.defs, {"automobile_plant"}, "auto_parts_plant") == 1
    assert co._relation(game.defs, {"steel_mill"}, "auto_parts_plant") == 2
    assert co._relation(game.defs, {"farm"}, "auto_parts_plant") == 3


def test_old_aluminium_recipe_moves_to_assembly(game, base):
    world = copy.deepcopy(base)
    island = market_island(world)
    f = private_members(game, island)[0]
    kind, recipe = f.kind, f.recipe
    f.kind, f.recipe = "automobile_plant", "aluminium"
    f.ext[fac.RECIPE_NEXT] = "aluminium"
    fix_recipes(island, game.defs)
    assert f.recipe == "default" and fac.RECIPE_NEXT not in f.ext
    f.kind, f.recipe = kind, recipe


def test_standard_plan_builds_assembly_and_parts_plants_together(game, base):
    """標準の計画は、部品を島で作っていなくても、部品の材料が手元にあれば自動車工場を建て、次に部品
    工場を余裕がなくても1つ目だけ建てる（どちらも相手を待って建たなかった。回7 後半の測り）。"""
    world = copy.deepcopy(base)
    defs, config = game.defs, game.config
    inputs = defs.facilities["auto_parts_plant"]["recipes"]["default"]["inputs"]

    def missing(i):
        made = {g for f in i.facilities for g in fac.recipe_of(defs, f)["outputs"]}
        return sorted(set(inputs) - made - {config.get("power.good")})

    # 部品の材料のうち作っていない物を、どれも島で作れる島（作れない材料が残る島もある）
    island = next(
        i
        for i in world.islands.values()
        if i.has_economy
        and any(autoplan._producers(defs, config, i, "automobile"))
        and all(any(autoplan._producers(defs, config, i, g)) for g in missing(i))
    )
    island.facilities = [
        f for f in island.facilities if f.kind not in ("automobile_plant", "auto_parts_plant")
    ]
    for good in missing(island):
        kind = next(k for k, _ in autoplan._producers(defs, config, island, good))
        island.facilities.append(Facility(id=900 + len(island.facilities), kind=kind, x=0, y=0))
    island.markets = [m for m in island.markets if m.good != "automobile"]
    island.markets.append(MarketRecord("automobile", 1, 0, 100_000, 0))
    turn = world.turn
    planned = list(autoplan._candidates(island, defs, config, turn))
    assert ("automobile_plant", "default") in planned
    assert not any(k == "auto_parts_plant" for k, _ in planned)  # 部品はまだ誰も使わない
    island.facilities.append(Facility(id=999, kind="automobile_plant", x=0, y=0))
    planned = list(autoplan._candidates(island, defs, config, turn))
    assert any(k == "auto_parts_plant" for k, _ in planned)


# --- 銀行の経費の切り詰めと合併 -------------------------------------------------------------


def test_losing_bank_cuts_staff_below_the_floor_until_it_earns_again(game, base):
    world = copy.deepcopy(base)
    island = market_island(world)
    f = banks.facility_of(island, banks.bank_list(island)[0])
    f.economy["profits"] = [-1] * 12
    banks.watch_costs(game.config, f)
    assert f.economy[banks.LEAN] == 0
    f.economy["profits"] = [1] * 12
    for _ in range(game.config.get("banking.lean_turns") - 1):
        banks.watch_costs(game.config, f)
    assert banks.LEAN in f.economy
    banks.watch_costs(game.config, f)
    assert banks.LEAN not in f.economy


def test_merger_pays_shareholders_and_moves_everything(game, base):
    """合併: 引き取る銀行は株主に値段を払い、弱った銀行の預金・融資などをすべて引き継ぐ。帳簿は
    合う。合併の後 1 年は新しい銀行が入らない。"""
    world = copy.deepcopy(base)
    island = market_island(world)
    ctx = context(game, world)
    target = banks.bank_list(island)[0]
    occupied = island.facilities_by_tile()
    spot = next((t.x, t.y) for t in island.tiles if (t.x, t.y) not in occupied)
    weak = ledger.bank_entity(open_bank(island, ctx, spot, OWNER_PRIVATE).id)
    owners = household(OWNERS)
    invest(island, owners, weak, 10_000)
    depositor, amount = next(iter(sorted(banks.depositors_of(island, target).items())))
    ledger.move_deposit(island, depositor, target, weak, amount // 2, "deposit_switch")
    open_books(island)
    cash = ledger.money(island, owners)
    merge(island, ctx, weak, target, 6_000)
    assert weak not in banks.bank_list(island)
    assert banks.depositors_of(island, target)[depositor] == amount
    assert ledger.money(island, owners) == cash + 6_000
    assert island.stats["banks.merged"] == 1
    books_ok(game, island)
    assert EntryExit()._open_private_bank(island, ctx) in (None, ("merged", {"turns": 0}))
