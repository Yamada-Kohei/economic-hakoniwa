"""見込みの値段 p*（prices.expected_price。掲示板 #1238）: 流れが釣り合えば平均の値段、在庫が減り
続ければ上がり、余れば下がる。品切れのターンの減った買いを需要の減りと読まない。"""

from __future__ import annotations

import dataclasses

from hakoniwa_core import facilities as fac
from hakoniwa_core.economy.prices import expected_price
from tests.core.test_rules import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import make_island, with_economy

GOOD = "grain"


def _island(game, demand, supply, sold, prices=(1000,) * 6):
    island = with_economy(game, make_island(game))
    island.economy.price_history[GOOD] = list(prices)
    island.economy.finance.update(
        {"demand": {GOOD: list(demand)}, "supply": {GOOD: list(supply)}, "sold": {GOOD: list(sold)}}
    )
    return island


def _p(game, island, ahead):
    return expected_price(island, GOOD, ahead, game.defs, game.config)


def test_balanced_flows_keep_the_average_price(game):
    # 毎ターン 1000 来て 1000 売れ、在庫 500 を持ち越す
    island = _island(game, [1000] * 6, [1500] * 6, [1000] * 6, prices=[900, 1100] * 3)
    assert _p(game, island, 6) == 1000
    assert _p(game, island, 0) == 1000


def test_a_draining_stock_raises_the_price_before_it_runs_out(game):
    """在庫が毎ターン 300 ずつ減る（来る量 700、買いたい量 1000）。今は売れ残りがあり値段は動いて
    いないが、在庫が尽きた後は値段が上がる。先を見るほど高い。"""
    supply = [3000, 2700, 2400, 2100, 1800, 1500]
    island = _island(game, [1000] * 6, supply, [1000] * 6)
    near, far = _p(game, island, 1), _p(game, island, 6)
    assert near == 1000  # 次のターンはまだ在庫がある
    assert far > 1000
    assert _p(game, island, 12) > far


def test_reduced_buying_during_a_stockout_is_not_read_as_less_demand(game):
    """品切れで買えずに買いが減ったターン（売りに出た分が全部売れた）は、買いたい量に数えない。"""
    demand = [1000, 1000, 1000, 600, 600, 600]
    supply = [1200, 1100, 1000, 600, 600, 600]
    sold = [1000, 1000, 1000, 600, 600, 600]
    island = _island(game, demand, supply, sold)
    # 買いたい量は品切れの前の 1000 と見るので、来る量 600 では足りず上がる
    assert _p(game, island, 3) > 1000


def test_a_glut_lowers_the_price(game):
    supply = [5000, 5500, 6000, 6500, 7000, 7500]  # 売れ残りが目安を超えて積み上がる
    island = _island(game, [1000] * 6, supply, [1000] * 6)
    assert _p(game, island, 6) < 1000


def _choice(game, supply, luxury_flows=None, selling=0.0, dear=(5, 4)):
    """穀物の民営の畑 1 つ（ほかに国営の穀物の畑）の島で、作物の見直しが選ぶ作り方（替えなければ
    None）。supply は穀物の売りに出た量の記録、luxury_flows は嗜好作物の（買いたい量・売りに出た
    量・売れた量）、selling は畑が全力の産出の何割を売りに出しているか、dear は嗜好作物の値段を
    初めの値段の何倍にするか（分子, 分母）。"""
    from hakoniwa_core.modules.firms import EntryExit
    from hakoniwa_core.state import ACCOUNTING_SELF, OWNER_PRIVATE, OWNER_STATE
    from tests.core.test_balance_round4 import _ctx_of
    from tests.core.test_rules import add, home

    island = make_island(game)
    home(game, island, 4, 4, 1000, "village")
    home(game, island, 9, 9, 4000)
    with_economy(game, island)
    farm = add(island, "farm", 6, 6, recipe="default", owner=OWNER_PRIVATE)
    farm.accounting = ACCOUNTING_SELF
    add(island, "farm", 7, 7, recipe="default", owner=OWNER_STATE)  # 穀物の作り手はほかにも
    economy = island.economy
    grain, luxury = economy.prices["grain"], economy.prices["luxury_crop"]
    economy.prices["luxury_crop"] = luxury = luxury * dear[0] // dear[1]
    economy.price_history.update({"grain": [grain] * 6, "luxury_crop": [luxury] * 6})
    flows = luxury_flows or ([1_000_000] * 6, [0] * 6, [0] * 6)
    economy.finance.update(
        {
            "demand": {"grain": [100_000] * 6, "luxury_crop": list(flows[0])},
            "supply": {"grain": list(supply), "luxury_crop": list(flows[1])},
            "sold": {"grain": [100_000] * 6, "luxury_crop": list(flows[2])},
        }
    )
    if selling:
        farm.economy["expected"] = {"grain": fac.outputs_of(game.defs, farm)["grain"] * selling}
    turn = (-farm.id - 1) % game.config.get("firms.recipe_turns")  # 次のターンがこの畑の見直し
    picked = EntryExit._recipe_choices(island, _ctx_of(game, island, turn), OWNER_PRIVATE)
    return next((r for f, r in picked if f is farm), None)


