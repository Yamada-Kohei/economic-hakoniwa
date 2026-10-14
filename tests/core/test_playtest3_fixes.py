"""国債の債務不履行の罰と、試し遊び（ターン283・333）で見つかった不具合の直し。

ターンを回す試験では、記録の段が毎ターン帳簿を確かめる（合わなければその場で止まる）。
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import (
    CONSTRUCTION,
    OWNERS,
    Payer,
    banks,
    debt,
    funds,
    household,
)
from hakoniwa_core.economy.groundwork import prepare
from hakoniwa_core.log import format_log
from hakoniwa_core.modules import failures
from hakoniwa_core.modules.banking import Banking
from hakoniwa_core.modules.bonds import Bonds
from hakoniwa_core.modules.education import _recount
from hakoniwa_core.modules.firms import EntryExit
from hakoniwa_core.modules.market import Market
from hakoniwa_core.modules.production import Production
from hakoniwa_core.state import ORDER_DONE, OWNER_PRIVATE
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
from tests.core.test_rules import add, home, make_island, with_economy
from tests.core.test_rules import run as run_stages

# --- 債務不履行の罰 -----------------------------------------------------------------


def _default(game, world, economy="market"):
    """島の国債の利払いを止めて不履行にし、(島, 国債で損をした銀行) を返す。"""
    island = island_of(world, economy)
    holders = {
        b for b in banks.bank_list(island) if debt.holdings_of(island, b) and economy != "planned"
    }
    state = order(game, world, island, "set_bond_payment", {"stop": True})
    assert state.status == ORDER_DONE
    assert any(e.kind == "bond_default_ordered" for e in island.log)
    return island, holders


def test_default_closes_the_bond_market_and_stops_lending(game, warmed):
    """不履行のターンから、銀行は国債を買わず（入札に来ない）、損をした銀行は新しく貸さない。"""
    world = copy.deepcopy(warmed)
    island, hit = _default(game, world)
    config = game.config
    assert hit
    exclusion = config.get("bonds.default_exclusion_turns")
    crunch = config.get("banking.default_crunch_turns")
    assert island.economy.finance["default_turn"] == world.turn
    assert debt.market_closed(island, config, world.turn + 1)
    assert debt.reopen_turn(island, config) == world.turn + exclusion
    for bank in hit:
        f = banks.facility_of(island, bank)
        assert f.economy["crunch_until"] == world.turn + crunch
        assert banks.lending_stopped(island, bank, world.turn + 1)
    log = next(e for e in island.log if e.kind == "bond_default_penalty")
    assert log.args["banks"] == len(hit)
    assert log.args["turn"] == world.turn + exclusion - 1
    text = format_log(game.defs, log.kind, log.args)
    assert f"ターン {world.turn + exclusion - 1} まで" in text and "15%" in text


def test_closed_auction_is_underwritten_or_cut(game, warmed):
    """入札で売れない間は、政策どおり中央銀行が引き受けるか（銀行は買わない）、支出を減らす。"""
    world = copy.deepcopy(warmed)
    island, _ = _default(game, world)
    ctx = context(game, world)
    island.log.clear()
    held = {b: sum(debt.holdings_of(island, b).values()) for b in banks.bank_list(island)}
    cb = sum(debt.holdings_of(island, ledger.CB).values())
    term = next(iter(game.defs.bond_terms()))
    assert Bonds().issue(island, ctx, 1_000_000, term, "bonds_issued") == 1_000_000
    assert {b: sum(debt.holdings_of(island, b).values()) for b in held} == held
    assert sum(debt.holdings_of(island, ledger.CB).values()) == cb + 1_000_000
    assert [e.kind for e in island.log] == ["bonds_issued_closed"]
    # 売れ残りを引き受けない政策なら売れず、いつまで売れないかを知らせる
    island.policy.bond_unsold = "cut"
    island.log.clear()
    assert Bonds().issue(island, ctx, 1_000_000, term, "bonds_issued") == 0
    (log,) = island.log
    assert log.kind == "bonds_unsold_closed"
    assert log.args["turn"] == debt.reopen_turn(island, game.config) - 1


def test_bond_market_reopens_and_lending_resumes(game, warmed):
    """期間が終わると、銀行はまた国債を買い、止めていた融資を再開する（どちらもログで知らせる）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    config = game.config
    # 次のターンで期間が終わるところにする
    island.economy.finance["default_turn"] = (
        world.turn + 1 - config.get("bonds.default_exclusion_turns")
    )
    f = banks.facility_of(island, banks.active_banks(island)[0])
    f.economy["crunch_until"] = world.turn + 1
    run(game, world, 1)
    kinds = [e.kind for e in island.log]
    assert "bonds_market_reopened" in kinds
    assert kinds.count("bank_lending_resumed") == 1
    assert not debt.market_closed(island, config, world.turn + 1)
    assert "crunch_until" not in f.economy


