"""海運業の再参入（W-308。切り替え trade.shipping_reentry、既定は切）。

0: 管財人の売りで船団ごと引き継ぐ（民営は資本家階級、国営は政府が別の道で引き取る）。
1: 買い手が付かずに取り壊されたら、船団の組を国の倉庫へ（船齢は組のまま残る）。
2: 港に海運の会社がなく、前のターンに運べなかった荷がある島に、後継の会社を置く。
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import (
    OWNERS,
    Payer,
    arrears,
    banks,
    facility_entity,
    funds,
    household,
    in_receivership,
    is_on_sale,
    is_stopped,
    receivership,
)
from hakoniwa_core.modules import failures
from hakoniwa_core.modules.firms import EntryExit
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import cells as route_cells
from hakoniwa_core.trade import (
    contracts,
    island_state,
    port_of,
    reentry,
    shipping_of,
    ships,
    weight,
)
from hakoniwa_core.units import QUANTITY_SCALE
from tests.core.test_finance import context, island_of
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_finance import warmed as warmed  # noqa: F401
from tests.core.test_trade import run as trade_run


@pytest.fixture()
def on(game, monkeypatch):  # noqa: F811
    """切り替えを入れる（game は module 単位で使い回すので、試験ごとに monkeypatch で戻す）。"""
    monkeypatch.setitem(game.config.raw["trade"], "shipping_reentry", True)
    return game


def _setup(game, warmed, economy="market"):  # noqa: F811
    world = copy.deepcopy(warmed)
    return world, island_of(world, economy)


def _rich_owners(island):
    bank = ledger.bank_of(island, household(OWNERS))
    ledger.create(island, household(OWNERS), ledger.DEPOSIT, bank, 10**15, "test_top_up")


def _seize_shipping(game, world, island, claims=None):  # noqa: F811
    """海運業を倒産させて差し押さえ、管財人のもとの稼ぎの記録をためる。"""
    f = shipping_of(island, game.config)
    assert f is not None and f.built
    failures.bankrupt(island, context(game, world, "failures"), f)
    assert in_receivership(f)
    if claims:
        f.economy["foreclosed"]["claims"] = claims
    keep = game.config.get("firms.ebitda_turns")
    f.economy["ebitda"] = [10**9] * keep
    return f


def test_switch_off_keeps_the_old_three_drops(game, warmed):  # noqa: F811
    """切のときは今と同じ: 差し押さえ中の海運業は居抜きの売りの輪に入らず、後継も置かない。"""
    world, island = _setup(game, warmed)
    f = _seize_shipping(game, world, island)
    _rich_owners(island)
    ctx = context(game, world, "entry_exit")
    assert EntryExit()._sell_on_sale(island, ctx, 1) == 0
    assert in_receivership(f)
    assert reentry.place_successor(island, ctx) is None


def test_receivership_sale_inherits_the_fleet_at_the_settlement_batches(game, warmed, on):  # noqa: F811
    """0（民営）: 売り値が残りの借入を下回っても、成約は 1 回で確定し、同じ施設の番号と船団で
    次のターンに運航できる。値付けと引き渡しは成約時点の組で見るので、管財中に退役した組は
    売り値にも引き渡しにも入らず、残った組は元の使い始めたターンのまま引き継がれる（#5512）。"""
    world, island = _setup(game, warmed)
    config = game.config
    f = shipping_of(island, config)
    assert f.owner == OWNER_PRIVATE
    turn = world.turn + 1
    unit = island.economy.prices[config.get("trade.ship_good")]
    assert unit > 0
    old, young = turn - config.get("trade.ship_life"), turn - 10
    f.economy["fleet"] = [[old, 1000], [young, 1000]]
    f.economy["capital"] = unit * 2
    f.economy["depreciated"] = 0
    f.stock = {}  # 在庫の値打ちを 0 にして、買い手の代金を売り値そのものにする（清算の差分用）
    bank = banks.active_banks(island)[0]
    # 残りの借入（請求）が売り値を上回る形にする
    _seize_shipping(game, world, island, claims={arrears.LOANS: {bank: unit * 100}})
    _rich_owners(island)
    # 管財中に古い組が退役する（設備の額と償却済みも割合で減る）
    assert ships.scrap(island, config, turn) == 1000
    assert f.economy["fleet"] == [[young, 1000]]
    ctx = context(game, world, "entry_exit")
    EntryExit()._foreclosures(island, ctx)  # 段の順どおり: 売り値の元を今の船団で付け直す
    assert in_receivership(f)  # 動いている管財中の施設は取り壊されない
    book = banks.book_capital(f)
    price = receivership.price_at(config, book, f.economy["foreclosed"]["since"], turn)
    assert receivership.asking_price(config, f, turn) == price
    assert price < unit * 100  # 売り値は残りの借入を下回る
    # 清算の差分（#5527・#5534・#5542）: 売りに出ているのはこの施設だけで買い手候補を 1 者に
    # 絞り、成約の前後で旧社の請求・買い手の使えるお金・仕訳を読む
    assert [x.id for x in island.facilities if is_on_sale(x)] == [f.id]
    seized = f.economy["foreclosed"]
    loans_before = sum(seized["claims"][arrears.LOANS].values())
    assert loans_before == unit * 100
    assert not seized["claims"].get(arrears.WAGES) and not seized["claims"].get(arrears.TAXES)
    owners = household(OWNERS)
    available_before = funds.available(island, Payer(owners))
    transfers_before = len(island_state(island).get("shipping_transfers", []))
    journal_before = len(island.journal)
    assert EntryExit()._sell_on_sale(island, ctx, 1) == 1
    assert not in_receivership(f) and f.owner == OWNER_PRIVATE
    assert f.economy["fleet"] == [[young, 1000]]  # 退役した組は戻らず、残った組は元の年数
    assert f.economy["capital"] == price  # 設備の額は成約時点の売り値（残った組だけの値付け）
    assert not receivership.loans_of(island, facility_entity(f.id))
    assert not is_stopped(f) and f.built  # 次のターンに同じ番号と船団で運航できる
    entry = island_state(island)["shipping_transfers"][-1]
    assert entry["facility"] == f.id and entry["turn"] == turn
    assert entry["fleet"] == [[young, 1000]] and entry["buyer"]
    # 清算（#5508・#5527・#5532〜#5544・#5566・#5568）: 買い手は資本家階級だけ（唯一の買い手）で、
    # 代金は全額が旧社の借入へ回り、買い手の使えるお金は売り値の分だけ減る（清算額を買い手側に
    # 二重に数えない）
    transfers = island_state(island)["shipping_transfers"]
    assert len(transfers) == transfers_before + 1 and entry["buyer"] == owners
    holders = {
        ledger.parse(name)[0]
        for name, value in island.accounts.items()
        if name.endswith(f"/{ledger.EQUITY}/{facility_entity(f.id)}") and value > 0
    }
    assert holders == {owners}  # 出資しているのは買い手だけ
    # 成約の区間の仕訳（foreclosure_sale）を隔離して見る（#5542）
    receipts = [e for e in island.journal[journal_before:] if e.kind == "foreclosure_sale"]
    # ② 旧社の純効果（#5534）: 借入の帳簿の減りが売り値に一致する
    loans_after = sum(seized["claims"][arrears.LOANS].values())
    assert loans_before - loans_after == price
    # ③ 貸方の帰属（#5566・#5568）: 貸方を勘定別に集計し、債権者の受け取りにあたる勘定
    # （債権者の銀行にある旧社の預金勘定）だけに、売り値ちょうどが計上されていること。
    # ②（帳簿の差分）とは独立に、計上先の取り違え（別の持ち主の預金勘定への計上など）を落とす
    credited: dict[str, int] = {}
    for e in receipts:
        credited[e.credit] = credited.get(e.credit, 0) + e.amount
    assert credited == {ledger.account(facility_entity(f.id), ledger.DEPOSIT, bank): price}
    # ④ 買い手の使えるお金は売り値の分だけ減る
    assert funds.available(island, Payer(owners)) == available_before - price
    # ⑤ 借方の帰属（#5542）: 銀行が受け取る支払いは、その銀行の預金の発行勘定が借方になる
    # （施設の預金が消えて銀行の取り分になる）形で残る
    assert sum(e.amount for e in receipts) == price
    assert all(e.debit == ledger.issued(bank, ledger.DEPOSIT) for e in receipts)


