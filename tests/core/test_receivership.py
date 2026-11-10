"""管財人と居抜きの売り（FM-59・FM-60・BK-50。2026-11-10）。

倒産した施設は、差し押さえた銀行が管財人として働き手を残して動かし、売り値を下げながら売りに出す。
閉鎖に入った施設も居抜きで売りに出す。管財人の運転資金は分割で返さず、売れたときに先に返す。
"""

from __future__ import annotations

import copy
from dataclasses import replace

from hakoniwa_core import ledger
from hakoniwa_core.economy import (
    OWNERS,
    banks,
    facility_entity,
    household,
    in_receivership,
    is_stopped,
    receivership,
)
from hakoniwa_core.modules import failures
from hakoniwa_core.modules.banking import Banking
from hakoniwa_core.modules.firms import EntryExit
from hakoniwa_core.state import OWNER_PRIVATE
from tests.core.test_finance import context, island_of
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401


def _working(game, world, island):
    """人を雇って動いている民営の施設（銀行・商業・サービス業・差し押さえ中・閉鎖の途中を除く）。"""
    ctx = context(game, world)
    return next(
        f
        for f in sorted(island.facilities, key=lambda f: f.id)
        if f.owner == OWNER_PRIVATE
        and f.built
        and f.x >= 0
        and f.workers > 0
        and f.kind not in ("bank", "services")
        and "foreclosed" not in f.economy
        and "closing" not in f.economy
        and receivership.viable(island, ctx, f)
    )


def _setup(game, warmed, economy="market"):
    world = copy.deepcopy(warmed)
    return world, island_of(world, economy)


def test_sale_price_falls_in_steps_to_the_floor(game):  # noqa: F811
    """売り値は帳簿の値打ち × firms.foreclosure_price から、firms.sale_cut_turns ごとに初めの
    売り値の firms.sale_cut ずつ下がり、帳簿の値打ち × firms.sale_floor で止まる
    （競売の二度目・三度目）。"""
    config = game.config
    book = 1_000_000
    every = config.get("firms.sale_cut_turns")
    first = receivership.first_price(config, book)
    floor = receivership.floor_price(config, book)
    assert receivership.price_at(config, book, 10, 10) == first
    assert receivership.price_at(config, book, 10, 10 + every - 1) == first
    second = receivership.price_at(config, book, 10, 10 + every)
    assert floor < second < first
    assert receivership.next_cut(config, book, 10, 10) == 10 + every
    late = 10 + every * 100
    assert receivership.price_at(config, book, 10, late) == floor
    assert receivership.next_cut(config, book, 10, late) is None


def test_bankrupt_facility_keeps_working_under_the_receiver(game, warmed):  # noqa: F811
    """倒産しても働き手と操業目標をそのまま引き継ぎ、次のターンの支払いは差し押さえた銀行が管財人の
    運転資金として貸す（FM-59・BK-50）。この融資は返ってこないおそれの大きい融資に入れず、利子は
    残高に足して遅れにしない。"""
    world, island = _setup(game, warmed)
    f = _working(game, world, island)
    workers, target = f.workers, f.economy.get("target")
    entity = facility_entity(f.id)
    ledger.pay(island, entity, household(OWNERS), ledger.money(island, entity), "equity_return")
    failures.bankrupt(island, context(game, world, "failures"), f)
    assert in_receivership(f) and not is_stopped(f)
    assert f.workers == workers and f.economy.get("target") == target
    seized = f.economy["foreclosed"]
    assert seized["book"] >= 0 and seized["run"] is True
    loans = receivership.loans_of(island, entity)
    assert loans, "次のターンの支払いの分を管財人の運転資金で借りる"
    assert {x.lender for x in loans} <= set(banks.active_banks(island))
    assert ledger.money(island, entity) == receivership.owed(island, f) > 0
    assert not any(banks.impaired(island, x) or banks.defaulting(island, x) for x in loans)
    # 利子は払わずに残高に足し、遅れにならない
    before = receivership.owed(island, f)
    Banking()._service_loans(
        island, context(game, world, "banking"), {b: [0, 0] for b in banks.bank_list(island)}
    )
    assert receivership.owed(island, f) >= before
    assert all(x.arrears == 0 for x in receivership.loans_of(island, entity))