BALANCED = [150_000] * 6  # 毎ターン 10 万来て 10 万売れ、5 万持ち越す


def test_a_farm_keeps_grain_when_the_stock_runs_out_before_its_harvest(game):
    """test_gate 種 2 の T293 の場面（掲示板 #1238 の (d)）: 嗜好作物が直近の平均の値段では穀物より
    もうかって見えても、島の穀物の在庫が穀物の育つ期間のうちに尽きる流れなら、民営の畑は穀物の
    ままにする。流れが釣り合っていれば今までどおり嗜好作物に替える。"""
    assert "luxury" in (_choice(game, BALANCED) or "")
    draining = [400_000, 350_000, 300_000, 250_000, 200_000, 150_000]  # 来る量 5 万で在庫が減る
    assert _choice(game, draining) is None


def test_a_big_farm_sees_its_own_crop_fill_the_shortage_where_it_moves(game):
    """替えた先の作物の見込みの値段は、自分が売りに出す分も足した流れで見る（掲示板 #1294。test_gate
    種 1 の T294: 島の穀物をほぼ 1 つで作る畑が替えた）。嗜好作物が品切れ続きで、平均の値段では
    穀物と変わらないとき、売っている量のごく小さい畑には品切れが続いて値段が上がると見えて替えるが、
    全力で売っている大きい畑は、自分が移れば品切れが埋まって値段が上がらないと見て替えない。"""
    short = ([36_000] * 6, [30_000] * 6, [30_000] * 6)  # 3 万来て売り切れ、6 千足りない
    assert "luxury" in (_choice(game, BALANCED, short, selling=0.01, dear=(1, 1)) or "")
    assert _choice(game, BALANCED, short, selling=1.0, dear=(1, 1)) is None


def _cotton_island(game, demand, supply, sold):
    island = with_economy(game, make_island(game))
    for good in ("cotton", GOOD):
        island.economy.price_history[good] = [1000] * 6
    island.economy.finance.update(
        {
            "demand": {g: list(demand) for g in ("cotton", GOOD)},
            "supply": {g: list(supply) for g in ("cotton", GOOD)},
            "sold": {g: list(sold) for g in ("cotton", GOOD)},
        }
    )
    return island


def test_crops_sown_on_other_farms_count_from_their_harvest(game):
    """ほかの畑が蒔いてまだ取れていない作物は、収穫の始まるターンから来る量に足す（掲示板 #1349 の
    1。test_gate 種 1 で、3 つの畑が続けて綿に入り、取れた後に綿が余った）。替えると決めたばかりの
    畑は、前の作物が1ターンに収穫の分ずつ減り、育つ期間の後に新しい作物が来る。見直している畑
    自身（skip）は数えない。"""
    from tests.core.test_rules import add

    supply = [100_000, 92_000, 84_000, 76_000, 68_000, 60_000]  # 在庫が毎ターン 8 千減る
    island = _cotton_island(game, [40_000] * 6, supply, [40_000] * 6)
    farm = add(island, "farm", 6, 6, recipe="cotton")
    farm.economy[fac.FIELDS] = {"default": 0}
    farm.economy[fac.SOWN] = [["cotton", 1000, 12]]  # ターン 12 に綿の畑が取れ始める
    defs, config = game.defs, game.config

    def p(good, ahead, turn=None, skip=None):
        return expected_price(island, good, ahead, defs, config, 0.0, turn, skip)

    assert p("cotton", 12) > 1000  # 蒔いた綿を見なければ、在庫が尽きて上がる
    assert p("cotton", 12, turn=10) < p("cotton", 12)
    assert p("cotton", 12, turn=10, skip=farm) == p("cotton", 12)

    island.facilities.remove(farm)
    switching = add(island, "farm", 7, 7, recipe="default")
    switching.ext[fac.RECIPE_NEXT] = "cotton"  # 穀物から綿へ替えると決めた
    assert p("cotton", 6, turn=10) == p("cotton", 6)  # 育つ 6 ターンのうちは綿は来ない
    assert p("cotton", 12, turn=10) < p("cotton", 12)
    assert p(GOOD, 8, turn=10) > p(GOOD, 8)  # 穀物は蒔き直す分ずつ減る


