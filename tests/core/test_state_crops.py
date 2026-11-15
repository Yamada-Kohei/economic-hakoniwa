"""市場経済・混合経済の島の国営の農場の作物の見直し（firms.state_crop_choices。掲示板の会議 #825）。

1つの島の 300 ターンの試験で、国の綿の農場が服の工場の買う量の 8 倍まで綿を積み、値段が初めの
0.23 倍まで落ちても綿を作り続けた。民営の農場と同じ見直しで、標準の計画が作物を替える。"""

from __future__ import annotations

from hakoniwa_core import autoplan
from hakoniwa_core import facilities as fac
from hakoniwa_core.modules import firms
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE
from tests.core.test_finance import game as game  # noqa: F401  （試験の部品を使い回す）
from tests.core.test_rules import add, home, make_island, with_economy

RECIPE_TURNS = 12


def _island(game, recipe="cotton", regime="market"):
    """村が1つ、町が1つ、国営の農場が1つの島。綿は余り、穀物は品切れ。"""
    island = make_island(game)
    island.regime["economy"] = regime
    home(game, island, 4, 4, 1000, "village")
    home(game, island, 9, 9, 4000)
    with_economy(game, island)
    farm = add(island, "farm", 6, 6, recipe=recipe, owner=OWNER_STATE)
    finance = island.economy.finance
    finance["supply"] = {"grain": [0] * 6, "cotton": [90000] * 6}
    finance["demand"] = {"grain": [5000] * 6, "cotton": [9000] * 6}
    island.economy.price_history["grain"] = [island.economy.prices["grain"] * 3]
    island.economy.price_history["cotton"] = [island.economy.prices["cotton"] // 4]
    island.stats["food.ratio"] = 0.97
    return island, farm


def _turn(farm, off=False):
    """農場の見直しの順番のターン（off なら順番でないターン）。"""
    return next(t for t in range(1, 30) if bool((t + farm.id) % RECIPE_TURNS) == off)


def test_state_farm_switches_to_the_missing_staple_on_a_market_island(game):
    """綿が余り、穀物が品切れなら、国営の農場も穀物に替える（足りないときに主食へ替えるのは止めない）。
    標準の計画は作り方の変更の命令で替える。"""
    island, farm = _island(game)
    turn = _turn(farm)
    chosen = firms.state_crop_choices(island, game.defs, game.config, turn)
    assert [
        (f.id, "grain" in game.defs.facilities[f.kind]["recipes"][r]["outputs"]) for f, r in chosen
    ] == [(farm.id, True)]
    orders = autoplan._crop_orders(island, game.defs, game.config, turn)
    assert orders == [("change_recipe", {"x": 6, "y": 6, "recipe": chosen[0][1]})]


def test_planned_islands_and_resowing_farms_are_left_alone(game):
    """計画経済の島の農場は見ない。畑を蒔き直している途中の農場も見ない（作物の周期に合わせる）。"""
    island, farm = _island(game, regime="planned")
    assert firms.state_crop_choices(island, game.defs, game.config, _turn(farm)) == []
    island, farm = _island(game)
    farm.economy[fac.FIELDS] = {"default": fac.FULL // 2, "cotton": fac.FULL // 2}
    assert firms.state_crop_choices(island, game.defs, game.config, _turn(farm)) == []


def test_private_farms_and_player_orders_are_not_touched(game):
    """民営の農場は今までの見直しのまま（国営の見直しには入らない）。"""
    island, farm = _island(game)
    farm.owner = OWNER_PRIVATE
    assert firms.state_crop_choices(island, game.defs, game.config, _turn(farm)) == []


def test_state_farm_keeps_the_staple_while_food_is_short(game):
    """食べ物が足りないあいだは、穀物を作る国営の農場を綿に替えない（主食の守り）。足りていれば、
    綿が品切れで採算が合うなら替える。"""
    island, farm = _island(game, recipe="default")
    add(island, "farm", 7, 7, recipe="default", owner=OWNER_PRIVATE)  # 穀物の農場はほかにもある
    finance = island.economy.finance
    finance["supply"] = {"grain": [90000] * 6, "cotton": [0] * 6}
    finance["demand"] = {"grain": [9000] * 6, "cotton": [5000] * 6}
    island.economy.price_history["grain"] = [island.economy.prices["grain"] // 4]
    island.economy.price_history["cotton"] = [island.economy.prices["cotton"] * 3]
    turn = _turn(farm)
    assert firms.state_crop_choices(island, game.defs, game.config, turn) == []
    island.stats["food.ratio"] = 1.0
    chosen = firms.state_crop_choices(island, game.defs, game.config, turn)
    assert [
        (f.id, "cotton" in game.defs.facilities[f.kind]["recipes"][r]["outputs"]) for f, r in chosen
    ] == [(farm.id, True)]
