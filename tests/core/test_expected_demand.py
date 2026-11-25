"""見込み需要（MK-48。掲示板の会議の締め #2030・倭国さんの要望 B）の試験。

会議の合格条件 (i)〜(iii) を単体試験にしたもの:

(i) 第一希望の出し方: 品切れが半分（魚が買いたい量の半分しかなく、代わりに肉を買う）・
    両方とも在庫ゼロ・在庫はあるが予算が足りない、の3つの場合。
(ii) 3か所（作る量の計画・見込みの値段・参入と作り方の見直し）で家計分が見込みで読まれ、
    売れた量はそのまま（魚 4・肉 6）。需要 ≧ 売れた量を前提にする見方のもとの記録は
    注文の買いたい量のまま。
(iii) 昔と今の混ざった記録: 肉の買いたい量・売れた量は両ターン 6、見込みの欄は旧行になく
    新行に 0。読み込みの後も、保存して読み直した後も、見込みの系列は [6, 0] で、旧行は
    書き換えられない。
"""

from __future__ import annotations

import dataclasses

from hakoniwa_core.economy import GENERAL, Payer, needs
from hakoniwa_core.economy.prices import expected_price, expected_series
from hakoniwa_core.market.auction import Order
from hakoniwa_core.modules.firms import EntryExit, _demand_view
from hakoniwa_core.modules.market import (
    CLEARING,
    EXPECTED_FIRST,
    EXPECTED_PLACED,
    EXPECTED_WANTED,
    FACILITY,
    Book,
    FoodMenu,
    Market,
    Party,
    first_choice,
    food_orders,
)
from hakoniwa_core.state import MarketRecord, WorldState
from hakoniwa_core.state.serialize import world_from_dict, world_to_dict
from hakoniwa_core.turn.context import StageContext
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import home, make_island, with_economy

FISH, MEAT = "fish", "meat"
NEED = 10_000  # 栄養 10（魚も肉も 1 単位の栄養 1.0。魚 10 単位でちょうど満たす）


def menu() -> FoodMenu:
    """魚だけが好みの献立（値段は同じ）。魚があれば魚だけを買う。"""
    return FoodMenu(
        values={FISH: 1.0, MEAT: 1.0},
        nutritions={FISH: 1000, MEAT: 1000},
        qualities={FISH: 0.0, MEAT: 0.0},
        unit={FISH: 100, MEAT: 100},
        limits={FISH: 100, MEAT: 100},
        rate=0,
        tax={FISH: 0, MEAT: 0},
        elasticity=0.5,
        prefs={FISH: 1.0, MEAT: 0.0},
    )


def on_config(game):
    raw = {
        **game.config.raw,
        "market": {**game.config.raw["market"], "expected_demand": True},
    }
    return dataclasses.replace(game.config, raw=raw)


def context(game, config, turn=1):
    return StageContext(
        defs=game.defs,
        config=config,
        registry=game.registry,
        formulas=game.engine.formulas,
        turn=turn,
        seed=1,
        stage="test",
        before=None,
    )


# --- (i) 第一希望の出し方 ------------------------------------------------------


def test_first_choice_when_the_preferred_good_is_half_stocked():
    """魚が買いたい量の半分しかない: 実際に買うのは魚 4・肉 6（代わり）、第一希望は魚 10・肉 0。"""
    m = menu()
    plan = food_orders(NEED, {FISH: 4_000, MEAT: 10**9}, m, 10**9, 10**9)
    assert {g: q for g, (q, _lim) in plan.items()} == {FISH: 4_000, MEAT: 6_000}
    assert first_choice(NEED, m, 10**9, 10**9) == {FISH: 10_000}


def test_first_choice_when_nothing_is_stocked():
    """両方とも在庫ゼロでも、第一希望は一度だけ組んだ献立（魚 10・肉 0）のまま。"""
    assert first_choice(NEED, menu(), 10**9, 10**9) == {FISH: 10_000}


def test_first_choice_excludes_what_the_budget_cannot_buy():
    """在庫はたっぷりあるが予算が半分しかない: 買えない半分は第一希望に入らない。"""
    # 魚 10 単位を指値 100 で買うと 1000。予算 500 では 5 単位まで
    assert first_choice(NEED, menu(), 10**9, 500) == {FISH: 5_000}