def test_state_takeover_is_a_separate_path(game, warmed, on):  # noqa: F811
    """0（国営）: 政府は売りの輪ではなく別の道で、売り値と引き継ぎ直後の不足分を国庫から
    出せるときだけ引き取る。出せない間は管財が続く（#5490・#5508）。"""
    world, island = _setup(game, warmed, "mixed")
    config = game.config
    f = shipping_of(island, config)
    assert f.owner == OWNER_STATE
    # 売り値を国庫で払える額に寄せる（この試験の見たいことは引き取りの形で、額の大きさではない）
    f.economy["fleet"] = [[world.turn - 10, 1000]]
    f.economy["capital"] = 100_000
    f.economy["depreciated"] = 0
    failures.bankrupt(island, context(game, world, "failures"), f)
    assert in_receivership(f)
    entity = facility_entity(f.id)
    # 施設の手元を空にして、引き継ぎ直後の不足分を作る
    ledger.pay(island, entity, ledger.GOV, ledger.money(island, entity), "test_drain")
    short = reentry.operating_need(island, config, f)
    assert short > 0
    price = receivership.asking_price(config, f, world.turn + 1)
    assert price + short <= funds.available(island, Payer(ledger.GOV))  # 成立の形になっている
    ctx = context(game, world, "entry_exit")
    EntryExit()._take_over_shipping(island, ctx)
    assert not in_receivership(f) and f.owner == OWNER_STATE
    assert ledger.money(island, entity) == short  # 不足分が出資で足りている
    assert ships.fleet(f) > 0  # 船団はそのまま残る
    entry = island_state(island)["shipping_transfers"][-1]
    assert entry["facility"] == f.id and entry["buyer"] == ledger.GOV


