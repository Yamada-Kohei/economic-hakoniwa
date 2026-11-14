"""土木業者の重機（W-270、設計書 08 の IN-34）。

土木業者は、市場で機械を買って重機にできます（``economy["machines"]``、1000 分の 1 台）。重機 1 台は
作業員 contractor.machines.workers 人分の工事を届け、1 ターンに contractor.machines.fuel の燃料を
使い、年 contractor.machines.wear の割合が傷んで使えなくなります。燃料が足りなければ、その分の重機は
動かず、その分は人手で（足りなければ直営で）埋めます。重機で替えるのは、受けた注文に要る人数の
contractor.machines.max_share までです。運送業のトラック（設計書 03 の MK-28、economy/trucks.py）と
同じ形です。

買うかどうかは、1 台を 1 ターン使う費用（値段 ×（設備の利子 ＋ 傷み）÷ 12 ＋ 燃料）と、替えられる
作業員の賃金を比べて決めます。安ければ、欲しい台数（要る人数 × max_share ÷ workers）との差の
contractor.machines.catch_up の割合と、傷む分を買います。燃料は、持っている重機の
contractor.machines.fuel_turns ターン分を持っておきます。

市場で買った機械は、雇用の段で在庫から重機に移して使い始めます（設備の額に足し、減価償却で費用に
する。GDP では買ったときの在庫の増えを設備への投資として残す）。重機は業者の全力にも足します。
"""

from __future__ import annotations

from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.dates import TURNS_PER_YEAR as YEAR
from hakoniwa_core.economy import books
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.units import QUANTITY_SCALE, money_of, round_money, to_milli

MILLI = QUANTITY_SCALE
MACHINES = "machines"  # 業者の economy: 持っている重機（1000 分の 1 台）
WORKING = "machines_working"  # 業者の economy: このターンに動いた重機（1000 分の 1 台）
REPLACED = "machines_replaced"  # 業者の economy: このターンに重機で替えた作業員（人）
UNIT_COST = "machine_cost"  # 業者の economy: 重機の変動費（作業員 1 人・ターン分に直した額）


def settings(config: GameConfig) -> dict:
    return config.get("contractor.machines")


def owned(f: Facility) -> int:
    return f.economy.get(MACHINES, 0)


def replaced(f: Facility) -> int:
    """このターンに重機で替えた作業員（人）。"""
    return f.economy.get(REPLACED, 0)


def persons(config: GameConfig, machines: int) -> int:
    """重機 machines（1000 分の 1 台）が替える作業員の人数。"""
    return machines * settings(config)["workers"] // MILLI


def commission(island: IslandState, config: GameConfig, f: Facility) -> int:
    """傷んだ重機を除き、在庫の機械（市場で買った分）を重機に移す。移した量を返す。"""
    s = settings(config)
    have = owned(f)
    have -= round_money(have * s["wear"] / YEAR + 0.5)
    good = s["good"]
    qty = stock.total(f.stock, good)
    if qty > 0:
        stock.take(f.stock, good, qty)
        value = money_of(island.economy.prices.get(good, 0), qty)
        f.economy["capital"] = f.economy.get("capital", 0) + value
        have += qty
    if have > 0:
        f.economy[MACHINES] = max(0, have)
    else:
        f.economy.pop(MACHINES, None)
    return max(0, qty)


def _fuel_machines(config: GameConfig, f: Facility) -> int:
    """手元の燃料で 1 ターン動かせる重機（1000 分の 1 台）。燃料の財が複数あれば合わせる。"""
    total = 0
    for good, per in settings(config)["fuel"].items():
        need = to_milli(per)
        if need > 0:
            total += stock.total(f.stock, good) * MILLI // need
        else:
            return owned(f)
    return total