# --- 記録（市場の記録 → finance の履歴） ----------------------------------------


def _recorded_island(game, config):
    """会議の例の記録（魚: 買いたい 10・売れた 4、肉: 買いたい 6・売れた 6。見込みは
    魚 10・肉 0）を置いて、品切れの記録を残した島。"""
    island = with_economy(game, make_island(game))
    island.markets = [
        MarketRecord(FISH, 100, 4_000, 10_000, 4_000, "trade", expected_demand=10_000),
        MarketRecord(MEAT, 100, 6_000, 6_000, 6_000, "trade", expected_demand=0),
    ]
    EntryExit._record_shortages(island, context(game, config))
    return island


def test_history_records_expected_and_keeps_sold(game):
    island = _recorded_island(game, on_config(game))
    finance = island.economy.finance
    assert finance["expected_demand"] == {FISH: [10_000], MEAT: [0]}
    # 注文の記録と売れた量は今までどおり（充足率のような見方のもと）
    assert finance["demand"] == {FISH: [10_000], MEAT: [6_000]}
    assert finance["sold"] == {FISH: [4_000], MEAT: [6_000]}
    assert finance["shortages"] == {FISH: [6_000], MEAT: [0]}


def test_history_records_nothing_while_the_switch_is_off(game):
    island = _recorded_island(game, game.config)
    assert "expected_demand" not in island.economy.finance


def test_history_fills_a_record_without_expected_from_its_demand(game):
    """見込みのない記録（作り手の違う記録）は、その行の買いたい量で補って足す。"""
    island = with_economy(game, make_island(game))
    island.markets = [MarketRecord(FISH, 100, 4_000, 10_000, 4_000, "trade")]
    EntryExit._record_shortages(island, context(game, on_config(game)))
    assert island.economy.finance["expected_demand"] == {FISH: [10_000]}


# --- (ii) 3か所で共通に読む ------------------------------------------------------


def _example_island(game):
    island = _recorded_island(game, on_config(game))
    island.economy.price_history.update({FISH: [1000], MEAT: [1000]})
    return island


def test_entry_and_recipe_review_read_expected(game):
    """参入と作り方の見直しの見方: 家計分は魚 10・肉 0。もとの記録は注文のまま。"""
    island = _example_island(game)
    demand, shortages = _demand_view(island, on_config(game))
    assert demand[FISH] == [10_000]
    assert demand[MEAT] == [0]  # 代わりに買った肉 6 は見込みに入らない
    assert shortages[FISH] == [6_000]  # 見込み 10 − 売りに出た 4
    assert shortages[MEAT] == [0]
    # 切り替えが切なら今までどおり注文の記録
    demand, shortages = _demand_view(island, game.config)
    assert demand[MEAT] == [6_000]
    assert island.economy.finance["demand"][MEAT] == [6_000]
    assert island.economy.finance["sold"][MEAT] == [6_000]


def test_expected_price_reads_expected(game):
    """見込みの値段: 肉は第一希望がないので値段を押し上げず、魚は見込み 10 で見る。"""
    island = _example_island(game)
    config = on_config(game)
    assert expected_price(island, MEAT, 3, game.defs, config) == 1000
    assert expected_price(island, FISH, 3, game.defs, config) > 1000


def test_production_plan_reads_expected(game):
    """作る量の計画（公定価格の島の需要の記録）: 魚は見込み 10、肉は見込み 0（目標に
    入らない）。切り替えが切なら注文の買いたい量（肉 6）で数える。"""
    island = _example_island(game)
    official = {FISH: 100, MEAT: 100}
    Market._plan_record(island, context(game, on_config(game)), {}, official, {})
    demand = island.ext["plan"]["demand"]
    assert demand[FISH] == [10_000]
    assert MEAT not in demand

    island = _example_island(game)
    Market._plan_record(island, context(game, game.config), {}, official, {})
    demand = island.ext["plan"]["demand"]
    assert demand[FISH] == [10_000]
    assert demand[MEAT] == [6_000]


# --- (iii) 昔と今の混ざった記録 ---------------------------------------------------


