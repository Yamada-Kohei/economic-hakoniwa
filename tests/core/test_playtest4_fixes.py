"""試し遊び（ターン383）で見つかった不具合の直し。"""

from __future__ import annotations

import copy
from types import SimpleNamespace

from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import OWNERS, banks, household
from hakoniwa_core.log import format_log
from hakoniwa_core.modules import failures
from hakoniwa_core.modules.market import Market
from tests.core.test_finance import books_ok, context, island_of, open_books, order
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401
from tests.core.test_rules import add, home, make_island, run, with_economy

# --- 余った穀物が捨てられるのに食品工場が「穀物不足」（ターン383の1） -------------------


def test_expiring_batches_are_taken_last_when_asked():
    """last_until を渡すと、このターンの終わりに捨てられる組は、ほかの組が尽きてから取り出す。"""
    goods: dict = {}
    goods["grain"] = [[1, 300], [5, 500], [9, 400]]
    assert stock.take_batches(goods, "grain", 600, last_until=1) == [[5, 500], [9, 100]]
    assert goods["grain"] == [[1, 300], [9, 300]]
    assert stock.take_batches(goods, "grain", 500, last_until=1) == [[9, 300], [1, 200]]
    assert goods["grain"] == [[1, 100]]
    # 渡さなければ古い順
    assert stock.take_batches(goods, "grain", 100) == [[1, 100]]
    assert "grain" not in goods


def test_input_needs_do_not_count_grain_that_spoils_tonight(game):
    """このターンの終わりに捨てられる原料は、次のターンに使えないので手元に数えない。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    f = add(island, "food_factory", 8, 8)
    ctx = SimpleNamespace(defs=game.defs, config=game.config, turn=20)
    power = game.config.get("power.good")
    want = dict(Market._input_needs(ctx, f, power))["grain"]
    life = game.defs.goods["grain"]["shelf_life"]
    stock.add(f.stock, "grain", want, ctx.turn - life, game.defs.goods)  # 今夜捨てられる
    assert dict(Market._input_needs(ctx, f, power))["grain"] == want


def test_factory_buys_grain_that_keeps_until_it_is_used(game):
    """在庫に置く工場には、今夜捨てられる穀物を回さない（その場で食べる家計に先に回す）。

    工場のそばの農場には今夜捨てられる穀物と少しの新しい穀物、遠くの農場には新しい穀物がある。
    道のりの近い順に割り当てると、工場はそばの農場の古い穀物を買い、使う前に捨てられていた。
    """
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    near = add(island, "farm", 9, 8)
    far = add(island, "farm", 4, 4)
    factory = add(island, "food_factory", 8, 8)
    with_economy(game, island)
    turn = 1  # 試験の世界は、ターン 0 から回してターン 1 になる
    life = game.defs.goods["grain"]["shelf_life"]
    near.stock = {"grain": [[turn - life, 6000], [turn, 2000]]}
    far.stock = {"grain": [[turn, 20_000]]}
    factory.stock = {}
    run(game, island, ["market"])
    bought = sum(t.quantity for t in island.trades if t.buyer == f"fac:{factory.id}")
    assert bought > 0
    sold_old = sum(
        t.quantity for t in island.trades if t.seller == f"fac:{near.id}" and t.good == "grain"
    )
    assert sold_old > 0
    # 工場が買った穀物は、今夜捨てられずに手元に残る
    assert stock.total(factory.stock, "grain") == bought


def test_grain_that_spoils_tonight_is_not_counted_as_bought(game):
    """売りに出ている穀物が今夜捨てられる物だけなら、工場が買えた分も使えないので、品不足として
    知らせる。次のターンまで持つ穀物が工場の指値より高く売れ残っていたなら、原料が割高と知らせる。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    farm = add(island, "farm", 9, 8)
    factory = add(island, "food_factory", 8, 8)
    with_economy(game, island)
    life = game.defs.goods["grain"]["shelf_life"]
    farm.stock = {"grain": [[1 - life, 20_000]]}
    factory.stock = {}
    run(game, island, ["market"])
    assert any(t.buyer == f"fac:{factory.id}" for t in island.trades)
    assert "grain" not in factory.stock  # 買えた分は今夜捨てられた
    assert factory.economy["short"] == {"grain": "supply"}

    island = make_island(game)
    home(game, island, 5, 6, 6000)
    farm = add(island, "farm", 9, 8)
    factory = add(island, "food_factory", 8, 8)
    with_economy(game, island)
    farm.stock = {"grain": [[1 - life, 20_000], [1, 20_000]]}
    factory.stock = {}
    farm.economy["ask"] = {"grain": island.economy.prices["grain"] * 2}  # 値幅の上端で売りに出す
    island.economy.prices["food"] = 1  # 食品は売れず、工場は今の値段までしか買わない（国庫直轄）
    run(game, island, ["market"])
    assert stock.total(farm.stock, "grain") > 0  # 新しい穀物は売れ残った
    assert factory.economy["short"] == {"grain": "price"}


