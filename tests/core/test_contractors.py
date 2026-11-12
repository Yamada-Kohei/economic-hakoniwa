"""土木業者（W-270）: 公共事業の工事と維持を請け負う業者への注文・単価・代金・参入を、
小さな手作りの島で
確かめる（設計書 08 の IN-29〜IN-37）。"""

from __future__ import annotations

from hakoniwa_core import infra, ledger, stock
from hakoniwa_core.economy import CONSTRUCTION, books, facility_entity, funds, household
from hakoniwa_core.modules.infrastructure import contracts
from hakoniwa_core.state import ACCOUNTING_SELF, ACCOUNTING_TREASURY, OWNER_PRIVATE
from tests.core.test_rules import add, home, make_island, run, with_economy, without_roads
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）

ROAD = "road"
WORKS = ["employment", "construction", "payroll", "market", "infrastructure"]
MARKET = {"politics": "democracy", "head_of_state": "republic", "economy": "market"}


def market_island(game, population: int = 6000):
    island = make_island(game)
    island.regime = dict(MARKET)
    home(game, island, 5, 6, population)
    with_economy(game, island)
    without_roads(island)
    return island


def private_contractor(game, island, x: int = 8, y: int = 8, cash: int = 0):
    f = add(island, "contractor", x, y, owner=OWNER_PRIVATE, accounting=ACCOUNTING_SELF)
    if cash:
        ledger.pay(island, ledger.GOV, facility_entity(f.id), cash, "capital_injection")
    return f


def queue_road(game, island, tiles=((3, 3),)):
    infra.enqueue(island, ROAD, list(tiles))
    stock.add(island.warehouse, "building_materials", 20_000, 0, game.defs.goods)


def test_contractor_builds_the_queued_road_and_is_paid_for_what_it_delivered(game):
    # IN-29・IN-31・IN-32・IN-36: 人手はまず業者に請け負わせ、届けた分の代金を公共事業費から払う
    island = market_island(game)
    f = private_contractor(game, island)
    queue_road(game, island)
    run(game, island, WORKS)  # 1ターン目は、国の倉庫から工事の資材が移るだけ
    before = ledger.money(island, facility_entity(f.id))
    run(game, island, WORKS)
    assert infra.level(island.tile(3, 3), ROAD) > 0
    assert island.stats["works.contracted"] > 0
    assert island.stats["works.direct"] == 0  # 業者が全部届けたので直営は雇わない
    assert f.workers > 0
    done = f.economy[contracts.DELIVERED]
    # 代金は届けた量 × 単価（単価は代金を払った後の市場の段で見直すので、予定価格までの間）で、
    # 売上と工事の部門の請負に同じ額（GDP の生産の側と支出の側がそろう）
    paid = books.facility_items(island)[f.id][books.SALES]
    top = contracts.ceiling(island, game.config)
    assert done * contracts.floor(game.config, f) // 1000 <= paid <= done * top // 1000
    assert books.sector(island, books.CONSTRUCTION)["contracted"] == paid
    # 業者は代金を受け取り、賃金を払った
    wages = books.facility_items(island)[f.id][books.WAGES]
    assert ledger.money(island, facility_entity(f.id)) == before + paid - wages


def test_contractor_without_cash_hires_on_the_order_money(game):
    # 運転資金のない独立採算の業者も、取り置かれた注文の代金を払える賃金に数えて雇う（IN-32）
    island = market_island(game)
    f = private_contractor(game, island)
    assert ledger.money(island, facility_entity(f.id)) == 0
    queue_road(game, island)
    run(game, island, WORKS, turns=2)
    assert f.workers > 0 and island.stats["works.contracted"] > 0
    assert ledger.money(island, facility_entity(f.id)) >= 0
    assert not f.economy.get("unpaid")


def test_order_goes_to_the_cheapest_contractor_and_skips_one_above_the_ceiling(game):
    # IN-31・IN-33: 単価の安い業者から注文し、予定価格を超える業者には注文しない
    island = market_island(game)
    cheap = private_contractor(game, island, 8, 8)
    dear = private_contractor(game, island, 10, 10)
    queue_road(game, island)
    run(game, island, WORKS)
    top = contracts.ceiling(island, game.config)
    cheap.economy[contracts.PRICE] = contracts.floor(game.config, cheap)
    dear.economy[contracts.PRICE] = top + 1
    contracts.place(island, game.defs, game.config, 1, [5000, 7000])
    state = island.scratch[contracts.CONTRACTS]
    assert dear.id not in state["orders"]
    assert state["orders"][cheap.id] == [5000, 7000]


def test_capacity_counts_the_staff_and_who_it_could_hire_last_turn(game):
    island = market_island(game)
    f = private_contractor(game, island)
    full = contracts.full(game.defs, game.config, f)
    assert contracts.capacity(game.defs, game.config, f) == full  # 見込みのない初めのターンは全力
    f.workers, f.economy[contracts.REACH] = 10, 5
    assert contracts.capacity(game.defs, game.config, f) == 15 * contracts.per_worker(game.config)
    f.economy[contracts.REACH] = 10**9
    assert contracts.capacity(game.defs, game.config, f) == full