def _mixed_island(game):
    """旧ターン（見込みの記録なし）と新ターン（見込み 0）の混ざった島。肉の買いたい量・
    売れた量は両ターン 6 単位。"""
    island = with_economy(game, make_island(game))
    island.economy.finance.update(
        {
            "demand": {MEAT: [6_000, 6_000]},
            "supply": {MEAT: [6_000, 6_000]},
            "sold": {MEAT: [6_000, 6_000]},
            "expected_demand": {MEAT: [0]},
        }
    )
    island.economy.price_history[MEAT] = [1000, 1000]
    return island


def test_mixed_history_fills_old_rows_from_demand(game):
    island = _mixed_island(game)
    finance = island.economy.finance
    # 旧行はその行の買いたい量 6 で補い、新行の 0 は 0 のまま
    assert expected_series(finance, MEAT) == [6_000, 0]
    # 1回の見込みの値段の計算が、旧ターンの 6 と新ターンの 0 の両方を受ける
    assert expected_price(island, MEAT, 2, game.defs, on_config(game)) == 1000


def test_mixed_history_survives_a_save_round_trip_without_rewriting(game):
    island = _mixed_island(game)
    world = WorldState(turn=2, seed=1, islands={island.id: island})
    loaded = world_from_dict(world_to_dict(world)).islands[island.id]
    finance = loaded.economy.finance
    # 読み直しても旧行は足されない（欄そのものが [0] のまま。書き換えはしない）
    assert finance["expected_demand"] == {MEAT: [0]}
    assert finance["demand"][MEAT] == [6_000, 6_000]
    assert finance["sold"][MEAT] == [6_000, 6_000]
    assert expected_series(finance, MEAT) == [6_000, 0]
    assert expected_price(loaded, MEAT, 2, game.defs, on_config(game)) == 1000


# --- 案 A: 品切れターンの見込みを需要水準に入れる（#5005・#5006・#5014） -------------


def _flows_island(game, demand, supply, sold, expected=None):
    """穀物の流れ（買いたい量・売りに出た量・売れた量と、あれば見込みの記録）を置いた島。
    値段の履歴は 1000 で一定。"""
    island = with_economy(game, make_island(game))
    island.economy.price_history["grain"] = [1000] * 6
    finance = {
        "demand": {"grain": list(demand)},
        "supply": {"grain": list(supply)},
        "sold": {"grain": list(sold)},
    }
    if expected is not None:
        finance["expected_demand"] = {"grain": list(expected)}
    island.economy.finance.update(finance)
    return island


def _price(game, island, config):
    return expected_price(island, "grain", 3, game.defs, config)


def test_plan_a_recorded_stockout_turns_raise_the_price(game):
    """売れ残り（買いたい 4000）と品切れ（買いたい 5000・見込み 9000）が半々の島。
    切は今までどおり（1027 は直す前の値）。入では、記録のある品切れターンの見込みが
    需要水準に入って、見込みの値段が切より上がる（直す前は入でも 1027 で、信号が
    捨てられていた）。"""
    island = _flows_island(
        game,
        [4000] * 3 + [5000] * 3,
        [8000] * 3 + [5000] * 3,
        [4000] * 3 + [5000] * 3,
        [4000] * 3 + [9000] * 3,
    )
    assert _price(game, island, game.config) == 1027
    assert _price(game, island, on_config(game)) > 1027


def test_plan_a_no_stockout_no_difference(game):
    """品切れのない島（見込み = 買いたい量）では、入にしても値段は変わらない。"""
    island = _flows_island(game, [4000] * 6, [8000] * 6, [4000] * 6, [4000] * 6)
    assert _price(game, island, on_config(game)) == _price(game, island, game.config) == 1000


def test_plan_a_longer_recorded_stockout_raises_monotonically(game):
    """供給は一定。品切れ（見込み 9000）のターンが長いほど、入の見込みの値段は
    単調に上がる。切はどの長さでも今のまま動かない。"""
    prices = []
    for k in (0, 2, 4, 6):
        island = _flows_island(
            game,
            [4000] * (6 - k) + [5000] * k,
            [5000] * 6,
            [4000] * (6 - k) + [5000] * k,
            [4000] * (6 - k) + [9000] * k,
        )
        assert _price(game, island, game.config) == 1000
        prices.append(_price(game, island, on_config(game)))
    assert prices[0] == 1000
    assert prices[0] < prices[1] < prices[2] < prices[3]


