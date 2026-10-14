"""フェーズ2-2 の金融の部品: 融資、銀行の破綻処理、国債の不履行、倒産、参入、新しい命令。

ターンを回す試験では、記録の段が毎ターン帳簿を確かめる（合わなければその場で止まる）。
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from hakoniwa_core import ledger
from hakoniwa_core.autoplan import plan_turn, queue_orders
from hakoniwa_core.economy import OWNERS, Payer, banks, facility_entity, funds, household
from hakoniwa_core.economy.start import invest
from hakoniwa_core.game import load_game
from hakoniwa_core.modules import failures
from hakoniwa_core.modules.banking import Banking
from hakoniwa_core.modules.bonds import default_premium
from hakoniwa_core.modules.firms import EntryExit
from hakoniwa_core.orders import check_order
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
    ORDER_DONE,
    ORDER_FAILED,
    OWNER_PRIVATE,
    OWNER_STATE,
    ZONE_INDUSTRIAL,
    Facility,
    OrderState,
)
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.turn.context import StageContext

WARMUP = 40  # 融資と民営工場がそろうまで回すターン数


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture(scope="module")
def warmed(game):
    """標準の計画で WARMUP ターン回した3島（市場・混合・計画経済）の世界。試験ごとに写して使う。"""
    world = generate_world(1, 3, game.config, game.defs)
    run(game, world, WARMUP)
    return world


def run(game, world, turns, extra=None):
    """標準の計画で turns ターン回す。extra(island) は島ごとに足す命令（計画の後ろに並ぶ）。"""
    for _ in range(turns):
        for island in world.islands.values():
            queue_orders(island, plan_turn(island, game.defs, game.config, world.turn + 1))
            if extra is not None:
                queue_orders(island, extra(island))
        game.engine.run_turn(world)


def order(game, world, island, kind, payload):
    """命令を1つだけ並べて1ターン回し、その命令の状態を返す。"""
    island.orders = [OrderState(id=1, seq=1, kind=kind, payload=payload)]
    game.engine.run_turn(world)
    return island.orders[0]


def island_of(world, economy):
    return next(i for i in world.islands.values() if i.regime["economy"] == economy)


def context(game, world, stage="test"):
    return StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.registry,
        formulas=game.engine.formulas,
        turn=world.turn + 1,
        seed=world.seed,
        stage=stage,
        before=None,
    )


def open_books(island):
    """直接呼ぶ処理の帳簿を確かめられるように、今の残高を始まりにして仕訳を空にする。"""
    island.journal.clear()
    island.scratch["opening_accounts"] = dict(island.accounts)


def books_ok(game, island):
    opening = island.scratch["opening_accounts"]
    assert ledger.check(island, opening, game.defs.journal_kinds()) == []


# --- 融資 -----------------------------------------------------------------------


def test_working_loans_merge_per_lender_and_borrower(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    bank = banks.active_banks(island)[0]
    f = next(f for f in island.facilities if f.owner == OWNER_PRIVATE and f.built)
    borrower = facility_entity(f.id)
    open_books(island)
    before = len(island.loans)
    debt = banks.debt_of(island, borrower)
    money = ledger.money(island, borrower)
    first = banks.lend(island, bank, borrower, 1000, banks.WORKING, 6, world.turn, 500)
    second = banks.lend(island, bank, borrower, 2000, banks.WORKING, 6, world.turn, 500)
    assert first is second  # 同じ銀行からの運転資金は1件にまとめる（件数が増え続けないように）
    assert len(island.loans) <= before + 1
    assert banks.debt_of(island, borrower) == debt + 3000
    assert ledger.money(island, borrower) == money + 3000
    books_ok(game, island)


def _unexposed_private(island, bank):
    """bank から借りていない、できあがった民営施設の名前（勘定の持ち主）。"""
    return next(
        facility_entity(f.id)
        for f in island.facilities
        if f.owner == OWNER_PRIVATE
        and f.built
        and banks.exposure(island, bank, facility_entity(f.id)) == 0
    )


def test_large_exposure_keeps_but_does_not_grow_a_loan_over_the_limit(game, warmed):
    """大口融資の規制: 上限を超えた残高は、返す元金の分を借り換えて保てるが、増やせない。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    bank = banks.active_banks(island)[0]
    borrower = _unexposed_private(island, bank)
    limit = banks.exposure_limit(island, game.config, bank, world.turn)
    assert limit > 0
    assert banks.exposure_room(island, game.config, bank, borrower, world.turn) == limit
    banks.lend(island, bank, borrower, limit, banks.WORKING, 6, world.turn, 500)
    assert banks.exposure_room(island, game.config, bank, borrower, world.turn) == 0
    assert banks.exposure_room(island, game.config, bank, borrower, world.turn, due=700) == 700


