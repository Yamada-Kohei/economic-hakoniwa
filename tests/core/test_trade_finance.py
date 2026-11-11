"""W-269: 商社の外貨の見込みと貿易金融（FX-61・BK-51）、取り逃した儲けでの商社の参入（FX-62）。

- 外貨は前の輸入額の 1.5 倍で頭打ちにせず、輸入の見込みまで持つ。見込みは割り当て・船・扱える
  重さで縮む。買うのは賃金と輸出の仕入れの見込みを残したお金だけで、1 ターン分ずつ近づける
- 次の支払いの見込みに「1 ターン分の輸入の見込み − 持っている外貨」を入れ、荷と外貨とこれから買う
  荷を担保に loans.trade_ratio まで貸す。担保の外は断る。荷は仕入れ値と島の値段の低い方で見る
- 政府が持つ商社でも、借りきれない輸入の分は出資しない。計画経済の商社は前のまま
- 取り逃した儲けの利回りが参入の線を超えたら民営の商社が開き、銀行の体力の壁では数えない
"""

from __future__ import annotations

import copy

import pytest

from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import banks as bank_book
from hakoniwa_core.economy import entity_of, facility_entity
from hakoniwa_core.economy.start import next_payments
from hakoniwa_core.game import load_game
from hakoniwa_core.modules import banking as banking_stage
from hakoniwa_core.modules import trade as trade_stage
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.trade import setup as trade_setup
from hakoniwa_core.trade import traders, traders_of
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import money_of, part, to_basis

from .test_trade import run

TURNS = 30


@pytest.fixture(scope="module")
def game():
    return load_game()


@pytest.fixture(scope="module")
def traded(game):
    world = generate_world(7, 4, game.config, game.defs)
    run(game, world, TURNS)
    return world


def _context(game, world, stage="trade"):
    return StageContext(
        defs=game.defs,
        config=game.config,
        registry=game.registry,
        formulas=game.engine.formulas,
        turn=world.turn + 1,
        seed=world.seed,
        stage=stage,
        before=world if stage == "trade" else None,
    )


def _round(game, world):
    r = trade_stage._Round(world, _context(game, world))
    for i in r.ids:
        r.found[i] = traders.prospects(world.islands[i], game.defs, game.config)
    return r


def _importer(game, world, foreign=True):
    """輸入している財がある、市場経済の島の商社（foreign なら決済通貨が外貨の島）。"""
    for island in world.islands.values():
        if traders.is_planned(island):
            continue
        for f in traders_of(island, game.config):
            for item in traders.goods_of(f).values():
                bloc = item.get("bm")
                if item.get("dir") != traders.IMPORT or bloc is None or item["m"] <= 0:
                    continue
                from hakoniwa_core.trade import blocs

                currency = blocs.find(world, bloc)["currency"]
                if (currency != island.id) == foreign:
                    return island, f, currency
    pytest.skip("輸入している商社がない")


def _plan(game, world, island, f):
    r = _round(game, world)
    return r, r._import_plan(island, f, {})


# --- FX-61: 外貨の見込み -------------------------------------------------------------------


def _want_more(f, extra=5_000):
    """輸入している財の狙う量を、手元と運ぶ途中の荷より extra 多くする。"""
    goods = []
    for good, item in sorted(traders.goods_of(f).items()):
        if item.get("dir") == traders.IMPORT and item["m"] > 0:
            item["m"] = item["held"][0] + extra
            goods.append(good)
    return goods


def test_the_import_need_is_the_targets_valued_at_the_bloc_bid(game, traded):
    """見込みは狙う量 − 手元と運ぶ途中の荷を、圏の市場に出す指値で値を付けた額（前の輸入額の
    1.5 倍の頭打ちはない）。fx.keep_turns ターン分は、2 ターン目から狙う量の分を足す。"""
    world = copy.deepcopy(traded)
    island, f, _ = _importer(game, world)
    _want_more(f)
    traders.state_of(f)["abroad"] = {}  # 前の輸入額がなくても見込みは出る
    r, plan = _plan(game, world, island, f)
    assert plan
    keep = game.config.get("fx.keep_turns")
    most = max(item["m"] for item in traders.goods_of(f).values())
    _, step = r._import_needs(island, f, {})
    for _, first, total, limit, _ in plan:
        assert first == 5_000
        assert first <= total <= first + most * (keep - 1)
        assert limit > 0
    assert sum(step.values()) == sum(money_of(limit, first) for _, first, _, limit, _ in plan)


