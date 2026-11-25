"""W-186（市場経済の国の不況）の直し 回1: 建国の初めの値と、国の穀物の備蓄・損益の在庫の数え方。"""

from __future__ import annotations

import pytest

from hakoniwa_core import stock
from hakoniwa_core.economy import books, experience, reserve, services
from hakoniwa_core.economy.households import propensity
from hakoniwa_core.economy.prices import initial_prices, unit_floor
from hakoniwa_core.economy.start import founding_deposit
from hakoniwa_core.founding import found_island
from hakoniwa_core.game import load_game
from hakoniwa_core.modules.accounts import Accounts
from hakoniwa_core.modules.market import Market
from hakoniwa_core.modules.population import FED
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import money_of, scale_price, to_milli
from tests.core.test_founding import plan_for
from tests.core.test_rules import add, home, make_island, run, with_economy
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）


def context(game, turn=1) -> StageContext:
    return StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.registry,
        formulas=game.engine.formulas,
        turn=turn,
        seed=0,
        stage="test",
        before=None,
    )


def food_supply(game, island, kind: str) -> int:
    return sum(
        q
        for f in island.facilities
        if f.kind == kind
        for q in game.defs.facilities[kind]["recipes"]["default"]["outputs"].values()
    )


def test_founding_industries_start_with_experience_and_cheaper_prices(game):
    defs, config = game.defs, game.config
    island = found_island(defs, config, plan_for(game), 1, 3, 4)
    skill = experience.founding_factor(config)
    assert skill > 1
    present = {defs.facilities[f.kind].get("industry") for f in island.facilities} - {None}
    assert {"agriculture", "energy", "consumer_goods", "mining"} <= present
    for industry in present & experience.founding_industries(defs):
        assert experience.factor(island, config, industry) == pytest.approx(skill)
    assert experience.total(island, "steel") == 0  # 島に新しく入る産業は 0 から
    # 建国で置かなかった産業の経験は持たない（石材と石炭のない島には鉱業がない）
    bare = found_island(defs, config, plan_for(game, resources=["iron_ore"]), 2, 3, 4)
    assert "mining" not in bare.ext[experience.KEY]
    # 初めの値段: 昔からの産業の物は経験の係数で原価を割るので、累積 0 のときより安い
    raw = load_game()
    raw.config.raw["founding"]["experience_turns"] = 0
    before, after = initial_prices(raw.defs, raw.config), initial_prices(defs, config)
    for good in ("grain", "fish", "wood", "electricity", "consumer_goods"):
        assert after[good] < before[good]
    assert after["steel"] > 0 and after["steel"] <= before["steel"]  # 原料の分だけ下がる


def test_start_facilities_fit_the_need(game):
    """建国の農場の数は、住民の食べる量から漁港の産出を引いた残りに合わせる: 魚の多い寒い島は
    少なく、魚の少ない熱帯の島は多い。"""
    defs, config = game.defs, game.config
    cold = found_island(defs, config, plan_for(game), 1, 3, 4, climate="cold")
    hot = found_island(defs, config, plan_for(game), 2, 3, 4, climate="tropical")
    farms = {
        name: sum(f.kind == "farm" for f in isl.facilities)
        for name, isl in (("cold", cold), ("hot", hot))
    }
    assert farms["hot"] > farms["cold"]
    for island in (cold, hot):
        assert sum(f.kind == "fishing_port" for f in island.facilities) == 1
        assert 1 <= sum(f.kind == "lumber_camp" for f in island.facilities) <= 3
        assert sum(f.kind == "mine" for f in island.facilities) == 1  # 石炭の島に1つ
        # 国の穀物の備蓄を倉庫に入れてある
        assert stock.total(island.warehouse, "grain") == reserve.target(island, defs, config) > 0


def test_founding_deposit_is_the_desired_holding(game):
    config = game.config
    c1 = propensity(config, "general", config.get("banking.deposit_rate"))
    assert c1 < config.get("household.layers")["general"]["c1"]  # 預金金利の分だけ貯める
    # 持っていたい額なら、使う額 c1 × 所得 + c2 × 預金 が所得と釣り合う
    income = config.get("household.base_income")
    assert c1 * income + config.get("household.c2") * founding_deposit(config) == pytest.approx(
        income, rel=1e-3
    )


def reserve_orders(game, island, turn, offered=None):
    """_reserve_orders が出す売り（財, 量, 指値）と買いの本（板）を返す。"""
    sells = []
    offered = {} if offered is None else offered

    def sell(good, qty, limit, layer=None):
        qty = min(qty, stock.total(island.warehouse, good) - offered.get(good, 0))
        if qty > 0:
            offered[good] = offered.get(good, 0) + qty
            sells.append((good, qty, limit))

    books_by_good = {}
    Market()._reserve_orders(island, context(game, turn), books_by_good, sell, offered)
    return sells, books_by_good