def test_state_takeover_waits_until_the_treasury_can_pay(game, warmed, on):  # noqa: F811
    """0（国営）: 売り値が国庫の使える分を超える間は引き取らず、管財が続く。"""
    world, island = _setup(game, warmed, "mixed")
    config = game.config
    f = shipping_of(island, config)
    f.economy["capital"] = 10**16  # 売り値が国庫では払えない額になる
    f.economy["depreciated"] = 0
    failures.bankrupt(island, context(game, world, "failures"), f)
    ctx = context(game, world, "entry_exit")
    EntryExit()._take_over_shipping(island, ctx)
    assert in_receivership(f)


def _no_shipping_island(game, world, island, short_load=5000):  # noqa: F811
    """海運業を取り除き（取り壊しの後）、前のターンに運べなかった荷の記録を置く。"""
    config = game.config
    f = shipping_of(island, config)
    island.facilities.remove(f)
    # 造船所のない島の形にする（#5484: 倉庫の船から後継が船団に入れるかを見る）
    for other in [x for x in island.facilities if x.kind == "shipyard"]:
        island.facilities.remove(other)
    assert port_of(island, config) is not None
    island_state(island)[reentry.SHORT_PREV] = short_load
    return f


def test_fleet_to_warehouse_keeps_batches_and_successor_buys_the_oldest(game, warmed, on):  # noqa: F811
    """1 → 2: 取り壊しの船は組のまま倉庫に残り（船齢が残る）、後継は造船所のない島でも
    倉庫のいちばん古い組を買って船団に入れる。買い手の設備の額は購入額（#5484・#5496・#5504）。"""
    world, island = _setup(game, warmed)
    config = game.config
    turn = world.turn + 1
    f = shipping_of(island, config)
    old, young = turn - 50, turn - 5
    f.economy["fleet"] = [[old, 1000], [young, 1000]]
    assert reentry.fleet_to_warehouse(island, config, f) == 2000
    # 倉庫の組は作られたターン 0 に潰れず、使い始めたターンのまま残る
    assert island.warehouse[config.get("trade.ship_good")] == [[old, 1000], [young, 1000]]
    _no_shipping_island(game, world, island)
    _rich_owners(island)
    ctx = context(game, world, "entry_exit")
    new = reentry.place_successor(island, ctx)
    assert new is not None and new.id != f.id
    unit = island.economy.prices[config.get("trade.ship_good")]
    price = reentry.batch_price(config, unit, old, turn, 1000)
    assert price > 0
    assert new.economy["fleet"] == [[old, 1000]]  # 古い組が渡り、使い始めたターンが残る
    assert new.economy["capital"] == price  # 購入額がそのまま設備の額（償却は 0 から）
    assert new.economy.get("depreciated", 0) == 0
    assert island.warehouse[config.get("trade.ship_good")] == [[young, 1000]]
    entry = island_state(island)["shipping_transfers"][-1]
    assert entry["facility"] == new.id and entry["fleet"] == [
        [old, 1000]
    ]  # 初輸送は後継の番号で拾う