def test_stopped_bank_only_rolls_over_principal(game, warmed):
    """新しい融資を止めた銀行は、返す元金の借り換えの分しか貸さない（断った分で利ざやが上がる）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "mixed")
    ctx = context(game, world)
    bank = banks.active_banks(island)[0]
    state = next(f for f in island.facilities if f.built and f.owner != OWNER_PRIVATE)
    lent = {b: [0, 0] for b in banks.bank_list(island)}
    huge = 10**12
    assert Banking._room(island, ctx, state, bank, huge, lent, 700) > 700
    banks.facility_of(island, bank).economy["crunch_until"] = ctx.turn + 1
    lent = {b: [0, 0] for b in banks.bank_list(island)}
    assert Banking._room(island, ctx, state, bank, huge, lent, 700) == 700
    assert lent[bank][1] >= huge - 700
    assert Banking._room(island, ctx, state, bank, huge, lent, 0) == 0


def test_private_entry_waits_for_lending(game, warmed):
    """どの銀行も新しく貸さず、資産を持つ層だけでは出資しきれない参入は、そのわけで止まる。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "mixed")
    ctx = context(game, world)
    for i, bank in enumerate(banks.active_banks(island)):
        banks.facility_of(island, bank).economy["crunch_until"] = ctx.turn + 5 + i
    owners = funds.available(island, Payer(household(OWNERS)))
    reason, args = EntryExit()._finance(island, ctx, owners * 2 + 1000, 10**12)
    assert reason == "credit_crunch" and args["turn"] == ctx.turn + 4
    text = format_log(game.defs, "entry_block_credit_crunch", args)
    assert f"ターン {ctx.turn + 4} まで" in text


def test_planned_island_banks_keep_lending_after_default(game, warmed):
    world = copy.deepcopy(warmed)
    island, _ = _default(game, world, "planned")
    assert not any("crunch_until" in f.economy for f in island.facilities)
    kinds = [e.kind for e in island.log]
    assert "bond_default_penalty_no_banks" in kinds


def test_bonds_issued_after_valuation_are_not_counted_as_gains(game, warmed):
    """決算の段の後（破綻の段の救済など）に出した国債は、次のターンの評価損益に益として入らない。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    island.scratch[debt.VALUED] = True
    book = dict(island.economy.finance.get("bond_book", {}))
    term = next(iter(game.defs.bond_terms()))
    sold = Bonds().issue(island, ctx, 1_000_000, term, "bonds_issued")
    assert sold > 0
    after = island.economy.finance["bond_book"]
    assert sum(after.values()) - sum(book.values()) == sold


# --- 救済の後の国庫（ターン333の2） ---------------------------------------------------


def test_rescue_refills_the_treasury_for_next_turn(game, warmed, monkeypatch):
    """救済で国庫から出したターンは、処理の後に次のターンの支出の分まで国債を出して戻す。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    calls = []
    original = Bonds.top_up

    def spy(self, island, ctx):
        calls.append(ledger.money(island, ledger.GOV))
        original(self, island, ctx)

    monkeypatch.setattr(Bonds, "top_up", spy)
    bank = banks.active_banks(island)[0]
    f = banks.facility_of(island, bank)
    f.economy["failed"] = world.turn
    island.economy.finance["resolve"] = {bank: failures.BAILOUT}
    for loan in banks.loans_by_lender(island, bank):
        banks.write_off(island, loan, banks.outstanding(island, loan))
    banks.drop_settled(island)
    open_books(island)
    ctx = context(game, world)
    before = ledger.money(island, ledger.GOV)
    failures.Failures().run_island(island, ctx)
    assert any(e.kind == "bank_bailed_out" for e in island.log)
    assert calls and calls[0] < before
    books_ok(game, island)


# --- 都市に発展したログ（ターン333の3） -----------------------------------------------


def test_settlement_grew_is_logged_when_the_town_level_rises(game):
    island = make_island(game)
    town = home(game, island, 5, 6, 2300, level="village")  # 上限 2,400 の8割を超えている
    add(island, "steel_mill", 8, 8, stock={"iron_ore": 100_000, "coal": 50_000})  # 人手が足りない
    home(game, island, 2, 13, 20000, level="metropolis")  # 通勤圏の外（国庫を厚くする）
    with_economy(game, island)
    held = island.economy.housing["stock"]
    for good, qty in game.defs.settlements["town"]["materials"].items():
        stock.add(held, good, qty, 0, game.defs.goods)
    run_stages(game, island, ["employment", "housing"])
    assert town.ext["level"] == "town"
    grew = [e for e in island.log if e.kind == "settlement_grew"]
    assert [(e.args["x"], e.args["y"], e.args["level"]) for e in grew] == [(5, 6, "town")]
    # 人数が見た目の段階を超えただけでは知らせない（育つのは町の段階が上がったとき）
    island.log.clear()
    island.tile(5, 6).population = 8500
    run_stages(game, island, ["population"])
    assert not any(e.kind == "settlement_grew" for e in island.log)


