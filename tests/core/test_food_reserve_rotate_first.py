"""国の穀物の備蓄の回転備蓄（下書き。food_reserve.rotate_first、既定は切。会議 #5233）。

入のときは、入れ替えの組は買い足しが埋まって新しい組が倉庫に入った分だけ売る（買ってから
売る）。あわせて、倉庫が目安の半分（floor_fraction）を割ったら、買い足しの指値を
いつもの値段 × (1 + market.band) まで上げる。切のときは今までと完全に同じ。
"""

from __future__ import annotations

from hakoniwa_core import stock
from hakoniwa_core.economy import Payer, ledger, reserve
from hakoniwa_core.market.auction import Order, Result
from hakoniwa_core.modules.market import (
    RESERVE_REFILL,
    RESERVE_RELEASE,
    RESERVE_ROTATE,
    RESERVE_ROTATE_ORDER,
    Book,
    Market,
    Party,
)
from hakoniwa_core.units import scale_price
from tests.core.test_recession_round1 import context, reserve_island, reserve_orders
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）

STORE = "warehouse"
WAREHOUSE = "warehouse"


def _rotate_on(game, **kw):
    """回転備蓄を入にする（game は module 共有なので、dict ごと置き換える）。"""
    setting = {"enabled": True, "floor_fraction": 0.5}
    setting.update(kw)
    game.config.raw["food_reserve"]["rotate_first"] = setting
    return setting


def _rotate_off(game):
    """回転備蓄を切にする（module 共有の game に前の試験の「入」が残らないように）。"""
    game.config.raw["food_reserve"]["rotate_first"] = {
        "enabled": False,
        "floor_fraction": 0.5,
    }