def test_closing_bankruptcy_stops_the_facility(game, warmed):  # noqa: F811
    """閉鎖の途中で返しきれずに倒産した施設は、採算が合わないから閉じた施設なので動かさない。"""
    world, island = _setup(game, warmed)
    f = _working(game, world, island)
    f.economy["closing"] = world.turn
    failures.bankrupt(island, context(game, world, "failures"), f)
    assert not in_receivership(f) and is_stopped(f)
    assert f.workers == 0
    assert "closing" not in f.economy
    assert not receivership.loans_of(island, facility_entity(f.id))


def test_receivership_repays_its_loan_first_when_sold(game, warmed):  # noqa: F811
    """売れたときは、施設の預金と売り値から管財人の運転資金を先に返し、残りを請求の順に分ける。"""
    world, island = _setup(game, warmed)
    f = _working(game, world, island)
    entity = facility_entity(f.id)
    failures.bankrupt(island, context(game, world, "failures"), f)
    owed = receivership.owed(island, f)
    assert owed > 0
    buyer = household(OWNERS)
    price = owed * 3
    own = ledger.money(island, entity)
    ledger.pay(island, buyer, entity, price, "capital_injection")
    lenders = {x.lender for x in receivership.loans_of(island, entity)}
    before = {b: banks.loans_of(island, b) for b in lenders}
    failures.close_foreclosure(island, f, own + price, "foreclosure_sale")
    assert "foreclosed" not in f.economy
    assert not receivership.loans_of(island, entity)
    assert sum(before[b] - banks.loans_of(island, b) for b in lenders) == owed


def test_receivership_is_resold_to_another_firm(game, warmed):  # noqa: F811
    """管財人が動かして稼いでいる施設は、元の企業でない資本家階級が実際の稼ぎで値踏みして居抜きで
    買い、働き手を残したまま新しい持ち主のもとで動き続ける（FM-60）。"""
    world, island = _setup(game, warmed)
    f = _working(game, world, island)
    failures.bankrupt(island, context(game, world, "failures"), f)
    workers = f.workers
    keep = game.config.get("firms.ebitda_turns")
    # 管財人のもとで、売り値に比べて十分に稼いだ記録
    f.economy["ebitda"] = [max(1, f.economy["foreclosed"]["price"])] * keep
    ctx = context(game, world, "entry_exit")
    sold = EntryExit()._sell_on_sale(island, ctx, 1)
    assert sold == 1
    assert "foreclosed" not in f.economy
    assert f.owner == OWNER_PRIVATE and f.workers == workers
    assert not receivership.loans_of(island, facility_entity(f.id))
    assert any(e.kind == "foreclosure_sold" for e in island.log)


def test_receivership_waits_for_a_record_before_it_is_valued(game, warmed):  # noqa: F811
    """管財人のもとの稼ぎの記録が firms.ebitda_turns たまるまでは値を付けない。"""
    world, island = _setup(game, warmed)
    f = _working(game, world, island)
    failures.bankrupt(island, context(game, world, "failures"), f)
    assert "ebitda" not in f.economy  # 倒産の前の稼ぎは使わない
    ctx = context(game, world, "entry_exit")
    EntryExit()._sell_on_sale(island, ctx, 1)
    assert "foreclosed" in f.economy


