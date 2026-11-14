"""お金の経済の部品（板寄せ・帳簿・家計の食料の割り振り）を、小さな入力で確かめる。"""

from __future__ import annotations

import random

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import Payer, funds
from hakoniwa_core.game import load_game
from hakoniwa_core.market import auction
from hakoniwa_core.modules.market import (
    FoodMenu,
    Market,
    consumer_tiers,
    food_cheapest,
    food_orders,
    food_prefs,
    food_split,
    gross_cost,
    late_weight,
    price_update,
    upgrade_food,
)
from hakoniwa_core.state import JournalEntry
from hakoniwa_core.state.generate import generate_world


@pytest.fixture(scope="module")
def game():
    return load_game()


# --- 板寄せ -----------------------------------------------------------------------


def _random_orders(rng: random.Random, reference: int) -> list[auction.Order]:
    orders = []
    for _ in range(rng.randint(0, 6)):
        limit = (
            None if rng.random() < 0.2 else rng.randint(reference * 6 // 10, reference * 14 // 10)
        )
        orders.append(auction.Order(rng.randint(1, 50), limit, rng.randint(0, 3)))
    return orders


@pytest.mark.parametrize("seed", range(300))
def test_auction_properties(seed):
    """約定量が最大、値幅を守る、指値より不利な約定がない、取り置いたお金を超えない。"""
    rng = random.Random(seed)
    reference = rng.randint(20, 200)
    buys, sells = _random_orders(rng, reference), _random_orders(rng, reference)
    result = auction.clear(buys, sells, reference, width=1000)

    assert result.low <= result.price <= result.high
    best = 0
    for p in range(result.low, result.high + 1):
        d = sum(o.quantity for o in buys if auction.buy_limit(o, result.high) >= p)
        s = sum(o.quantity for o in sells if auction.sell_limit(o) <= p)
        best = max(best, min(d, s))
    assert result.volume == best
    assert sum(result.bought) == sum(result.sold) == result.volume
    for o, got in zip(buys, result.bought, strict=True):
        assert 0 <= got <= o.quantity
        if got:
            assert auction.buy_limit(o, result.high) >= result.price
            if o.limit is not None:  # 「量 × 指値」を取り置けば払える
                assert got * result.price <= o.quantity * o.limit
    for o, got in zip(sells, result.sold, strict=True):
        assert 0 <= got <= o.quantity
        if got:
            assert auction.sell_limit(o) <= result.price
    pairs = auction.match(result.bought, result.sold)
    assert sum(q for *_, q in pairs) == result.volume


def test_auction_stops_at_band_and_allocates_by_priority():
    buys = [auction.Order(10, None, 1), auction.Order(10, None, 5)]
    sells = [auction.Order(5, 90)]
    result = auction.clear(buys, sells, 100, width=1000)
    assert result.price == 110  # 買いが余ると値幅の上端で止まる
    assert result.bought == [0, 5]  # 上端で止まったら優先順位の高い方から


def test_auction_settles_at_an_order_price_nearest_the_reference():
    """売りと買いが同じ量になる値段が幅で並ぶときは、出た指値のうち基準の値段に近い方で約定する。
    基準の値段そのものにはしない（すると幅に基準の値段が入っている間は値段が動かず、上がった値段が
    戻らなかった。2026-11-01 の試し、MK-02）。"""
    buys, sells = [auction.Order(10, 100)], [auction.Order(10, 90)]
    r = auction.clear(buys, sells, 98)
    assert (r.price, r.volume) == (100, 10)
    assert auction.clear(buys, sells, 92).price == 90
    assert auction.clear(buys, sells, 95).price == 90  # 並べば安い方


def test_auction_without_cross_returns_midpoint_quote():
    result = auction.clear([auction.Order(5, 95)], [auction.Order(5, 105)], 100, width=1000)
    assert result.volume == 0
    assert result.price == 100


def test_afford_never_exceeds_available_money(monkeypatch):
    """量を同じ割合で減らしたとき、切り捨ての誤差で取り置けるお金を超えない。

    50島の一括シミュレーションで、運賃込み 250,771 を 250,770 しかない施設が取り置こうとして
    ターンが止まった組み合わせ。
    """
    monkeypatch.setattr(funds, "available", lambda island, payer: 250770)
    out = Market._afford(None, Payer("fac:29"), {"grain": (9029, 27777)}, 200, 0)
    assert 0 < sum(gross_cost(lim, q, 200, 0) for q, lim in out.values()) <= 250770

    rng = random.Random(1)
    for _ in range(2000):
        plan = {
            f"g{i}": (rng.randint(1, 50000), rng.randint(1, 3_000_000))
            for i in range(rng.randint(1, 4))
        }
        freight, tax = rng.choice([0, 150, 200, 777]), rng.choice([0, 500])
        cost = sum(gross_cost(lim, q, freight, tax) for q, lim in plan.values())
        money = rng.randint(1, cost)
        monkeypatch.setattr(funds, "available", lambda island, payer, m=money: m)
        out = Market._afford(None, Payer("fac:1"), plan, freight, tax)
        assert sum(gross_cost(lim, q, freight, tax) for q, lim in out.values()) <= money


def test_price_update_moves_toward_balance_within_band(game):
    ctx = type("Ctx", (), {"config": game.config})()
    band = game.config.get("market.band")
    assert price_update(1000, 100, 100, 0.5, ctx) > 1000  # 売り切れて買えない人がいれば上げる
    assert price_update(1000, 100, 100, 0.0, ctx) == 1000
    assert price_update(1000, 100, 60, 0.0, ctx) < 1000  # 売れ残れば下げる
    assert price_update(1000, 100, 0, 0.0, ctx) >= 1000 * (1 - band)


def test_late_sales_count_less_as_the_price_falls_below_the_floor():
    """売れ残りの投げ売りで売れた分は、値段が変動費を下回った幅に応じて少なく数える（少し下回った
    だけで全く数えなくなると、魚の余った島でどの漁港も同時に減産して飢饉になった）。"""
    assert late_weight(1100, 1000, 0.1) == 1.0
    assert late_weight(1000, 1000, 0.1) == 1.0
    assert late_weight(990, 1000, 0.1) == pytest.approx(0.9)
    assert late_weight(950, 1000, 0.1) == pytest.approx(0.5)
    assert late_weight(900, 1000, 0.1) == 0.0
    assert late_weight(100, 1000, 0.1) == 0.0  # 大きく下回れば数えない（作り続けて値段が崩れない）
    assert late_weight(0, 0, 0.1) == 1.0


# --- 帳簿 -------------------------------------------------------------------------


@pytest.fixture
def books(game):
    """経済の始まった島と、確かめの基準にする残高（ここから仕訳を足していく）。"""
    world = generate_world(1, 1, game.config, game.defs)
    island = next(iter(world.islands.values()))
    island.journal.clear()
    return island, dict(island.accounts), game.defs.journal_kinds()


def test_ledger_passes_for_proper_payments(books):
    island, opening, kinds = books
    ledger.pay(island, "gov", "hh:general", 1000, "wage")
    ledger.pay(island, "hh:general", "gov", 300, "tax_income")
    assert ledger.check(island, opening, kinds) == []


def test_ledger_catches_entry_between_different_items(books):
    """1. 仕訳が同じ持ち物の勘定の間にない（お金が出し手なしに生まれる。3の確かめにもかかる）。"""
    island, opening, kinds = books
    debit, credit = ledger.deposit(island, "hh:general"), ledger.TREASURY_ACCOUNT
    island.journal.append(JournalEntry(kind="wage", debit=debit, credit=credit, amount=500))
    island.accounts[debit] = island.accounts.get(debit, 0) + 500
    island.accounts[credit] = island.accounts.get(credit, 0) - 500
    problems = ledger.check(island, opening, kinds)
    assert any("形の正しくない仕訳" in p for p in problems)
    assert any("生まれた・消えた額" in p for p in problems)


def test_ledger_catches_balance_changed_without_entry(books):
    """2. 前の残高と仕訳から求めた残高が、今の残高と違う。"""
    island, opening, kinds = books
    name = ledger.deposit(island, "hh:general")
    island.accounts[name] = island.accounts.get(name, 0) + 1
    problems = ledger.check(island, opening, kinds)
    assert any(f"勘定 {name}" in p for p in problems)


def test_ledger_catches_unknown_kind_and_bad_amount(books):
    island, opening, kinds = books
    ledger.pay(island, "gov", "hh:general", 10, "no_such_kind")
    island.journal.append(
        JournalEntry(kind="wage", debit="gov/treasury/cb", credit="cb/issued/treasury", amount=0)
    )
    problems = ledger.check(island, opening, kinds)
    assert any("定義されていない仕訳の種類" in p for p in problems)
    assert any("形の正しくない仕訳" in p for p in problems)


def test_ledger_catches_negative_deposit(books):
    """4. 預金と国庫がマイナスにならない（払えるかの確かめを飛ばした支払い）。"""
    island, opening, kinds = books
    held = ledger.money(island, "hh:general")
    ledger.pay(island, "hh:general", "gov", held + 1, "tax_consumption")
    problems = ledger.check(island, opening, kinds)
    assert any("マイナス" in p for p in problems)


def test_ledger_rejects_malformed_posts(books):
    island, *_ = books
    with pytest.raises(ledger.LedgerError):
        ledger.post(island, "gov/treasury/cb", "hh:general/deposit/bank:1", 1, "wage")
    with pytest.raises(ledger.LedgerError):
        ledger.post(island, "gov/treasury/cb", "cb/issued/treasury", -1, "wage")


# --- 家計の食料と消費財 -------------------------------------------------------------


def test_food_split_prefers_cheap_goods_within_offers():
    """好みの献立 D: 好みが同じなら安い財を多く、少し高いだけの財にもかなり割り振る（LB-20）。"""
    values = {"grain": 1.0, "fish": 1.2}
    nutritions = {"grain": 1000, "fish": 1000}
    prefs = {"grain": 0.5, "fish": 0.5}
    got = food_split(100_000, values, nutritions, {"grain": 80_000, "fish": 80_000}, prefs, 1)
    assert got["grain"] > got["fish"] > 0
    assert sum(got.values()) == 100_000
    assert got["grain"] <= 80_000 and got["fish"] <= 80_000
    # 重みは a ×（値段の比）^(−e): 1 : 1/1.2
    assert abs(got["fish"] / got["grain"] - 1 / 1.2) < 0.01


def test_food_split_buys_a_small_offer_up_to_the_preference():
    """少しだけ売りに出た財も、好みの分までは買い切る（売りの量は上限としてだけ使う。前の式は
    重みに売りの量を掛け、大和帝国 T101 の魚 84.5 トンのうち 0.4 トンしか買われなかった。#16）。
    止まった分は残りの財で分け直す。"""
    values = {"grain": 1.0, "fish": 1.0, "food": 1.0}
    nutritions = {"grain": 1000, "fish": 1000, "food": 1000}
    prefs = {"grain": 0.45, "fish": 0.10, "food": 0.45}
    caps = {"grain": 10**9, "fish": 2_000, "food": 10**9}
    got = food_split(100_000, values, nutritions, caps, prefs, 1)
    assert got["fish"] == 2_000  # 好みの分（1 割＝10,000）より少ないので売りの量を買い切る
    assert sum(got.values()) == 100_000
    assert abs(got["grain"] - got["food"]) <= 1  # 止まった分は残りで同じ重みで分け直す


def test_food_split_puts_shortfall_on_cheapest():
    values = {"grain": 1.0, "fish": 2.0}
    nutritions = {"grain": 1000, "fish": 500}
    got = food_split(100_000, values, nutritions, {"grain": 10_000, "fish": 20_000}, {}, 1)
    # 売りに出ている分（栄養で 10_000 + 10_000）を詰めて、残りは一番安い財に足す
    assert got["fish"] == 20_000
    assert got["grain"] == 90_000


def test_food_cheapest_fills_cheapest_per_nutrition_first():
    """一番安く満たす献立 L: 栄養あたりの値段（質を足さない）の安い順に、売りの量まで詰める。"""
    values = {"grain": 1.0, "food": 0.8}  # 質を足すと加工食品が安い
    costs = {"grain": 1.2, "food": 1.6}  # 栄養あたりは穀物が安い
    nutritions = {"grain": 1000, "food": 1000}
    caps = {"food": 100_000, "grain": 30_000}
    assert food_cheapest(50_000, values, costs, nutritions, caps) == {
        "grain": 30_000,
        "food": 20_000,
    }
    # 足りない分は、売りに出ている財のうち質を足した値段の一番安い財に足す
    got = food_cheapest(50_000, values, costs, nutritions, {"grain": 30_000})
    assert got == {"grain": 50_000}


def test_upgrade_food_mixes_within_budget():
    cheap, best = {"grain": 10_000}, {"food": 10_000}
    unit = {"grain": 100, "food": 300}  # 費用 1000 と 3000
    assert upgrade_food(cheap, best, unit, 500) == cheap
    assert upgrade_food(cheap, best, unit, 5000) == best
    mixed = upgrade_food(cheap, best, unit, 2000)
    assert mixed == {"grain": 5_000, "food": 5_000}
    assert upgrade_food(cheap, {"food": 10_000}, {"grain": 300, "food": 100}, 0) == {"food": 10_000}


def _shares(basket, nutritions):
    total = sum(q * nutritions[g] for g, q in basket.items())
    return {g: q * nutritions[g] / total for g, q in basket.items()}


def _start_menu(game, prefs):
    from hakoniwa_core.economy.prices import initial_prices

    defs = game.defs
    start = initial_prices(defs, game.config)
    foods = sorted(g for g, d in defs.goods.items() if "food" in d)
    nutritions = {g: int(defs.goods[g]["food"]["nutrition"] * 1000) for g in foods}
    qualities = {g: defs.goods[g]["food"].get("quality", 0) for g in foods}
    values = {
        g: start[g] / (defs.goods[g]["food"]["nutrition"] * (1 + qualities[g])) for g in foods
    }
    return FoodMenu(
        values=values,
        nutritions=nutritions,
        qualities=qualities,
        unit={g: start[g] for g in foods},
        limits={g: start[g] for g in foods},
        rate=0,
        tax=0,
        elasticity=game.config.get("household.food_elasticity"),
        prefs=prefs,
    )


def test_food_prefs_reach_the_target_at_start_prices(game):
    """島の初めの値段で、たっぷり食べられる家計の栄養の割合が目標（household.food_target）になる。
    所得の高い層ほど穀物の割合が低い（rich の目標へ寄る）。"""
    target = game.config.get("household.food_target")
    rich = game.config.get("household.food_target_rich_income")
    plenty = 10**12
    grains = []
    for income, goal in ((1.0, target["base"]), (2.0, None), (rich, target["rich"])):
        menu = _start_menu(game, food_prefs(game.defs, game.config, income))
        caps = dict.fromkeys(menu.values, plenty)
        orders = food_orders(1_000_000, caps, menu, plenty, plenty)
        shares = _shares({g: q for g, (q, _) in orders.items()}, menu.nutritions)
        if goal is not None:
            for good, share in goal.items():
                assert abs(shares.get(good, 0) - share) < 0.005, (income, good, shares)
        assert shares["fish"] > 0
        grains.append(shares["grain"])
    assert grains[0] > grains[1] > grains[2]


def test_wanted_food_matches_the_order_once_it_is_offered(game):
    """売りに出ていない財の買いたい量は、その財だけ要る量いっぱい売りに出ていたとしたときの注文の
    量（同じ式）。売りに出れば、その量を注文する（前はどの財もたっぷりある献立で数え、売りに出た
    ときの注文と数百倍ずれた）。"""
    menu = _start_menu(game, food_prefs(game.defs, game.config, 1.0))
    need = 1_000_000
    caps = {"grain": 2 * need, "food": 2 * need, "meat": 2 * need, "fish": 0}
    budget = 10**12
    enough = need * 1000 // menu.nutritions["fish"]
    wanted = food_orders(need, {**caps, "fish": enough}, menu, budget, budget)["fish"][0]
    offered = food_orders(need, {**caps, "fish": 2 * need}, menu, budget, budget)["fish"][0]
    assert wanted > 0
    assert abs(wanted - offered) <= 1


def test_food_orders_reach_the_need_for_a_tiny_layer(game):
    """人数の少ない層（要る栄養が 1000 分の 1 の単位で数個）でも、4 つの食料に分けた丸めで要る量を
    割らない（資本家階級ができたばかりの島で、毎ターン少しずつ飢えた）。"""
    menu = _start_menu(game, food_prefs(game.defs, game.config, 1.0))
    caps = dict.fromkeys(menu.values, 1000)
    for need in range(1, 40):
        plan = food_orders(need, caps, menu, 10**9, 10**9)
        got = sum(q * menu.nutritions[g] // 1000 for g, (q, _) in plan.items())
        assert got >= need, (need, plan)


def test_consumer_tiers_spend_about_the_budget():
    tiers = consumer_tiers(10_000, 100, [0.95, 1.0, 1.05], 0, 0)
    limits = [limit for _, limit in tiers]
    assert limits == sorted(limits, reverse=True)
    for k in range(1, len(tiers) + 1):  # 指値が k 番目の段まで約定したときに使う額
        price = tiers[k - 1][1]
        spent = sum(q for q, _ in tiers[:k]) * price // 1000
        assert 9_500 <= spent <= 10_000
