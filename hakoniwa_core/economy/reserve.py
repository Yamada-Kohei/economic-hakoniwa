"""国の穀物の備蓄（W-186 の直し 回1）。飢饉の備えを、余分の農場でなく国の倉庫の穀物で持ちます。

現実の国の備蓄（日本の米は年の消費の約1.5か月分を持ち、古い物から入れ替える。中国やインドの穀物の
備蓄は、値段が上がると放出し、下がると買い入れる）に倣い、game.yaml の food_reserve で次のように
します。

- 目安: 島の住民の食べる量（population.food_per_person）の turns ターン分（穀物の量に直す）。建国の
  とき、この量を国の倉庫に入れておく（stock_up。founding.py の place_start）。昔から少しずつ積んで
  きた物なので、作られたターンを日持ちの間にならしておく（同じターンの組にすると、建国の約 20
  ターン後に全部が一度に入れ替えの時期になり、農場の売上を奪って値崩れさせる）。
- 放出: 前のターンに飢えた家計があったか、値段がいつもの値段（economy/prices.py の usual_price）の
  (1 + release) 倍以上になったら、市場に売りに出す。指値は、飢えた家計があればいつもの値段（計画
  経済の公定価格や値段の上限の下でも約定する）、なければ (1 + release) 倍。ふだんは売りに出さない
  （出しておくと、需要が生産を上回っても市場の売りが足りて見え、農場が建たない）。
- 入れ替え: 日持ち（goods.yaml の shelf_life）の残りが rotate ターン以下になった組は、傷む前に今の
  値段を指値に売る。
- 買い足し: 放出しないターンに、目安に足りない分の refill の割合と、入れ替えで売りに出した分を、
  いつもの値段を指値に、国の倉庫の買い入れの予算（budget.warehouse）から買う。契約の輸入や命令で
  倉庫が穀物を売りに出しているターンは買わない（自分の売りを買わない）。予算が 0 なら買えない
  （遊ぶ人が予算で止められる）。

売り買いは市場の段の国の倉庫の注文（modules/market の _warehouse_orders）として出し、代金は国庫に
入る・国庫から出る。結果はログに出さない（毎ターン出すため）。
"""

from __future__ import annotations

from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.state import IslandState
from hakoniwa_core.units import to_milli


def target(island: IslandState, defs: Defs, config: GameConfig) -> int:
    """備蓄の目安の量（1000 分の 1 単位）: 住民の食べる量の food_reserve.turns ターン分。"""
    spec = config.get("food_reserve")
    if spec["turns"] <= 0:
        return 0
    population = sum(t.population for t in island.tiles)
    nutrition = defs.goods[spec["good"]]["food"]["nutrition"]
    return to_milli(
        population * config.get("population.food_per_person") * spec["turns"] / nutrition
    )


def rotating(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> int:
    """倉庫の備蓄のうち、日持ちの残りが food_reserve.rotate ターン以下の量（入れ替えで売る量）。"""
    spec = config.get("food_reserve")
    life = defs.goods[spec["good"]].get("shelf_life")
    if life is None:
        return 0
    return sum(
        qty
        for made, qty in island.warehouse.get(spec["good"], ())
        if turn - made >= life - spec["rotate"]
    )


def stock_up(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> None:
    """建国のとき、目安の量を国の倉庫に入れる。作られたターンは、入れ替えまでのターン数（日持ち −
    rotate）にならす。"""
    spec = config.get("food_reserve")
    good, total = spec["good"], target(island, defs, config)
    life = defs.goods[good].get("shelf_life")
    ages = max(1, life - spec["rotate"]) if life is not None else 1
    for age in range(ages):
        qty = total // ages + (total % ages if age == 0 else 0)
        stock.add(island.warehouse, good, qty, turn - age, defs.goods)
