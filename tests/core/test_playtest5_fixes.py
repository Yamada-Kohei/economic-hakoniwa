"""試し遊び（Codex の世界、ターン150〜200。フェーズ3-2 の後）で見つかった点の直しと、2026-10-15 の
3つの決定（契約の届いた物の行き先・為替の厚み・量の単位）。"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import OWNERS, banks, household
from hakoniwa_core.economy.prices import usual_price
from hakoniwa_core.log import format_log
from hakoniwa_core.market import auction
from hakoniwa_core.modules import failures
from hakoniwa_core.modules import trade as trade_stage
from hakoniwa_core.modules.firms import EntryExit
from hakoniwa_core.modules.trade import check
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE
from hakoniwa_core.trade import blocs, contracts, swap, to_market, traders, world_state
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.units import QUANTITY_SCALE, money_of, to_basis
from tests.core.test_finance import context, island_of
from tests.core.test_finance import warmed as warmed  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import add, home, make_island, with_economy
from tests.core.test_rules import run as run_stages
from tests.core.test_trade import game as game  # noqa: F401
from tests.core.test_trade import run as run_world
from tests.core.test_trade import traded as traded  # noqa: F401

# --- 1. 作り始めた財が何ターンも約定しない ------------------------------------------------


def test_usual_price_counts_only_turns_households_bought(game):
    """家計の「いつもの値段」は、家計が買ったターンの値段だけで平均する。売り買いのなかったターンの
    値段（気配や、作る費用に向けて上げた値段）は数えず、1度も買っていなければ今の値段にする。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    with_economy(game, island)
    economy = island.economy
    economy.prices["clothing"] = 300
    economy.price_history["clothing"] = [100] * 6 + [200] * 6
    economy.bought_history["clothing"] = [5] * 6 + [0] * 6
    assert usual_price(island, "clothing", 12) == 100
    economy.bought_history["clothing"] = [0] * 12
    assert usual_price(island, "clothing", 12) == 300
    # 新しく値段を置いた財は、値段の記録が買った量の記録より1つ長い（古い方をそろえない）
    economy.price_history["clothing"] = [999, 100, 200]
    economy.bought_history["clothing"] = [1, 0]
    assert usual_price(island, "clothing", 12) == 100
    # 家計が買う物でない財（工事の資材）は、これまでどおり値段の平均
    economy.price_history["steel"] = [100, 200]
    economy.bought_history.pop("steel", None)
    assert usual_price(island, "steel", 12) == 150


# --- 2. 石炭の切れた発電所が「電力需要なし」 -------------------------------------------------


def test_power_plant_without_coal_says_coal_is_short(game):
    island = make_island(game)
    home(game, island, 6, 6, 8000, level="city")
    plant = add(island, "thermal_power_plant", 5, 6)  # 石炭がない
    add(island, "mine", 4, 6, recipe="coal")  # 電力を使う
    with_economy(game, island)
    run_stages(game, island, ["employment", "production"])
    assert plant.operating_rate == 0
    assert plant.status == "input:coal"
    stopped = [e for e in island.log if e.kind.startswith("facility_stopped") and e.args["x"] == 5]
    assert [e.kind for e in stopped] == ["facility_stopped_input"]


def test_new_stop_reasons_have_texts(game):
    defs = game.defs
    args = {"facility": "school", "x": 3, "y": 4, "good": "consumer_goods"}
    text = format_log(defs, "facility_stopped_public_price", args)
    assert "教材の消費財が高く買えない" in text and "採算" not in text
    args = {"facility": "thermal_power_plant", "x": 3, "y": 4}
    assert "電気料金が発電の費用に届かず" in format_log(defs, "facility_stopped_power_price", args)
    assert format_log(defs, "status_business", {}) == "預金と融資の大きさに合わせて操業"


# --- 3. 民営の農場がそろって同じ作物に替える --------------------------------------------------