def test_plan_a_zero_expected_erases_the_difference(game):
    """見込みが 0 の財（第一希望が無い。肉の例）では、全ターン品切れでも入と切で
    差は出ない。"""
    island = _flows_island(game, [6000] * 6, [6000] * 6, [6000] * 6, [0] * 6)
    assert _price(game, island, on_config(game)) == _price(game, island, game.config) == 1000


def test_plan_a_leftover_turns_are_not_overcompensated(game):
    """品切れターンの見込みが売れ残りと同じ水準（4000）なら、需要水準は変わらず、
    値段は入でも切でも平均のまま（売れ残りを過補填しない）。"""
    island = _flows_island(
        game,
        [4000] * 3 + [5000] * 3,
        [5000] * 6,
        [4000] * 3 + [5000] * 3,
        [4000] * 6,
    )
    assert _price(game, island, on_config(game)) == _price(game, island, game.config) == 1000


def test_plan_a_stockout_without_records_stays_excluded(game):
    """記録の始まる前の品切れターン（見込みの記録がない）は、入でも平均に入らない。
    記録のある売れ残りだけなら切と同じになる。"""
    island = _flows_island(
        game,
        [5000] * 3 + [4000] * 3,
        [5000] * 3 + [8000] * 3,
        [5000] * 3 + [4000] * 3,
        [4000] * 3,  # 記録は末尾の売れ残り 3 ターン分だけ
    )
    assert _price(game, island, on_config(game)) == _price(game, island, game.config) == 1000


def test_fully_old_history_has_no_expected_key(game):
    """見込みの記録のない昔だけのセーブ: 欄そのものがない（None でなく欠落）。"""
    island = with_economy(game, make_island(game))
    island.economy.finance.update({"demand": {MEAT: [6_000]}})
    world = WorldState(turn=1, seed=1, islands={island.id: island})
    loaded = world_from_dict(world_to_dict(world)).islands[island.id]
    assert "expected_demand" not in loaded.economy.finance
    # 読み手は全行を買いたい量で補う
    assert expected_series(loaded.economy.finance, MEAT) == [6_000]


# --- 案 B: 衣料の第一希望も見込みの材料にする（#5007(2)・#5062・#5065） -------------

CLOTHING = "clothing"


def _clothing_island(game, config, held, offered_qty, centre=100):
    """衣料だけを見る島: 1000 人の層が衣料を held だけ持っている（None なら記録なし =
    目安どおり）。centre はいつもの値段（price_history）で、売りに出ている量が
    offered_qty（None なら売りなし）。家計の注文まで回して、島と板と（買いたい量・
    本来ほしかった量）を返す。"""
    island = make_island(game)
    home(game, island, 4, 4, 1000, "village")
    with_economy(game, island)
    state = island.economy.households[GENERAL]
    if held is not None:
        state[needs.CLOTHES] = {CLOTHING: held}
    if centre is not None:
        island.economy.price_history[CLOTHING] = [centre] * 12
        island.economy.prices[CLOTHING] = centre
    qty, full = needs.clothes_wanted(config, state, CLOTHING)
    books: dict[str, Book] = {}
    offered: dict[str, int] = {}
    if offered_qty is not None:
        seller = Party(FACILITY, "fac:a", Payer("fac:a"))
        books = {CLOTHING: Book([], [(Order(offered_qty, centre), seller)])}
        offered = {CLOTHING: offered_qty}
    Market()._household_orders(island, context(game, config), books, 0, offered)
    return island, books, qty, full


def _clothing_record(game, config, island, books):
    """板寄せ（払いはしない）と記録まで回し、衣料の市場の記録を返す。"""
    market = Market()
    results: dict = {}
    if books:
        island.scratch[CLEARING] = (None, None)
        market._settle = lambda *args, **kwargs: {}  # type: ignore[method-assign]
        persons = {n: h["persons"] for n, h in island.economy.households.items()}
        market._clear(island, context(game, config), CLOTHING, books, {}, persons, results, {})
    market._record(island, context(game, config), books, results, {})
    return next(r for r in island.markets if r.good == CLOTHING)