def _stocked_island(game):
    """古い組（入れ替えの時期）と新しい組が目安の半分ずつ入った島と、そのターンを返す。"""
    island = reserve_island(game)
    spec = game.config.get("food_reserve")
    life = game.defs.goods["grain"]["shelf_life"]
    goal = reserve.target(island, game.defs, game.config)
    stock.add(island.warehouse, "grain", goal // 4, 0, game.defs.goods)  # 古い組
    stock.add(island.warehouse, "grain", goal // 4, 10, game.defs.goods)
    turn = life - spec["rotate"]  # 古い組の日持ちの残りが rotate になったターン
    return island, goal, turn


def test_rotate_first_off_keeps_old_behaviour(game):
    """既定は切: 入れ替えは今までどおり今の値段で売り、買い足しは今までどおりの量と指値。"""
    _rotate_off(game)
    island, goal, turn = _stocked_island(game)
    spec = game.config.get("food_reserve")
    sells, books_by_good = reserve_orders(game, island, turn)
    price = island.economy.prices["grain"]
    assert sells == [("grain", goal // 4, price)]
    ((order, party),) = books_by_good["grain"].buys
    short = goal - 2 * (goal // 4)
    assert order.quantity == int(short * spec["refill"]) + goal // 4 and order.limit == price
    assert party.payer.item == "warehouse"
    note = island.scratch[RESERVE_ROTATE]
    assert note["offered"] == note["due"] == goal // 4 and not note["raised"]


def test_rotate_first_on_sells_nothing_without_credit(game):
    """入で、買い足しがまだ埋まっていなければ（credit が無い）、入れ替えの組は売らない。
    買い足し自体は、入れ替えの分も含めて今までどおり出す。"""
    _rotate_on(game)
    island, goal, turn = _stocked_island(game)
    spec = game.config.get("food_reserve")
    sells, books_by_good = reserve_orders(game, island, turn)
    assert sells == []
    ((order, _),) = books_by_good["grain"].buys
    short = goal - 2 * (goal // 4)
    assert order.quantity == int(short * spec["refill"]) + goal // 4  # 入れ替えの分も買う
    note = island.scratch[RESERVE_ROTATE]
    assert note["due"] == goal // 4 and note["offered"] == 0 and note["credit"] == 0


def test_rotate_first_on_sells_up_to_credit(game):
    """入では、前のターンまでに買い足しが埋まった分（credit）までだけ入れ替えを売る。"""
    _rotate_on(game)
    island, goal, turn = _stocked_island(game)
    island.economy.ext[reserve.ROTATE_CREDIT] = goal // 8
    sells, _ = reserve_orders(game, island, turn)
    price = island.economy.prices["grain"]
    assert sells == [("grain", goal // 8, price)]
    # credit が入れ替えの量以上あれば、全部売る
    island.economy.ext[reserve.ROTATE_CREDIT] = goal
    sells, _ = reserve_orders(game, island, turn)
    assert sells == [("grain", goal // 4, price)]


def test_rotate_first_on_raises_limit_below_half_target(game):
    """入で倉庫が目安の半分を割っていたら、買い足しの指値は
    いつもの値段 × (1 + market.band) まで上がる。半分以上あれば今までどおり。"""
    _rotate_on(game)
    island = reserve_island(game)
    goal = reserve.target(island, game.defs, game.config)
    price = island.economy.prices["grain"]
    band = game.config.get("market.band")
    stock.add(island.warehouse, "grain", goal // 4, 10, game.defs.goods)  # 半分未満・新しい組だけ
    _, books_by_good = reserve_orders(game, island, 11)
    ((order, _),) = books_by_good["grain"].buys
    assert order.limit == scale_price(price, 1 + band)
    assert island.scratch[RESERVE_ROTATE]["raised"]
    # 半分以上あれば指値は今までどおり（いつもの値段）
    stock.add(island.warehouse, "grain", goal // 2, 10, game.defs.goods)
    _, books_by_good = reserve_orders(game, island, 11)
    ((order, _),) = books_by_good["grain"].buys
    assert order.limit == price
    assert not island.scratch[RESERVE_ROTATE]["raised"]


def _remember(game, island, turn, note, refill_filled, rotate_sold, release=False):
    """_remember_food に、買い足しと入れ替えの売りの埋まりを渡して credit の更新を見る。"""
    price = island.economy.prices["grain"]
    refill = Order(note["want"], price)
    rotate = Order(note["offered"], price)
    party = Party(STORE, ledger.GOV, Payer(ledger.GOV, WAREHOUSE))
    book = Book(buys=[(refill, party)], sells=[(rotate, party)])
    result = Result(
        price=price,
        volume=refill_filled + rotate_sold,
        demand=note["want"],
        supply=note["offered"],
        low=price,
        high=price,
        bought=[refill_filled],
        sold=[rotate_sold],
    )
    island.scratch[RESERVE_ROTATE] = note
    island.scratch[RESERVE_ROTATE_ORDER] = rotate
    island.scratch[RESERVE_REFILL] = refill
    if release:
        island.scratch[RESERVE_RELEASE] = {
            "qty": 0,
            "reason": "hunger",
            "price": price,
            "base": price,
        }
    Market()._remember_food(island, context(game, turn), {"grain": book}, {"grain": result})
    return island.scratch[RESERVE_ROTATE]


def _note(**kw):
    note = {
        "due": 400,
        "offered": 0,
        "credit": 0,
        "want": 1000,
        "renew": 400,
        "limit": 0,
        "raised": False,
        "placed": True,
        "refill_filled": None,
        "renew_filled": None,
        "rotate_sold": None,
    }
    note.update(kw)
    return note


def test_rotate_first_credit_accrues_from_refill_fills(game):
    """買い足しが埋まった分が credit に積まれ（入れ替えの分から先に数える）、入れ替えの
    売りが埋まった分が引かれる。放出のターンは 0 に戻る。"""
    _rotate_on(game)
    island = reserve_island(game)
    # 買い足し 1000 のうち 700 埋まり: 入れ替えの分 400 が先に数えられ、credit は 400
    note = _remember(game, island, 11, _note(), refill_filled=700, rotate_sold=0)
    assert note["refill_filled"] == 700 and note["renew_filled"] == 400
    assert island.economy.ext[reserve.ROTATE_CREDIT] == 400
    # 次のターン: 入れ替えを 150 売って埋まり、買い足しは埋まらず → 400 - 150 = 250
    note = _remember(
        game, island, 12, _note(offered=150, credit=400), refill_filled=0, rotate_sold=150
    )
    assert note["renew_filled"] == 0
    assert island.economy.ext[reserve.ROTATE_CREDIT] == 250
    # 放出のターン: credit は 0 に戻る
    _remember(game, island, 13, _note(credit=250), refill_filled=0, rotate_sold=0, release=True)
    assert island.economy.ext[reserve.ROTATE_CREDIT] == 0


def test_rotate_first_credit_untouched_when_off(game):
    """切のときは credit を動かさない（埋まりの記録だけ）。"""
    _rotate_off(game)
    island = reserve_island(game)
    island.economy.ext[reserve.ROTATE_CREDIT] = 123
    note = _remember(game, island, 11, _note(), refill_filled=700, rotate_sold=0)
    assert note["renew_filled"] == 400  # 記録は付く
    assert island.economy.ext[reserve.ROTATE_CREDIT] == 123  # 値は動かない
