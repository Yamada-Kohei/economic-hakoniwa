"""フェーズ3-3「経験・集積・産業政策と国際金融」: 経験と集積と称号、関税・輸出補助・輸入の割り当て・
禁輸・圏の決まり、援助、為替の介入と固定相場、国際収支、資本取引と外国の国債。

ターンを回す試験では、記録の段が毎ターン帳簿を確かめ、trade_check の段が島をまたぐお金（外貨と外国の
国債）と運ぶ途中の荷を確かめる（合わなければその場で止まる）。ここではさらに、毎ターン経常収支と
金融収支が等しいことと、非居住者の預金の増減が国際収支の記録と合うことを見る（run_checked）。
"""

from __future__ import annotations

import copy
import json

import pytest

from hakoniwa_core import ledger, stock
from hakoniwa_core.economy import books, debt, experience
from hakoniwa_core.gate import Recorder
from hakoniwa_core.log import format_log
from hakoniwa_core.modules import experience as rankings
from hakoniwa_core.modules.bonds import Bonds
from hakoniwa_core.modules.trade import check
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.simulate import world_digest
from hakoniwa_core.state import Facility, OrderState
from hakoniwa_core.state.generate import generate_world
from hakoniwa_core.state.model import WorldState
from hakoniwa_core.state.serialize import world_from_dict, world_to_dict
from hakoniwa_core.trade import (
    abroad,
    balance,
    blocs,
    contracts,
    policy,
    swap,
    traders,
    world_state,
)
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, to_basis
from tests.core.test_finance import books_ok, context, open_books
from tests.core.test_trade import run

BASE_TURNS = 40


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


def run_checked(game, world, turns, each_turn=None, extra=None):
    """標準の計画で回し、毎ターン国際収支の釣り合いと島をまたぐお金の突き合わせを確かめる
    （each_turn はそのあとに世界を渡して呼ぶ）。"""
    before = {i: _nonresident(world.islands[i]) for i in world.island_ids()}

    def each(w):
        for i in w.island_ids():
            island = w.islands[i]
            s = island.stats
            if "bop.current" in s:
                assert s["bop.current"] == s["bop.financial"], (w.turn, i)
                assert _nonresident(island) - before[i] == s["bop.liabilities.deposits"], (
                    w.turn,
                    i,
                )
            before[i] = _nonresident(island)
        assert check(w) == []
        if each_turn is not None:
            each_turn(w)

    run(game, world, turns, each, extra)


def _nonresident(island):
    return ledger.aggregates(island.accounts)["nonresident"]


def _pair(world):
    """為替のレートのある (島, 相手の通貨の島) の組（番号の小さい順の最初）。"""
    for i in world.island_ids():
        for key in sorted(fxrates.rates(world.islands[i]), key=int):
            if int(key) != i:
                return world.islands[i], world.islands[int(key)]
    raise AssertionError("為替の組がない")


def _give_fx(world, holder, entity, currency, amount):
    """試験のため、発行した島の中央銀行から holder の entity に外貨 currency を渡す。"""
    value = fxrates.to_own(holder, currency, amount)
    fxmoney.transfer(
        world, currency, (currency, ledger.CB), (holder.id, entity), amount, "fx_exchange", value
    )


# --- 経験・集積・称号 -------------------------------------------------------------------


def test_experience_grows_with_output_and_raises_productivity(game, base):
    settings = game.config.get("experience")
    grown = [
        (i, industry, amount)
        for i in base.island_ids()
        for industry, (amount, _) in experience.table(base.islands[i]).items()
    ]
    assert grown, "どの島にも経験がない"
    island_id, industry, amount = max(grown, key=lambda r: r[2])
    island = base.islands[island_id]
    value = experience.factor(island, game.config, industry)
    assert 1 < value <= max(settings["cap"], settings["assembly_cap"])
    assert island.stats[f"experience.{industry}"] == amount / experience.FULL


def test_experience_factor_follows_the_formula_and_its_caps(game):
    settings = game.config.get("experience")
    island = generate_world(2, 1, game.config, game.defs).islands[1]
    base_turns = settings["base_turns"]
    experience.table(island)["steel"] = [base_turns * experience.FULL, 0]  # log2(2) = 1
    assert experience.factor(island, game.config, "steel") == pytest.approx(1 + settings["slope"])
    experience.table(island)["steel"] = [10**12, 0]
    experience.table(island)["automobile"] = [10**12, 0]
    assert experience.factor(island, game.config, "steel") == settings["cap"]
    assert experience.factor(island, game.config, "automobile") == settings["assembly_cap"]
    assert experience.factor(island, game.config, None) == 1.0


def test_an_industry_left_idle_for_a_year_fades(game):
    settings = game.config.get("experience")
    island = generate_world(2, 1, game.config, game.defs).islands[1]
    busy = settings["base_turns"] * experience.FULL
    experience.table(island)["steel"] = [100 * busy, busy]  # その年も動かした
    experience.table(island)["textile"] = [100 * busy, 0]  # その年は動かさなかった
    faded = experience.close_year(island, game.config)
    assert faded == ["textile"]
    assert experience.total(island, "steel") == 100 * busy
    assert experience.total(island, "textile") == int(100 * busy * (1 - settings["decay"]))
    assert experience.table(island)["steel"][1] == 0  # その年の量は 0 に戻る