def test_order_is_cut_to_what_the_public_works_budget_can_hold(game):
    # IN-31: 取り置けない分は注文せず、業者の雇いたい人数もそこまで
    island = market_island(game)
    f = private_contractor(game, island)
    queue_road(game, island)
    run(game, island, WORKS)
    contracts.place(island, game.defs, game.config, 1, [0, 40_000])
    price = contracts.price_of(island, game.config, f)
    room = funds.available(island, contracts.PAYER)
    # 公共事業費が 10 単位分しか使えないようにする
    funds.commit(island, contracts.PAYER, max(0, room - price * 10))
    got = contracts.reserve(island, game.config, False, 40_000)
    assert got == 10_000
    assert contracts.wanted(island, game.config, f) == -(
        -10_000 // contracts.per_worker(game.config)
    )


def test_treasury_contractor_is_paid_from_public_works_without_a_contract_price(game):
    # IN-35: 国庫直轄の業者は代金のやり取りをせず、賃金を公共事業費から直に払う
    island = market_island(game)
    f = add(island, "contractor", 8, 8, accounting=ACCOUNTING_TREASURY)
    queue_road(game, island)
    run(game, island, WORKS, turns=2)
    assert f.workers > 0 and island.stats["works.contracted"] > 0
    items = books.facility_items(island).get(f.id, {})
    assert items.get(books.SALES, 0) == 0
    assert books.sector(island, books.CONSTRUCTION)["wages"] >= f.workers * f.economy["wage"]
    assert funds.fiscal_state(island)["used"].get(CONSTRUCTION, 0) > 0


def test_price_rises_when_asked_beyond_full_and_falls_when_idle(game):
    # IN-33: 頼まれた量が全力を超えれば上げ、手が空けば下げる。下限は変動費、上限は予定価格
    island = market_island(game)
    f = private_contractor(game, island)
    full = contracts.full(game.defs, game.config, f)
    low, top = contracts.floor(game.config, f), contracts.ceiling(island, game.config)
    start = (low + top) // 2
    f.economy[contracts.PRICE] = start
    island.scratch[contracts.CONTRACTS] = {"asked": {f.id: full * 2}}
    contracts.review_prices(island, game.defs, game.config)
    assert start < f.economy[contracts.PRICE] <= top
    island.scratch[contracts.CONTRACTS] = {"asked": {f.id: 0}}
    for _ in range(30):
        contracts.review_prices(island, game.defs, game.config)
    assert f.economy[contracts.PRICE] == low


def test_prepare_places_one_contractor_near_the_capital_once(game):
    island = market_island(game)
    contracts.prepare(island, game.defs, game.config)
    placed = contracts.contractors(island, game.config)
    assert len(placed) == 1 and placed[0].built
    assert [e.kind for e in island.log if e.kind == "contractor_placed"] == ["contractor_placed"]
    contracts.prepare(island, game.defs, game.config)
    assert len(contracts.contractors(island, game.config)) == 1


def test_private_contractor_enters_after_unfilled_works(game):
    # IN-35: 直近の埋まらなかった工事が新しい業者の全力の決めた割合を超えて続けば、民間が参入する
    island = market_island(game)
    turns = game.config.get("contractor.entry_turns")
    probe = add(island, "contractor", 8, 8)
    whole = contracts.full(game.defs, game.config, probe)
    island.facilities.remove(probe)
    island.economy.infra[contracts.UNFILLED] = [whole] * (turns - 1)
    assert contracts.entry(island, game.defs, game.config, False) is None  # まだ足りない
    island.economy.infra[contracts.UNFILLED] = [whole] * turns
    owners = household("owners")
    ledger.pay(
        island, ledger.GOV, owners, contracts.capital(game.defs, game.config, island), "grant"
    )
    f = contracts.entry(island, game.defs, game.config, False)
    assert f is not None and f.owner == OWNER_PRIVATE and f.built
    assert ledger.money(island, facility_entity(f.id)) > 0
    assert contracts.entry(island, game.defs, game.config, False) is None  # 動き出すのを待つ
    assert contracts.entry(island, game.defs, game.config, True) is None  # 計画経済は開かない


def test_contractor_exits_after_losses_or_idleness(game):
    island = market_island(game)
    f = private_contractor(game, island)
    turns = game.config.get("firms.exit_idle_turns")
    f.workers = 0
    signals = [contracts.exit_signal(island, game.config, f) for _ in range(turns)]
    assert signals[-1] and not any(signals[:-1])


def test_planned_contractor_hires_at_the_priority_of_its_orders(game):
    # IN-37: 計画経済の島の業者は、受けた注文の工事の優先度の位置で雇う（命令で変えた順も効く）
    from types import SimpleNamespace

    from hakoniwa_core.modules import infrastructure as works
    from hakoniwa_core.modules.employment import Employment

    island = market_island(game)
    f = add(island, "contractor", 8, 8)
    ctx = SimpleNamespace(config=game.config, defs=game.defs)
    works.works_state(island)[works.UPKEEP_PRIORITY] = 9
    works.works_state(island)[works.BUILD_PRIORITY] = 2
    f.economy[contracts.ORDERS] = [1000, 0]
    assert Employment._priority(island, ctx, f) == 9
    f.economy[contracts.ORDERS] = [0, 1000]
    assert Employment._priority(island, ctx, f) == 2
    f.economy[contracts.ORDERS] = [1000, 1000]
    assert Employment._priority(island, ctx, f) == 9
