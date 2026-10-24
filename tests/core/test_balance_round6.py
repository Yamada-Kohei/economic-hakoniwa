"""バランス調整の回6: 設備の傷みと直し（FM-11・FM-12）、船の先回りの買い替え（E31）、自動車の材料
（W-75）、揺さぶりの試験2つ（GT-19: 決済通貨の島の生産を止める、船を全部失った島）。

ターンを回す試験は、記録の段が毎ターン帳簿を確かめ、trade_check の段が島をまたぐお金と運ぶ途中の荷を
確かめる（合わなければその場で止まる）。揺さぶりの試験では、さらに毎ターン国際収支の釣り合いを見る
（tests/core/test_phase3_3.py の run_checked）。
"""

from __future__ import annotations

import copy
import random
from types import SimpleNamespace

import pytest

from hakoniwa_core import autoplan, ledger, stock, trade
from hakoniwa_core import facilities as fac
from hakoniwa_core.economy import equipment, facility_entity
from hakoniwa_core.economy.prices import initial_prices
from hakoniwa_core.modules.accounts import Accounts
from hakoniwa_core.modules.events import Events
from hakoniwa_core.modules.firms import EntryExit, remove_facility, restart
from hakoniwa_core.state import (
    OWNER_PRIVATE,
    OWNER_STATE,
    ZONE_INDUSTRIAL,
    ZONE_NO_DEVELOPMENT,
    ZONE_NONE,
)
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.trade import blocs, shipping_of, ships
from hakoniwa_core.units import QUANTITY_SCALE, money_of
from tests.core.test_finance import context
from tests.core.test_phase3_3 import run_checked

BASE_TURNS = 30


@pytest.fixture(scope="module")
def game():
    from hakoniwa_core.game import load_game

    return load_game()


@pytest.fixture(scope="module")
def base(game):
    """標準の計画で BASE_TURNS ターン回した6島（市場・混合・計画経済が2つずつ）。試験ごとに写す。"""
    world = generate_world(1, 6, game.config, game.defs)
    run_checked(game, world, BASE_TURNS)
    return world


def worn_facility(game, island, owner):
    """持ち主が owner の、設備を持つ動いている施設（機械を設備に持つものを先に）。"""
    found = [
        f
        for f in sorted(island.facilities, key=lambda f: f.id)
        if f.owner == owner and equipment.has_equipment(game.defs, game.config, f)
    ]
    found.sort(key=lambda f: "machinery" not in equipment.kit(game.defs, f.kind))
    return found[0] if found else None


def island_with(world, game, owner):
    for island in world.islands.values():
        f = worn_facility(game, island, owner)
        if f is not None:
            return island, f
    raise AssertionError(f"{owner} の施設がない")


def wear_down(game, f, share):
    """設備の資材をみな定義の share の割合にする。"""
    kit = equipment.kit(game.defs, f.kind)
    have = {g: fac.scale(q, round(share * fac.FULL)) for g, q in kit.items() if q > 0}
    f.economy[equipment.EQUIPMENT] = have
    f.economy[equipment.CONDITION] = equipment._ratio(game.defs, f.kind, have)


# --- 設備の傷み ------------------------------------------------------------------------


def test_equipment_wears_by_the_yearly_rates(game, base):
    """傷みは資材ごとの年の割合の 12 分の 1。設備の割合は一番減った資材で決まる。"""
    world = copy.deepcopy(base)
    _, f = island_with(world, game, OWNER_STATE)
    f.economy.pop(equipment.EQUIPMENT, None)
    f.economy.pop(equipment.CONDITION, None)
    prices = {g: 1000 for g in game.defs.goods}
    worn, before = equipment.wear(game.defs, game.config, f, prices)
    kit = equipment.kit(game.defs, f.kind)
    assert before == sum(q for q in kit.values()) * 1000 // QUANTITY_SCALE
    assert worn > 0
    for good, qty in kit.items():
        gone = qty - f.economy[equipment.EQUIPMENT][good]
        assert abs(gone - qty * equipment.wear_rate(game.config, good) / 12) <= 1
    fastest = max(kit, key=lambda g: equipment.wear_rate(game.config, g))
    rate = equipment.wear_rate(game.config, fastest)
    assert abs(equipment.condition(f) - fac.FULL * (1 - rate / 12)) <= 1