def test_cluster_counts_same_industry_factories_nearby(game):
    defs, config = game.defs, game.config
    settings = config.get("experience")
    island = generate_world(2, 1, defs=defs, config=config).islands[1]
    island.facilities = []
    recipe = defs.facilities["steel_mill"]["recipes"]
    recipe = next(iter(recipe.values()))
    assert experience.cluster_factor(island, defs, config, recipe, 5, 5) == 1.0
    island.facilities = [
        Facility(id=1, kind="steel_mill", x=6, y=6, built=True),
        Facility(id=2, kind="steel_mill", x=5, y=5 + settings["cluster_radius"], built=True),
        Facility(id=3, kind="steel_mill", x=5, y=5 + settings["cluster_radius"] + 1, built=True),
        Facility(id=4, kind="steel_mill", x=7, y=7, built=False),  # 工事中は数えない
        Facility(id=5, kind="textile_mill", x=4, y=4, built=True),  # ほかの産業
        Facility(id=6, kind="farm", x=4, y=5, built=True),
    ]
    found = experience.neighbors(island, defs, config, "steel", 5, 5)
    assert found == 2
    value = experience.cluster_factor(island, defs, config, recipe, 5, 5)
    assert value == pytest.approx(1 + settings["cluster_step"] * 2)
    farm = next(iter(defs.facilities["farm"]["recipes"].values()))
    assert experience.cluster_factor(island, defs, config, farm, 4, 4) == 1.0  # 農場には効かない


def test_titles_go_to_the_leader_and_the_loser_is_told(game, base):
    world = copy.deepcopy(base)
    ctx = context(game, world)
    a, b = world.islands[1], world.islands[2]
    experience.table(a)["shipbuilding"] = [10**9, 0]
    experience.table(b).pop("shipbuilding", None)
    rankings.update(world, ctx)
    assert world.ext[experience.RANKINGS]["titles"]["shipbuilding"] == 1
    assert "shipbuilding" in experience.titles_of(world, 1)
    assert any(e.kind == "title_gained" for e in a.log)
    a.log.clear()
    experience.table(b)["shipbuilding"] = [2 * 10**9, 0]
    rankings.update(world, ctx)
    assert world.ext[experience.RANKINGS]["titles"]["shipbuilding"] == 2
    lost = next(e for e in a.log if e.kind == "title_lost")
    assert lost.args["country"] == b.name
    # 称号の名前（史実のあだ名）は、表示のときに画面の側が title として足す（display.title_name）
    text = format_log(game.defs, lost.kind, {**lost.args, "title": "海の馬車屋"})
    assert b.name in text and "海の馬車屋" in text and "{" not in text
    assert b.stats["experience_rank.shipbuilding"] == 1.0
    assert a.stats["experience_rank.shipbuilding"] == 2.0


def test_gate_flags_titles_held_by_one_island(game, base):
    recorder = Recorder(base, game.defs, game.config)
    world = copy.deepcopy(base)
    world.ext[experience.RANKINGS]["titles"] = {name: 1 for name in game.defs.industries()}
    recorder.record(world)
    assert any("称号" in line for line in recorder.failures(start=0))
    world.ext[experience.RANKINGS]["titles"] = {
        name: 1 + n % 6 for n, name in enumerate(sorted(game.defs.industries()))
    }
    recorder.record(world)
    assert not any("称号" in line for line in recorder.failures(start=0))


# --- 産業政策 -------------------------------------------------------------------------


def _all_goods(game):
    return [g for g, spec in game.defs.goods.items() if "freight_weight" in spec]


def test_tariffs_are_paid_on_bloc_imports_and_lower_the_bids(game, base):
    world = copy.deepcopy(base)
    rate = to_basis(0.2)
    for island in world.islands.values():
        island.policy.tariffs = dict.fromkeys(_all_goods(game), rate)
    totals = dict.fromkeys(world.island_ids(), 0)

    def keep(w):
        for i in w.island_ids():
            totals[i] += w.islands[i].stats.get("trade.tariffs", 0)

    run_checked(game, world, 8, keep)
    assert sum(totals.values()) > 0
    # 商社の見込みの輸入の指値は、関税の分だけ下がる（計画経済の国営の商社は、関税によらず圏の
    # 値幅の上端で輸入するので除く）
    market = [i for i in totals if not traders.is_planned(world.islands[i])]
    island = world.islands[max(market, key=totals.get)]
    plain = copy.deepcopy(island)
    plain.policy.tariffs = {}
    taxed = traders.prospects(island, game.defs, game.config)
    free = traders.prospects(plain, game.defs, game.config)
    compared = [
        good
        for good, p in free.items()
        if p.import_limit > 0 and good in taxed and taxed[good].import_bloc is not None
    ]
    assert compared
    for good in compared:
        assert taxed[good].import_limit < free[good].import_limit, good


def test_bloc_without_tariffs_exempts_its_market(game, base):
    world = copy.deepcopy(base)
    for island in world.islands.values():
        island.policy.tariffs = dict.fromkeys(_all_goods(game), to_basis(0.3))
    for bloc in blocs.blocs(world):
        blocs.set_no_tariff(world, bloc, True)
    member = world.islands[blocs.blocs(world)[0]["members"][0]]
    assert any(e.kind == "bloc_no_tariff" for e in member.log)
    total = []
    run(
        game,
        world,
        8,
        lambda w: total.append(sum(i.stats.get("trade.tariffs", 0) for i in w.islands.values())),
    )
    assert sum(total) == 0


def test_export_subsidies_are_paid_from_the_subsidy_budget(game, base):
    world = copy.deepcopy(base)
    for island in world.islands.values():
        island.policy.export_subsidies = dict.fromkeys(_all_goods(game), 5)
        island.policy.budget["subsidies"] = 10**9
    paid = []
    run_checked(game, world, 6)
    run(
        game,
        world,
        4,
        lambda w: paid.append(
            sum(i.stats.get("trade.export_subsidies", 0) for i in w.islands.values())
        ),
    )
    assert sum(paid) > 0