def drive(island: IslandState, config: GameConfig, f: Facility, crew: int) -> int:
    """要る人数 crew 人のうち、重機で替える人数を決め、動かす重機の燃料を使う。替えた人数を返し、
    動いた重機と替えた人数を economy に残す。"""
    s = settings(config)
    per = s["workers"]
    working = 0
    if crew > 0 and per > 0 and owned(f) > 0:
        room = round_money(crew * s["max_share"] * MILLI / per)
        working = min(owned(f), room, _fuel_machines(config, f))
    if working <= 0:
        for key in (WORKING, REPLACED, UNIT_COST):
            f.economy.pop(key, None)
        return 0
    left = working  # まだ燃料を使っていない重機
    for good, use in s["fuel"].items():
        need = round_money(left * use + 0.999)
        if need <= 0:
            continue
        used = stock.take(f.stock, good, min(need, stock.total(f.stock, good)))
        books.stock_moved(island, f.id, good, -used)
        left -= used * MILLI // max(1, to_milli(use))
        if left <= 0:
            break
    f.economy[WORKING] = working
    f.economy[REPLACED] = persons(config, working)
    f.economy[UNIT_COST] = round_money(running_cost(island, config) / max(1, per) + 0.999)
    return f.economy[REPLACED]


def turn_cost(island: IslandState, config: GameConfig) -> float:
    """重機 1 台を 1 ターン使う費用（設備の利子と傷み、燃料。今の値段で）。"""
    s = settings(config)
    prices = island.economy.prices
    rate = config.get("market.initial_prices.capital_rate") + s["wear"]
    fuel = sum(prices.get(g, 0) * q for g, q in s["fuel"].items())
    return prices.get(s["good"], 0) * rate / YEAR + fuel


def running_cost(island: IslandState, config: GameConfig) -> float:
    """重機 1 台を 1 ターン動かす変動費（燃料と傷み。持っているだけでかかる利子は入れない）。"""
    s = settings(config)
    prices = island.economy.prices
    fuel = sum(prices.get(g, 0) * q for g, q in s["fuel"].items())
    return prices.get(s["good"], 0) * s["wear"] / YEAR + fuel


def pays(island: IslandState, config: GameConfig, wage: int) -> bool:
    """重機が替える作業員の賃金より安く使えるか。"""
    s = settings(config)
    if island.economy.prices.get(s["good"], 0) <= 0:
        return False
    return turn_cost(island, config) < s["workers"] * wage


def fuel_limit(island: IslandState, config: GameConfig, f: Facility, good: str) -> int:
    """重機の燃料 good を買う値段の上限（1 単位あたり）: 重機 1 台を 1 ターン使う費用が、替える
    作業員の賃金に届く値段。これより高い燃料を買うより、人を雇う方が安い。"""
    s = settings(config)
    per = s["fuel"].get(good, 0)
    if per <= 0:
        return 0
    rate = config.get("market.initial_prices.capital_rate") + s["wear"]
    machine = island.economy.prices.get(s["good"], 0) * rate / YEAR
    room = s["workers"] * f.economy.get("wage", 0) - machine
    return max(0, int(room / per))


def orders(island: IslandState, config: GameConfig, f: Facility, crew: int) -> dict[str, int]:
    """業者が市場で買いたい機械と燃料（1000 分の 1）。crew は受けた注文に
    要る人数（重機の分を含む）。"""
    s = settings(config)
    out: dict[str, int] = {}
    wage = f.economy.get("wage", 0)
    have = owned(f)
    if wage > 0 and pays(island, config, wage):
        desired = round_money(crew * s["max_share"] * MILLI / s["workers"])
        gap = max(0, desired - have)
        wear = round_money(have * s["wear"] / YEAR + 0.5)
        want = round_money(gap * s["catch_up"] + 0.999) + wear if gap > 0 else 0
        want = min(want, gap + wear) - stock.total(f.stock, s["good"])
        if want > 0:
            out[s["good"]] = want
    if have > 0:
        # 燃料: 持っている重機の fuel_turns ターン分に足りない分を、安い財で
        prices = island.economy.prices
        fuel = s["fuel"]
        good = min(fuel, key=lambda g: (prices.get(g, 0) * fuel[g], list(fuel).index(g)))
        short = have * s["fuel_turns"] - _fuel_machines(config, f)
        want = round_money(short * fuel[good] + 0.999)
        if want > 0:
            out[good] = want
    return out
