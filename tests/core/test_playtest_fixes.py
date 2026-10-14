"""フェーズ2-3 の前に、試し遊び（ターン183・233）で見つかった不具合の直し。

ターンを回す試験では、記録の段が毎ターン帳簿を確かめる（合わなければその場で止まる）。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.economy import CIVIL_SERVANTS, Payer, banks, debt, funds, household
from hakoniwa_core.log import format_log
from hakoniwa_core.modules import failures
from hakoniwa_core.modules.firms import EntryExit
from hakoniwa_core.naming import facility_name
from hakoniwa_core.state import (
    OWNER_STATE,
    ZONE_INDUSTRIAL,
    ZONE_NONE,
    ZONE_OFFICE,
    Facility,
)
from tests.core.test_finance import (
    books_ok,
    context,
    island_of,
    open_books,
    order,
    run,
)
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401


def _break(game, world, island, bank):
    """bank の融資を貸し倒れにして債務超過にする（自己資本は、処理するターン world.turn + 1 の
    国債の時価で見る）。"""
    turn = world.turn + 1
    for loan in banks.loans_by_lender(island, bank):
        banks.write_off(island, loan, banks.outstanding(island, loan))
    banks.drop_settled(island)
    worth = banks.net_worth(island, game.config, bank, turn)
    if worth >= 0:
        ledger.pay(island, bank, household("owners"), worth + 1000, "dividend")
    assert banks.net_worth(island, game.config, bank, turn) < 0


def _empty_treasury(island):
    """国庫を、このターンに公務員に使った額の分だけ残して空にする（銀行の預金に移す）。"""
    keep = funds.spent(island).get(CIVIL_SERVANTS, 0)
    extra = ledger.money(island, ledger.GOV) - keep
    if extra > 0:
        ledger.pay(island, ledger.GOV, household("general"), extra, "grant")


# --- 国債の不履行の後の救済（ターン183の1） -------------------------------------------


def test_rescue_keeps_next_turn_priority_payments(game, warmed):
    """救済と預金保護に国庫から出すのは、次のターンの公務員と利払いを残した額まで。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    used = {CIVIL_SERVANTS: 5000}
    funds.fiscal_state(island)["used"].update(used)
    keep = 5000 + debt.due(island, ctx.turn + 1, redemptions=False)
    keep += keep * game.config.get("fiscal.reserve_margin")
    room = failures.rescue_room(island, ctx)
    assert room <= max(0, ledger.money(island, ledger.GOV) - int(keep)) + 1


def test_bailout_without_money_bails_in_and_leaves_bank_solvent(game, warmed, monkeypatch):
    """国庫で埋めきれない債務超過は預金の切り捨てで埋め、銀行が債務超過のまま残らない。"""
    from hakoniwa_core.modules.bonds import Bonds

    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    monkeypatch.setattr(Bonds, "raise_funds", lambda *args, **kwargs: 0)  # 国債も売れない
    open_books(island)
    bank = banks.active_banks(island)[0]
    _break(game, world, island, bank)
    _empty_treasury(island)
    failures.bailout(island, ctx, bank)
    worth = banks.net_worth(island, game.config, bank, ctx.turn)
    assert worth >= 0
    assert any(e.kind == "bank_bailed_in" for e in island.log)
    books_ok(game, island)


