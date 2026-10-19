"""小売りと店（バランス調整の回7。設計書 4 経済「小売り」・5 施設と企業「店」）。

家計は品物を、店の売り場を通して買います（電力と町の資材を除く。食べ物も）。

- 小売り: 品目 ``retail``（持ち越せず、島の外へ運べない。量は売り場の人1人の1ターンの
  働き）。家計は品物を買うと、代金を物価指数で割った額 ``retail.value_per_unit`` ごとに
  小売り1人月を買います。品物は工場から家計へ直接動き、小売りの代金が店の売上になります
  （現実の統計の数え方と同じく、小売業の産出は売買の差額）。家計は使う額のうち小売りの分を
  先に取り分け、品物の市場の後に小売りの市場で買います（modules/market）。
- 店: 商店（``shop``。町の中でマスを取らず、``ext["site"]`` に町のマス。町の段階ごとの数は
  settlements.yaml の shops）と百貨店（``department_store``。商業地のマス）。企業が採算で
  開き、退き、計画経済では国が建てます。
- お客の届く範囲: 家計の層は島全体で1つなので、店ごとに売れる小売りの量に「届くお客の
  割合」で上限を付けます（現実と違う形）。商店は通勤圏に住む人の割合、百貨店は車を持つ人
  （``retail.persons_per_car`` 人で1台）の割合に、車のない人のうち通勤圏に住む人の割合を
  足した割合。
- 店が足りないとき: 店で買いきれなかった小売りは、商業・サービス業（小さな店と屋台の
  集まり。economy/services.py）から、小売りの値段の ``1 + retail.direct_premium`` 倍で
  買います（直売・屋台）。その売上で商業・サービス業が人を増やし（自営の受け皿）、店が
  増えて小売りが足りれば縮みます。
- 出店の規制: 命令 ``set_retail_policy`` の large_store（none・permit・ban）。許可制では、
  企業が百貨店を建てたいときに申請を1件出し（``economy.finance["store_application"]``）、
  ``retail.permit_turns`` ターンのうちに命令で許せば建ちます。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import needs
from hakoniwa_core.geo import within
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.units import round_money, to_milli

Pos = tuple[int, int]
SITE = fac.SITE
APPLICATION = "store_application"  # economy.finance: 百貨店の出店の申請
NONE, PERMIT, BAN = "none", "permit", "ban"
LARGE_STORE = (NONE, PERMIT, BAN)
NEED = "retail_need"  # island.scratch: 層ごとの、このターンに買う小売りの量（1000 分の 1）
DEMAND = "retail_demand"  # economy.finance: 直近のターンの、家計の小売りの要る量（1000 分の 1）
DIRECT = "retail_direct"  # island.scratch: 店で買いきれず直売・屋台で買った量（1000 分の 1）と代金
SPENT = "retail_spent"  # island.scratch: 層ごとの、小売りを通して買った品物の代金
RESERVE = "retail_reserve"  # island.scratch: 層ごとの、小売りの代金に取り置いた額
PLANNED = "retail_planned"  # island.scratch: 層ごとの、このターンの使う額
PAID = "retail_paid"  # island.scratch: 層ごとの、このターンに小売りに払った額（税込み）
SHARE = "retail_share"  # 家計の層の状態: 使う額のうち小売りに取り分ける割合（1万分の1）
UNRETAILED = "retail_free"  # island.scratch: 小売りを通さない物（サービスと小売り）


def settings(config: GameConfig) -> dict[str, Any]:
    return config.get("retail")


def good_of(config: GameConfig) -> str:
    return settings(config)["good"]


def is_shop(defs: Defs, kind: str) -> bool:
    """町の中に置く店（マスを取らない）か。"""
    return defs.facilities[kind].get("site") == "town"


def sells_retail(defs: Defs, config: GameConfig, f: Facility) -> bool:
    return good_of(config) in fac.recipe_of(defs, f)["outputs"]


def cpi(island: IslandState) -> float:
    history = island.economy.cpi_history
    return history[-1] / 100 if history and history[-1] > 0 else 1.0


def units_for(island: IslandState, config: GameConfig, value: int) -> int:
    """品物の代金 value に見合う小売りの量（1000 分の 1）。"""
    if value <= 0:
        return 0
    return to_milli(value / cpi(island) / settings(config)["value_per_unit"])


def margin(island: IslandState, config: GameConfig, price: int, tax: int) -> int:
    """品物の代金 1 あたりの小売りの代金（1万分の1。消費税込み）。家計が使う額から先に取り分ける割合
    に使う。"""
    per = settings(config)["value_per_unit"] * cpi(island)
    return round_money(price * (10000 + tax) / per) if per > 0 else 0


def initial_price(config: GameConfig, wage: float) -> int:
    """小売り1単位の初めの値段（商店の1人あたりの量で、原価＋上乗せ）。"""
    markup = 1 + config.get("market.initial_prices.markup")
    return max(1, round_money(wage / 1.2 * markup + 0.5))


def prepare(island: IslandState, config: GameConfig) -> None:
    """小売りの値段がまだなければ、今の平均賃金から決める（移し替えの段と市場の段の初め）。"""
    good = good_of(config)
    if not island.has_economy or island.economy.prices.get(good):
        return
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    price = initial_price(config, wage)
    island.economy.prices[good] = price
    island.economy.price_history[good] = [price]


# --- 店の場所 ---------------------------------------------------------------------------


def position(f: Facility) -> Pos:
    return fac.position(f)


def located(f: Facility) -> bool:
    return fac.located(f)


def shops_at(island: IslandState, pos: Pos) -> int:
    return fac.shops_at(island, pos)


def town_slots(defs: Defs, town: Facility) -> int:
    """町のマスに入る商店の数（settlements.yaml の shops）。"""
    return fac.shop_slots(defs, town)


def free_towns(island: IslandState, defs: Defs, config: GameConfig) -> list[tuple[Pos, int]]:
    """商店の入る余地のある町のマスと、空いた数（人の多い順）。"""
    out = []
    for f in island.facilities:
        if not fac.is_housing(config, f) or not fac.on_map(f):
            continue
        free = town_slots(defs, f) - shops_at(island, (f.x, f.y))
        if free > 0:
            out.append(((f.x, f.y), free, island.tile(f.x, f.y).population))
    out.sort(key=lambda r: (-r[2], r[0][1], r[0][0]))
    return [(pos, free) for pos, free, _ in out]


def tileless_position(island: IslandState) -> Pos:
    """町の中の店の、ほかの施設と重ならない負の位置（サーバーは島の中で位置が重ならないように保存する）。"""
    used = {(f.x, f.y) for f in island.facilities}
    n = 10
    while (-n, -n) in used:
        n += 1
    return -n, -n


# --- お客の届く範囲 -----------------------------------------------------------------------


def car_share(island: IslandState, config: GameConfig) -> float:
    """自動車で百貨店に行ける人の割合（持っている台数 × 1台の人数 ÷ 人口。1まで）。"""
    people = sum(h["persons"] for h in island.economy.households.values())
    if people <= 0:
        return 0.0
    cars = sum(needs.cars_owned(island, layer) for layer in island.economy.households) / 1000
    return min(1.0, cars * settings(config)["persons_per_car"] / people)


def near_share(island: IslandState, config: GameConfig, pos: Pos) -> float:
    """pos の通勤圏に住む人の、島の人口に対する割合。"""
    total = sum(t.population for t in island.tiles)
    if total <= 0:
        return 0.0
    radius = config.get("labor.commute_radius")
    near = sum(island.tile(x, y).population for x, y in within(island, pos, radius))
    return min(1.0, near / total)


def reach(island: IslandState, defs: Defs, config: GameConfig, f: Facility) -> float:
    """店 f に届くお客の割合（0〜1）。"""
    near = near_share(island, config, position(f))
    if is_shop(defs, f.kind):
        return near
    cars = car_share(island, config)
    return min(1.0, cars + (1 - cars) * near)


# --- 出店の規制（3B） -----------------------------------------------------------------------


def is_large(defs: Defs, kind: str) -> bool:
    """出店の規制がかかる大きな店（商業地に建つ店）か。"""
    return defs.facilities[kind].get("zone") == "commercial"


def application(island: IslandState) -> dict[str, Any] | None:
    return island.economy.finance.get(APPLICATION)


def entry_block(
    island: IslandState, defs: Defs, config: GameConfig, kind: str, pos: Pos, turn: int
) -> str | None:
    """大きな店 kind を pos に建ててよいか。だめなら参入の建たないわけ（large_store_banned・
    large_store_waiting）。許可制で申請がなければ申請を出し、ログで知らせる。許された申請は使う。"""
    from hakoniwa_core.state import LogEntry

    if not is_large(defs, kind):
        return None
    policy = island.policy.large_store or NONE
    if policy == BAN:
        return "large_store_banned"
    if policy != PERMIT:
        return None
    found = application(island)
    if found is not None and found.get("approved"):
        island.economy.finance.pop(APPLICATION)
        return None
    if found is not None and turn - found["turn"] < settings(config)["permit_turns"]:
        return "large_store_waiting"
    island.economy.finance[APPLICATION] = {"turn": turn, "facility": kind, "x": pos[0], "y": pos[1]}
    island.log.append(LogEntry("store_application", {"facility": kind, "x": pos[0], "y": pos[1]}))
    return "large_store_waiting"