def test_goods_another_facility_just_switched_into_are_busy(game):
    """ほかの施設が替えると決めた作り方の産出物と、替えて作り始めたばかりの施設の産出物は、次の
    施設の見直しで選ばない。"""
    island = make_island(game)
    home(game, island, 5, 6, 6000)
    first = add(island, "farm", 8, 8, owner=OWNER_PRIVATE)
    second = add(island, "farm", 9, 8, owner=OWNER_PRIVATE)
    with_economy(game, island)
    ctx = context(game, SimpleNamespace(turn=29, seed=1))
    assert EntryExit._switched_into(island, ctx) == set()
    first.ext[fac.RECIPE_NEXT] = "cotton"
    assert "cotton" in EntryExit._switched_into(island, ctx)
    first.ext.pop(fac.RECIPE_NEXT)
    fac.switch_recipe(game.defs, second, "cotton", ctx.turn - 1)
    assert "cotton" in EntryExit._switched_into(island, ctx)
    # 畑を蒔き直している間も、蒔き直し終えて firms.price_turns ターンたつまでも、綿は選ばない
    turn = ctx.turn
    while fac.FIELDS in second.economy:
        turn += 1
        fac.step_fields(game.defs, second, turn)
    done = context(game, SimpleNamespace(turn=turn, seed=1))
    assert "cotton" in EntryExit._switched_into(island, done)
    late = turn + game.config.get("firms.price_turns")
    later = context(game, SimpleNamespace(turn=late, seed=1))
    assert "cotton" not in EntryExit._switched_into(island, later)


def test_private_recipe_switch_says_private(game):
    args = {"facility": "farm", "x": 3, "y": 4, "recipe": "farm_cotton"}
    text = format_log(game.defs, "recipe_changed_private", args)
    assert "民営の" in text and "採算を見て" in text


# --- 4. 預金の入らない民間銀行 ------------------------------------------------------------


def _small_bank(game, world):
    """混合経済の島の民間銀行の預金を、すべて国営銀行へ移した世界（預金 0 の民間銀行）。"""
    island = island_of(world, "mixed")
    private = next(
        b for b in banks.bank_list(island) if banks.facility_of(island, b).owner == OWNER_PRIVATE
    )
    state = next(b for b in banks.bank_list(island) if b != private)
    for entity, amount in sorted(banks.depositors_of(island, private).items()):
        if not ledger.is_bank(entity):
            ledger.move_deposit(island, entity, private, state, amount, "deposit_switch")
    return island, private


def test_small_bank_hires_for_its_business(game, warmed):
    world = copy.deepcopy(warmed)
    island, private = _small_bank(game, world)
    f = banks.facility_of(island, private)
    need = fac.workers_needed(game.defs, f.kind)
    wanted = banks.staff_wanted(island, game.config, game.defs, f)
    # 赤字の銀行は経費を切り詰めて下限を外す（回7 後半。banks.watch_costs）
    floor = 0 if banks.LEAN in f.economy else game.config.get("banking.staff_floor")
    full = game.config.get("banking.staff_volume") * island.economy.average_wage
    volume = banks.deposits_of(island, private) + banks.loans_of(island, private)
    assert volume < full
    assert max(1, round(need * floor)) <= wanted < need
    lean = f.economy.pop(banks.LEAN, None)
    floor = game.config.get("banking.staff_floor")
    assert round(need * floor) <= banks.staff_wanted(island, game.config, game.defs, f) < need
    if lean is not None:
        f.economy[banks.LEAN] = lean
    big = max(banks.bank_list(island), key=lambda b: banks.deposits_of(island, b))
    if banks.deposits_of(island, big) >= full:  # 大きい銀行は定義の人数のまま
        f_big = banks.facility_of(island, big)
        assert banks.staff_wanted(island, game.config, game.defs, f_big) == need
    # 雇いたい分を雇えていれば、労働者不足ではなく規模に合わせた操業
    run_world_turns(game, world, 1)
    assert f.workers <= wanted
    if f.workers == wanted:
        assert f.status == "business"
    assert banks.labor_ratio(island, game.config, game.defs, private) > 0


def run_world_turns(game, world, turns):
    from tests.core.test_finance import run

    run(game, world, turns)


