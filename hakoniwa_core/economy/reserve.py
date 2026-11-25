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
- 先行放出（下書き第 2 版。food_reserve.price_lead、既定は切）: 入のときは、値段の上がり方
  だけでも先に出す（price_lead 関数。基準は直近の値段の移動中央値で、倍率の線か上がりの
  速さの線のどちらか）。第 1 版は全量を一度に出し、倉庫が先に細って後半の跳ね止めが弱く
  なったため、第 2 版では出す量を「値段を基準に戻すのに足りる分」（前のターンの市場の記録の
  買えなかった量を price/base で割り増した見積もり）までにし、1 ターンの割合上限と倉庫の
  下限（住民の食べる量の floor_turns ターン分。先行放出だけに適用し、飢えの線の放出は
  今までどおり全量で下限も使う）を同時に効かせる（price_lead_amount 関数）。飢えの線より
  早く出すことで、飢えが出る前に値段の上がりを抑える狙い。切の間は今までと完全に同じ動き。
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

from statistics import median

from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import standing
from hakoniwa_core.state import IslandState
from hakoniwa_core.units import scale_price, to_milli


def target(island: IslandState, defs: Defs, config: GameConfig) -> int:
    """備蓄の目安の量（1000 分の 1 単位）: 住民の食べる量の food_reserve.turns ターン分。"""
    spec = config.get("food_reserve")
    item = standing.settings(island).get(spec["good"], {})
    if item.get("kind") == "reserve":
        return item["quantity"]
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


def price_lead(island: IslandState, config: GameConfig, price: int) -> tuple[str, int] | None:
    """値段の上がりで先に放出するか（下書き。game.yaml の food_reserve.price_lead、既定は切）。

    飢えの線と (1 + release) の線より先に、値段の上がり方だけで放出する線。判定に使う値段は、
    注文を出す時点で決まっている直近の約定値（前のターンの値段。_reserve_orders の price）で、
    今のターンの値段そのものはまだ無い。基準は直近 window ターンの値段の移動中央値（平均では
    なく中央値: 上がったターン自体が基準を押し上げ、線が遠のくことを防ぐ）。倍率の線
    （price が中央値の multiple 倍以上）か、上がりの速さの線（rise_turns ターン前からの
    上がりが rise 以上）のどちらかで発火する。戻り値は (わけ, 基準の値段) で、発火しなければ
    None。切の間は enabled を見てすぐ None を返すので、今までの動きは変わらない。"""
    spec = config.get("food_reserve")
    lead = spec.get("price_lead") or {}
    if not lead.get("enabled"):
        return None
    history = island.economy.price_history.get(spec["good"], [])
    window = int(lead.get("window", 12))
    if price <= 0 or len(history) < 3:
        return None
    base = round(median(history[-window:]))
    if base > 0 and price >= scale_price(base, float(lead.get("multiple", 1.15))):
        return "multiple", base
    rise_turns = int(lead.get("rise_turns", 3))
    if len(history) > rise_turns:
        before = history[-1 - rise_turns]
        if before > 0 and price >= scale_price(before, 1 + float(lead.get("rise", 0.10))):
            return "rise", base
    return None


# island.economy.ext の置き場: 前のターンの穀物の買えなかった量（demand − volume）。
# 市場の記録（island.markets）はターンの初めに消える（turn/engine）ので、市場の段の
# 終わり（modules/market の _record）が、先行放出が入のときだけここに残す。
LAST_UNMET = "food_reserve_last_unmet"


def price_lead_amount(
    island: IslandState, defs: Defs, config: GameConfig, base: int, price: int
) -> tuple[int, dict]:
    """先行放出の量（下書き第 2 版）: 「値段を基準に戻すのに足りる分」を上限に、1 ターンの
    割合上限と倉庫の下限とを同時に効かせた小出しの量。

    足りる分の見積もりは、前のターンの市場の記録の買えなかった量（demand − volume。
    記録そのものはターンの初めに消えるので、_record が economy.ext の LAST_UNMET に
    残した値を読む）。値段を押し上げているのはこの不足分で、記録の demand は今の値段での
    買いたい量なので、基準の値段で買えた量に直すため price/base を掛けて割り増す（同じ
    予算で買える量は値段に反比例する）。これは見積もりであり、値段は 1 ターンに市場の
    値幅（market.band）までしか動かないので、1 回で基準まで戻すのではなく、線が続く間は
    毎ターン小出しにして近づける形にする。残した値がなければ見積もりは立たないので、
    割合上限と下限だけで決める。
    割合上限（max_fraction）は 1 ターンに倉庫の量のこの割合まで。下限（floor_turns）は
    住民の食べる量のこのターン分で、先行放出では残す（目安 target が food_reserve.turns
    ターン分なので、下限 = 目安 × floor_turns / turns）。飢えの線の放出は今までどおり
    全量で下限も使うので、この関数は先行放出だけに使う。
    戻り値は (量, 内訳)。内訳は need（見積もり。残した値がなければ None）・floor（下限の
    量）・cap（どの上限で決まったか: need / fraction / floor）。量が 0 なら出さない。"""
    spec = config.get("food_reserve")
    lead = spec.get("price_lead") or {}
    good = spec["good"]
    total = stock.total(island.warehouse, good)
    turns = int(spec.get("turns", 0))
    floor_turns = int(lead.get("floor_turns", 1))
    floor = target(island, defs, config) * floor_turns // turns if turns > 0 else 0
    room = max(0, total - floor)
    fraction = int(total * float(lead.get("max_fraction", 0.25)))
    need: int | None = None
    unmet = island.economy.ext.get(LAST_UNMET)
    if unmet is not None:
        need = unmet * price // base if base > 0 else unmet
    qty, cap = room, "floor"
    if fraction < qty:
        qty, cap = fraction, "fraction"
    if need is not None and need < qty:
        qty, cap = need, "need"
    return max(0, qty), {"need": need, "floor": floor, "cap": cap}


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