def test_the_import_need_shrinks_with_the_import_quota(game, traded):
    """見込みは輸入の割り当ての残りで縮む（買えない分の外貨と借りは持たない）。"""
    world = copy.deepcopy(traded)
    island, f, _ = _importer(game, world)
    goods = _want_more(f)
    _, plan = _plan(game, world, island, f)
    assert sum(first for _, first, _, _, _ in plan) > len(goods)
    for good in goods:
        island.policy.import_quotas[good] = 1
    _, quoted = _plan(game, world, island, f)
    assert all(
        first <= 1 and total <= 1 * game.config.get("fx.keep_turns")
        for _, first, total, _, _ in quoted
    )


def test_the_import_need_shrinks_without_ships(game, traded, monkeypatch):
    """船で運べない分（圏の空いた船の全部でも）は見込みに入れない。"""
    world = copy.deepcopy(traded)
    island, f, _ = _importer(game, world)
    monkeypatch.setattr(trade_stage._Round, "_reach", lambda self, buyer, bloc, good: 0)
    _, plan = _plan(game, world, island, f)
    heavy = {g for g in traders.goods_of(f) if trade_stage.unit_weight(game.defs, g) > 0}
    assert heavy
    assert all(first == 0 for _, first, _, _, _ in plan) or not plan


def test_fx_is_bought_one_turn_at_a_time_and_only_with_spare_money(game, traded):
    """持ち高は fx.keep_turns ターン分の見込みへ 1 ターン分ずつ近づけ、使えるお金（賃金と輸出の
    仕入れの見込みを残した分）で頭打ちになる。頭打ちになったら取り逃しに数える印を返す。"""
    world = copy.deepcopy(traded)
    island, f, currency = _importer(game, world)
    r = _round(game, world)
    entity = entity_of(game.config, f)
    full = {currency: [(1_000_000, None)]}
    step = {currency: 400_000}
    rich = fxrates.to_own(island, currency, 10**9)
    caps, limited = r._fx_caps(island, entity, full, step, rich)
    have = fxmoney.holding(island, entity, currency)
    assert caps[currency] == max(0, min(1_000_000 - have, 400_000))
    assert not limited or caps[currency] == 0
    caps, limited = r._fx_caps(island, entity, full, step, 0)
    assert caps[currency] == 0
    assert limited == (min(1_000_000 - have, 400_000) > 0)


def test_wages_and_export_purchases_are_not_turned_into_fx(game, traded, monkeypatch):
    """預金が賃金と輸出の仕入れの見込みしかない商社は、輸入のための外貨を買わない。"""
    world = copy.deepcopy(traded)
    island, f, currency = _importer(game, world)
    entity = entity_of(game.config, f)
    keep = game.config.get("fx.keep_turns")
    wage = island.economy.average_wage or game.config.get("labor.initial_wage")
    wages = traders.workers_wanted(game.config, game.defs, f) * wage * keep
    reserve = wages + traders.spend_estimate(f) * keep
    money = ledger.money(island, entity)
    if money > reserve:  # 余りを持ち主へ移して、手元を見込みちょうどにする
        ledger.pay(island, entity, ledger.GOV, money - reserve, "capital_injection")
    bought = []
    original = fxrates.add_buy

    def add_buy(island_, book, who, payer, qty, *args, **kwargs):
        if who == entity:
            bought.append(qty)
        return original(island_, book, who, payer, qty, *args, **kwargs)

    monkeypatch.setattr(fxrates, "add_buy", add_buy)
    r = _round(game, world)
    r._fx()
    assert not bought


# --- BK-51: 貿易金融 -------------------------------------------------------------------------


