"""見込みの値段 p*（prices.expected_price。掲示板 #1238）: 流れが釣り合えば平均の値段、在庫が減り
続ければ上がり、余れば下がる。品切れのターンの減った買いを需要の減りと読まない。"""

from __future__ import annotations

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


def test_a_farm_keeps_grain_when_the_stock_runs_out_before_its_harvest(game):
    """test_gate 種 2 の T293 の場面（掲示板 #1238 の (d)）: 嗜好作物が直近の平均の値段では穀物より
    もうかって見えても、島の穀物の在庫が穀物の育つ期間のうちに尽きる流れなら、民営の畑は穀物の
    ままにする。流れが釣り合っていれば今までどおり嗜好作物に替える。"""
    from hakoniwa_core.modules.firms import EntryExit
    from hakoniwa_core.state import ACCOUNTING_SELF, OWNER_PRIVATE, OWNER_STATE
    from tests.core.test_balance_round4 import _ctx_of
    from tests.core.test_rules import add, home

    def choices(supply):
        island = make_island(game)
        home(game, island, 4, 4, 1000, "village")
        home(game, island, 9, 9, 4000)
        with_economy(game, island)
        farm = add(island, "farm", 6, 6, recipe="default", owner=OWNER_PRIVATE)
        farm.accounting = ACCOUNTING_SELF
        add(island, "farm", 7, 7, recipe="default", owner=OWNER_STATE)  # 穀物の作り手はほかにも
        economy = island.economy
        grain, luxury = economy.prices["grain"], economy.prices["luxury_crop"]
        economy.prices["luxury_crop"] = luxury = luxury * 5 // 4  # 嗜好作物がよく売れている
        economy.price_history.update({"grain": [grain] * 6, "luxury_crop": [luxury] * 6})
        economy.finance.update(
            {
                "demand": {"grain": [100_000] * 6, "luxury_crop": [1_000_000] * 6},
                "supply": {"grain": list(supply), "luxury_crop": [0] * 6},
                "sold": {"grain": [100_000] * 6, "luxury_crop": [0] * 6},
            }
        )
        turn = -farm.id % game.config.get("firms.recipe_turns")  # この畑の見直しの順番
        ctx = _ctx_of(game, island, turn)
        picked = EntryExit._recipe_choices(island, ctx, OWNER_PRIVATE)
        return next((r for f, r in picked if f is farm), None)

    balanced = [150_000] * 6  # 毎ターン 10 万来て 10 万売れ、5 万持ち越す
    assert choices(balanced) is not None and "luxury" in choices(balanced)
    draining = [400_000, 350_000, 300_000, 250_000, 200_000, 150_000]  # 来る量 5 万で在庫が減る
    assert choices(draining) is None