def test_large_exposure_applies_to_private_facilities_only(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "mixed")
    ctx = context(game, world)
    bank = banks.active_banks(island)[0]
    lent = {b: [0, 0] for b in banks.bank_list(island)}
    huge = 10**12
    capital = banks.capital_room(island, game.config, bank, ctx.turn)
    private = island.facility(int(_unexposed_private(island, bank).split(":")[1]))
    state = next(f for f in island.facilities if f.owner == OWNER_STATE and f.built)
    room = banks.exposure_room(island, game.config, bank, facility_entity(private.id), ctx.turn)
    assert 0 < room < capital
    assert Banking._room(island, ctx, private, bank, huge, lent, 0) == room
    assert Banking._room(island, ctx, state, bank, huge, lent, 0) == capital


def test_construction_loans_wait_for_grace(game):
    world = generate_world(1, 3, game.config, game.defs)
    grace = game.config.get("loans.grace_turns")
    seen = {}
    for _ in range(120):
        for island in world.islands.values():
            queue_orders(island, plan_turn(island, game.defs, game.config, world.turn + 1))
        game.engine.run_turn(world)
        for island in world.islands.values():
            for e in island.log:
                if e.kind == "construction_done":
                    f = island.facility_at(e.args["x"], e.args["y"])
                    if f is not None and f.economy.get("entered") == world.turn:
                        seen[(island.id, f.id)] = (
                            world.turn,
                            banks.loans_by_borrower(island, facility_entity(f.id)),
                        )
        if seen:
            break
    assert seen, "民営工場が建たなかった"
    for done, loans in seen.values():
        for loan in loans:
            if loan.purpose == banks.CONSTRUCTION:
                assert loan.repay_from == done + 1 + grace