def test_single_bank_transfer_resolves_in_place(game, warmed):
    """引き継ぐ銀行のない島で清算を選ぶと、黙って救済にせず、政府が引き取って立て直す。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    active = banks.active_banks(island)
    for other in active[1:]:  # 銀行を1つにする
        banks.facility_of(island, other).economy["failed"] = world.turn + 100
    bank = active[0]
    open_books(island)
    _break(game, world, island, bank)
    f = banks.facility_of(island, bank)
    f.economy["failed"] = world.turn
    island.economy.finance["resolve"] = {bank: failures.LIQUIDATION}
    ctx = context(game, world)
    failures.Failures._resolve(island, ctx)
    assert bank in banks.bank_list(island)
    assert f.owner == OWNER_STATE and "failed" not in f.economy
    assert banks.net_worth(island, game.config, bank, ctx.turn) >= 0
    assert any(e.kind == "bank_resolved_in_place" for e in island.log)
    assert not any(e.kind == "bank_bailed_out" for e in island.log)
    books_ok(game, island)


def test_ending_independence_restores_underwriting(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    assert island.policy.bond_unsold == "underwrite"
    order(game, world, island, "set_central_bank_independence", {"independent": True})
    assert island.policy.bond_unsold == "cut"
    island.economy.finance["cb_switched"] = -100  # 戻せない期間を飛ばす
    order(game, world, island, "set_central_bank_independence", {"independent": False})
    assert island.policy.bond_unsold == "underwrite"


def test_unsold_choice_made_while_independent_is_kept(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    order(game, world, island, "set_central_bank_independence", {"independent": True})
    order(game, world, island, "set_bond_policy", {"unsold": "cut"})
    island.economy.finance["cb_switched"] = -100
    order(game, world, island, "set_central_bank_independence", {"independent": False})
    assert island.policy.bond_unsold == "cut"


# --- 止まった理由（ターン183の3） -----------------------------------------------------


def test_facility_without_wage_money_says_wages(game, warmed):
    """国庫が空で賃金を払えない国庫直轄の施設は「賃金の資金不足」で止まる（労働者不足ではない）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "planned")
    island.policy.budget = {k: 0 for k in island.policy.budget}
    island.policy.bond_unsold = "cut"
    island.policy.cb_independent = True
    ledger.pay(island, ledger.GOV, household("general"), ledger.money(island, ledger.GOV), "grant")
    game.engine.run_turn(world)
    statuses = {f.status for f in island.facilities if f.built}
    assert "wages" in statuses
    assert any(e.kind == "facility_stopped_wages" for e in island.log) or "wages" in statuses


# --- 参入しないわけ（ターン183の4） -----------------------------------------------------