def test_worn_equipment_caps_hiring_inputs_and_output(game, base):
    """設備の割合が、雇う人数・原料を買う量・稼働率の上限になる（傷んだ分は作れない）。"""
    world = copy.deepcopy(base)
    island, f = island_with(world, game, OWNER_STATE)
    wear_down(game, f, 0.4)
    f.economy["target"] = fac.FULL
    cap = equipment.condition(f)
    assert cap == 400
    assert fac.labor_demand(game.defs, f, world.turn + 1) <= fac.scale(
        fac.workers_needed(game.defs, f.kind), cap
    )
    game.engine.run_turn(world)
    assert f.operating_rate <= max(cap, equipment.condition(f))


def test_state_facilities_are_repaired_from_the_construction_budget(game, base):
    """国営の施設は、公共事業費（と国の倉庫の資材）で、動かすつもりの分まで直す。直した代金は設備の
    額に足し、GDP では工事（投資）に数える。"""
    world = copy.deepcopy(base)
    island, f = island_with(world, game, OWNER_STATE)
    wear_down(game, f, 0.5)
    f.economy["target"] = fac.FULL
    capital = f.economy.get("capital", 0)
    before = equipment.condition(f)
    run_checked(game, world, 3)
    assert equipment.condition(f) > before
    assert f.economy.get("capital", 0) > capital
    assert any(i.stats.get("equipment.repair", 0) > 0 for i in world.islands.values())


def test_private_owner_repairs_only_when_it_pays(game, base):
    """民営の持ち主は、変動費の利益の1年分が、直して持ち続ける年の費用（傷みと直す代金の利子）以上の
    ときだけ直す。国営は比べない。"""
    world = copy.deepcopy(base)
    _, f = island_with(world, game, OWNER_PRIVATE)
    wear_down(game, f, 0.5)
    prices = {g: 1000 for g in game.defs.goods}
    cost = equipment.yearly_cost(game.defs, game.config, f, prices)
    assert cost > 0
    assert equipment.worthwhile(game.defs, game.config, f, int(cost / 12) + 1, prices)
    assert not equipment.worthwhile(game.defs, game.config, f, int(cost / 12) - 1, prices)
    f.owner = OWNER_STATE
    assert equipment.worthwhile(game.defs, game.config, f, -1, prices)


def test_repairs_stop_at_the_scarcest_material(game, base):
    """一番足りない資材で届く割合までしか、ほかの資材を買わない（ほかだけ買っても直らない）。"""
    world = copy.deepcopy(base)
    _, f = island_with(world, game, OWNER_STATE)
    wear_down(game, f, 0.5)
    f.economy["target"] = fac.FULL
    kit = equipment.kit(game.defs, f.kind)
    scarce = next(iter(sorted(kit)))
    room = {g: (0 if g == scarce else 10**9) for g in kit}
    assert equipment.wants(game.defs, game.config, f, room) == {}
    assert equipment.wants(game.defs, game.config, f)  # 手に入るなら直したい