def test_a_mill_short_of_cotton_still_needs_what_its_sales_call_for(game):
    """原料が足りずに操業を絞った工場の注文の減りを、需要の減りと読まない（掲示板 #1349 の 2）。
    買いたい量は、工場が作る物の売れ行きの見込みで動く稼働率の分の原料までは数える。売れ行きが
    落ちて見込みが下がった工場は、その分だけ少なく数える（会議の確かめ: 高すぎないか）。"""
    from tests.core.test_rules import add

    island = _cotton_island(game, [4_000] * 6, [4_000] * 6, [4_000] * 6)  # 4 千来て売り切れ
    mill = add(island, "textile_mill", 6, 6)
    full = fac.outputs_of(game.defs, mill)["clothing"]
    need = fac.recipe_of(game.defs, mill, fuelled=False)["inputs"]["cotton"]
    assert need > 4_000

    def p():
        return expected_price(island, "cotton", 6, game.defs, game.config)

    mill.economy["expected"] = {"clothing": full}  # 服は全力の分売れる見込み
    assert p() > 1000
    mill.economy["expected"] = {"clothing": full * 4_000 // need}  # 服の売れ行きが落ちた
    assert p() == 1000


# --- 案 A の 1 行証拠（#5014）: 記録のある品切れターンを見込みで平均に入れる --------


def _on_config(game):
    raw = {
        **game.config.raw,
        "market": {**game.config.raw["market"], "expected_demand": True},
    }
    return dataclasses.replace(game.config, raw=raw)


def _flows(game, demand, supply, sold, expected):
    island = with_economy(game, make_island(game))
    island.economy.price_history[GOOD] = [1000] * 6
    island.economy.finance.update(
        {
            "demand": {GOOD: list(demand)},
            "supply": {GOOD: list(supply)},
            "sold": {GOOD: list(sold)},
            "expected_demand": {GOOD: list(expected)},
        }
    )
    return island


def test_plan_a_price_rises_monotonically_with_recorded_stockout_length(game):
    """品切れ（見込み 9000）のターンが なし → 短い → 長い で、入の見込みの値段は単調に
    上がる。切はどの長さでも同じ。"""
    on, off = _on_config(game), game.config
    prices = []
    for k in (0, 3, 6):
        island = _flows(
            game,
            [4000] * (6 - k) + [5000] * k,
            [5000] * 6,
            [4000] * (6 - k) + [5000] * k,
            [4000] * (6 - k) + [9000] * k,
        )
        assert expected_price(island, GOOD, 3, game.defs, off) == 1000
        prices.append(expected_price(island, GOOD, 3, game.defs, on))
    assert prices[0] == 1000 < prices[1] < prices[2]


def test_plan_a_zero_expected_erases_the_on_off_difference(game):
    """見込みが 0 の財では、全ターン品切れでも入と切で差は出ない。"""
    island = _flows(game, [6000] * 6, [6000] * 6, [6000] * 6, [0] * 6)
    on = expected_price(island, GOOD, 3, game.defs, _on_config(game))
    off = expected_price(island, GOOD, 3, game.defs, game.config)
    assert on == off


def test_plan_a_leftover_only_turns_are_unchanged(game):
    """売れ残りのターン（売りに出た量 > 売れた量）だけの島では、入にしても値段は
    切と同じ（売れ残りの平均の仕方は変えない）。"""
    island = _flows(game, [4000] * 6, [8000] * 6, [4000] * 6, [4000] * 6)
    on = expected_price(island, GOOD, 3, game.defs, _on_config(game))
    off = expected_price(island, GOOD, 3, game.defs, game.config)
    assert on == off == 1000