# --- 清算した銀行の預金保護の額（ターン383の2） -------------------------------------


def test_liquidating_a_bank_without_deposits_pays_no_deposit_insurance(game, warmed):
    """預金のない銀行の穴（中央銀行からの借り入れ）を政府が埋めた分は、預金保護に数えず、
    肩代わりとして別に出す（預金 0 の銀行の清算で「預金保護 92,478」と出ていた）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "mixed")  # 銀行が2つある島
    ctx = context(game, world)
    bank, other = banks.active_banks(island)[:2]
    worth = banks.net_worth(island, game.config, bank, world.turn)
    borrow = max(0, worth) + 50_000_000  # 中央銀行から借りて配当に出し、債務超過にする
    ledger.create(island, ledger.CB, ledger.CB_LOAN, bank, borrow, "cb_loan")
    ledger.pay(island, ledger.CB, bank, borrow, "cb_loan")
    ledger.pay(island, bank, household(OWNERS), borrow, "dividend")
    for entity, amount in banks.depositors_of(island, bank).items():  # 預金をみな移す
        ledger.move_deposit(island, entity, bank, other, amount, "bank_transfer")
    assert banks.deposits_of(island, bank) == 0
    f = banks.facility_of(island, bank)
    open_books(island)
    failures.transfer(island, ctx, bank, other, liquidation=True)
    done = next(e for e in island.log if e.kind == "bank_liquidated")
    assert done.args["insurance"] == 0
    covered = next(e for e in island.log if e.kind == "bank_debts_covered")
    assert covered.args["amount"] > 0
    assert (covered.args["x"], covered.args["y"]) == (f.x, f.y)
    assert "肩代わり" in format_log(game.defs, covered.kind, covered.args)
    books_ok(game, island)


def test_deposit_insurance_is_at_most_the_deposits_left(game, warmed):
    """預金のある銀行の小さな穴は、これまでどおりみな預金保護に数える。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "mixed")
    ctx = context(game, world)
    bank, other = banks.active_banks(island)[:2]
    worth = banks.net_worth(island, game.config, bank, world.turn)
    ledger.pay(island, bank, household(OWNERS), worth + 1_000_000, "dividend")
    open_books(island)
    failures.transfer(island, ctx, bank, other, liquidation=False)
    done = next(e for e in island.log if e.kind == "bank_transferred")
    assert done.args["insurance"] > 0
    assert not any(e.kind == "bank_debts_covered" for e in island.log)


# --- 国債の政策を変えたログ（ターン183から残っていた点） -------------------------------


def test_bond_policy_log_names_only_what_changed(game, warmed):
    """国債の政策の命令のログには、変えた項目だけを出す（期間の割合と売れ残りは前の値も）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    order(game, world, island, "set_bond_policy", {"unsold": "cut"})
    shown = {e.kind: format_log(game.defs, e.kind, e.args) for e in island.log}
    assert "bond_terms_set" not in shown and "bond_issue_ordered" not in shown
    assert "→ 支出を減らす" in shown["bond_unsold_set"]

    terms = {"bond_5y": 1, "bond_10y": 3}
    order(game, world, island, "set_bond_policy", {"terms": terms, "issue": 2_000_000})
    shown = {e.kind: format_log(game.defs, e.kind, e.args) for e in island.log}
    assert "bond_unsold_set" not in shown
    before, after = shown["bond_terms_set"].split("→")
    assert "5年国債 25%・10年国債 75%" in after and "5年国債 25%" not in before
    assert "を 2,000,000 追加で出します" in shown["bond_issue_ordered"]