def test_arrears_bankrupt_private_facility(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    bank = banks.active_banks(island)[0]
    f = next(
        f
        for f in island.facilities
        if f.built
        and f.owner == OWNER_PRIVATE
        and f.x >= 0
        and f.kind != "bank"
        and "foreclosed" not in f.economy
        and "closing" not in f.economy
    )
    entity = facility_entity(f.id)
    loan = banks.lend(island, bank, entity, 10**9, banks.CONSTRUCTION, 120, world.turn, 500)
    # 借りたお金はもう使ってしまった（出資者に払い戻した）ことにする
    ledger.pay(island, entity, household(OWNERS), ledger.money(island, entity), "equity_return")
    loan.arrears = game.config.get("loans.arrears_limit") - 1
    loan.repay_from = world.turn  # 返せない額の元金が、このターンから返す時期に入る
    game.engine.run_turn(world)
    assert any(e.kind == "facility_bankrupt" for e in island.log)
    assert f.economy["foreclosed"]["bank"] == bank
    assert f.workers == 0 and banks.debt_of(island, entity) == 0


# --- 銀行の破綻処理 ---------------------------------------------------------------


def _break_bank(game, world, island):
    """銀行の融資の2割を貸し倒れにし、それで足りなければ債務超過になるまで貸し倒れにする。"""
    bank = max(banks.active_banks(island), key=lambda b: banks.loans_of(island, b))
    loans = sorted(banks.loans_by_lender(island, bank), key=lambda x: x.id)
    total = sum(banks.outstanding(island, x) for x in loans)
    cut = total // 5
    for loan in loans:
        if cut <= 0:
            break
        amount = min(cut, banks.outstanding(island, loan))
        banks.write_off(island, loan, amount)
        cut -= amount
    for loan in loans:
        if banks.net_worth(island, game.config, bank, world.turn) < 0:
            break
        banks.write_off(island, loan, banks.outstanding(island, loan))
    banks.drop_settled(island)
    worth = banks.net_worth(island, game.config, bank, world.turn)
    if worth >= 0:  # 融資が自己資本より少ない銀行は、配当の払いすぎで債務超過にする
        ledger.pay(island, bank, household(OWNERS), worth + 1000, "dividend")
    assert banks.net_worth(island, game.config, bank, world.turn) < 0
    return bank


def _depositors(island, bank):
    return {e: v for e, v in banks.depositors_of(island, bank).items() if not ledger.is_bank(e)}


@pytest.mark.parametrize("method", failures.METHODS)
def test_bank_resolution_keeps_insured_deposits_and_books(game, warmed, method):
    world = copy.deepcopy(warmed)
    island = island_of(world, "mixed")  # 銀行が2つある島
    ctx = context(game, world)
    open_books(island)
    bank = _break_bank(game, world, island)
    others = [b for b in banks.healthy_banks(island, game.config, ctx.turn) if b != bank]
    if method != failures.BAILOUT and not others:
        pytest.skip("引き継ぐ銀行がない")
    before = _depositors(island, bank)
    money_before = {e: ledger.money(island, e) for e in before}
    f = banks.facility_of(island, bank)
    f.economy["failed"] = ctx.turn
    if method == failures.BAILOUT:
        failures.bailout(island, ctx, bank)
        assert banks.net_worth(island, game.config, bank, ctx.turn) >= 0
        assert f.owner == OWNER_STATE and "failed" not in f.economy
    else:
        failures.transfer(island, ctx, bank, others[0], liquidation=method == failures.LIQUIDATION)
        assert bank not in banks.bank_list(island)
        assert banks.deposits_of(island, bank) == 0
    for entity, amount in before.items():
        insured = banks.insured(island, entity)
        protected = amount if insured is None else min(amount, insured)
        # 保護される分は必ず残る（削られるのは上限を超えた分だけ）
        assert ledger.money(island, entity) >= money_before[entity] - (amount - protected)
    books_ok(game, island)


def test_resolve_bank_order_needs_failed_bank(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f = banks.facility_of(island, banks.active_banks(island)[0])
    result = check_order(
        game.defs,
        game.registry,
        island,
        "resolve_bank",
        {"x": f.x, "y": f.y, "method": "bailout"},
    )
    assert result.reason == "reason_bank_not_failed"


# --- 国債 -------------------------------------------------------------------------


def test_bond_default_raises_yields_and_hits_bank_capital(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    bank = max(
        banks.bank_list(island), key=lambda b: banks.bond_value(island, game.config, b, world.turn)
    )
    yields_before = list(island.economy.finance["yields"])
    held = banks.bond_value(island, game.config, bank, world.turn)
    worth = banks.net_worth(island, game.config, bank, world.turn)
    assert held > 0
    state = order(game, world, island, "set_bond_payment", {"stop": True})
    assert state.status == ORDER_DONE
    assert any(e.kind == "bond_default_ordered" for e in island.log)
    # 不履行の上乗せ δ が付き、区分の利回りが跳ね上がる（借金が減った分の上乗せは減り、入札で
    # 高めに付いていた区分は式の値に戻るので、区分ごとの上がり幅はそろわない）
    premium = game.config.get("bonds.default_premium")
    assert default_premium(island, context(game, world)) > premium * 0.9
    after = island.economy.finance["yields"]
    assert all(a > b for a, b in zip(after, yields_before, strict=True))
    rise = (sum(after) - sum(yields_before)) / len(after)
    assert rise >= premium * 10000 * 0.5
    after = banks.net_worth(island, game.config, bank, world.turn)
    haircut = game.config.get("bonds.haircut")
    assert after < worth - held * haircut / 2


def test_unsold_bonds_cut_spending_instead_of_underwriting(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    assert order(game, world, island, "set_bond_policy", {"unsold": "cut"}).status == ORDER_DONE
    assert island.policy.bond_unsold == "cut"
    run(game, world, 6)
    assert island.stats.get("bonds.underwritten", 0) == 0


# --- 中央銀行 ---------------------------------------------------------------------


def test_monetary_policy_and_independence(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    state = order(game, world, island, "set_monetary_policy", {"policy_rate": 5})
    assert state.status == ORDER_DONE and island.policy.policy_rate == 500
    state = order(game, world, island, "set_central_bank_independence", {"independent": True})
    assert state.status == ORDER_DONE and island.policy.cb_independent
    # 独立した中央銀行には政策金利を命令できず、決めたターンから一定のターンは戻せない
    result = check_order(
        game.defs, game.registry, island, "set_monetary_policy", {"policy_rate": 1}
    )
    assert result.reason == "reason_cb_independent"
    state = order(game, world, island, "set_central_bank_independence", {"independent": False})
    assert state.status == ORDER_FAILED and state.reason == "reason_cb_switch_locked"


# --- 民営工場の参入と撤退 -----------------------------------------------------------


def test_private_entry_needs_inputs_on_the_island(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    supplied = EntryExit._supplied(island, ctx)
    made = {
        g
        for f in island.facilities
        if f.built and "closing" not in f.economy and "foreclosed" not in f.economy
        for g in game.defs.facilities[f.kind]["recipes"][f.recipe].get("outputs", {})
    }
    assert made <= supplied
    if "stone" not in island.resources:
        assert "stone" not in supplied or island.warehouse.get("stone")


def test_entry_fill_follows_shortage_size():
    settings = {"entry_gap": 0.5, "shortage_turns": 3, "cost_ratio": 1.2}
    recipe = {"outputs": {"steel": 1000}}

    def fill(shortages, demand=None, capacity=None, price=100):
        demand, capacity = demand or {}, capacity or {}
        return EntryExit._fill(recipe, shortages, demand, capacity, {"steel": price}, 100, settings)

    # 品切れが続いても、足りない量が小さければ売れる見込みも小さい
    small = fill({"steel": [600, 600, 600, 0, 0, 0]})
    big = fill({"steel": [2000] * 6})
    none = fill({"steel": [400] * 6})
    assert 0 < small < 1 and big == 1.0 and none == 0
    # 値段が原価を大きく超えていれば、買いたい量を今ある施設と分け合った分だけ売れると見る
    assert fill({}, {"steel": [3000] * 6}, {"steel": 2000}, price=130) == 1.0
    assert fill({}, {"steel": [1500] * 6}, {"steel": 2000}, price=130) == 0.5
    # 売り買いがなく値段が前のままの財では、値段が高くても建たない
    assert fill({}, {"steel": [0] * 6}, {"steel": 2000}, price=130) == 0


def test_entry_waits_while_inputs_are_short():
    settings = {"entry_gap": 0.5, "shortage_turns": 3}
    recipe = {"inputs": {"iron_ore": 2000}, "outputs": {"steel": 1000}}
    # 原料が、新しい工場の使う量の半分以上足りないターンが続けば建たない
    assert EntryExit._inputs_short(recipe, {"iron_ore": [1500] * 3 + [0] * 3}, settings) == (
        "iron_ore"
    )
    assert EntryExit._inputs_short(recipe, {"iron_ore": [1500] * 2 + [0] * 4}, settings) is None
    assert EntryExit._inputs_short(recipe, {"iron_ore": [500] * 6}, settings) is None
    assert EntryExit._inputs_short(recipe, {}, settings) is None


def test_entry_needs_spare_input_when_its_source_is_capped(game):
    """鉱山の数の上限などで原料を増やせない島では、原料に空きがなければ工場は建たない。"""
    ctx = SimpleNamespace(config=game.config)
    recipe = {"inputs": {"iron_ore": 2000}, "outputs": {"steel": 1000}}

    def capped(uses, wanted, expandable=False):
        return EntryExit._inputs_capped(
            None,
            ctx,
            recipe,
            {"iron_ore": 12000},
            {"iron_ore": uses},
            {"iron_ore": wanted},
            {"iron_ore": expandable},
        )

    # 今ある工場が全力で使うと、空きが新しい工場の使う量の半分に足りない
    assert capped(11500, [8000] * 6) == "iron_ore"
    assert capped(8000, [8000] * 6) is None
    # 買いたい量が全力の使う量より多ければ、買いたい量で見る
    assert capped(8000, [11500] * 6) == "iron_ore"
    # 原料を作る施設を増やせるなら建つ（品切れになれば、そちらが参入する）
    assert capped(11500, [8000] * 6, expandable=True) is None


def test_construction_loan_is_split_across_banks_within_the_limit(game, warmed):
    """建設資金は、金利の低い銀行から大口融資の規制の残りまで借り、借りきれない分は資産を持つ層が
    多く出資する。出資しきれなければ建たない。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "mixed")  # 銀行が2つある島
    ctx = context(game, world)
    config = game.config
    active = banks.active_banks(island)
    assert len(active) >= 2
    limits = {b: banks.exposure_limit(island, config, b, ctx.turn) for b in active}
    owners = funds.available(island, Payer(household(OWNERS)))
    amount = sum(limits.values()) * 2
    assert amount - sum(limits.values()) <= owners  # 試験の前提: 資産を持つ層が残りを出せる
    plan = EntryExit()._finance(island, ctx, amount, 10**12)
    assert isinstance(plan, list) and len(plan) == len(active)
    for bank, loan, _ in plan:
        assert 0 < loan <= limits[bank]
    loan = sum(x for _, x, _ in plan)
    assert amount - loan >= amount * config.get("firms.equity_share")
    reason, args = EntryExit()._finance(island, ctx, (owners + loan) * 2, 10**12)
    assert reason == "owners_short" and args["have"] == owners
    # 利益で返せなければ審査に通らない
    assert EntryExit()._finance(island, ctx, amount, 0)[0] == "loan_refused"


def test_private_entry_happens_only_where_allowed(game, warmed):
    entered = {i.regime["economy"]: 0 for i in warmed.islands.values()}
    world = copy.deepcopy(warmed)
    for _ in range(40):
        run(game, world, 1)
        for island in world.islands.values():
            entered[island.regime["economy"]] += sum(
                1 for e in island.log if e.kind == "private_entry"
            )
    assert entered["planned"] == 0
    assert entered["market"] + entered["mixed"] > 0


def test_stalled_private_site_is_abandoned(game, warmed):
    """わけを問わず firms.abandon_turns ターン進まない民営の工事はやめ、先に受け取った設備投資補助を
    政府に返し、建設資金を預金で返して、残りを出資者に返す。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    ctx = context(game, world)
    open_books(island)
    occupied = island.facilities_by_tile()
    tile = next(
        t
        for t in island.tiles
        if t.terrain == "plain" and (t.x, t.y) not in occupied and not t.population
    )
    kind = "consumer_goods_factory"
    site = Facility(
        id=island.next_facility_id(),
        kind=kind,
        x=tile.x,
        y=tile.y,
        recipe=next(iter(game.defs.facilities[kind]["recipes"])),
        owner=OWNER_PRIVATE,
        accounting=ACCOUNTING_SELF,
        economy={"capital": 0},
        ext={
            "private_entry": True,
            "stalled_turns": game.config.get("firms.abandon_turns"),
            "subsidy_paid": 2000,
        },
    )
    island.facilities.append(site)
    entity = facility_entity(site.id)
    invest(island, household(OWNERS), entity, 3000)
    ledger.pay(island, ledger.GOV, entity, 2000, "subsidy")
    treasury = ledger.money(island, ledger.GOV)
    bank = banks.active_banks(island)[0]
    banks.lend(island, bank, entity, 7000, banks.CONSTRUCTION, 120, ctx.turn, 500, 10**9)
    owners = ledger.money(island, household(OWNERS))
    EntryExit()._exits(island, ctx)
    assert site not in island.facilities
    assert any(e.kind == "private_site_abandoned" for e in island.log)
    assert banks.loans_by_borrower(island, entity) == []
    assert ledger.money(island, entity) == 0
    assert ledger.money(island, household(OWNERS)) >= owners + 3000
    assert ledger.money(island, ledger.GOV) == treasury + 2000
    books_ok(game, island)


def test_closed_facility_leaves_power_line(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f = next(f for f in island.facilities if f.owner == OWNER_PRIVATE and f.built and f.x >= 0)
    from hakoniwa_core import facilities as fac

    fac.vacate(island, f)
    assert island.facility_at(f.x, f.y) is None
    assert island.tile(f.x, f.y).power_line


# --- 命令: 指定・国有化・民営化・補助金 ---------------------------------------------


def test_set_zone_marks_tiles(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    occupied = island.facilities_by_tile()
    tiles = [
        [t.x, t.y]
        for t in island.tiles
        if t.terrain == "plain" and (t.x, t.y) not in occupied and island.capital != (t.x, t.y)
    ][:3]
    state = order(game, world, island, "set_zone", {"zone": "industrial", "tiles": tiles})
    assert state.status == ORDER_DONE
    assert all(island.tile(x, y).zone == ZONE_INDUSTRIAL for x, y in tiles)
    result = check_order(
        game.defs,
        game.registry,
        island,
        "set_zone",
        {"zone": "industrial", "tiles": [list(island.capital)]},
    )
    assert result.reason == "reason_zone_capital"


def test_nationalize_then_privatize(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    f = next(
        f
        for f in island.facilities
        if f.owner == OWNER_PRIVATE
        and f.built
        and f.kind in ("farm", "lumber_camp", "fishing_port")
        and "foreclosed" not in f.economy
        and "closing" not in f.economy
    )
    state = order(game, world, island, "nationalize", {"x": f.x, "y": f.y})
    assert state.status == ORDER_DONE, state.reason
    assert f.owner == OWNER_STATE
    state = order(game, world, island, "privatize", {"x": f.x, "y": f.y})
    assert state.status == ORDER_DONE, state.reason
    assert f.owner == OWNER_PRIVATE


def test_planned_economy_cannot_privatize_or_license_banks(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "planned")
    f = next(f for f in island.facilities if f.kind == "farm" and f.built)
    result = check_order(game.defs, game.registry, island, "privatize", {"x": f.x, "y": f.y})
    assert result.reason == "reason_cannot_privatize"
    result = check_order(game.defs, game.registry, island, "grant_bank_license", {})
    assert result.reason == "reason_planned_economy"


def test_subsidy_is_set_and_cleared(game, warmed):
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    payload = {"kind": "production", "good": "steel", "rate": 10, "turns": 12}
    assert order(game, world, island, "set_subsidy", payload).status == ORDER_DONE
    (subsidy,) = [s for s in island.policy.subsidies if s.get("good") == "steel"]
    assert subsidy["rate"] == 1000 and subsidy["until"] == world.turn + 11
    payload = {**payload, "rate": 0}
    assert order(game, world, island, "set_subsidy", payload).status == ORDER_DONE
    assert not [s for s in island.policy.subsidies if s.get("good") == "steel"]