def test_import_quota_stops_bloc_imports_of_the_good(game, base):
    world = copy.deepcopy(base)
    importer = None
    for island in world.islands.values():
        for f in traders.traders_of(island, game.config):
            for item in traders.goods_of(f).values():
                if item.get("dir") == traders.IMPORT and item.get("m", 0) > 0:
                    importer = island
    assert importer is not None, "輸入する島がない"
    importer.policy.import_quotas = {g: 0 for g in _all_goods(game)}
    start = world.turn
    run_checked(game, world, 4)
    shipped = [
        s
        for s in world_state(world)["shipments"]
        if s["to"] == importer.id and s["kind"] == "bloc" and s["loaded"] > start
    ]
    assert shipped == []
    whys = {
        item.get("why")
        for f in traders.traders_of(importer, game.config)
        for item in traders.goods_of(f).values()
    }
    assert "quota" in whys


def test_embargo_stops_trade_between_the_two_countries(game, base):
    world = copy.deepcopy(base)
    a = world.islands[1]
    others = [i for i in world.island_ids() if i != 1]
    a.policy.embargoes = {str(i): [] for i in others}
    assert policy.blocked(world, 1, others[0], "coal")
    assert policy.blocked(world, others[0], 1, "coal")  # 相手の国からも止まる
    start = world.turn
    run_checked(game, world, 6)
    moved = [
        s
        for s in world_state(world)["shipments"]
        if s["loaded"] > start and 1 in (s["from"], s["to"]) and s["kind"] == "bloc"
    ]
    assert moved == []


def test_island_embargoed_by_everyone_keeps_running(game, base):
    """設計書の揺さぶりの試験: 全部の国から禁輸された島は、海外の商人と島の中だけで回り続ける。"""
    world = copy.deepcopy(base)
    target = 3
    for i in world.island_ids():
        if i != target:
            world.islands[i].policy.embargoes = {str(target): []}
    gdp = []
    run_checked(game, world, 24, lambda w: gdp.append(w.islands[target].stats.get("gdp", 0)))
    assert min(gdp) > 0
    assert world.islands[target].stats.get("population", 0) > 0


def test_fixed_bloc_price_clears_at_the_price(game, base):
    world = copy.deepcopy(base)
    bloc = blocs.blocs(world)[0]
    good, price = max(bloc["prices"].items(), key=lambda kv: kv[1])
    blocs.set_fixed(world, bloc, good, price)
    rep = world.islands[bloc["rep"]]
    assert any(e.kind == "bloc_price_fixed" for e in rep.log)
    run_checked(game, world, 3)
    bloc = next(b for b in blocs.blocs(world) if b["id"] == bloc["id"])
    assert bloc["prices"][good] == price
    blocs.set_fixed(world, bloc, good, 0)
    assert "fixed" not in bloc