def test_zero_life_batch_is_chosen_transferred_and_booked_in_order(game, warmed, on):  # noqa: F811
    """倉庫で待つ間に寿命へ達した組（#5509・#5515・#5516）。断言の順:
    1. 残りの使える期間が 0 の最古の組が選ばれる（倉庫では傷んで消えず、選考からも外さない）。
    2. その組が実際に渡り、購入額が買い手の設備の額に載る。
    3. その後に、通常の船齢・退役の処理が後のターンから効く（置いたターンに消えない）。"""
    world, island = _setup(game, warmed)
    config = game.config
    turn = world.turn + 1
    good = config.get("trade.ship_good")
    old, young = turn - config.get("trade.ship_life"), turn - 10
    stock.put_batches(island.warehouse, good, [[old, 1000], [young, 1000]])
    # 倉庫では傷まない（持ち越せる期間のない財は spoil の対象外）
    assert stock.spoil(island.warehouse, turn, game.defs.goods).get(good, 0) == 0
    assert island.warehouse[good] == [[old, 1000], [young, 1000]]
    _no_shipping_island(game, world, island)
    _rich_owners(island)
    ctx = context(game, world, "entry_exit")
    new = reentry.place_successor(island, ctx)
    assert new is not None
    # 1. 最古の組が選ばれる（新しい組へ飛ばない）
    assert new.economy["fleet"] == [[old, 1000]]
    assert island.warehouse[good] == [[young, 1000]]
    # 2. 実際に渡り、購入額が設備の額に載る（判定・支払い・帳簿で同じ額）
    unit = island.economy.prices[good]
    price = reentry.batch_price(config, unit, old, turn, 1000)
    assert new.economy["capital"] == price
    # 3. 置いたターンには消えず、後のターンの通常の退役で初めて消える
    assert ships.fleet(new) == 1000
    assert ships.scrap(island, config, turn + 1) == 1000
    assert ships.fleet(new) == 0