def reserve_island(game):
    island = make_island(game)
    home(game, island, 5, 5, 3000)
    with_economy(game, island)
    island.policy.budget["warehouse"] = 10**9
    return island


def test_reserve_releases_when_the_price_spikes_or_people_go_hungry(game):
    """備蓄は、値段がいつもの値段の (1 + release) 倍以上になったらその値段で、飢えた家計があれば
    いつもの値段で売りに出す。ふだんは売りに出さない（市場の売りが足りて見えないように）。"""
    island = reserve_island(game)
    spec = game.config.get("food_reserve")
    goal = reserve.target(island, game.defs, game.config)
    stock.add(island.warehouse, "grain", goal, 5, game.defs.goods)
    usual = island.economy.prices["grain"]
    sells, books_by_good = reserve_orders(game, island, 6)
    assert sells == [] and "grain" not in books_by_good  # ふだんは売らず、目安があるので買わない
    high = scale_price(usual, 1 + spec["release"])
    island.economy.prices["grain"] = high
    sells, _ = reserve_orders(game, island, 6)
    assert sells == [("grain", goal, high)]
    # 値段の上限や公定価格で値段が上がらなくても、飢えた家計があれば出す
    island.economy.prices["grain"] = usual
    island.economy.households["general"][FED] = 500
    sells, _ = reserve_orders(game, island, 6)
    assert sells == [("grain", goal, usual)]
    stock.take(island.warehouse, "grain", goal // 2)
    _, books_by_good = reserve_orders(game, island, 6)
    assert "grain" not in books_by_good  # 飢えた家計があるターンは買い足さない


def _lead_on(game, **kw):
    """下書きの先行放出（food_reserve.price_lead）を入にする（game は module 共有なので、
    前の試験の書き換えが残らないよう dict ごと置き換える）。"""
    lead = {
        "enabled": True,
        "multiple": 1.15,
        "rise": 0.10,
        "rise_turns": 3,
        "window": 12,
        "max_fraction": 0.25,
        "floor_turns": 1,
    }
    lead.update(kw)
    game.config.raw["food_reserve"]["price_lead"] = lead
    return lead


def test_reserve_price_lead_is_off_by_default(game):
    """先行放出は既定で切: 移動中央値の 1.15 倍まで上がっても、(1 + release) の線に
    届かなければ今までどおり出さない。"""
    island = reserve_island(game)
    goal = reserve.target(island, game.defs, game.config)
    stock.add(island.warehouse, "grain", goal, 5, game.defs.goods)
    usual = island.economy.prices["grain"]
    island.economy.price_history["grain"] = [usual] * 12
    island.economy.prices["grain"] = scale_price(usual, 1.15)
    sells, _ = reserve_orders(game, island, 6)
    assert sells == []
    assert "food_reserve_release" not in island.scratch


def test_reserve_price_lead_releases_on_multiple(game):
    """倍率の線: 直近の値段が移動中央値の multiple 倍以上で先に出す。第 2 版では全量でなく、
    割合上限（max_fraction）までの小出し（市場の記録がなければ見積もりは立たず、上限と
    下限だけで決まる）。"""
    _lead_on(game, rise=0.99)  # 上がりの線は遠くに置き、倍率の線だけで見る
    island = reserve_island(game)
    goal = reserve.target(island, game.defs, game.config)
    stock.add(island.warehouse, "grain", goal, 5, game.defs.goods)
    usual = island.economy.prices["grain"]
    island.economy.price_history["grain"] = [usual] * 12
    island.economy.prices["grain"] = scale_price(usual, 1.15)
    sells, _ = reserve_orders(game, island, 6)
    qty = int(goal * 0.25)
    assert sells == [("grain", qty, scale_price(usual, 1.15))]
    note = island.scratch["food_reserve_release"]
    assert note["reason"] == "lead_multiple" and note["qty"] == qty and note["base"] == usual
    assert note["cap"] == "fraction" and note["floor"] == goal // 2 and note["stock"] == goal
    assert note["need"] is None


def test_reserve_price_lead_releases_on_rise(game):
    """上がりの速さの線: 倍率の線に届かなくても、rise_turns ターン前から rise 以上
    上がっていれば先に出す。"""
    _lead_on(game, multiple=1.30)  # 倍率の線は遠くに置き、上がりの速さだけで見る
    island = reserve_island(game)
    goal = reserve.target(island, game.defs, game.config)
    stock.add(island.warehouse, "grain", goal, 5, game.defs.goods)
    usual = island.economy.prices["grain"]
    island.economy.price_history["grain"] = [usual] * 10 + [
        scale_price(usual, 1.04),
        scale_price(usual, 1.08),
    ]
    island.economy.prices["grain"] = scale_price(usual, 1.12)  # 3 ターン前から +12%
    sells, _ = reserve_orders(game, island, 6)
    assert sells == [("grain", int(goal * 0.25), scale_price(usual, 1.12))]
    assert island.scratch["food_reserve_release"]["reason"] == "lead_rise"


def test_reserve_price_lead_median_ignores_a_spike(game):
    """基準は移動中央値: 過去に 1 ターンだけ跳ねた値段があっても基準は跳ねない
    （平均だと基準が跳ねて線が遠のく）。"""
    _lead_on(game, rise=0.99)
    island = reserve_island(game)
    goal = reserve.target(island, game.defs, game.config)
    stock.add(island.warehouse, "grain", goal, 5, game.defs.goods)
    usual = island.economy.prices["grain"]
    island.economy.price_history["grain"] = [usual] * 11 + [usual * 3]
    island.economy.prices["grain"] = scale_price(usual, 1.2)
    sells, _ = reserve_orders(game, island, 6)
    assert sells == [("grain", int(goal * 0.25), scale_price(usual, 1.2))]
    assert island.scratch["food_reserve_release"]["base"] == usual


def test_reserve_price_lead_quiet_when_calm(game):
    """入でも、どちらの線にも届かなければ出さない。"""
    _lead_on(game)
    island = reserve_island(game)
    goal = reserve.target(island, game.defs, game.config)
    stock.add(island.warehouse, "grain", goal, 5, game.defs.goods)
    usual = island.economy.prices["grain"]
    island.economy.price_history["grain"] = [usual] * 12
    island.economy.prices["grain"] = scale_price(usual, 1.05)
    sells, _ = reserve_orders(game, island, 6)
    assert sells == []


def test_reserve_price_lead_amount_uses_unmet_need(game):
    """第 2 版の量: 前のターンの買えなかった量（demand − volume）を price/base で割り増した
    見積もりが、割合上限より小さければその量だけ出す。"""
    _lead_on(game, rise=0.99)
    island = reserve_island(game)
    goal = reserve.target(island, game.defs, game.config)
    stock.add(island.warehouse, "grain", goal, 5, game.defs.goods)
    usual = island.economy.prices["grain"]
    island.economy.price_history["grain"] = [usual] * 12
    price = scale_price(usual, 1.15)
    island.economy.prices["grain"] = price
    unmet = 1_000
    island.economy.ext[reserve.LAST_UNMET] = unmet  # _record が前のターンに残した値
    sells, _ = reserve_orders(game, island, 6)
    need = unmet * price // usual
    assert sells == [("grain", need, price)]
    note = island.scratch["food_reserve_release"]
    assert note["cap"] == "need" and note["need"] == need and note["qty"] == need


def test_reserve_price_lead_respects_floor(game):
    """第 2 版の下限: 先行放出は下限（住民の食べる量の floor_turns ターン分）を割らない。
    倉庫が下限ちょうどなら出さず、下限より少しだけ多ければ、その余りだけ出す。"""
    _lead_on(game, rise=0.99, max_fraction=0.9)  # 割合上限は緩めて、下限だけで見る
    island = reserve_island(game)
    goal = reserve.target(island, game.defs, game.config)
    floor = goal // 2  # 目安は turns=2 ターン分、下限は floor_turns=1 ターン分
    usual = island.economy.prices["grain"]
    island.economy.price_history["grain"] = [usual] * 12
    island.economy.prices["grain"] = scale_price(usual, 1.15)
    stock.add(island.warehouse, "grain", floor, 5, game.defs.goods)
    sells, _ = reserve_orders(game, island, 6)
    assert sells == [] and "food_reserve_release" not in island.scratch
    stock.add(island.warehouse, "grain", 1000, 5, game.defs.goods)
    sells, _ = reserve_orders(game, island, 6)
    assert sells == [("grain", 1000, scale_price(usual, 1.15))]
    note = island.scratch["food_reserve_release"]
    assert note["cap"] == "floor" and note["floor"] == floor


def test_reserve_price_lead_floor_does_not_block_hunger(game):
    """下限は先行放出だけのもの: 飢えの線の放出は、倉庫が下限ちょうどでも今までどおり
    全量をいつもの値段で出す。"""
    _lead_on(game)
    island = reserve_island(game)
    goal = reserve.target(island, game.defs, game.config)
    floor = goal // 2
    stock.add(island.warehouse, "grain", floor, 5, game.defs.goods)
    usual = island.economy.prices["grain"]
    island.economy.price_history["grain"] = [usual] * 12
    island.economy.households["general"][FED] = 500
    sells, _ = reserve_orders(game, island, 6)
    assert sells == [("grain", floor, usual)]
    assert island.scratch["food_reserve_release"]["reason"] == "hunger"


def test_reserve_rotates_old_grain_and_refills(game):
    """日持ちの残りが rotate 以下の組は今の値段で売り、売った分と、目安に足りない分の refill の
    割合を、いつもの値段で買う（新しい組と入れ替える）。"""
    island = reserve_island(game)
    spec = game.config.get("food_reserve")
    life = game.defs.goods["grain"]["shelf_life"]
    goal = reserve.target(island, game.defs, game.config)
    stock.add(island.warehouse, "grain", goal // 4, 0, game.defs.goods)  # 古い組
    stock.add(island.warehouse, "grain", goal // 4, 10, game.defs.goods)
    turn = life - spec["rotate"]  # 古い組の日持ちの残りが rotate になったターン
    sells, books_by_good = reserve_orders(game, island, turn)
    price = island.economy.prices["grain"]
    assert sells == [("grain", goal // 4, price)]  # 古い組だけ今の値段で
    ((order, party),) = books_by_good["grain"].buys
    short = goal - 2 * (goal // 4)
    assert order.quantity == int(short * spec["refill"]) + goal // 4 and order.limit == price
    assert party.payer.item == "warehouse"  # 国の倉庫の買い入れの予算から
    # 契約の輸入などで倉庫がもう穀物を売りに出していれば、買わない（自分の売りを買わない）
    _, books_by_good = reserve_orders(game, island, turn, offered={"grain": 1})
    assert "grain" not in books_by_good


def test_founding_reserve_is_spread_over_the_shelf_life(game):
    """建国の備蓄は作られたターンをならしてあり、入れ替えは毎ターン少しずつ。"""
    defs, config = game.defs, game.config
    island = found_island(defs, config, plan_for(game), 1, 3, 4)
    spec = config.get("food_reserve")
    goal = reserve.target(island, defs, config)
    ages = defs.goods["grain"]["shelf_life"] - spec["rotate"]
    assert len(island.warehouse["grain"]) == ages
    for turn in range(1, ages + 1):  # 入れ替えで売れた分を倉庫から出していく
        old = reserve.rotating(island, defs, config, turn)
        assert 0 < old <= goal // ages + goal % ages
        stock.take(island.warehouse, "grain", old)


def test_profit_counts_unsold_output_at_cost(game):
    """作って売れていない物は、損益では1単位の原価（とそのターンの値段の安い方）で在庫に数える。
    GDP はそのターンの値段のまま。"""
    island = make_island(game)
    home(game, island, 5, 5, 3000)
    mill = add(island, "lumber_camp", 7, 7)
    with_economy(game, island)
    island.economy.prices["wood"] *= 3  # 原価よりずっと高い値段
    books.stock_moved(island, mill.id, "wood", 10_000)
    got = Accounts._book_inventory(island, context(game))
    rates = {}
    cost = unit_floor(
        game.defs,
        game.config,
        mill,
        "wood",
        island.economy.prices,
        mill.economy["wage"],
        rates.get(mill.id, 0),
    )
    assert 0 < got[mill.id] < money_of(island.economy.prices["wood"], 10_000)
    assert got[mill.id] == pytest.approx(money_of(cost, 10_000), rel=0.05)
    assert books.inventory_changes(island, island.economy.prices)[mill.id] == money_of(
        island.economy.prices["wood"], 10_000
    )


def test_profit_stat_leaves_out_public_services(game):
    island = make_island(game)
    home(game, island, 5, 5, 3000)
    add(island, "government_office", 5, 6)
    with_economy(game, island)
    run(game, island, ["employment", "payroll", "accounts", "records"])
    office = next(
        b for b in island.books if island.facility(b.facility).kind == "government_office"
    )
    assert office.items.get(books.PROFIT, 0) < 0  # 役所の帳簿には賃金の分の赤字が残る
    total = sum(b.items.get(books.PROFIT, 0) for b in island.books)
    assert island.stats["profit"] == total - office.items[books.PROFIT]


def test_direct_retail_needs_workers(game):
    """商業・サービス業に働く人がいなければ、直売・屋台は小売りを売らない（建国の1ターン目に、
    誰も雇っていないのに売上がまるごともうけになった）。"""
    island = make_island(game, terrain=None)
    island.regime["economy"] = "market"
    home(game, island, 5, 5, 3000)
    with_economy(game, island)
    shop = services.prepare(island, game.defs, game.config)
    assert shop is not None and shop.workers == 0
    Market._direct(island, context(game), {"general": to_milli(10)}, {})
    assert island.scratch.get("retail_direct") in (None, (0, 0))
    assert not books.facility_items(island).get(shop.id, {}).get(books.SALES)
