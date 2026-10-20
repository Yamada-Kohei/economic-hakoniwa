"""試し遊び1回目（5 か国、ターン 1〜50）の報告のうち、貿易の直し: 国の契約の外貨は中央銀行の外貨から
先に払う（2026-10-20 の決定「国の契約だけ」）、海外の商人から買った額と輸入の数え方。"""

from __future__ import annotations

import copy

from hakoniwa_core import ledger, stock
from hakoniwa_core.log import format_log
from hakoniwa_core.modules.trade import check
from hakoniwa_core.trade import contracts, traders
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.units import QUANTITY_SCALE, money_of
from tests.core.test_trade import game as game  # noqa: F401
from tests.core.test_trade import run as run_world
from tests.core.test_trade import traded as traded  # noqa: F401

# --- 国の契約の外貨は、中央銀行の外貨から先に払う ------------------------------------------


def _give_reserve(world, holder, currency, amount):
    """試験のため、発行した島の中央銀行から holder の中央銀行に外貨 currency を渡す。"""
    value = fxrates.to_own(holder, currency, amount)
    fxmoney.transfer(
        world, currency, (currency, ledger.CB), (holder.id, ledger.CB), amount, "fx_exchange", value
    )


def _contract(world, buyer, seller, good, quantity, price, turns):
    """buyer が seller の通貨で買い、seller が受けた契約（次のターンから効く）。"""
    payload = {
        "partner": seller.id,
        "side": "buy",
        "good": good,
        "quantity": quantity,
        "price": price,
        "currency": seller.id,
        "turns": turns,
        "delivery": "warehouse",
    }
    assert contracts.propose(world, buyer, payload, world.turn) is None
    c = contracts.contracts(world)[-1]
    assert contracts.accept(world, seller, c, world.turn) is None
    return c


def test_contract_is_paid_from_the_central_bank_reserves_first(game, traded):
    """外貨建ての契約の払いは、中央銀行が持っているその通貨を今のレートで先に政府へ回す。介入で外貨
    を買っている国は中央銀行の受け身の売りが出ないので、前は為替の市場で外貨を買えず、外貨準備が
    あるのに契約が毎ターン届かなかった（試し遊び1回目）。"""
    world = copy.deepcopy(traded[0])
    seller, buyer = world.islands[1], world.islands[2]
    good, turns = "steel", 4
    price = seller.economy.prices[good]
    qty = 20
    due = money_of(price, qty * QUANTITY_SCALE)
    stock.add(
        seller.warehouse, good, (qty * turns + 5) * QUANTITY_SCALE, world.turn, game.defs.goods
    )
    ledger.pay(buyer, ledger.CB, ledger.GOV, 10**12, "trade")  # 国庫の自国通貨は足りている
    _give_reserve(world, buyer, seller.id, due * (turns + 2))
    buyer.policy.fx_intervention = {str(seller.id): 1000}  # 介入している（受け身の注文は出ない）
    had = fxmoney.holding(buyer, ledger.GOV, seller.id)
    c = _contract(world, buyer, seller, good, qty, price, turns)
    dealt = []
    run_world(
        game, world, turns, lambda w: dealt.append(w.islands[2].stats[f"fx.dealt.{seller.id}"])
    )
    assert c["delivered"] == c["qty"] * turns and c["short"] == 0
    assert "fx" not in c
    # 中央銀行が政府へ回した外貨は、中央銀行の受け身の売りとして数える（受け身の買いはない）
    assert all(d <= 0 for d in dealt)
    assert -sum(dealt) >= due * turns - had
    assert check(world) == []


def test_contract_short_of_foreign_currency_tells_where_it_came_from(game, traded):
    """中央銀行の外貨と為替の市場で足りないときは、政府の手持ち・中央銀行から・為替の市場で用意できた
    額と足りない額を、ログと契約の記録（fx）に残す。わけの文は為替介入に触れる。"""
    world = copy.deepcopy(traded[0])
    seller, buyer = world.islands[1], world.islands[2]
    good = "steel"
    stock.add(seller.warehouse, good, 5 * QUANTITY_SCALE, world.turn, game.defs.goods)
    ledger.pay(buyer, ledger.CB, ledger.GOV, 10**12, "trade")
    _give_reserve(world, buyer, seller.id, 50_000)
    buyer.policy.fx_intervention = {str(seller.id): 1000}
    had = fxmoney.holding(buyer, ledger.GOV, seller.id)
    reserve = fxmoney.holding(buyer, ledger.CB, seller.id)
    # 1単位の値段を、中央銀行の外貨と為替の市場の厚みを大きく超える額にする
    depth = fxrates.depth(buyer, seller.id, game.config)
    price = had + reserve + 20 * depth
    c = _contract(world, buyer, seller, good, 1, price, 2)
    run_world(game, world, 1)
    fx = c["fx"]
    assert fx["turn"] == world.turn and fx["need"] == price
    assert fx["had"] == had and fx["reserve"] == reserve
    assert fx["short"] > 0
    assert fx["need"] == fx["had"] + fx["reserve"] + fx["market"] + fx["short"]
    assert c["short"] > 0 and c["delivered"] == c["qty"] - c["short"]  # 用意できた外貨の分は届く
    [entry] = [e for e in buyer.log if e.kind == "contract_short"]
    assert entry.args["reason"] == "contract_short_fx_bought"
    text = format_log(game.defs, entry.kind, entry.args)
    assert f"中央銀行の外貨から {reserve:,}" in text and f"{fx['short']:,} 足りません" in text
    assert "為替介入" in text
    # 中央銀行の外貨は使い切った（受け身の売りとして数える）
    assert fxmoney.holding(buyer, ledger.CB, seller.id) <= fx["market"]
    assert buyer.stats[f"fx.dealt.{seller.id}"] <= -reserve
    assert check(world) == []


# --- 海外の商人から買った額と輸入 -----------------------------------------------------------


def test_planned_merchant_purchases_count_the_gap_outside_imports(game, traded):
    """公定価格のある島が海外の商人から買うと、島の買い手は公定価格で払い、商人の値段との差は政府が
    払う。輸入（GDP）は島の買い手が払った額で数え、海外の商人から買った額と国際収支は差も含める
    （試し遊び1回目で、海外の商人から買った額が、それを含むはずの輸入より大きく見えた。画面の名前と
    説明で分ける）。"""
    world = copy.deepcopy(traded[0])
    planned = next(i for i in world.islands.values() if traders.is_planned(i))
    gaps = []

    def keep(w):
        s = w.islands[planned.id].stats
        paid = sum(t.amount for t in w.islands[planned.id].trades if t.seller == "merchant")
        gap = s.get("trade.merchant_gap", 0)
        assert s["trade.merchant"] == paid + gap
        assert s["trade.imports"] >= paid
        gaps.append(gap)

    run_world(game, world, 3, keep)
    assert any(gaps)