def test_entry_block_is_recorded_and_logged_once(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    for tile in island.tiles:  # 工業団地をなくす
        if tile.zone == ZONE_INDUSTRIAL:
            tile.zone = ZONE_NONE
    island.economy.finance.pop("entry_block", None)  # 前のターンまでのわけは消しておく
    island.log.clear()  # 前のターンのログも
    ctx = context(game, world)
    entry = EntryExit()
    blocks = []
    for _ in range(3):
        entry._enter(island, ctx)
        blocks.append(island.economy.finance.get("entry_block"))
    logged = [e for e in island.log if e.kind == "private_entry_blocked"]
    if blocks[0] is None:
        pytest.skip("参入の合図がない")
    assert blocks[0]["reason"] in {
        "no_zone",
        "unprofitable",
        "materials",
        "inputs_missing",
        "inputs_short",
        "inputs_capped",
        "owners_short",
        "loan_refused",
        "no_bank",
    }
    assert len(logged) == 1  # わけが変わらなければ、ログは1度だけ
    text = format_log(game.defs, logged[0].kind, logged[0].args)
    assert "参入しません" in text and "{" not in text


def test_bank_entry_block_names_missing_office(game, warmed):
    """免許を出しても空いたオフィス街がなければ、そのわけを残して知らせる。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    for tile in island.tiles:
        if tile.zone == ZONE_OFFICE and island.facility_at(tile.x, tile.y) is None:
            tile.zone = ZONE_NONE
    island.economy.finance["bank_license"] = True
    island.economy.finance.pop("bank_entry_block", None)
    island.policy.bank_entry = "license"
    EntryExit()._bank_entry(island, context(game, world))
    assert island.economy.finance["bank_entry_block"]["reason"] == "no_office"
    entry = next(e for e in island.log if e.kind == "bank_entry_blocked")
    assert "オフィス街" in format_log(game.defs, entry.kind, entry.args)


# --- 設備投資補助と民営の工事（ターン233の1・2） ---------------------------------------


def test_capital_subsidy_is_paid_at_entry(game, warmed):
    """設備投資補助のある民営工場は、参入のときに補助を先に受け取る（建設費の残りだけを集めて、
    工事の途中で資金が尽きないように）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    island.policy.budget["subsidies"] = 10**12
    goods = {
        g
        for f in game.defs.facilities.values()
        for r in f["recipes"].values()
        for g in r["outputs"]
    }
    island.policy.subsidies = [
        {"kind": "capital", "good": g, "rate": 8000, "until": world.turn + 100} for g in goods
    ]
    paid = None
    for _ in range(20):
        run(game, world, 1)
        sites = [f for f in island.facilities if f.ext.get("private_entry") and not f.built]
        if any(f.ext.get("subsidy_paid") for f in sites):
            paid = next(f for f in sites if f.ext.get("subsidy_paid"))
            break
    if paid is None:
        pytest.skip("補助の付いた参入が起きなかった")
    assert paid.ext["subsidy_rate"] == 8000


def test_private_site_messages_name_company_funds(game, warmed):
    from hakoniwa_core.modules.construction import Construction

    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    site = Facility(
        id=island.next_facility_id(),
        kind="consumer_goods_factory",
        x=0,
        y=0,
        ext={"private_entry": True, "short_wages": True, "no_workers_turns": 1},
    )
    Construction._watch_workers(island, site, stalled=True)
    assert island.log[-1].kind == "construction_no_wages_private"
    assert "会社の資金" in format_log(game.defs, island.log[-1].kind, island.log[-1].args)


# --- 銀行の開業と名前（ターン233の3） ---------------------------------------------------


def test_state_bank_has_its_own_message_and_a_distinct_name(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "mixed")
    first = banks.facility_of(island, banks.bank_list(island)[0])
    twin = Facility(
        id=island.next_facility_id(),
        kind=first.kind,
        x=first.x,
        y=first.y,
        owner=first.owner,
        built=True,
        ext={"serial": first.ext.get("serial", first.id) + 1},
    )
    island.facilities.append(twin)
    a = facility_name(game.defs, game.config, island, first)
    b = facility_name(game.defs, game.config, island, twin)
    assert a != b
    assert "国営銀行" in game.defs.name("messages", "bank_opened_state")


# --- 国債のログ（ターン233の4、183の5） -------------------------------------------------


def test_central_bank_bond_trade_result_is_logged(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    order(game, world, island, "set_monetary_policy", {"bond_trade": 3_000_000})
    game.engine.run_turn(world)
    kinds = {e.kind for e in island.log}
    assert kinds & {"cb_bonds_bought", "cb_bonds_bought_short"}
    assert "bonds.cb_bought" in island.stats


def test_bond_policy_log_shows_terms(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    order(game, world, island, "set_bond_policy", {"terms": {"bond_5y": 1, "bond_10y": 1}})
    entry = next(e for e in island.log if e.kind == "bond_policy_set")
    text = format_log(game.defs, entry.kind, entry.args)
    assert "5年国債 50%" in text and "10年国債 50%" in text


def test_redemption_is_logged(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    for _ in range(14):  # 建国の1年国債の満期を越える
        run(game, world, 1)
        if any(e.kind in ("bonds_redeemed", "bonds_redeemed_any") for e in island.log):
            break
    else:
        pytest.skip("償還がなかった")
    entry = next(e for e in island.log if e.kind.startswith("bonds_redeemed"))
    assert "償還" in format_log(game.defs, entry.kind, entry.args)


def test_payer_names(game):
    """使う名前が定義にあること（文面の書き忘れの確かめ）。"""
    for kind in (
        "bank_bailed_in",
        "bank_resolved_in_place",
        "facility_stopped_wages",
        "status_wages",
        "private_entry_blocked",
        "bank_entry_blocked",
        "cb_bonds_bought",
        "cb_bonds_sold_short",
        "bonds_redeemed",
    ):
        assert game.defs.name("messages", kind) != kind
    assert Payer(ledger.GOV).entity == ledger.GOV