def test_next_payments_add_the_import_need_minus_the_fx_held(game, traded):
    world = copy.deepcopy(traded)
    island, f, currency = _importer(game, world)
    state = traders.state_of(f)
    state.pop(traders.IMPORT_NEED, None)
    base = next_payments(island, game.defs, game.config, f)
    entity = entity_of(game.config, f)
    have = fxmoney.holding(island, entity, currency)
    state[traders.IMPORT_NEED] = {str(currency): have + 500_000}
    assert next_payments(island, game.defs, game.config, f) - base == fxrates.to_own(
        island, currency, 500_000
    )
    state[traders.IMPORT_NEED] = {str(currency): max(0, have - 1)}
    assert next_payments(island, game.defs, game.config, f) == base


def test_planned_traders_keep_the_old_payments(game, traded):
    """計画経済の商社（公定価格のある島）は外貨を買わないので、見込みに輸入の分を入れない（前の
    とおり、輸入に払った外貨の直近の平均）。外貨の見込みも出さない。"""
    world = copy.deepcopy(traded)
    island, f, currency = _importer(game, world)
    island.ext.setdefault("plan", {})["prices"] = {"grain": 1}
    state = traders.state_of(f)
    state.pop(traders.IMPORT_NEED, None)
    base = next_payments(island, game.defs, game.config, f)
    state[traders.IMPORT_NEED] = {str(currency): 10**9}
    assert next_payments(island, game.defs, game.config, f) == base
    _, plan = _plan(game, world, island, f)
    assert plan == []


def _banking_context(game, world):
    return _context(game, world, "banking")


def _clear_working(island, entity):
    for loan in bank_book.loans_by_borrower(island, entity):
        if loan.purpose == bank_book.WORKING:
            bank_book.write_off(island, loan, bank_book.outstanding(island, loan))