def test_closing_facility_stays_on_sale_until_sale_turns(game, warmed):  # noqa: F811
    """閉鎖に入った施設は、在庫を売り切っても firms.sale_turns の間は居抜きで売りに出したまま
    残る。"""
    world, island = _setup(game, warmed)
    f = _working(game, world, island)
    f.economy["closing"] = world.turn
    f.economy[receivership.SALE_BOOK] = banks.book_capital(f)
    f.stock = {}
    ctx = context(game, world, "entry_exit")
    EntryExit._finish_closing(island, ctx, f)
    assert f in island.facilities and "closing" in f.economy
    assert receivership.asking_price(game.config, f, ctx.turn) is not None
    late = replace(ctx, turn=world.turn + game.config.get("firms.sale_turns"))
    EntryExit._finish_closing(island, late, f)
    assert f not in island.facilities or "closing" not in f.economy


def test_stopped_receivership_is_demolished_after_idle_turns(game, warmed):  # noqa: F811
    """動かしていない差し押さえの施設は、動かないターンが firms.exit_idle_turns 続けば取り壊す。"""
    world, island = _setup(game, warmed)
    f = _working(game, world, island)
    f.economy["closing"] = world.turn
    failures.bankrupt(island, context(game, world, "failures"), f)
    f.stock = {}
    f.operating_rate = 0
    f.economy["idle_turns"] = game.config.get("firms.exit_idle_turns") - 1
    EntryExit()._foreclosures(island, context(game, world, "entry_exit"))
    assert f not in island.facilities
    assert any(e.kind == "foreclosure_demolished" for e in island.log)


def test_original_firm_is_not_a_buyer(game, warmed):  # noqa: F811
    """居抜きの売りでは、倒産した施設の元の企業を出資する者に選ばない。"""
    from hakoniwa_core.economy import companies

    world, island = _setup(game, warmed)
    firms = [
        int(k) for k, rec in island.economy.firms.items() if rec[companies.OWNER] == OWNER_PRIVATE
    ]
    for number in firms:
        kinds = [m.kind for m in companies.members(island, number)]
        if not kinds:
            continue
        pick = companies.choose_investor(island, game.defs, kinds[0], 1, None, number)
        assert pick is None or pick[1] != number


def test_closing_facility_that_cannot_pay_its_loans_is_wound_up_now(game, warmed):  # noqa: F811
    """売りに出している間に次の返済を預金で払えなくなった閉鎖の施設は、待たずにその場でたたむ
    （返済の遅れを重ねて、同じ企業の施設まで倒産に巻き込まないように）。"""
    world, island = _setup(game, warmed)
    f = _working(game, world, island)
    entity = facility_entity(f.id)
    bank = banks.active_banks(island)[0]
    banks.lend(island, bank, entity, 10**8, banks.CONSTRUCTION, 12, world.turn, 500)
    ledger.pay(island, entity, household(OWNERS), ledger.money(island, entity), "equity_return")
    f.economy["closing"] = world.turn
    f.economy[receivership.SALE_BOOK] = banks.book_capital(f)
    EntryExit._finish_closing(island, context(game, world, "entry_exit"), f)
    assert "closing" not in f.economy
    assert f not in island.facilities or not in_receivership(f)


def test_worn_out_facility_is_not_bought(game, warmed):  # noqa: F811
    """直す採算の合わないまま設備が傷みきった施設は、買っても直さずにすぐまた閉じるので買わない。"""
    from hakoniwa_core.economy import equipment

    world, island = _setup(game, warmed)
    f = next(
        (
            x
            for x in sorted(island.facilities, key=lambda x: x.id)
            if x.owner == OWNER_PRIVATE
            and x.built
            and x.x >= 0
            and equipment.has_equipment(game.defs, game.config, x)
            and "foreclosed" not in x.economy
            and "closing" not in x.economy
        ),
        None,
    )
    if f is None:
        return
    failures.bankrupt(island, context(game, world, "failures"), f)
    f.economy[equipment.CONDITION] = 0
    keep = game.config.get("firms.ebitda_turns")
    f.economy["ebitda"] = [10**12] * keep
    real = equipment.worthwhile
    try:
        equipment.worthwhile = lambda *a, **k: False
        EntryExit()._sell_on_sale(island, context(game, world, "entry_exit"), 1)
    finally:
        equipment.worthwhile = real
    assert "foreclosed" in f.economy