def test_bank_does_not_open_when_it_cannot_raise_its_deposit_rate(game, warmed):
    """政策金利が今の銀行の預金金利より高くなければ、新しい銀行は預金を集められないので開業しない。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "market")
    island.policy.bank_entry = "free"
    ctx = context(game, world)
    for bank in banks.bank_list(island):
        banks.facility_of(island, bank).economy["deposit_rate"] = 0
    island.policy.policy_rate = 0
    count = len(banks.bank_list(island))
    reason = EntryExit()._open_private_bank(island, ctx)
    assert reason is not None and reason[0] == "no_rate_room"
    assert len(banks.bank_list(island)) == count
    text = format_log(game.defs, "bank_block_no_rate_room", reason[1])
    assert "預金を集められません" in text
    island.policy.policy_rate = to_basis(0.03)  # 政策金利まで上げる余地があれば、ほかの条件で決まる
    reason = EntryExit()._open_private_bank(island, ctx)
    assert reason is None or reason[0] != "no_rate_room"


def test_bailout_leaves_reserves_for_next_turns_wages(game, warmed):
    """救済では、準備預金が次のターンの職員の賃金に足りなければ、その分も入れる（預金のない銀行は、
    救済の次のターンに賃金を払って準備預金が足りなくなり、資金繰りでまた破綻していた）。"""
    world = copy.deepcopy(warmed)
    island = island_of(world, "mixed")
    private = next(
        b for b in banks.bank_list(island) if banks.facility_of(island, b).owner == OWNER_PRIVATE
    )
    state = next(b for b in banks.bank_list(island) if b != private)
    # 準備預金の余りが 0 になるまで、預金をもう1つの銀行へ移す
    for entity, amount in sorted(banks.depositors_of(island, private).items()):
        excess = banks.excess_reserves(island, game.config, private)
        if ledger.is_bank(entity) or excess <= 0:
            continue
        move = min(amount, excess * 50 // 49)  # 準備預金の最低限（預金の 2%）の分も戻る
        ledger.move_deposit(island, entity, private, state, move, "deposit_switch")
    assert banks.excess_reserves(island, game.config, private) < 1000
    ctx = context(game, world)
    f = banks.facility_of(island, private)
    f.economy["failed"] = ctx.turn - 1
    f.economy["failed_reason"] = "capital"
    failures.bailout(island, ctx, private)
    assert f.owner == OWNER_STATE
    assert "failed" not in f.economy and "failed_reason" not in f.economy
    wage = f.economy.get("wage") or island.economy.average_wage
    payroll = banks.staff_wanted(island, game.config, game.defs, f) * wage
    assert banks.excess_reserves(island, game.config, private) >= payroll
    run_world_turns(game, world, 2)
    assert not banks.is_failed(island, private)


def test_failed_bank_keeps_the_reason_it_was_logged_with(game, warmed):
    """債務超過と資金繰りが同じターンに重なった銀行は、ログと同じ「債務超過」を理由に残す（画面は
    資金繰りと出ていた）。"""
    world = copy.deepcopy(warmed)
    island, private = _small_bank(game, world)
    ctx = context(game, world)
    f = banks.facility_of(island, private)
    worth = banks.net_worth(island, game.config, private, ctx.turn)
    ledger.pay(island, private, household(OWNERS), max(0, worth) + 1000, "dividend")
    f.economy["liquidity_failure"] = ctx.turn
    failures.Failures().run_island(island, ctx)
    assert f.economy["failed"] == ctx.turn
    assert f.economy["failed_reason"] == "capital"
    assert any(e.kind == "bank_failed_capital" for e in island.log)


# --- 5. 圏の燃料が約定しない ------------------------------------------------------------


def test_import_bids_stop_at_the_band_top_and_say_why_they_fall_short(game, traded, monkeypatch):
    """輸入の買いの指値は圏の値幅の上端まで（それより高くは約定しないので、決済通貨を余計に取り置か
    ない）。狙う量に届かなかったわけを商社の財の状態に書く。"""
    world = copy.deepcopy(traded[0])
    width = to_basis(game.config.get("trade.bloc_band"))
    original = trade_stage._Round._import_order
    seen = []

    def import_order(self, island, f, entity, item, good, bloc, left, money_left):
        order = original(self, island, f, entity, item, good, bloc, left, money_left)
        reference = blocs.reference(self.world, bloc, good)
        seen.append((order, reference, item.get("why")))
        return order

    monkeypatch.setattr(trade_stage._Round, "_import_order", import_order)
    run_world(game, world, 3)
    placed = [(o, r) for o, r, _ in seen if o is not None]
    assert placed
    assert all(o.limit <= auction.band(r, width)[1] for o, r in placed)
    whys = {
        item.get("why")
        for island in world.islands.values()
        for f in traders.traders_of(island, game.config)
        for item in traders.goods_of(f).values()
    } - {None}
    assert whys <= {
        trade_stage.WHY_NO_PRICE,
        trade_stage.WHY_PRICE,
        trade_stage.WHY_SHIPS,
        trade_stage.WHY_CAPACITY,
        trade_stage.WHY_MONEY,
        trade_stage.WHY_FUNDS,
        trade_stage.WHY_SELLERS,
        trade_stage.WHY_BUYERS,  # 輸出の荷の売れ残り（試し遊び1回目）
    }
    assert check(world) == []


def test_importer_without_settlement_money_says_so(game, traded, monkeypatch):
    world = copy.deepcopy(traded[0])
    original = trade_stage._Round._import_order
    broke = []

    def import_order(self, island, f, entity, item, good, bloc, left, money_left):
        if island.id != bloc["currency"]:
            money_left[(island.id, entity, bloc["currency"])] = 0  # 決済通貨を持っていない
            wanted = item["m"] > item["held"][0]  # 呼ばれたときに狙う量に届いていない
            result = original(self, island, f, entity, item, good, bloc, left, money_left)
            if wanted:
                broke.append(item.get("why"))
            return result
        return original(self, island, f, entity, item, good, bloc, left, money_left)

    monkeypatch.setattr(trade_stage._Round, "_import_order", import_order)
    run_world(game, world, 1)
    whys = broke
    assert trade_stage.WHY_MONEY in whys
    # お金のほかに先に当たったわけ（船・扱える重さ・値段）でなければ、決済通貨が足りないと書く
    others = {trade_stage.WHY_SHIPS, trade_stage.WHY_CAPACITY, trade_stage.WHY_PRICE}
    assert all(why == trade_stage.WHY_MONEY or why in others for why in whys)


# --- 決めること2: 契約で届いた物の行き先 ---------------------------------------------------


def _sold_by_government(island, good):
    return sum(t.quantity for t in island.trades if t.good == good and t.seller == ledger.GOV)


def _contract(world, seller, buyer, good, qty, turns, delivery=None, currency=None):
    payload = {
        "partner": seller.id,
        "side": "buy",
        "good": good,
        "quantity": qty,
        "price": buyer.economy.prices[good],
        "currency": currency or buyer.id,
        "turns": turns,
    }
    if delivery is not None:
        payload["delivery"] = delivery
    assert contracts.propose(world, buyer, payload, world.turn) is None
    c = contracts.contracts(world)[-1]
    assert contracts.accept(world, seller, c, world.turn) is None
    return c


def _no_warehouse_sales(monkeypatch):
    """標準の計画が国の倉庫の物を売らないようにする（契約で届いた物の売りだけを数えるため）。"""
    from hakoniwa_core import autoplan

    monkeypatch.setattr(autoplan, "_warehouse_sales", lambda *args: [])


def test_contract_goods_go_to_the_market_by_default(game, traded, monkeypatch):
    """腐らない物も、決めていなければ売り切れるまで島の市場の値段で売りに出す。"""
    _no_warehouse_sales(monkeypatch)
    world = copy.deepcopy(traded[0])
    seller, buyer = world.islands[1], world.islands[2]
    good = "steel"
    stock.add(seller.warehouse, good, 20 * QUANTITY_SCALE, world.turn, game.defs.goods)
    c = _contract(world, seller, buyer, good, 1, 2)
    assert contracts.delivery_of(c) == contracts.MARKET
    told, sold = [], []

    def keep(w):
        here = w.islands[2]
        told.extend(e for e in here.log if e.kind == "cargo_to_market_contract")
        sold.append(_sold_by_government(here, good))

    run_world(game, world, 8, keep)
    assert c["delivered"] == 2 * QUANTITY_SCALE
    assert told and told[0].args["good"] == good
    assert to_market(buyer).get(good, 0) + sum(sold) <= 2 * QUANTITY_SCALE
    assert check(world) == []


def test_contract_goods_can_stay_in_the_warehouse(game, traded, monkeypatch):
    _no_warehouse_sales(monkeypatch)
    world = copy.deepcopy(traded[0])
    seller, buyer = world.islands[1], world.islands[2]
    good = "steel"
    stock.add(seller.warehouse, good, 20 * QUANTITY_SCALE, world.turn, game.defs.goods)
    before = stock.total(buyer.warehouse, good)
    c = _contract(world, seller, buyer, good, 1, 2, delivery=contracts.WAREHOUSE)
    # 国の倉庫の量（標準の計画の工事や、機械化した農場の機械の直しが資材として少し使う）
    held = []
    run_world(game, world, 8, lambda w: held.append(stock.total(w.islands[2].warehouse, good)))
    assert c["delivered"] == 2 * QUANTITY_SCALE
    assert to_market(buyer).get(good, 0) == 0
    assert max(held) >= before + 2 * QUANTITY_SCALE - QUANTITY_SCALE // 10  # 市場に売られていない
    assert not any(e.kind == "cargo_to_market_contract" for e in buyer.log)


def test_only_the_buyer_chooses_where_contract_goods_go(game, traded):
    world = copy.deepcopy(traded[0])
    a, b = world.islands[1], world.islands[2]
    payload = {
        "partner": b.id,
        "side": "sell",
        "good": "coal",
        "quantity": 1,
        "price": 100,
        "turns": 3,
        "delivery": "warehouse",
    }
    with pytest.raises(OrderFailed, match="reason_contract_delivery"):
        trade_stage.check_contract(a, payload, game.defs)
    assert contracts.propose(world, a, payload, world.turn) == "reason_contract_delivery"
    del payload["delivery"]
    assert contracts.propose(world, a, payload, world.turn) is None
    c = contracts.contracts(world)[-1]
    # 売り手の申し込みは、買い手が受けるときに決める（売り手の側からは決められない）
    assert contracts.accept(world, b, c, world.turn, contracts.WAREHOUSE) is None
    assert contracts.delivery_of(c) == contracts.WAREHOUSE
    payload["side"] = "buy"
    assert contracts.propose(world, a, payload, world.turn) is None
    c = contracts.contracts(world)[-1]
    assert contracts.accept(world, b, c, world.turn, contracts.MARKET) == (
        "reason_contract_delivery"
    )
    with pytest.raises(OrderFailed, match="reason_invalid_payload"):
        trade_stage.check_contract_ref(a, {"contract": 1, "delivery": "sea"}, game.defs)


def test_shipments_before_the_choice_go_to_the_market(game, traded):
    """行き先を持たない契約（決める前に結んだ契約と、運ぶ途中の荷）は、市場に出す。"""
    world = copy.deepcopy(traded[0])
    seller, buyer = world.islands[1], world.islands[2]
    good = "steel"
    stock.add(seller.warehouse, good, 20 * QUANTITY_SCALE, world.turn, game.defs.goods)
    c = _contract(world, seller, buyer, good, 1, 1)
    c.pop("delivery")
    assert contracts.delivery_of(c) == contracts.MARKET
    run_world(game, world, 1)
    for s in world_state(world)["shipments"]:
        if s["kind"] == "contract" and s["ref"] == c["id"]:
            s.pop("delivery", None)
    told = []
    run_world(
        game,
        world,
        6,
        lambda w: told.extend(e for e in w.islands[2].log if e.kind == "cargo_to_market_contract"),
    )
    assert c["delivered"] == QUANTITY_SCALE
    assert told


# --- 決めること3: 外貨建ての契約と為替の厚み -----------------------------------------------


def test_fx_depth_covers_the_contracts_due_this_turn(game, traded):
    world = copy.deepcopy(traded[0])
    island = world.islands[2]
    currency = 1
    base = fxrates.depth(island, currency, game.config)
    assert fxrates.depth(island, currency, game.config, base * 3) == base * 3
    assert fxrates.depth(island, currency, game.config, base // 2) == base


def test_foreign_currency_contract_larger_than_the_depth_is_delivered(game, traded):
    """1ターンの代金が市場の厚みの下限を超える外貨建ての契約も、ほぼ全部届く。レートは1ターンに
    値幅より動かない。"""
    world = copy.deepcopy(traded[0])
    seller, buyer = world.islands[1], world.islands[2]
    good = "steel"
    price = seller.economy.prices[good]
    floor = fxrates.depth(buyer, seller.id, game.config)
    qty = max(1, round(floor * 1.6 / price))  # 1ターンの代金が厚みの下限の 1.6 倍ほど
    # 島2の中央銀行が売る島1の通貨は、中央銀行どうしの融通で借りる。借りられる残り（島1の年の GDP
    # の割合まで）が尽きれば外貨を買えずに届かないのが正しいので、ここでは厚みを見るため、残りの
    # 3/4 に収まるターン数にする（ほかの買いの分も借りる。回6 で世界の道筋が変わり、島1の GDP が
    # 下がって6ターン分に足りなくなった）
    room = swap.available(world, game.config, buyer.id, seller.id)
    turns = min(6, room * 3 // 4 // money_of(price, qty * QUANTITY_SCALE))
    assert turns >= 3
    stock.add(seller.warehouse, good, (qty * 8 + 5) * QUANTITY_SCALE, world.turn, game.defs.goods)
    ledger.pay(buyer, ledger.CB, ledger.GOV, 10**12, "trade")  # 国庫の自国通貨は足りている
    payload = {
        "partner": seller.id,
        "side": "buy",
        "good": good,
        "quantity": qty,
        "price": price,
        "currency": seller.id,
        "turns": turns,
        "delivery": "warehouse",
    }
    assert contracts.propose(world, buyer, payload, world.turn) is None
    c = contracts.contracts(world)[-1]
    assert contracts.accept(world, seller, c, world.turn) is None
    assert money_of(price, c["qty"]) > floor
    width = fxrates.band_width(game.config)
    rates = []
    run_world(game, world, turns + 1, lambda w: rates.append(fxrates.rate(w.islands[2], seller.id)))
    assert c["delivered"] >= c["qty"] * turns * 95 // 100
    for before, after in zip(rates, rates[1:], strict=False):
        assert abs(after - before) <= before * width // 10_000 + 1
    assert check(world) == []
