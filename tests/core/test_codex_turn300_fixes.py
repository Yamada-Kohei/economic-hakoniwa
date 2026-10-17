"""試し遊び（Codex の世界、ターン250〜300。回4 の後）で見つかった点と、夜の点検（2026-10-17）で
見つかった国債の期間の割合の小数の直し。"""

from __future__ import annotations

import copy

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import GENERAL
from hakoniwa_core.economy.prices import input_limit
from hakoniwa_core.modules.population import (
    FOOD_BUYERS,
    FOOD_ORDERS,
    FOOD_SHORT,
    HUNGER,
    HUNGER_RISK,
    Population,
    food_short_reason,
)
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.units import BASIS
from tests.core.test_finance import context, island_of, order
from tests.core.test_finance import warmed as warmed  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import add, make_island
from tests.core.test_trade import game as game  # noqa: F401

# --- 作物を替えている途中の農場の原料 ---------------------------------------------------------


def test_farm_changing_crops_keeps_buying_inputs_of_its_old_fields(game):
    """肥料（化学品）を使う綿の畑を、肥料のいらない穀物に替えている途中の農場は、残った綿の畑の
    化学品に、綿の作り方で払える値段まで払う。上限は畑の割合によらず、替える前と同じ（新しい作物で
    見て上限が 0 になり、前の畑が「原料が割高」で止まった）。畑がみな穀物になれば化学品は
    要らない。"""
    defs, config = game.defs, game.config
    island = make_island(game)
    farm = add(island, "farm", 8, 8, recipe="cotton_fertilized")
    prices = {"cotton": 46_000, "grain": 21_000, "chemicals": 100_000}
    wage = 1_000
    before = input_limit(defs, config, farm, "chemicals", prices, wage, 0)
    assert before > 0
    fac.switch_recipe(defs, farm, "default", 1)
    limits = []
    for turn in range(2, 12):
        fac.step_fields(defs, farm, turn)
        needs = fac.recipe_of(defs, farm)["inputs"].get("chemicals", 0)
        limits.append((needs > 0, input_limit(defs, config, farm, "chemicals", prices, wage, 0)))
    assert limits[0] == (True, before)
    assert all(limit == before for needs, limit in limits if needs)
    assert all(limit == 0 for needs, limit in limits if not needs)
    assert limits[-1] == (False, 0)
    # 新しい作物にも要る原料は、今までどおり新しい作物で見る
    fac.switch_recipe(defs, farm, "cotton_fertilized", 12)
    fac.step_fields(defs, farm, 13)
    steady = input_limit(defs, config, fac.steady(farm), "chemicals", prices, wage, 0)
    assert input_limit(defs, config, farm, "chemicals", prices, wage, 0) == steady


# --- 為替の受け身の注文 ------------------------------------------------------------------------