def test_trade_finance_lends_up_to_the_collateral(game, traded):
    """利益で返す力が足りなくても、運転資金の残高が（荷 + 外貨 + この借りで買う荷）×
    loans.trade_ratio 以内なら貸す。それを超えた分は断る。"""
    world = copy.deepcopy(traded)
    island, f, currency = _importer(game, world)
    entity = facility_entity(f.id)
    _clear_working(island, entity)
    have = fxmoney.holding(island, entity, currency)
    traders.state_of(f)[traders.IMPORT_NEED] = {str(currency): have + 2_000_000}
    imports = fxrates.to_own(island, currency, 2_000_000)
    held, transit, fx_value = traders.trade_collateral(island, f)
    ratio = to_basis(game.config.get("loans.trade_ratio"))
    room = part(held + transit + fx_value + imports, ratio)
    stage = banking_stage.Banking()
    ctx = _banking_context(game, world)
    got = stage._trade_secured(island, ctx, f, 10**12)
    assert got in (0, room)  # 借入の割合の審査に通れば担保の分まで
    assert stage._trade_secured(island, ctx, f, room // 2) in (0, room // 2)
    # 担保を超えて借りている商社には貸さない
    bank = bank_book.active_banks(island)[0]
    bank_book.lend(island, bank, entity, room + 1, bank_book.WORKING, 6, world.turn, 0)
    assert stage._trade_secured(island, ctx, f, 1_000) == 0


def test_collateral_takes_the_lower_of_cost_and_island_price(game, traded):
    world = copy.deepcopy(traded)
    island, f, _ = _importer(game, world)
    good, item = next(
        (g, it) for g, it in traders.goods_of(f).items() if it.get("dir") == traders.IMPORT
    )
    for other, it in traders.goods_of(f).items():
        if other != good:
            it["dir"] = traders.EXPORT
    stock.take_batches(f.stock, good, stock.total(f.stock, good))
    item["transit"] = 1000
    item["held"] = [1000, 300]  # 1 単位 300
    island.economy.prices[good] = 900
    entity = facility_entity(f.id)
    for key in list(fxmoney.holdings(island).get(entity, {})):
        fxmoney.holdings(island)[entity].pop(key)
    held, transit, fx_value = traders.trade_collateral(island, f)
    assert (held, transit, fx_value) == (0, 300, 0)
    island.economy.prices[good] = 100  # 島の値段が仕入れ値より安い
    assert traders.trade_collateral(island, f)[1] == 100


def test_assets_count_fx_and_cargo_in_transit(game, traded):
    world = copy.deepcopy(traded)
    island, f, _ = _importer(game, world)
    entity = facility_entity(f.id)
    _, transit, fx_value = traders.trade_collateral(island, f)
    base = (
        bank_book.book_capital(f)
        + ledger.money(island, entity)
        + bank_book.inventory_value(island, f)
    )
    assert bank_book.assets_of(island, f, entity) == base + transit + fx_value


def test_a_state_trader_gets_no_capital_for_imports_it_cannot_borrow(game, traded, monkeypatch):
    """国営の商社は、借りきれない輸入の分を政府の出資で埋めない（賃金・仕入れの分だけ）。"""
    world = copy.deepcopy(traded)
    island, f, currency = _importer(game, world)
    f.owner = "state"
    entity = facility_entity(f.id)
    have = fxmoney.holding(island, entity, currency)
    traders.state_of(f)[traders.IMPORT_NEED] = {str(currency): have + 5_000_000}
    monkeypatch.setattr(
        banking_stage.Banking, "_borrow", lambda self, island, ctx, f, amount, *a, **k: amount
    )
    ctx = _banking_context(game, world)
    payments = next_payments(island, game.defs, game.config, f)
    imports = traders.import_need_own(island, f)
    before = ledger.money(island, entity)
    lent = {b: [0, 0] for b in bank_book.bank_list(island)}
    stage = banking_stage.Banking()
    for other in island.facilities:  # この商社だけを見る
        if other is not f:
            other.economy["closing"] = True
    stage._working_capital(island, ctx, lent)
    injected = ledger.money(island, entity) - before
    assert injected <= max(0, payments - imports - before)


# --- FX-62: 取り逃した儲けでの参入 -----------------------------------------------------------


def test_missed_profit_above_the_entry_line_opens_a_trader(game, traded):
    world = copy.deepcopy(traded)
    island, f, _ = _importer(game, world)
    keep = game.config.get("trade.entry_turns")
    for g in traders_of(island, game.config):
        traders.state_of(g)["missed"] = [[0, 0]] * keep
    assert not trade_setup._missed_pays(island, game.defs, game.config)
    traders.state_of(f)["missed"] = [[10**12, 10**6]] * keep
    assert trade_setup._missed_pays(island, game.defs, game.config)


def test_money_missed_behind_a_bank_wall_is_not_counted(game, traded):
    """前のターンの銀行の段で銀行の体力に断られた商社の、お金で買えなかった分は数えない（扱える
    重さで買えなかった分は数える）。外貨を自分のお金で買いきれなかった商社の分は数える。"""
    world = copy.deepcopy(traded)
    island, f, currency = _importer(game, world)
    r = _round(game, world)
    entity = entity_of(game.config, f)
    terms = trade_stage._ImportTerms(100, 0, 0, 50, 50)
    r.fx_limited[island.id] = {entity}
    traders.state_of(f)["bank_wall"] = r.turn - 1
    r._note_missed(island, f, entity, "clothing", terms, 0, 1000, currency)
    assert (island.id, f.id) not in r.missed
    r._note_missed(island, f, entity, "clothing", terms, 300, 1000, currency)
    assert r.missed[(island.id, f.id)]["clothing"][0] == 300
    traders.state_of(f).pop("bank_wall")
    r._note_missed(island, f, entity, "clothing", terms, 0, 1000, currency)
    assert r.missed[(island.id, f.id)]["clothing"][0] == 1000
    r.fx_limited[island.id] = set()  # 外貨の注文は出しきった（為替で買えなかった）
    r.missed.clear()
    if currency != island.id:
        r._note_missed(island, f, entity, "clothing", terms, 0, 1000, currency)
        assert (island.id, f.id) not in r.missed


def test_the_world_keeps_its_books_with_trade_finance(game, traded):
    """貿易金融を入れても、島をまたぐお金と運ぶ途中の荷は毎ターン合う（30 ターン回した世界）。"""
    from hakoniwa_core.modules.trade import check

    assert check(traded) == []
    needs = [
        traders.state_of(f).get(traders.IMPORT_NEED)
        for island in traded.islands.values()
        for f in traders_of(island, game.config)
    ]
    assert any(needs)