def test_successor_records_what_is_missing_and_waits(game, warmed, on):  # noqa: F811
    """2: 元手が足りなければ置かず、足りなかった物を記録して次のターンも判定する（#5477）。
    後継が唯一の買い手でも、倉庫の船の代金（置いた後に国庫へ入る分）を元手の余力に数えない
    （二重に数えない。#5489・#5508）。"""
    world, island = _setup(game, warmed, "mixed")  # 国営の島（元手は国庫の GOV_SHARE）
    config = game.config
    good = config.get("trade.ship_good")
    turn = world.turn + 1
    stock.put_batches(island.warehouse, good, [[turn - 5, 1000]])
    _no_shipping_island(game, world, island)
    ctx = context(game, world, "entry_exit")
    # 国庫の使える分を、船の代金を足せば届くが、それなしでは届かない額まで落とす
    _, ship_price = reentry.warehouse_offer(island, config, turn)
    need = ship_price + reentry.operating_funds(island, config)
    room = reentry.owner_room(island, game.defs, config, OWNER_STATE)
    assert 0 < room < need < room + ship_price  # 試験の形（代金を回せば届く所にある）
    assert reentry.place_successor(island, ctx) is None
    block = island_state(island)[reentry.BLOCK]
    assert block["reason"] == "funds" and block["short"] == need - room
    assert shipping_of(island, config) is None


def test_successor_needs_leftover_demand(game, warmed, on):  # noqa: F811
    """2: 前のターンに運べなかった荷がなければ、後継は置かない。"""
    world, island = _setup(game, warmed)
    _no_shipping_island(game, world, island, short_load=0)
    _rich_owners(island)
    ctx = context(game, world, "entry_exit")
    assert reentry.place_successor(island, ctx) is None
    assert shipping_of(island, game.config) is None


def test_short_load_comes_only_from_what_could_not_be_carried(game, on):  # noqa: F811
    """残荷の作られ方（#5507・#5529・#5535）: 船腹が注文の一部しか積めないターンでは、残荷は
    （需要 − 実際に運んだ分）から作られ、運んだ分は含まれない。全量運べたターンは残荷が 0 で、
    次のターンの判定（前のターンの分）にも何も残らない。"""
    world = generate_world(7, 4, game.config, game.defs)
    trade_run(game, world, 3)
    seller, buyer = world.islands[1], world.islands[2]
    good = "building_materials"
    stock.add(seller.warehouse, good, 2000 * QUANTITY_SCALE, world.turn, game.defs.goods)
    bf = shipping_of(buyer, game.config)
    bf.economy["fleet"] = [[world.turn, 125]]  # 船腹を絞って、一部しか積めない形にする
    bf.economy["busy"] = {}
    payload = {
        "partner": buyer.id,
        "side": "sell",
        "good": good,
        "quantity": 30,
        "price": 10,
        "currency": buyer.id,
        "turns": 6,
    }
    assert contracts.propose(world, seller, payload, world.turn) is None
    c = contracts.contracts(world)[-1]
    assert contracts.accept(world, buyer, c, world.turn) is None
    want = 30 * QUANTITY_SCALE
    trade_run(game, world, 1)
    c = contracts.find(world, c["id"])
    assert c["short_why"] == "ships" and 0 < c["short"] < want
    assert c["delivered"] == want - c["short"]  # 実際に運んだ分
    expected = (
        weight(game.defs, good, c["short"])
        * 2
        * route_cells(buyer, seller)
        // max(1, game.config.get("trade.ship_speed"))
    )
    assert expected > 0
    assert island_state(buyer)[reentry.SHORT_LOAD] == expected  # 残荷は運べなかった分だけ
    # 船腹を戻せば全量運べて、そのターンの残荷は 0 になる
    shipping_of(buyer, game.config).economy["fleet"] = [[world.turn, 100000]]
    trade_run(game, world, 1)
    c = contracts.find(world, c["id"])
    assert c["short"] == 0 and island_state(buyer)[reentry.SHORT_LOAD] == 0
    # 次のターンの判定（前のターンの分）にも、全量運べたターンの分は何も残らない
    trade_run(game, world, 1)
    assert island_state(buyer)[reentry.SHORT_PREV] == 0
