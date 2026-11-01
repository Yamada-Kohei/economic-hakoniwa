"""お金の経済の部品（板寄せ・帳簿・家計の食料の割り振り）を、小さな入力で確かめる。"""

from __future__ import annotations

import random

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import Payer, funds
from hakoniwa_core.game import load_game
from hakoniwa_core.market import auction
from hakoniwa_core.modules.market import (
    Market,
    consumer_tiers,
    food_by_quality,
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
    values = {"grain": 1.0, "fish": 1.2}
    nutritions = {"grain": 1000, "fish": 1000}
    got = food_split(100_000, values, nutritions, {"grain": 80_000, "fish": 80_000}, 4)
    assert got["grain"] > got["fish"] > 0  # 少し高いだけの財にも少しは割り振る
    assert sum(got.values()) == 100_000
    assert got["grain"] <= 80_000 and got["fish"] <= 80_000


def test_food_split_puts_shortfall_on_cheapest():
    values = {"grain": 1.0, "fish": 2.0}
    nutritions = {"grain": 1000, "fish": 500}
    got = food_split(100_000, values, nutritions, {"grain": 10_000, "fish": 20_000}, 4)
    # 売りに出ている分（栄養で 10_000 + 10_000）を詰めて、残りは一番安い財に足す
    assert got["fish"] == 20_000
    assert got["grain"] == 90_000


def test_food_by_quality_fills_high_quality_first():
    qualities = {"grain": 1.0, "food": 1.5}
    values = {"grain": 1.0, "food": 1.2}
    nutritions = {"grain": 1000, "food": 1000}
    got = food_by_quality(50_000, qualities, values, nutritions, {"food": 30_000, "grain": 100_000})
    assert got == {"food": 30_000, "grain": 20_000}


def test_upgrade_food_mixes_within_budget():
    cheap, best = {"grain": 10_000}, {"food": 10_000}
    unit = {"grain": 100, "food": 300}  # 費用 1000 と 3000
    assert upgrade_food(cheap, best, unit, 500) == cheap
    assert upgrade_food(cheap, best, unit, 5000) == best
    mixed = upgrade_food(cheap, best, unit, 2000)
    assert mixed == {"grain": 5_000, "food": 5_000}
    assert upgrade_food(cheap, {"food": 10_000}, {"grain": 300, "food": 100}, 0) == {"food": 10_000}


def test_consumer_tiers_spend_about_the_budget():
    tiers = consumer_tiers(10_000, 100, [0.95, 1.0, 1.05], 0, 0)
    limits = [limit for _, limit in tiers]
    assert limits == sorted(limits, reverse=True)
    for k in range(1, len(tiers) + 1):  # 指値が k 番目の段まで約定したときに使う額
        price = tiers[k - 1][1]
        spent = sum(q for q, _ in tiers[:k]) * price // 1000
        assert 9_500 <= spent <= 10_000