# --- 幸福度の重みの切り替え（ターン283の1） -------------------------------------------


def test_happiness_weight_change_is_announced_once(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    turns = game.config.get("population.blend_turns")
    island.economy.happiness_blend = turns
    island.economy.ext["happiness_from"] = "happiness_phase2"
    run(game, world, 1)
    (log,) = [e for e in island.log if e.kind == "happiness_weights_changed"]
    now = game.config.get("population.happiness")
    assert log.args["water"] == now["water"] and log.args["base_from"] == 25
    assert "上下水道" in format_log(game.defs, log.kind, log.args)
    assert island.economy.happiness_blend == turns - 1
    run(game, world, 1)  # 次のターンからは出さない
    assert not any(e.kind == "happiness_weights_changed" for e in island.log)


# --- 技術者の数（ターン283の2） -------------------------------------------------------


def test_engineer_counts_follow_retirement_and_training():
    island = SimpleNamespace(scratch={"engineers_employed": 738, "engineers_left": 0})
    _recount(island, 738, 0, 4)  # 4人引退して 734 人
    assert island.scratch["engineers_employed"] + island.scratch["engineers_left"] == 734
    assert island.scratch["engineers_employed"] <= 734
    island = SimpleNamespace(scratch={"engineers_employed": 400, "engineers_left": 200})
    _recount(island, 600, 10, 6)
    assert island.scratch["engineers_employed"] == 396
    assert island.scratch["engineers_left"] == 198 + 10
    island = SimpleNamespace(scratch={})
    _recount(island, 0, 5, 0)  # 雇用の段を通っていなければ何もしない
    assert island.scratch == {}


def test_engineers_employed_never_exceed_the_total(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    for _ in range(6):
        run(game, world, 1)
        assert island.stats["engineers.employed"] <= island.stats["engineers"]


# --- 公共事業費の取り置き（ターン283の3） ---------------------------------------------


def test_public_sites_do_not_reserve_money_for_goods_not_on_sale(game):
    """同じ公共事業費の工事は、合わせて売りに出ている量までしか注文しない（買えない分にお金を
    取り置いて、後の工事が「公共事業費が足りない」にならない）。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    first = add(island, "food_factory", 8, 8, built=False)
    second = add(island, "food_factory", 9, 9, built=False)
    need = fac.step_materials(game.defs, first)
    good = next(iter(need))
    island.economy.prices[good] = 1000  # 公共事業費は足りている
    on_sale = need[good] * 3 // 2  # 2つ目の工事の分は半分しか売りに出ていない
    ctx = SimpleNamespace(defs=game.defs, config=game.config, turn=1)
    books: dict = {}
    Market()._site_orders(island, ctx, books, {}, {good: on_sale}, 0)
    payer = Payer(ledger.GOV, CONSTRUCTION)
    # インフラの維持（先に注文する）と工事を合わせて、売りに出ている量まで
    ordered = sum(o.quantity for o, p in books[good].buys if p.payer == payer)
    assert ordered == on_sale
    sites = sum(o.quantity for o, p in books[good].buys if p.facility is not None)
    # 注文しなかった分は、値段の見直しでは買えなかった量に数える（売りに出ている量までの注文を、
    # 工事ごとに出していたときと同じ合図）
    assert island.scratch["unplaced"][good] == need[good] * 2 - sites
    assert "short_funds" not in first.ext and "short_funds" not in second.ext


# --- 食品工場の止まった理由（ターン283の4） -------------------------------------------


def test_input_bought_for_the_plan_is_not_a_shortage(game):
    """前のターンに買うつもりだった原料はそろっていて、売れ行きに合わせて少なく買っただけなら、
    止まった理由は原料不足ではなく売れ行き。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    f = add(island, "food_factory", 8, 8)
    f.economy["target"] = 300
    ctx = SimpleNamespace(defs=game.defs, config=game.config, turn=1)
    power = game.config.get("power.good")
    f.economy["input_plan"] = 0
    assert Production._prepare(f, ctx, power).cause == "target"
    f.economy["input_plan"] = 300  # 300 の分を買うつもりで、買えなかった
    assert Production._prepare(f, ctx, power).cause == "input:grain"


# --- 置いた学校の教材（ターン283の5） -------------------------------------------------


def test_placed_school_has_its_first_materials(game):
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    island.economy.ext.pop("version", None)
    prepare(island, game.defs, game.config, 3)
    school = next(
        f for f in island.facilities if f.kind == game.config.get("education.start_school")
    )
    power = game.config.get("power.good")
    for good, qty in fac.recipe_of(game.defs, school)["inputs"].items():
        if good != power:
            assert stock.total(school.stock, good) == qty
    have, short = fac.input_ratio(game.defs, school, power)
    assert have == fac.FULL and short is None