def test_policy_orders_check_and_log(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    defs = game.defs
    ctx = context(game, world)
    with pytest.raises(OrderFailed):
        policy.check_tariff(island, {"good": "electricity", "rate": 10}, defs)
    policy.set_tariff(island, {"good": "coal", "rate": 12.5}, ctx)
    assert island.policy.tariffs["coal"] == to_basis(0.125)
    policy.set_tariff(island, {"good": "coal", "rate": 0}, ctx)
    assert "coal" not in island.policy.tariffs
    policy.set_import_quota(island, {"good": "coal", "quantity": 2.5}, ctx)
    assert island.policy.import_quotas["coal"] == 2500
    policy.set_import_quota(island, {"good": "coal"}, ctx)
    assert "coal" not in island.policy.import_quotas
    policy.set_embargo(island, {"partner": 2, "good": "coal"}, ctx)
    policy.set_embargo(island, {"partner": 2, "good": "steel"}, ctx)
    assert island.policy.embargoes == {"2": ["coal", "steel"]}
    policy.set_embargo(island, {"partner": 2, "good": "coal", "lift": True}, ctx)
    assert island.policy.embargoes == {"2": ["steel"]}
    policy.set_embargo(island, {"partner": 2}, ctx)
    assert island.policy.embargoes == {"2": []}
    with pytest.raises(OrderFailed):
        policy.check_embargo(island, {"partner": 1}, defs)
    policy.write_notes(world, island)
    kinds = [e.kind for e in island.log]
    assert {"tariff_set", "tariff_removed", "import_quota_set", "import_quota_removed"} <= set(
        kinds
    )
    assert "embargo_set_good" in kinds and "embargo_lifted_good" in kinds and "embargo_set" in kinds
    for e in island.log:
        text = format_log(defs, e.kind, e.args)
        assert "{" not in text, (e.kind, text)
    assert world.islands[2].name in format_log(defs, "embargo_set", island.log[-1].args)
    # 禁輸された側の国にも知らせる（相手は禁輸した国の名前）
    told = [e for e in world.islands[2].log if e.kind.startswith("embargo")]
    assert [e.kind for e in told] == [
        "embargoed_good",
        "embargoed_good",
        "embargo_lifted_by_good",
        "embargoed",
    ]
    text = format_log(defs, told[-1].kind, told[-1].args)
    assert island.name in text and "{" not in text


# --- 援助 ---------------------------------------------------------------------------


def test_money_aid_is_a_transfer_in_both_balances(game, base):
    world = copy.deepcopy(base)
    donor, partner = _pair(world)
    amount = 5000
    donor.orders = [
        OrderState(id=1, seq=1, kind="send_aid", payload={"partner": partner.id, "amount": amount})
    ]
    game.engine.run_turn(world)
    assert donor.stats["aid.sent"] == amount
    assert donor.stats["bop.transfers"] == -amount
    assert partner.stats["bop.transfers"] == partner.stats["aid.received"] > 0
    assert any(e.kind == "aid_sent" for e in donor.log)
    assert any(e.kind == "aid_received" for e in partner.log)
    assert donor.stats["bop.current"] == donor.stats["bop.financial"]
    assert check(world) == []


def test_goods_aid_ships_goods_for_free(game, base):
    world = copy.deepcopy(base)
    donor, partner = _pair(world)
    good = "coal"
    stock.add(donor.warehouse, good, 50 * QUANTITY_SCALE, world.turn, game.defs.goods)
    contracts.aid(
        world, donor, {"partner": partner.id, "good": good, "quantity": 5, "turns": 3}, world.turn
    )
    c = contracts.contracts(world)[-1]
    assert c["aid"] and c["price"] == 0 and c["state"] == contracts.ACTIVE
    assert any(e.kind == "aid_goods_received" for e in partner.log)
    received = []
    run_checked(
        game,
        world,
        4,
        lambda w: received.append(w.islands[partner.id].stats.get("bop.transfers", 0)),
    )
    c = next(x for x in contracts.contracts(world) if x["id"] == c["id"])
    assert c["delivered"] > 0
    assert sum(received) > 0  # 届いた物は、受け取った国の経常収支の移転に入る


def test_aid_to_an_embargoed_country_is_refused(game, base):
    world = copy.deepcopy(base)
    donor, partner = _pair(world)
    partner.policy.embargoes = {str(donor.id): []}
    donor.orders = [
        OrderState(id=1, seq=1, kind="send_aid", payload={"partner": partner.id, "amount": 1000})
    ]
    game.engine.run_turn(world)
    failed = [e for e in donor.log if e.kind == "trade_request_failed"]
    assert failed and failed[0].args["reason"] == "reason_embargo"


# --- 為替の介入と固定相場 -----------------------------------------------------------------


def test_intervention_buys_foreign_currency_with_new_money(game, base):
    """介入の買いは、島の売り手（ここでは外貨を受け取った政府）から買う。買った外貨は、中央銀行どうしの
    融通で借りている分の利子と返済にも回るので、中央銀行の持ち高ではなく、介入で買った量と政府が売った
    量で確かめる。"""
    world = copy.deepcopy(base)
    island, other = _pair(world)
    amount = 20_000
    island.policy.fx_intervention = {str(other.id): amount}
    _give_fx(world, island, ledger.GOV, other.id, amount)
    before = fxmoney.holding(island, ledger.GOV, other.id)
    run_checked(game, world, 1)
    assert fxmoney.holding(island, ledger.GOV, other.id) < before
    assert island.stats.get(f"fx.intervened.{other.id}", 0) > 0
    assert any(e.kind == "fx_intervention" for e in island.journal)


def test_independent_central_bank_stops_steering_the_rate(game, base):
    world = copy.deepcopy(base)
    island, other = _pair(world)
    island.policy.fx_intervention = {str(other.id): 1000}
    island.policy.fx_peg = {}
    with pytest.raises(OrderFailed):
        policy.check_fx_peg(_independent(island), {"currency": other.id, "rate": 1.0}, game.defs)
    from hakoniwa_core.modules.central_bank import set_independence

    set_independence(island, {"independent": True}, context(game, world))
    assert island.policy.fx_intervention == {}
    assert any(e.kind == "cb_independent_fx" for e in island.log)


def _independent(island):
    copy_ = copy.deepcopy(island)
    copy_.policy.cb_independent = True
    return copy_


def test_peg_holds_the_rate_near_the_peg(game, base):
    world = copy.deepcopy(base)
    island, other = _pair(world)
    _give_fx(world, island, ledger.CB, other.id, 10**8)  # 守るのに十分な外貨準備
    peg = fxrates.rate(island, other.id)
    island.policy.fx_peg = {str(other.id): peg}
    band = fxrates.peg_band(game.config)
    rates = []
    run_checked(
        game, world, 6, lambda w: rates.append(fxrates.rate(w.islands[island.id], other.id))
    )
    assert island.policy.fx_peg == {str(other.id): peg}
    for r in rates:
        assert abs(r - peg) <= peg * band // BASIS + 1


def test_peg_breaks_when_reserves_run_out(game, base):
    """設計書の揺さぶりの試験: 外貨準備の少ない国の固定相場は、準備が尽きたターンに外れる。"""
    world = copy.deepcopy(base)
    island, other, volume = _busiest(world)
    have = fxmoney.holding(island, ledger.CB, other.id)
    _take_fx(world, island, ledger.CB, other.id, have - volume // 50)  # 準備を少しだけ残す
    # 商社などの手持ちの外貨も取り上げる（手持ちで輸入を払えると、外貨を買う人が来ず準備が減らない）
    for entity in fxmoney.entities_holding(island, other.id):
        if entity != ledger.CB:
            _take_fx(world, island, entity, other.id, fxmoney.holding(island, entity, other.id))
    island.policy.fx_peg = {str(other.id): fxrates.rate(island, other.id) * 95 // 100}
    broken = []
    run_checked(
        game,
        world,
        6,
        lambda w: broken.extend(e for e in w.islands[island.id].log if e.kind == "fx_peg_broken"),
    )
    assert broken and island.policy.fx_peg == {}
    assert fxmoney.holding(island, ledger.CB, other.id) >= 0


def test_peg_far_from_the_rate_moves_it_by_the_band(game, base):
    """決めたレートが値幅の外なら、寄せる向きの注文だけを出し、レートは値幅ずつ寄っていく（買いと
    売りを値幅の同じ端に出して、自分どうしで当たっていた不具合の確かめ）。"""
    world = copy.deepcopy(base)
    island, other = _pair(world)
    _give_fx(world, island, ledger.CB, other.id, 10**8)
    start = fxrates.rate(island, other.id)
    island.policy.fx_peg = {str(other.id): start // 2}
    rates = []
    run_checked(
        game, world, 3, lambda w: rates.append(fxrates.rate(w.islands[island.id], other.id))
    )
    assert island.policy.fx_peg  # 準備は十分にある
    width = to_basis(game.config.get("fx.band"))
    # 毎ターンほぼ値幅だけ下がる。片側だけの注文では、相手のない量 ÷（相手のない量 + 市場の厚み）の
    # 割合だけ動く（fx._step）ので、準備が厚みよりずっと多くても、値幅の端にわずかに届かないことが
    # ある（自分どうしで当たる不具合では、レートがほとんど動かなかった）
    for before, after in zip([start, *rates], rates, strict=False):
        low = before * (BASIS - width) // BASIS
        assert after <= low + (before - low) // 100 + 1


def _passive(game, island, other, depth, reference, width):
    """受け身の注文だけの板を作り、(島の中央銀行の買い, 島の中央銀行の売り) の量を返す。上限は貿易の
    段と同じく abroad.reserve_room で。"""
    room = abroad.reserve_room(island, game.config)
    book = fxrates.Book(
        depth=depth,
        reserve_room=None if room is None else fxrates.to_foreign(island, other.id, room),
    )
    fxrates.dealer_orders(island, other.id, book, reference, width)
    assert all(who == ledger.CB for _, who, _ in book.buys + book.sells)  # 発行した国は出さない
    return (
        sum(o.quantity for o, who, _ in book.buys if who == ledger.CB),
        sum(o.quantity for o, who, _ in book.sells if who == ledger.CB),
    )


def test_passive_orders_stop_at_the_reserve_cap(game, base):
    """受け身の買いで中央銀行が持つ外国の資産は、借りている額を引いた正味で年の GDP の
    fx.reserve_share まで。上限に届くと買わない（関門で、外貨準備が年の GDP の何倍にも増え続けた
    直し）。売りは出す。GDP の記録がなければ上限なし。"""
    world = copy.deepcopy(base)
    island = next(  # 融通で借りていない島（借りていれば、その分まで買える）
        world.islands[i]
        for i in world.island_ids()
        if not swap.owed(world.islands[i]) and len(fxrates.rates(world.islands[i])) > 0
    )
    other = world.islands[int(min(fxrates.rates(island), key=int))]
    reference = fxrates.rate(island, other.id)
    width = to_basis(game.config.get("fx.band"))
    depth = 10_000
    _give_fx(world, island, ledger.CB, other.id, depth)
    share = to_basis(game.config.get("fx.reserve_share"))
    island.economy.gdp_history = [10**12] * 12  # 上限まで遠い
    cap = 10**12 * 12 * share // BASIS
    have = abroad.foreign_assets(island, ledger.CB) - swap.debt_value(island)
    assert abroad.reserve_room(island, game.config) == cap - have
    assert _passive(game, island, other, depth, reference, width)[0] == depth
    island.economy.gdp_history = [1] * 12  # 上限を超えている
    assert abroad.reserve_room(island, game.config) == 0
    cb_buy, cb_sell = _passive(game, island, other, depth, reference, width)
    assert cb_buy == 0 and cb_sell > 0
    island.economy.gdp_history = []  # 建国の最初のターン
    assert abroad.reserve_room(island, game.config) is None
    assert _passive(game, island, other, depth, reference, width)[0] == depth


def test_gate_flags_foreign_assets_that_keep_growing(game, base):
    recorder = Recorder(base, game.defs, game.config)
    world = copy.deepcopy(base)
    recorder.record(world)
    rec = next(iter(recorder.islands.values()))
    rec.reserves = [0.1] * 150
    assert not any("外国の資産" in line for line in recorder.failures(start=0))
    rec.reserves = [0.1] * 30 + [0.1 + 0.01 * t for t in range(120)]  # 120 ターンで 1.19 増える
    assert any("増え続けている" in line for line in recorder.failures(start=0))
    rec.reserves = [2.5] * 150
    assert any("まで増えた" in line for line in recorder.failures(start=0))


# --- 中央銀行どうしの融通（3-3 の続き） ----------------------------------------------------


def _bloc_trio(world):
    """決済通貨のある圏の (発行した島, 借り手, 貸し手)。

    借り手と貸し手は、発行した島でない加盟国。
    """
    for b in blocs.blocs(world):
        members = [i for i in sorted(b["members"]) if i != b["currency"]]
        if len(members) >= 2:
            return (world.islands[b["currency"]], *(world.islands[i] for i in members[:2]))
    raise AssertionError("加盟国が2つ以上の圏がない")


def _clean_swaps(world, *islands):
    """試験のため、島の融通の貸し借りと国際収支の流れを消す（貸し借りの相手の側の記録も）。"""
    for island in islands:
        for key, row in list(swap.owed(island).items()):
            for lender in list(row):
                swap._book(world, int(lender), island.id, int(key), -row[lender])
        for key, row in list(swap.lent(island).items()):
            for borrower in list(row):
                swap._book(world, island.id, int(borrower), int(key), -row[borrower])
        island.scratch.pop(balance.KEY, None)


def test_central_bank_borrows_from_a_bloc_surplus_before_the_issuer(game, base):
    """融通は、同じ圏で決済通貨が余っている加盟国の中央銀行から先に借り、足りない分を発行した国の
    中央銀行から借りる。貸し借りは両方の国の国際収支の金融収支に入り、釣り合う。"""
    world = copy.deepcopy(base)
    issuer, borrower, lender = _bloc_trio(world)
    c = issuer.id
    _clean_swaps(world, issuer, borrower, lender)
    borrower.economy.gdp_history = [10**12] * 12  # 上限まで遠い
    keep = fxrates.turnover(lender, c) * game.config.get("fx.swap_keep_turns")
    have = fxmoney.holding(lender, ledger.CB, c)
    _give_fx(world, lender, ledger.CB, c, keep - have + 30_000)  # 余りは 30,000
    lender.scratch.pop(balance.KEY, None)
    spares = dict(swap.lenders(world, game.config, borrower.id, c))
    assert spares[lender.id] == 30_000
    spare = sum(spares.values())  # ほかの加盟国にも余りがあれば、その分も
    want = spare + 20_000
    before = fxmoney.holding(borrower, ledger.CB, c)
    got = swap.draw(world, game.config, borrower.id, c, want)
    assert got == want
    pool = sum(v for k, v in swap.owed(borrower)[str(c)].items() if int(k) != c)
    assert pool == spare  # 加盟国の余りを先に（ほかの加盟国にも余りがあれば比例して）
    assert swap.owed(borrower)[str(c)][str(c)] == 20_000  # 残りは発行した国から
    assert swap.lent(lender)[str(c)][str(borrower.id)] <= 30_000
    assert swap.issued(issuer) == 20_000
    assert fxmoney.holding(borrower, ledger.CB, c) == before + want
    assert swap.reconcile(world) == [] and check(world) == []
    for island in (borrower, lender):
        current, financial = balance.totals(island.scratch[balance.KEY])
        assert current == 0 and financial == 0
    assert borrower.scratch[balance.KEY]["liabilities.swaps"] > 0
    assert lender.scratch[balance.KEY]["assets.swaps"] > 0
    # 借りている国は、同じ通貨を貸さない（借り手から借りる国があっても、借り手は貸し手にならない）
    _give_fx(world, borrower, ledger.CB, c, 10**9)
    assert borrower.id not in dict(swap.lenders(world, game.config, lender.id, c))


def test_swap_borrowing_stops_at_the_quota(game, base):
    """借りられるのは年の GDP の fx.swap_quota まで。

    使い切ると、受け身の売りは持っている分だけになる。
    """
    world = copy.deepcopy(base)
    issuer, borrower, _ = _bloc_trio(world)
    c = issuer.id
    _clean_swaps(world, borrower)
    borrower.economy.gdp_history = [10**12] * 12
    assert swap.available(world, game.config, borrower.id, c) > 0
    borrower.economy.gdp_history = [0] * 11 + [1]  # 上限は 0
    assert swap.available(world, game.config, borrower.id, c) == 0
    assert swap.draw(world, game.config, borrower.id, c, 10_000) == 0
    issuer.economy.gdp_history = [1] * 12  # 発行した国も新しく作れない
    borrower.economy.gdp_history = [10**12] * 12
    assert swap.available(world, game.config, borrower.id, c) == sum(
        spare for _, spare in swap.lenders(world, game.config, borrower.id, c)
    )


def test_swap_interest_is_paid_and_the_issuer_is_repaid_first(game, base):
    """利子は発行した国の政策金利 + fx.swap_spread で毎ターン払い（払えない分は借りた額に足す）、
    手元に置く分より多く持ったら、発行した国から先に返す。"""
    world = copy.deepcopy(base)
    issuer, borrower, lender = _bloc_trio(world)
    c = issuer.id
    _clean_swaps(world, issuer, borrower, lender)
    swap._book(world, c, borrower.id, c, 100_000)
    swap._book(world, lender.id, borrower.id, c, 50_000)
    rate = swap.rate(world, game.config, c)
    assert rate == issuer.policy.policy_rate + to_basis(game.config.get("fx.swap_spread"))
    # 持っている決済通貨がない: 利子は借りた額に足す
    _take_fx(world, borrower, ledger.CB, c, fxmoney.holding(borrower, ledger.CB, c))
    swap.service(world, game.config)
    assert swap.owed(borrower)[str(c)][str(c)] == 100_000 + ledger.interest(100_000, rate)
    assert swap.lent(lender)[str(c)][str(borrower.id)] == 50_000 + ledger.interest(50_000, rate)
    assert borrower.scratch[balance.KEY]["income"] < 0
    # 多く持つと、発行した国から先に返す
    owed_issuer = swap.owed(borrower)[str(c)][str(c)]
    keep = fxrates.turnover(borrower, c)
    _give_fx(world, borrower, ledger.CB, c, keep + owed_issuer + 10_000)
    lender_before = fxmoney.holding(lender, ledger.CB, c)
    swap.service(world, game.config)
    assert str(c) not in swap.owed(borrower).get(str(c), {})  # 発行した国の分は返し終えた
    assert swap.issued(issuer) == 0
    assert fxmoney.holding(lender, ledger.CB, c) > lender_before  # 加盟国にも利子と返済
    assert swap.reconcile(world) == [] and check(world) == []


def test_issuer_central_bank_no_longer_hoards_member_currencies(game, base):
    """決済通貨を発行した国の中央銀行は、加盟国の通貨をため込まない（受け身の売りは借り手の中央銀行
    が出し、発行した国は貸すだけ）。発行した国が新しく作って貸す額は上限の中。"""
    world = copy.deepcopy(base)
    run_checked(game, world, 12)
    for b in blocs.blocs(world):
        issuer = world.islands[b["currency"]]
        held = sum(fxmoney.holding(issuer, ledger.CB, m) for m in b["members"] if m != issuer.id)
        assert held == 0
        gdp = debt.annual_gdp(issuer.economy)
        share = to_basis(game.config.get("fx.swap_issuer_share"))
        assert swap.issued(issuer) <= gdp * share // BASIS + gdp // 100  # 利子を足した分の余裕
        assert issuer.stats["fx.swap.lent"] >= swap.issued(issuer)


def test_world_check_finds_swap_mismatch(game, base):
    world = copy.deepcopy(base)
    issuer, borrower, _ = _bloc_trio(world)
    swap.owed(borrower).setdefault(str(issuer.id), {})[str(issuer.id)] = 123
    swap.lent(issuer).pop(str(issuer.id), None)
    assert any("合いません" in p for p in swap.reconcile(world))


def _busiest(world):
    """前のターンに為替の売買の一番多かった (島, 相手の通貨の島, 売買量)。"""
    found = [
        (island.stats.get(f"fx.volume.{key}", 0), i, int(key))
        for i, island in world.islands.items()
        for key in fxrates.rates(island)
        if int(key) != i
    ]
    volume, i, other = max(found)
    assert volume > 0
    return world.islands[i], world.islands[other], volume


def _take_fx(world, holder, entity, currency, amount):
    """試験のため、holder の entity の外貨 currency を、発行した島の中央銀行に戻す。"""
    if amount <= 0:
        return
    value = fxrates.to_own(holder, currency, amount)
    fxmoney.transfer(
        world, currency, (holder.id, entity), (currency, ledger.CB), amount, "fx_exchange", value
    )


# --- 外国の国債 -----------------------------------------------------------------------


def _bonds_stage(world, bids):
    stage = Bonds()
    stage.world = world
    stage.bids = {bid.name: bid for bid in bids}
    return stage


def test_central_bank_buys_foreign_bonds_at_auction_and_is_repaid(game, base):
    world = copy.deepcopy(base)
    holder, issuer = _pair(world)
    before = fxmoney.holding(holder, ledger.CB, issuer.id)
    _give_fx(world, holder, ledger.CB, issuer.id, 50_000)
    open_books(issuer)
    bid = abroad.Bid(holder.id, ledger.CB, budget=50_000, target=50_000)
    stage = _bonds_stage(world, [bid])
    ctx = context(game, world)
    sold = stage.issue(issuer, ctx, 200_000, "bond_1y", "bonds_issued")
    bought = bid.done
    assert sold > 0 and bought > 0 and bid.budget == 50_000 - bought
    assert abroad.held(holder, ledger.CB, issuer.id)[0] == bought
    assert fxmoney.holding(holder, ledger.CB, issuer.id) == before + 50_000 - bought
    assert abroad.reconcile(world) == [] and check(world) == []
    books_ok(game, issuer)
    assert holder.scratch[balance.KEY]["assets.bonds"] > 0
    assert issuer.scratch[balance.KEY]["liabilities.bonds"] == bought
    assert any(e.kind == "bonds_bought_abroad" for e in issuer.log)
    # 満期（この銘柄だけ）: 利子と額面が外貨の持ち高に戻り、所得収支に入る
    bond = issuer.bonds[-1]
    issuer.bonds = [bond]
    ctx.turn = bond.maturity
    open_books(issuer)
    for island in (holder, issuer):  # 前のターンの国際収支（融通の利子など）を外す
        island.scratch.pop(balance.KEY, None)
    stage._pay(issuer, ctx, stage_payer())
    assert abroad.held(holder, ledger.CB, issuer.id)[0] == 0
    assert fxmoney.holding(holder, ledger.CB, issuer.id) > before + 50_000
    assert holder.scratch[balance.KEY]["income"] > 0
    assert issuer.scratch[balance.KEY]["income"] < 0
    assert abroad.reconcile(world) == [] and check(world) == []
    books_ok(game, issuer)


def stage_payer():
    from hakoniwa_core.economy import BONDS, Payer

    return Payer(ledger.GOV, BONDS)


def test_default_cuts_foreign_holders_too(game, base):
    world = copy.deepcopy(base)
    holder, issuer = _pair(world)
    bank = ledger.bank_entity(holder.economy.banks[0])
    _give_fx(world, holder, bank, issuer.id, 40_000)
    bid = abroad.Bid(holder.id, bank, budget=40_000, premium=0)
    stage = _bonds_stage(world, [bid])
    ctx = context(game, world)
    stage.issue(issuer, ctx, 10**9, "bond_1y", "bonds_issued")
    face = abroad.held(holder, bank, issuer.id)[0]
    assert face > 0
    stage._default(issuer, ctx, list(issuer.bonds), "bond_default_ordered")
    haircut = game.config.get("bonds.haircut")
    assert abroad.held(holder, bank, issuer.id)[0] == face - int(face * haircut)
    loss = books.facility_items(holder)[banks_number(bank)][books.FX_REALIZED]
    assert loss < 0
    assert abroad.reconcile(world) == [] and check(world) == []


def banks_number(bank):
    return int(bank.split(":")[1])


def test_foreign_holders_trade_in_the_secondary_market(game, base):
    world = copy.deepcopy(base)
    holder, issuer = _pair(world)
    _give_fx(world, holder, ledger.GOV, issuer.id, 30_000)
    stage = _bonds_stage(world, [abroad.Bid(holder.id, ledger.GOV, budget=30_000, target=30_000)])
    ctx = context(game, world)
    stage.issue(issuer, ctx, 10**9, "bond_1y", "bonds_issued")
    face = abroad.held(holder, ledger.GOV, issuer.id)[0]
    assert face > 0
    # 売る: 国内の銀行が買い手（中央銀行の買いの政策で必ず買い手がいるようにする）
    issuer.policy.cb_independent = False
    issuer.policy.cb_bond_trade = 10**9
    seller = abroad.Bid(holder.id, ledger.GOV, sell=face, target=face)
    stage.bids = {seller.name: seller}
    open_books(issuer)
    stage._secondary(issuer, ctx)
    assert seller.done > 0
    assert abroad.held(holder, ledger.GOV, issuer.id)[0] == face - seller.done
    assert abroad.reconcile(world) == [] and check(world) == []
    books_ok(game, issuer)


def test_capital_controls_decide_who_may_buy(game, base):
    world = copy.deepcopy(base)
    holder, issuer = _pair(world)
    defs = game.defs
    bank = ledger.bank_entity(0)
    holder.policy.capital_controls = policy.FREE
    issuer.policy.capital_controls = policy.FREE
    assert abroad.may_buy(world, defs, holder, bank, issuer)
    issuer.policy.capital_controls = policy.OFFICIAL
    assert not abroad.may_buy(world, defs, holder, bank, issuer)
    assert abroad.may_buy(world, defs, holder, ledger.CB, issuer)
    holder.policy.capital_controls = policy.CLOSED
    assert not abroad.may_buy(world, defs, holder, ledger.CB, issuer)
    with pytest.raises(OrderFailed) as e:
        policy.check_foreign_bonds(
            holder, {"issuer": issuer.id, "holder": "cb", "amount": 100}, defs
        )
    assert e.value.reason == "reason_capital_closed"
    policy.check_foreign_bonds(holder, {"issuer": issuer.id, "holder": "cb", "amount": -100}, defs)
    holder.policy.capital_controls = ""
    regime = holder.regime["economy"]
    assert policy.capital_controls(holder, defs) == defs.regimes[regime]["capital_controls"]


def test_banks_plan_foreign_currency_only_when_it_pays_and_capital_is_free(game, base):
    world = copy.deepcopy(base)
    holder, issuer = _pair(world)
    holder.policy.capital_controls = policy.FREE
    issuer.policy.capital_controls = policy.FREE
    # 相手の通貨の値下がりの率を引いても引き合うだけ上乗せする（値下がりの率は世界の流れで変わる）
    falling = max(0, to_basis(abroad.depreciation(holder, issuer.id)))
    issuer.economy.finance["yields"] = [y + 500 + falling for y in issuer.economy.finance["yields"]]
    holder.economy.finance["yields"] = list(holder.economy.finance["yields"])
    assert abroad.bank_pick(world, game.defs, game.config, holder, world.turn) == issuer.id
    needs = abroad.bank_needs(world, game.defs, game.config, holder, world.turn)
    assert needs and all(issuer.id in v and v[issuer.id] > 0 for v in needs.values())
    holder.policy.capital_controls = policy.OFFICIAL
    assert abroad.bank_needs(world, game.defs, game.config, holder, world.turn) == {}
    holder.policy.capital_controls = policy.FREE
    issuer.economy.finance["yields"] = list(holder.economy.finance["yields"])
    assert abroad.bank_pick(world, game.defs, game.config, holder, world.turn) is None


def test_central_bank_order_buys_foreign_bonds_over_turns(game, base):
    world = copy.deepcopy(base)
    holder, issuer = _pair(world)
    _give_fx(world, holder, ledger.CB, issuer.id, 200_000)
    holder.policy.foreign_bonds = {"cb": {str(issuer.id): 20_000}}
    bought = []
    run_checked(
        game,
        world,
        12,
        lambda w: bought.extend(
            e.args["amount"] for e in holder.log if e.kind.startswith("foreign_bonds_bought")
        ),
    )
    # 売りに出た額面が決めた額に届かないターンは foreign_bonds_bought_short と出る（どちらも
    # 買っている）
    assert bought and sum(bought) > 0
    stats = holder.stats
    assert stats["foreign_bonds.cb"] > 0
    assert stats["fx.reserves"] >= stats["foreign_bonds.cb"]


def test_world_check_finds_foreign_bond_mismatch(game, base):
    world = copy.deepcopy(base)
    holder, issuer = _pair(world)
    abroad._add(holder, ledger.GOV, issuer.id, 10, 10)
    problems = abroad.reconcile(world)
    assert problems and "合いません" in problems[0]


def test_island_order_does_not_change_the_bond_stage(game, base, monkeypatch):
    """国債の段（世界の段）は、島を逆の順に処理しても同じ結果になる（外国の持ち手の注文を段の初めに
    決め、ある島の処理がほかの島の処理の読む値を変えないため）。貿易の段は島の番号の順に決めるので、
    ここでは国債の段の中だけ順番を逆にする。"""

    def scenario(world):
        holder, issuer = _pair(world)
        # 融通で借りている分は先に返すので、その分も渡す
        _give_fx(world, holder, ledger.CB, issuer.id, 100_000 + swap.debt_in(holder, issuer.id))
        holder.policy.foreign_bonds = {"cb": {str(issuer.id): 10_000}}
        _give_fx(world, issuer, ledger.GOV, holder.id, 100_000)
        issuer.policy.foreign_bonds = {"gov": {str(holder.id): 10_000}}
        for island in world.islands.values():
            island.policy.capital_controls = policy.FREE
        return world

    forward = scenario(copy.deepcopy(base))
    holder = _pair(forward)[0].id
    bought = []
    run(
        game,
        forward,
        8,
        lambda w: bought.append(w.islands[holder].stats.get("foreign_bonds.cb", 0)),
    )
    assert max(bought) > 0
    original = Bonds.resolve

    def backward_resolve(self, requests, ctx):
        with monkeypatch.context() as patch:
            patch.setattr(WorldState, "island_ids", lambda w: sorted(w.islands, reverse=True))
            return original(self, requests, ctx)

    monkeypatch.setattr(Bonds, "resolve", backward_resolve)
    backward = scenario(copy.deepcopy(base))
    run(game, backward, 8)
    assert world_digest(forward) == world_digest(backward)


# --- 保存と国際収支 ---------------------------------------------------------------------


def test_new_policy_fields_survive_saving_and_old_saves_load(game, base):
    world = copy.deepcopy(base)
    island = world.islands[1]
    island.policy.tariffs = {"coal": 100}
    island.policy.embargoes = {"2": ["coal"]}
    island.policy.capital_controls = "closed"
    island.policy.foreign_bonds = {"cb": {"2": 5}}
    data = json.loads(json.dumps(world_to_dict(world), ensure_ascii=False))
    loaded = world_from_dict(data)
    assert loaded.islands[1].policy == island.policy
    for key in ("tariffs", "embargoes", "capital_controls", "foreign_bonds", "fx_peg"):
        data["islands"][0]["policy"].pop(key)
    old = world_from_dict(data)
    assert old.islands[1].policy.tariffs == {} and old.islands[1].policy.capital_controls == ""


def test_balance_of_payments_items_add_up(game, base):
    island = base.islands[1]
    s = island.stats
    current = sum(s[f"bop.{k}"] for k in balance.CURRENT_ITEMS)
    assets = sum(s[f"bop.{k}"] for k in balance.ASSET_ITEMS)
    liabilities = sum(s[f"bop.{k}"] for k in balance.LIABILITY_ITEMS)
    assert current == s["bop.current"]
    assert assets - liabilities == s["bop.financial"] == current
