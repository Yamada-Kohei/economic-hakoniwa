"""運送業のトラック（フェーズ3-2）。

運送業（島に1つ、マスを持たない）は、市場で自動車を買ってトラックにできます（``economy["trucks"]``、
1000 分の 1 台）。トラック1台は運転手 freight.trucks.drivers 人分の働きをし、1ターンに
freight.trucks.fuel の燃料を使い、年 freight.trucks.wear の割合が傷んで使えなくなります。燃料が
足りなければ、その分のトラックは動きません。トラックで替えるのは、運送業が雇いたい人数（運賃の
収入 × freight.labor_share ÷ 賃金）の freight.trucks.max_share までです。

買うかどうかは、1台を1ターン使う費用（値段 ×（設備の利子 + 傷み）÷ 12 + 燃料）と、替えられる運転手の
賃金を比べて決めます。安ければ、欲しい台数（雇いたい人数 × max_share ÷ drivers）との差の
freight.trucks.catch_up の割合と、傷む分を買います。燃料は、持っているトラックの
freight.trucks.fuel_turns ターン分を持っておきます。

市場で買った自動車は、雇用の段で在庫からトラックに移して使い始めます（設備の額に足し、減価償却で
費用にする。輸送船と同じく、GDP では買ったときの在庫の増えを設備への投資として残す）。
"""

from __future__ import annotations

from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.economy import books
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.units import QUANTITY_SCALE, money_of, round_money, to_milli

MILLI = QUANTITY_SCALE
YEAR = 12
TRUCKS = "trucks"  # 運送業の economy: 持っているトラック（1000 分の 1 台）
WORKING = "trucks_working"  # 運送業の economy: このターンに動いたトラック（1000 分の 1 台）


def _settings(config: GameConfig) -> dict:
    return config.get("freight.trucks")


def owned(f: Facility) -> int:
    return f.economy.get(TRUCKS, 0)


def commission(island: IslandState, config: GameConfig, f: Facility) -> int:
    """傷んだトラックを除き、在庫の自動車（市場で買った分）をトラックに移す。移した量を返す。"""
    settings = _settings(config)
    have = owned(f)
    have -= round_money(have * settings["wear"] / YEAR + 0.5)
    good = settings["good"]
    qty = stock.total(f.stock, good)
    if qty > 0:
        stock.take(f.stock, good, qty)
        value = money_of(island.economy.prices.get(good, 0), qty)
        f.economy["capital"] = f.economy.get("capital", 0) + value
        have += qty
    if have > 0:
        f.economy[TRUCKS] = max(0, have)
    else:
        f.economy.pop(TRUCKS, None)
    return max(0, qty)


def _fuel_trucks(config: GameConfig, f: Facility) -> int:
    """手元の燃料で1ターン動かせるトラック（1000 分の 1 台）。燃料の財が複数あれば合わせる。"""
    total = 0
    for good, per_truck in _settings(config)["fuel"].items():
        need = to_milli(per_truck)
        if need > 0:
            total += stock.total(f.stock, good) * MILLI // need
        else:
            return owned(f)
    return total


def drive(island: IslandState, config: GameConfig, f: Facility, drivers: int) -> int:
    """雇いたい運転手 drivers 人のうち、トラックで替える人数を決め、動かすトラックの燃料を使う。

    替えた人数を返し、動いたトラックを economy["trucks_working"] に残す。
    """
    settings = _settings(config)
    per_truck = settings["drivers"]
    if drivers <= 0 or per_truck <= 0 or owned(f) <= 0:
        f.economy.pop(WORKING, None)
        return 0
    room = round_money(drivers * settings["max_share"] * MILLI / per_truck)
    working = min(owned(f), room, _fuel_trucks(config, f))
    if working <= 0:
        f.economy.pop(WORKING, None)
        return 0
    left = working  # まだ燃料を使っていないトラック
    for good, per in settings["fuel"].items():
        need = round_money(left * per + 0.999)
        if need <= 0:
            continue
        used = stock.take(f.stock, good, min(need, stock.total(f.stock, good)))
        books.stock_moved(island, f.id, good, -used)
        left -= used * MILLI // max(1, to_milli(per))
        if left <= 0:
            break
    f.economy[WORKING] = working
    return working * per_truck // MILLI


def turn_cost(island: IslandState, config: GameConfig) -> float:
    """トラック1台を1ターン使う費用（設備の利子と傷み、燃料。今の値段で）。"""
    settings = _settings(config)
    prices = island.economy.prices
    rate = config.get("market.initial_prices.capital_rate") + settings["wear"]
    fuel = sum(prices.get(g, 0) * q for g, q in settings["fuel"].items())
    return prices.get(settings["good"], 0) * rate / YEAR + fuel


def pays(island: IslandState, config: GameConfig, wage: int) -> bool:
    """トラックが替える運転手の賃金より安く使えるか。"""
    good = _settings(config)["good"]
    if island.economy.prices.get(good, 0) <= 0:
        return False
    return turn_cost(island, config) < _settings(config)["drivers"] * wage


def fuel_limit(island: IslandState, config: GameConfig, f: Facility, good: str) -> int:
    """トラックの燃料 good を買う値段の上限（1 単位あたり）: トラック1台を1ターン使う費用が、替える
    運転手の賃金に届く値段。これより高い燃料を買うより、運転手を雇う方が安い。"""
    settings = _settings(config)
    per_truck = settings["fuel"].get(good, 0)
    if per_truck <= 0:
        return 0
    rate = config.get("market.initial_prices.capital_rate") + settings["wear"]
    car = island.economy.prices.get(settings["good"], 0) * rate / YEAR
    room = settings["drivers"] * f.economy.get("wage", 0) - car
    return max(0, int(room / per_truck))


def orders(island: IslandState, config: GameConfig, f: Facility) -> dict[str, int]:
    """運送業が市場で買いたい自動車と燃料（1000 分の 1）。"""
    settings = _settings(config)
    out: dict[str, int] = {}
    wage = f.economy.get("wage", 0)
    have = owned(f)
    if wage > 0 and pays(island, config, wage):
        drivers = f.economy.get("wanted", 0) + f.economy.get("drivers_replaced", 0)
        desired = round_money(drivers * settings["max_share"] * MILLI / settings["drivers"])
        gap = max(0, desired - have)
        wear = round_money(have * settings["wear"] / YEAR + 0.5)
        want = round_money(gap * settings["catch_up"] + 0.999) + wear if gap > 0 else 0
        want = min(want, gap + wear) - stock.total(f.stock, settings["good"])
        if want > 0:
            out[settings["good"]] = want
    if have > 0:
        # 燃料: 持っているトラックの fuel_turns ターン分に足りない分を、安い財で
        prices = island.economy.prices
        fuel = settings["fuel"]
        good = min(fuel, key=lambda g: (prices.get(g, 0) * fuel[g], list(fuel).index(g)))
        short = have * settings["fuel_turns"] - _fuel_trucks(config, f)
        want = round_money(short * fuel[good] + 0.999)
        if want > 0:
            out[good] = want
    return out