def test_repairs_pay_the_import_price_when_only_the_merchant_sells(game, base):
    """直す資材は、島の売り手がいれば工事の資材と同じ指値（高ければ待つ）。海外の商人しか売って
    いなければ、待っても安くならないので値幅の上端まで払う。"""
    from hakoniwa_core.economy import Payer
    from hakoniwa_core.economy.prices import material_limit
    from hakoniwa_core.market import auction
    from hakoniwa_core.market.auction import Order
    from hakoniwa_core.modules.market import FACILITY, MERCHANT, Book, Market, Party

    world = copy.deepcopy(base)
    island, f = island_with(world, game, OWNER_STATE)
    good = sorted(equipment.kit(game.defs, f.kind))[0]
    width = 1000
    # いつもの値段が今の値段より安かった（値段が上がってきた）ことにする
    price = island.economy.prices.get(good, 1)
    island.economy.price_history[good] = [price // 2] * 12
    usual = material_limit(island, game.config, good, width)
    _, high = auction.band(island.economy.prices.get(good, 1), width)
    assert high > usual

    def limit(kind):
        book = Book(sells=[(Order(5000, high), Party(kind, kind, Payer(ledger.GOV, "x")))])
        ctx = context(game, world, "market")
        return Market._repair_limit(island, ctx, f, [good], None, {}, width, {good: book})[good]

    assert limit(MERCHANT) == high
    assert limit(FACILITY) == usual


def test_depreciation_is_the_worn_share_of_the_book_value(game, base):
    """設備を持つ施設の減価償却は、帳簿の設備の値打ち × 傷んだ値打ち ÷ 傷む前の値打ち。"""
    world = copy.deepcopy(base)
    _, f = island_with(world, game, OWNER_STATE)
    f.economy["capital"] = 1_000_000
    f.economy["depreciated"] = 200_000
    amount = Accounts._wear_off(f, 30, 1000)
    assert amount == 800_000 * 30 // 1000
    assert f.economy["depreciated"] == 200_000 + amount
    assert Accounts._wear_off(f, 30, 0) == 0  # 値打ちが 0 でも止まらない


def test_a_foreclosed_plant_keeps_its_worn_equipment(game, base):
    """差し押さえた施設を売っても、設備は傷んだまま新しい持ち主へ移る。"""
    world = copy.deepcopy(base)
    island, f = island_with(world, game, OWNER_PRIVATE)
    wear_down(game, f, 0.6)
    worn = dict(f.economy[equipment.EQUIPMENT])
    restart(island, context(game, world), f, OWNER_PRIVATE, 12345)
    assert f.economy[equipment.EQUIPMENT] == worn
    assert equipment.condition(f) == 600
    assert f.economy["capital"] == 12345


def test_scrapped_private_plant_closes(game, base):
    """直す採算が合わないまま設備が equipment.scrap を切った民営の施設は閉じる。"""
    world = copy.deepcopy(base)
    island, f = island_with(world, game, OWNER_PRIVATE)
    wear_down(game, f, 0.1)
    # 売り物の値段を 1 にして、直す採算が合わないようにする
    for g in fac.recipe_of(game.defs, f)["outputs"]:
        island.economy.prices[g] = 1
        island.economy.price_history[g] = [1]
    from hakoniwa_core.modules.firms import EntryExit

    EntryExit()._exits(island, context(game, world, "entry_exit"))
    assert "closing" in f.economy


def test_stock_of_a_closed_plant_goes_back_on_the_market(game, base):
    """閉じた施設の在庫は国が引き取り、売れるまで島の市場の値段で売りに出す（国の倉庫に眠らせない。
    閉じた製材所の木材が倉庫に眠り、漁港が設備を直せなかった）。"""
    world = copy.deepcopy(base)
    island, f = island_with(world, game, OWNER_PRIVATE)
    stock.add(f.stock, "wood", 7000, world.turn, game.defs.goods)
    held = stock.total(f.stock, "wood")
    before = stock.total(island.warehouse, "wood")
    remove_facility(island, context(game, world), f)
    assert stock.total(island.warehouse, "wood") == before + held
    assert trade.to_market(island)["wood"] == held
    run_checked(game, world, 1)
    record = next(m for m in island.markets if m.good == "wood")
    assert record.supply >= held  # 国が引き取った木材が売りに出ている


# --- 船の先回りの買い替え --------------------------------------------------------------


def test_ships_are_replaced_before_they_retire(game, base):
    """寿命まで trade.ship_order_turns ターン以内の船は、使っている分だけ前もって買う。"""
    world = copy.deepcopy(base)
    island = next(i for i in world.islands.values() if shipping_of(i, game.config))
    f = shipping_of(island, game.config)
    config = game.config
    life = config.get("trade.ship_life")
    turn = world.turn
    f.stock.pop(config.get("trade.ship_good"), None)
    f.economy["ships_short"] = []
    f.economy["fleet"] = [[turn - life + 5, 2000], [turn, 1000]]  # 2隻が5ターン後に寿命
    load = config.get("trade.ship_capacity") * 2500  # 2.5 隻分を使っている
    f.economy["used"] = [load]
    assert ships.ships_wanted(config, f, turn) == 1500  # 寿命の来ない1隻を除いた 1.5 隻
    f.economy["used"] = [0]
    assert ships.ships_wanted(config, f, turn) == 0  # 使っていない船は替えない
    f.economy["fleet"] = [[turn, 3000]]
    f.economy["used"] = [load]
    assert ships.ships_wanted(config, f, turn) == 0  # 寿命はまだ先


# --- 自動車の材料 -----------------------------------------------------------------------


def test_farms_and_fishing_ports_open_on_land_with_no_use_set(game, base):
    """工業団地がなくても、民営の農場と漁港は指定のない人の住んでいないマスに建つ（開墾は国の用途の
    指定を待たない）。ほかの工場は工業団地にしか建たず、開発を止めるマスにはどれも建たない。"""
    world = copy.deepcopy(base)
    island = next(i for i in world.islands.values() if i.has_economy)
    for tile in island.tiles:
        if tile.zone == ZONE_INDUSTRIAL:
            tile.zone = ZONE_NONE
    ctx = context(game, world)
    site = EntryExit()._site(island, ctx, "farm", "default")
    assert not isinstance(site, str)
    tile = island.tile(*site[0])
    assert tile.zone == ZONE_NONE and tile.population == 0
    assert EntryExit()._site(island, ctx, "steel_mill", "default") == "no_zone"
    for t in island.tiles:
        if t.zone == ZONE_NONE and t.population == 0:
            t.zone = ZONE_NO_DEVELOPMENT
    assert EntryExit()._site(island, ctx, "farm", "default") == "no_zone"


def test_a_disaster_does_not_shorten_a_longer_halt(game, base):
    """暴動などでもっと長く止まっている施設は、災害に遭っても早く動き出さない。"""
    world = copy.deepcopy(base)
    island = next(i for i in world.islands.values() if i.has_economy)
    for f in island.facilities:
        if f.built and not fac.is_housing(game.config, f):
            f.ext["halted_until"] = world.turn + 100
    settings = {**game.config.get("disasters"), "rate": 1.0}
    config = SimpleNamespace(
        get=lambda key: settings if key == "disasters" else game.config.get(key)
    )
    ctx = SimpleNamespace(
        defs=game.defs, config=config, turn=world.turn + 1, rng=lambda _: random.Random(1)
    )
    Events().run_island(island, ctx)
    assert any(e.kind.startswith("disaster_") for e in island.log)
    assert all(
        f.ext["halted_until"] == world.turn + 100
        for f in island.facilities
        if f.built and not fac.is_housing(game.config, f)
    )


def test_automobile_needs_the_parts_of_a_real_car(game):
    """自動車は部品工場（鋼鉄・アルミ・化学品・衣料・機械・電力）の部品を組み立てて作る（回7 後半。
    アルミのない作り方は鋼鉄で）。部品工場と合わせた材料は回6 の自動車工場と同じ。"""
    parts = game.defs.facilities["auto_parts_plant"]["recipes"]
    assert set(parts["aluminium"]["inputs"]) == {
        "steel",
        "aluminium",
        "chemicals",
        "clothing",
        "machinery",
        "electricity",
    }
    assert set(parts["default"]["inputs"]) == set(parts["aluminium"]["inputs"]) - {"aluminium"}
    cars = game.defs.facilities["automobile_plant"]["recipes"]
    assert set(cars) == {
        "default",
        "automated",
        "military_vehicle",
    }  # 軍用車両はフェーズ4-2 の作り方
    assert set(cars["default"]["inputs"]) == {"auto_parts", "steel", "chemicals", "electricity"}
    steel = parts["default"]["inputs"]["steel"] + cars["default"]["inputs"]["steel"]
    assert steel == 5400  # 回6 の1ターン全力の 5.4（1000 分の 1）
    prices = initial_prices(game.defs, game.config)
    assert prices["auto_parts"] > 0
    assert prices["automobile"] > prices["auto_parts"]


# --- 揺さぶりの試験（GT-19） ------------------------------------------------------------


def test_halting_the_settlement_currency_island_keeps_the_world_running(game, base):
    """決済通貨の島の施設を全部止めても、帳簿と国際収支が合ったままターンが回る。止めている間は
    その島の生産が止まって GDP が落ち、止めるのをやめると生産が戻る。ほかの島は回り続ける。"""
    world = copy.deepcopy(base)
    currencies = {b["currency"] for b in blocs.blocs(world) if len(b.get("members", [])) > 1}
    target = min(currencies) if currencies else min(world.island_ids())
    island = world.islands[target]
    halt = 12
    # マスを持つ施設（生産の段で動く施設。店・海運業・運送業はマスを持たない）
    halted = [
        f
        for f in island.facilities
        if f.built and fac.on_map(f) and not fac.is_housing(game.config, f)
    ]
    for f in halted:
        f.ext["halted_until"] = world.turn + halt
    made, gdp, rates = [], [], []

    def goods_made(stats):
        # 店のサービスは人が出すので数えない
        return sum(
            v
            for k, v in stats.items()
            if k.startswith("production.") and k != "production.services"
        )

    def each(w):
        stats = w.islands[target].stats
        made.append(goods_made(stats))
        gdp.append(stats.get("gdp", 0))
        # 止めている間に暴動で壊れた施設は、止めた印が消えて国が建て直し、動き出す（数えない）
        rates.append(max((f.operating_rate for f in halted if "halted_until" in f.ext), default=0))

    before, usual = island.stats.get("gdp", 0), goods_made(island.stats)
    run_checked(game, world, 36, each)
    # 止めた施設は止めている間は動かない（止めた後に建ち終わった施設は動く）。やめると、売れ行きを
    # 見ながら1年ほどかけて作り戻す
    assert max(rates[: halt - 1]) == 0
    assert max(made[-6:]) > usual * 0.3
    assert min(gdp[1 : halt - 1]) < before
    for i in world.island_ids():
        assert world.islands[i].stats.get("population", 0) > 0
        if i != target:
            assert world.islands[i].stats.get("gdp", 0) > 0


def _all_facilities(world):
    return [x for i in world.islands.values() for x in i.facilities]


def test_an_island_that_lost_every_ship_orders_new_ones(game, base):
    """船を全部失った島: 帳簿と国際収支が合ったままターンが回り、輸出は買い手の船で続く。船が
    足りない月が続くと海運業が船を買いに出て、手元の資金で足りない代金は持ち主が出す（船は運賃で
    稼ぐ前に買うので、変動費の利益を見ない）。どの島も船を作っていなければ、標準の計画が造船所を
    建てる。"""
    world = copy.deepcopy(base)
    island = next(
        i for i in world.islands.values() if shipping_of(i, game.config) and i.has_economy
    )
    f = shipping_of(island, game.config)
    f.economy["fleet"] = []
    good = game.config.get("trade.ship_good")
    exports, wanted = [], []

    def each(w):
        exports.append(w.islands[island.id].stats.get("trade.exports", 0))
        wanted.append(f.economy.get("ships_wanted", 0))

    # 船の作り手がいない間を見る（造船所ができると、その船を買って船団が戻る。試し遊び1回目の直しで
    # 流れが変わり、36 ターンのうちに標準の計画の造船所が動き出すようになった）
    for _ in range(36):
        if any(x.kind == "shipyard" and x.built for x in _all_facilities(world)):
            break
        run_checked(game, world, 1, each)
    assert ships.fleet(f) == 0  # どの島も造船所をまだ持っていない
    assert sum(exports) > 0  # 輸出は買い手の島の船で運ぶ
    assert wanted[0] == 0 and wanted[-1] == QUANTITY_SCALE  # 足りない月が続いてから1隻
    price = island.economy.prices[good]
    assert ledger.money(island, facility_entity(f.id)) >= money_of(price, wanted[-1])
    market = next(m for m in island.markets if m.good == good)
    assert market.demand >= QUANTITY_SCALE // 2 and market.supply == 0
    turn = world.turn
    planned = list(autoplan._candidates(island, game.defs, game.config, turn))
    # 標準の計画が造船所を候補に挙げるか、もう建て始めている（回7 の企業で流れが変わり、36 ターンの
    # うちに工事が始まるようになった）
    assert any(kind == "shipyard" for kind, _ in planned) or any(
        x.kind == "shipyard" for x in island.facilities
    )