def test_plan_b_stockout_clothing_expected_reflects_the_full_amount(game):
    """品切れ（売り 50 < 注文）: 記録の demand は出した注文の量のまま、見込みは
    本来ほしかった量（目安まで全部）になる。注文の材料（PLACED）は板に出た注文の
    合計と一致する。"""
    config = on_config(game)
    island, books, qty, full = _clothing_island(game, config, 0, 50)
    assert 0 < qty < full  # 差のある層（傷み分と埋める分の一部だけ注文する）
    placed = island.scratch[EXPECTED_PLACED][CLOTHING]
    assert placed == sum(o.quantity for o, _p in books[CLOTHING].buys) == qty
    assert island.scratch[EXPECTED_FIRST][CLOTHING] == full
    record = _clothing_record(game, config, island, books)
    assert record.demand == qty
    assert record.expected_demand == full


def test_plan_b_unoffered_clothing_expected_reflects_the_full_amount(game):
    """売りに出ていない衣料も、買いたい量を差し引いて本来ほしかった量が見込みになる
    （食料の売りに出ていない分と同じ形）。"""
    config = on_config(game)
    island, books, qty, full = _clothing_island(game, config, 0, None)
    assert island.scratch[EXPECTED_WANTED][CLOTHING] == qty
    assert island.scratch[EXPECTED_FIRST][CLOTHING] == full
    record = _clothing_record(game, config, island, books)
    assert record.demand == qty
    assert record.expected_demand == full


def test_plan_b_no_gap_means_no_difference(game):
    """目安どおり持っている層（unmet_first = 0: 本来の量 = 注文の量）では、見込みは
    記録の demand と同じ（反応が出ない）。"""
    config = on_config(game)
    island, books, qty, full = _clothing_island(game, config, None, 10**9)
    assert qty == full > 0  # 傷んだ分の買い替えだけ
    first = island.scratch[EXPECTED_FIRST][CLOTHING]
    placed = island.scratch[EXPECTED_PLACED][CLOTHING]
    assert max(0, first - placed) == 0  # unmet_first
    record = _clothing_record(game, config, island, books)
    assert record.expected_demand == record.demand == qty


def test_plan_b_first_is_the_budget_capped_amount_added_once(game):
    """本来ほしかった量は、衣料の分の予算で買える量に絞った値をちょうど 1 回だけ足す
    （二重計算で膨れない）。真ん中の値段が高い場面では、買いたい量の絞りと同じ値になる。"""
    config = on_config(game)
    island, _books, qty, full = _clothing_island(game, config, 0, None, centre=None)
    wanted = island.scratch["wanted"][CLOTHING]
    assert 0 < wanted < qty  # 予算の絞りが効いている場面であること
    assert island.scratch[EXPECTED_WANTED][CLOTHING] == wanted
    assert island.scratch[EXPECTED_FIRST][CLOTHING] == wanted  # 絞った値を 1 回だけ


def test_plan_b_reaction_grows_monotonically_with_the_gap(game):
    """不足の深さ（目安どおり → 半分 → ゼロ）で、見込みの反応（見込み − 記録の demand）
    は単調に大きくなる。不足がなければ反応は 0。"""
    config = on_config(game)
    reactions = []
    for held in (None, 600, 0):
        island, books, qty, full = _clothing_island(game, config, held, 10**9)
        record = _clothing_record(game, config, island, books)
        assert record.expected_demand - record.demand == full - qty
        reactions.append(record.expected_demand - record.demand)
    assert reactions[0] == 0 < reactions[1] < reactions[2]


def test_plan_b_switch_off_records_nothing_for_clothing(game):
    """切り替えが切なら、衣料の材料は貯まらず、記録の見込みも残らない（従来どおり）。"""
    island, books, _qty, _full = _clothing_island(game, game.config, 0, 50)
    assert EXPECTED_FIRST not in island.scratch
    assert EXPECTED_PLACED not in island.scratch
    assert EXPECTED_WANTED not in island.scratch
    record = _clothing_record(game, game.config, island, books)
    assert record.expected_demand is None
