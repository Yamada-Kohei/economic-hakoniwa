"""バランス調整の回5（国庫と国債）の試験。"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import Payer, funds
from tests.core.test_finance import context, island_of
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401

# --- 国庫の払う順番 -----------------------------------------------------------------------


def _treasury(island, amount):
    """国庫を amount にする（差は資産を持つ層とやり取りする）。"""
    gap = amount - ledger.money(island, ledger.GOV)
    if gap > 0:
        ledger.pay(island, "hh:owners", ledger.GOV, gap, "capital_injection")
    elif gap < 0:
        ledger.pay(island, ledger.GOV, "hh:owners", -gap, "equity_return")


def test_payroll_group_is_cut_in_proportion_and_old_arrears_wait(game, warmed):
    """国庫が足りないとき、今月の人件費と運営費の組は見積もりに同じ割合を掛けて取り置き、1つの項目
    （国営の農場など）だけが押し出されない。昔の未払い賃金は今月の分と失業手当の後に回る。"""
    island = copy.deepcopy(island_of(warmed, "market"))
    order = game.config.get("fiscal.payment_order")
    estimates = {
        "bonds": 100,
        "arrears": 5_000,
        "civil_servants": 1_000,
        "state_facilities": 3_000,
        "education": 0,
        "security": 0,
        "unemployment": 500,
        "construction": 800,
    }
    _treasury(island, 100 + 2_000)
    funds.open_treasury(island, estimates, order, {})
    reserved = funds.fiscal_state(island)["reserved"]
    assert reserved["bonds"] == 100
    assert reserved["civil_servants"] == 500
    assert reserved["state_facilities"] == 1_500  # 公務員と同じ半分
    assert reserved["unemployment"] == reserved["arrears"] == reserved["construction"] == 0
    # 足りれば今月の分と失業手当の後に、残りで昔の未払いを返す
    _treasury(island, 100 + 4_000 + 500 + 1_000)
    funds.open_treasury(island, estimates, order, {})
    reserved = funds.fiscal_state(island)["reserved"]
    assert reserved["state_facilities"] == 3_000
    assert reserved["unemployment"] == 500
    assert reserved["arrears"] == 1_000
    assert reserved["construction"] == 0


def test_rescue_keeps_the_whole_payroll_group(game, warmed):
    """銀行の救済に出せる額は、fiscal.rescue_after の入った組の全部の項目を国庫に残す。"""
    from hakoniwa_core.modules import failures

    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    funds.open_treasury(island, {}, game.config.get("fiscal.payment_order"), {})
    _treasury(island, 10**12)
    used = funds.fiscal_state(island)["used"]
    base = failures.rescue_room(island, ctx)
    used["state_facilities"] = 70_000  # rescue_after（公務員）と同じ組
    with_group = failures.rescue_room(island, ctx)
    used["construction"] = 90_000  # 組の後ろ
    assert failures.rescue_room(island, ctx) == with_group
    assert base - with_group >= 70_000


# --- 国債の利回りと銀行の入札 ---------------------------------------------------------------


def test_yield_counts_only_interest_paid_outside_the_central_bank(game, warmed):
    """返す力の比は、中央銀行以外の持ち手に払う利子だけを数える（中央銀行の持ち分は市場の外）。
    中央銀行が銀行から国債を買い取ると、比と市場の利回りが下がる。"""
    from hakoniwa_core.economy import banks, debt
    from hakoniwa_core.modules.bonds import bond_yield

    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    island.economy.finance[debt.REVENUE] = [10**7] * 12  # 年の収入 1.2億
    before = debt.burden(island)
    assert before == debt.market_interest(island) * 12 / (12 * 10**7)
    # 銀行の持つ国債を全部、中央銀行に移す（額面のまま）
    for bank in banks.bank_list(island):
        for number, face in debt.holdings_of(island, bank).items():
            item = ledger.bond_item(number)
            ledger.cancel(island, bank, item, ledger.GOV, face, "bond_trade")
            ledger.create(island, ledger.CB, item, ledger.GOV, face, "bond_trade")
    after = debt.burden(island)
    assert after < before
    # 市場の利回りは、比が閾値を超えた分だけ上がる
    island.scratch.pop("bond_burden", None)
    low = bond_yield(island, 12, ctx)
    island.economy.finance[debt.REVENUE] = [10**3] * 12
    island.scratch.pop("bond_burden", None)
    assert debt.burden(island) > game.config.get("bonds.burden_threshold") or after == 0
    assert bond_yield(island, 12, ctx) >= low


def test_bank_bids_all_spare_reserves_at_rising_yields(game, warmed):
    """銀行は手元に残す分を除いた準備預金を全部入札に回し、買うほど高い利回りを求める（偏りの上乗せ）。
    計画経済の国営銀行は上乗せしない。"""
    from hakoniwa_core.economy import banks
    from hakoniwa_core.modules.bonds import Bonds, auction_budget, concentration

    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    filled, rate = Bonds._auction(island, ctx, 10**15, 500)
    total = sum(filled.values())
    spare = sum(auction_budget(island, ctx, b) for b in banks.active_banks(island))
    assert spare > 0
    assert total <= spare
    assert total >= spare - len(banks.active_banks(island)) * game.config.get(
        "banking.auction_steps"
    )
    # 前のターンに中央銀行から借りて今ターン返した分は、手元に残す（満期のお金で買い直さない）
    bank = banks.active_banks(island)[0]
    before = auction_budget(island, ctx, bank)
    island.scratch.setdefault(banks.OVERNIGHT, {})[bank] = before // 2
    assert auction_budget(island, ctx, bank) == before - before // 2
    island.scratch.pop(banks.OVERNIGHT)
    free, premium = concentration(ctx, island)
    assert premium > 0
    assert rate > 500  # 全部売れるまで買うと、偏りの上乗せが付く
    planned = island_of(world, "planned")
    assert concentration(ctx, planned) == (1.0, 0.0)


# --- 標準の計画 -----------------------------------------------------------------------------


def test_plan_raises_taxes_when_the_state_cannot_pay_wages(game, warmed):
    """国が賃金を払いきれていなければ、見直しのターンに所得税と消費税を上げる。ゆとりがあり比が低ければ
    建国の税率まで戻す。"""
    from hakoniwa_core import autoplan
    from hakoniwa_core.economy import arrears

    island = copy.deepcopy(island_of(warmed, "market"))
    turn = autoplan.BUDGET_TURNS * 10 + autoplan.TAX_TURN
    arrears.add_wages(island, ledger.GOV, "hh:general", 1_000)
    kind, payload = autoplan._tax_order(island, game.config, turn)
    assert kind == "set_taxes"
    assert payload["income_tax"] == 10 + autoplan.TAX_STEP
    assert payload["consumption_tax"] == 5 + autoplan.TAX_STEP
    assert autoplan._tax_order(island, game.config, turn + 1) is None  # 見直しのターンだけ
    # 払い終えて、国庫にゆとりがあり比が低ければ戻す
    ledger.cancel(
        island, "hh:general", arrears.WAGE_ARREARS, ledger.GOV, 1_000, "wage_arrears_paid"
    )
    island.policy.income_tax = 1400
    island.stats["bonds.burden"] = 0.0
    island.stats["treasury"] = 10**15
    kind, payload = autoplan._tax_order(island, game.config, turn)
    assert payload["income_tax"] == 14 - autoplan.TAX_STEP
    assert "consumption_tax" not in payload  # 建国の税率より下げない


def test_plan_turns_state_farms_to_grain_and_opens_the_warehouse_in_a_famine(game, warmed):
    """食べ物が足りない月、標準の計画は国営の農場を穀物に替え、国の倉庫の食べ物を出し、穀物を高めの
    指値で買い入れる。"""
    from hakoniwa_core import autoplan, stock

    island = copy.deepcopy(island_of(warmed, "planned"))
    farm = next(f for f in island.facilities if f.kind == "farm" and f.built)
    farm.recipe = "cotton"
    stock.add(island.warehouse, "fish", 5_000, 49, game.defs.goods)
    island.stats["food.ratio"] = 0.8
    island.stats["food.need"] = 100.0
    orders = autoplan._food_orders(island, game.defs, game.config, 50)
    kinds = [(k, p.get("side"), p.get("good"), p.get("recipe")) for k, p in orders]
    assert ("change_recipe", None, None, "default") in kinds
    assert ("warehouse_trade", "sell", "fish", None) in kinds
    buy = next(p for k, p in orders if k == "warehouse_trade" and p["side"] == "buy")
    assert buy["good"] == "grain"
    assert buy["limit"] > island.economy.prices["grain"]
    assert buy["quantity"] == 20.0  # 足りない2割


# --- 政府の預金 ---------------------------------------------------------------------------


def test_treasury_surplus_is_deposited_at_banks_and_drawn_back(game, warmed):
    """国庫の余りは市中銀行に政府の預金として預け（準備預金が増える）、要るときに引き出す。政府の
    預金は国のお金に数え、銀行の預金（準備の最低限・預金者・お金の量）と銀行の純資産は変えない。"""
    from hakoniwa_core.economy import banks
    from hakoniwa_core.modules.bonds import auction_budget

    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    _treasury(island, 10_000_000)
    bank = banks.active_banks(island)[0]
    reserves = banks.reserves_of(island, bank)
    deposits = banks.deposits_of(island, bank)
    worth = banks.net_worth(island, game.config, bank, world.turn)
    money = ledger.aggregates(island.accounts)
    ctx = context(game, world)
    budget = auction_budget(island, ctx, bank)
    assert funds.deposit_surplus(island, 4_000_000, {bank: 1}) == 6_000_000
    assert auction_budget(island, ctx, bank) == budget  # 預かった分は国債に替えずに持つ
    assert ledger.money(island, ledger.GOV) == 4_000_000
    assert banks.reserves_of(island, bank) == reserves + 6_000_000
    assert funds.government_money(island) == funds.spendable(island) == 10_000_000
    assert banks.deposits_of(island, bank) == deposits
    assert ledger.GOV not in banks.depositors_of(island, bank)
    assert banks.net_worth(island, game.config, bank, world.turn) == worth
    assert ledger.aggregates(island.accounts) == money
    # 国庫で足りない分は、払うときに政府の預金から戻す
    funds.open_treasury(island, {}, game.config.get("fiscal.payment_order"), {})
    funds.pay(island, Payer(ledger.GOV), "hh:general", 5_000_000, "grant")
    assert funds.government_money(island) == 5_000_000
    assert funds.deposited(island)[bank] == 5_000_000
    # 国庫が残す額に足りなければ、預け入れのかわりに引き出す
    assert funds.deposit_surplus(island, 5_000_000, {bank: 1}) == -5_000_000
    assert ledger.money(island, ledger.GOV) == 5_000_000


# --- 中央銀行の独立（回5 のカード「両方」） ---------------------------------------------------


def test_planned_economy_cannot_make_its_central_bank_independent(game, warmed):
    """計画経済の国立銀行は政府の一部なので、独立させる命令は通らない。独立したまま計画経済に
    移った国（前の版の世界も）は、中央銀行の段で政府の一部に戻る。"""
    from hakoniwa_core.modules.central_bank import check_independence, end_for_planned
    from hakoniwa_core.registry import OrderFailed

    world = copy.deepcopy(warmed)
    planned = island_of(world, "planned")
    with pytest.raises(OrderFailed) as failed:
        check_independence(planned, {"independent": True}, game.defs)
    assert failed.value.reason == "reason_cb_independence_planned"
    check_independence(island_of(world, "market"), {"independent": True}, game.defs)
    planned.policy.cb_independent = True
    end_for_planned(planned, context(game, world))
    assert not planned.policy.cb_independent
    assert planned.log[-1].kind == "cb_dependent_planned"


def test_independent_central_bank_buys_from_short_banks_and_sells_back(game, warmed):
    """独立した中央銀行は、準備預金の足りない銀行が売りに出した国債を市場の値段で買い（買いオペ）、
    足りない銀行がなければ持っている国債を、持ち高の少ない銀行へ市場の利回りで売り戻す（売りオペ）。
    国が出す国債を直接買うことはしない。"""
    from hakoniwa_core.economy import banks, debt
    from hakoniwa_core.modules.bonds import Bonds

    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    island.policy.cb_independent = True
    ctx = context(game, world)
    bank = banks.active_banks(island)[0]
    held = sum(debt.holdings_of(island, bank).values())
    assert held > 0
    cb_before = sum(debt.holdings_of(island, ledger.CB).values())
    # 準備預金を最低限より大きく下げる（預金の流出の代わり。持ち高の多くを売らせる）
    short = banks.excess_reserves(island, game.config, bank) + held * 2 // 3
    ledger.pay(island, bank, ledger.GOV, short, "tax_income")
    assert banks.excess_reserves(island, game.config, bank) < 0
    Bonds()._secondary(island, ctx)
    bought = sum(debt.holdings_of(island, ledger.CB).values()) - cb_before
    assert bought > 0
    assert sum(debt.holdings_of(island, bank).values()) == held - bought
    assert island.log[-1].kind == "cb_open_market_bought"
    assert banks.excess_reserves(island, game.config, bank) >= -held // 100  # 売った代金で戻った
    # 足りない銀行がなければ、持ち高の減った銀行が余りのお金で買う分を売り戻す
    ledger.pay(island, ledger.CB, bank, held, "cb_loan")
    island.scratch.pop("bond_stats", None)
    Bonds()._secondary(island, ctx)
    sold = island.scratch["bond_stats"]["cb_sold"]
    assert 0 < sold <= bought  # 売り戻すのは買いオペで買った分まで（独立の前の持ち分は売らない）
    assert sum(debt.holdings_of(island, ledger.CB).values()) == cb_before + bought - sold
    assert island.economy.finance.get("cb_open_market", 0) == bought - sold