def test_passive_fx_orders_place_the_whole_depth(game):
    """中央銀行の受け身の買いは、5段に分けた余りをいちばん遠い段に足して、厚みをちょうど出す（余りを
    出さずにいると、厚みちょうどの額を両替する契約の支払いが数円足りず、0.001 トン届かなかった）。
    厚みが段の数より少ないときは、今までどおり出さない。"""
    island = make_island(game)
    for depth in (1_003, 1_000, 5, 9):
        book = fxrates.Book(depth=depth)
        fxrates.dealer_orders(island, 99, book, 10_000, 200)
        quantities = [o.quantity for o, _, _ in book.buys]
        assert sum(quantities) == depth
        assert quantities[:-1] == [depth // fxrates.TIERS] * (fxrates.TIERS - 1)
        prices = [o.limit for o, _, _ in book.buys]
        assert prices == sorted(prices, reverse=True)  # 遠い段ほど安く買う
    book = fxrates.Book(depth=4)
    fxrates.dealer_orders(island, 99, book, 10_000, 200)
    assert not book.buys and not book.sells


# --- 国債の期間の割合の小数 --------------------------------------------------------------------


def test_bond_terms_given_with_fractions_become_whole_shares(game, warmed):
    """国債の期間の割合に小数（33.3% など。命令の型は数）を渡しても、割合は整数で持つ（小数のまま
    だと、発行額から国庫・工事・配給まで小数が広がって数ターン後に止まった。夜の点検 2026-10-17）。
    発行は割合の合計で分けるので、丸めで合計がずれても全額を出す。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    terms = {"bond_5y": 1.5, "bond_10y": 2.5}
    order(game, world, island, "set_bond_policy", {"terms": terms, "issue": 1_000_000})
    shares = island.policy.bond_terms
    assert set(shares) == set(terms)
    assert all(type(v) is int for v in shares.values())
    assert abs(sum(shares.values()) - BASIS) <= len(shares)
    assert shares["bond_5y"] == round(1.5 * BASIS / 4)
    order(game, world, island, "set_bond_policy", {"terms": {"bond_5y": 33.3, "bond_10y": 66.7}})
    assert all(type(v) is int for v in island.policy.bond_terms.values())
    for _ in range(3):  # 発行・国庫・工事に小数が届かない（届くと止まる）
        game.engine.run_turn(world)
    assert island.bonds and all(type(b.face) is int for b in island.bonds)
    assert all(type(v) is int for v in island.accounts.values())


# --- 飢えの知らせ -------------------------------------------------------------------------------


def test_food_short_reason_tells_money_from_goods():
    """食べられなかった層の買えなかったわけ: 食費が使えるお金を使い切っていればお金がない、
    そうでなければ売りに出ていた物が足りない（買い負けた）。"""
    assert food_short_reason({"need": 10, "ordered": 0, "offered": 0, "cost": 0, "budget": 0}) == (
        "money"
    )
    orders = {"need": 10, "ordered": 4, "offered": 50, "cost": 990, "budget": 1_000}
    assert food_short_reason(orders) == "money"
    orders = {"need": 10, "ordered": 4, "offered": 4, "cost": 300, "budget": 1_000}
    assert food_short_reason(orders) == "goods"
    assert food_short_reason(None) == "goods"


def test_population_stage_records_who_is_hungry_and_why(game, warmed):
    """人口の段は、食べられなかった層に買えなかったわけ・市場で食料を買う人の数・いまの飢えの蓄えで
    亡くなる割合を残し、統計 layer.<層>.food と layer.<層>.hunger に出す。食べられるようになり蓄えが
    消えたら、わけと割合も消す（お金や物の流れは変えない）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    general = island.economy.households[GENERAL]
    general[HUNGER] = 2.5
    island.scratch["household_goods"] = {}  # このターンは市場で何も買えなかった
    island.scratch[FOOD_ORDERS] = {
        GENERAL: {"need": 1_000, "ordered": 0, "offered": 5_000, "cost": 0, "budget": 0}
    }
    Population().run_island(island, context(game, world, "population"))
    assert general[FOOD_SHORT] == "money"
    assert 0 < general[FOOD_BUYERS] <= general["persons"]
    assert 0 < general[HUNGER_RISK] < 1
    assert island.stats[f"layer.{GENERAL}.food"] < 1
    assert island.stats[f"layer.{GENERAL}.hunger"] > 0

    island.stats.clear()
    island.scratch[FOOD_ORDERS] = {
        GENERAL: {"need": 1_000, "ordered": 900, "offered": 900, "cost": 10, "budget": 10**9}
    }
    island.scratch["household_goods"] = {}
    Population().run_island(island, context(game, world, "population"))
    assert general[FOOD_SHORT] == "goods"

    # 食べられて蓄えも消えた層は、わけと割合を消す
    general[HUNGER] = 0.0
    general.pop("hunger_endured", None)
    island.stats.clear()
    island.scratch.pop("household_goods")
    island.scratch.pop(FOOD_ORDERS)
    game.engine.run_turn(world)  # 市場の段が層ごとの食料の注文の要点を残す
    assert set(island.scratch[FOOD_ORDERS][GENERAL]) == {
        "need",
        "ordered",
        "offered",
        "cost",
        "budget",
    }
    if general["fed"] >= fac.FULL and not general.get(HUNGER):
        assert FOOD_SHORT not in general and HUNGER_RISK not in general
