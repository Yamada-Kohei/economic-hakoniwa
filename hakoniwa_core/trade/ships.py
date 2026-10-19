"""港と海運業（フェーズ3-1）。

海運業（島に1つ、マスを持たない）は輸送船を設備として持ちます（``economy["fleet"]``: [使い始めた
ターン, 1000 分の 1 隻] の組）。1 隻で 1 航海に trade.ship_capacity トン運べ、航海に出ている
船の数に合わせて船員（trade.ship_crew）を雇います（港につないでいる船は少しの船員で守る）。船は
trade.ship_life ターン使ったら解体します。市場で買った船（造船所の船や輸入した船）は、在庫から
船団に移して使い始めます。

荷は買う側の島の船で運びます。船は空で売り手の港へ行き、積んで戻るので、往復の間（片道の 2 倍）は
ほかに使えません（``economy["busy"]``: 荷の番号 → 使っている重さ）。船出のときに、往復の分の燃料
（trade.ship_fuel。1隻1ターンあたり）を使います。燃料が足りなければ、その分だけ運べる重さが減ります。
trade.ship_fuel に財が複数あれば、どれか1つで動きます（フェーズ3-2 から燃料と石炭）。使うときも買う
ときも、今の値段で1隻1ターンあたりの費用が安い財から先にします。

運賃は「荷 1 トン × 区画 1 つ」あたりで、直近 trade.cost_turns ターンの費用（船員の賃金・燃料・船の
傷み）÷ 運んだ量（トン × 区画）×（1 + trade.cost_markup）です。運んだ量は、船を半分使ったときの量を
下限にして数えます（荷の少ない間に運賃が跳ね上がらないように）。

港は荷の積み下ろしを 1 ターンに trade.port_capacity トンまで扱い、直近の扱った量の平均 ×
trade.port_labor 人を雇います。使用料は荷 1 トンあたり
「trade.port_labor × 平均賃金 ×（1 + trade.cost_markup）」です。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import books
from hakoniwa_core.state import Facility, IslandState, LogEntry
from hakoniwa_core.trade import port_of, shipping_of
from hakoniwa_core.units import QUANTITY_SCALE, from_milli, money_of, round_money, to_milli

MILLI = QUANTITY_SCALE


# --- 船団 --------------------------------------------------------------------------


def fleet(f: Facility) -> int:
    """船の数（1000 分の 1 隻）。"""
    return sum(q for _, q in f.economy.get("fleet", []))


def add_ships(f: Facility, qty: int, turn: int) -> None:
    if qty > 0:
        f.economy.setdefault("fleet", []).append([turn, qty])


def capacity(config: GameConfig, f: Facility) -> int:
    """船団が1航海で運べる重さ（1000 分の 1 トン）。"""
    return fleet(f) * config.get("trade.ship_capacity")


def busy(f: Facility) -> int:
    """航海に出ている船の重さ（1000 分の 1 トン）。"""
    return sum(f.economy.get("busy", {}).values())


def free(config: GameConfig, f: Facility) -> int:
    return max(0, capacity(config, f) - busy(f))


def occupy(f: Facility, shipment: int, load: int) -> None:
    if load > 0:
        table = f.economy.setdefault("busy", {})
        table[str(shipment)] = table.get(str(shipment), 0) + load


def release(f: Facility, shipment: int) -> None:
    f.economy.get("busy", {}).pop(str(shipment), None)


def ships_wanted(config: GameConfig, f: Facility, turn: int) -> int:
    """海運業が市場で買う輸送船（1000 分の 1 隻。買って船団に入れる前の船があれば、その分を引く）。

    - 直近 trade.cost_turns ターンの半分以上で、船が足りずに輸入を減らしていたら 1 隻。
    - 先回りの買い替え（回6。Codex E31）: 寿命まで trade.ship_order_turns ターン以内の船があれば、
      直近 trade.cost_turns ターンに一番多く航海に出ていた船の数から、寿命の来ない船の数を引いた
      分を、寿命の来る船の数まで買う（使っていない船は替えない）。
    """
    want = 0
    history = f.economy.get("ships_short", [])
    turns = config.get("trade.cost_turns")
    if len(history) >= turns and sum(history) * 2 >= len(history):
        want = MILLI
    life = config.get("trade.ship_life")
    ahead = config.get("trade.ship_order_turns")
    retiring = sum(q for since, q in f.economy.get("fleet", []) if turn + ahead - since >= life)
    if retiring > 0:
        used = ships_of_load(config, max(f.economy.get("used", []), default=0))
        want = max(want, min(retiring, used - (fleet(f) - retiring)))
    have = stock.total(f.stock, config.get("trade.ship_good"))
    return max(0, want - have)


def ships_of_load(config: GameConfig, load: int) -> int:
    """重さ load を運ぶ船の数（1000 分の 1 隻）。"""
    return load // max(1, config.get("trade.ship_capacity"))


def commission(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> int:
    """海運業の在庫にある輸送船（市場で買った船）を船団に移す。移した量を返す。"""
    f = shipping_of(island, config)
    if f is None:
        return 0
    good = config.get("trade.ship_good")
    qty = stock.total(f.stock, good)
    if qty <= 0:
        return 0
    stock.take(f.stock, good, qty)
    # 船は設備（設備の額に足し、減価償却で費用にする）。GDP では買ったときの在庫の増えを、そのまま
    # 設備への投資として残す（在庫の減りに数えない）
    value = money_of(island.economy.prices.get(good, 0), qty)
    f.economy["capital"] = f.economy.get("capital", 0) + value
    add_ships(f, qty, turn)
    island.log.append(LogEntry("ships_commissioned", {"count": from_milli(qty)}))
    return qty


def scrap(island: IslandState, config: GameConfig, turn: int) -> int:
    """使えるターン数を過ぎた船を解体する。解体した量を返す。"""
    f = shipping_of(island, config)
    if f is None:
        return 0
    life = config.get("trade.ship_life")
    keep, gone = [], 0
    for since, qty in f.economy.get("fleet", []):
        if turn - since >= life:
            gone += qty
        else:
            keep.append([since, qty])
    if gone:
        f.economy["fleet"] = keep
        island.log.append(LogEntry("ships_scrapped", {"count": from_milli(gone)}))
    return gone


# --- 燃料 --------------------------------------------------------------------------


def fuel_per_turn(config: GameConfig, load: int) -> dict[str, int]:
    """重さ load の荷を運ぶ船が1ターンに使う燃料（1000 分の 1 単位）。財が複数あれば、どれか1つ
    の量。"""
    ships = load / (config.get("trade.ship_capacity") * MILLI)
    return {
        g: round_money(to_milli(q) * ships + 0.999)
        for g, q in config.get("trade.ship_fuel").items()
    }


def fuel_order(config: GameConfig, prices: dict[str, int]) -> list[str]:
    """燃料の財を、1隻1ターンあたりの費用が安い順に（同じなら定義の順）。"""
    settings = config.get("trade.ship_fuel")
    names = list(settings)
    return sorted(names, key=lambda g: (prices.get(g, 0) * settings[g], names.index(g)))


def _ship_turns(config: GameConfig, f: Facility) -> float:
    """手元の燃料で動ける「船 1 隻 × 1 ターン」の数。"""
    total = 0.0
    for good, per_ship in config.get("trade.ship_fuel").items():
        need = to_milli(per_ship)
        if need > 0:
            total += stock.total(f.stock, good) / need
    return total


def fuel_room(config: GameConfig, f: Facility, turns: int) -> int:
    """手元の燃料で、turns ターンの航海に出せる重さ（1000 分の 1 トン）。"""
    if not any(to_milli(q) > 0 for q in config.get("trade.ship_fuel").values()) or turns <= 0:
        return capacity(config, f)
    return int(_ship_turns(config, f) / turns * config.get("trade.ship_capacity") * MILLI)


def burn(island: IslandState, config: GameConfig, f: Facility, load: int, turns: int) -> int:
    """船出のときに往復の燃料を使う（安い財から）。使った燃料の値打ち（今の値段）を返す。"""
    prices = island.economy.prices
    per_load = fuel_per_turn(config, load)
    left = 1.0  # まだ燃料を使っていない割合
    value = 0
    for good in fuel_order(config, prices):
        need = per_load[good] * turns
        if need <= 0 or left <= 0:
            continue
        want = round_money(need * left + 0.999)
        used = stock.take(f.stock, good, want)
        books.stock_moved(island, f.id, good, -used)
        value += money_of(prices.get(good, 0), used)
        left -= used / need
    return value


def fuel_wanted(island: IslandState, config: GameConfig, f: Facility) -> dict[str, int]:
    """海運業が市場で買う燃料: 船を全部出したときの trade.fuel_turns ターン分に足りない分を、今の
    値段で一番安い燃料で。"""
    turns = config.get("trade.fuel_turns")
    settings = config.get("trade.ship_fuel")
    ships = fleet(f) / MILLI
    short = ships * turns - _ship_turns(config, f)
    if short <= 0:
        return {}
    good = fuel_order(config, island.economy.prices)[0]
    want = round_money(to_milli(settings[good]) * short + 0.999)
    return {good: want} if want > 0 else {}


# --- 運賃と使用料 --------------------------------------------------------------------


def freight_rate(island: IslandState, config: GameConfig, f: Facility | None = None) -> int:
    """船の運賃（荷 1 トン × 区画 1 つあたり。自国通貨）。"""
    f = f or shipping_of(island, config)
    if f is None:
        return 0
    if "rate" in f.economy:
        return f.economy["rate"]
    return _estimate(island, config, f)


def _estimate(island: IslandState, config: GameConfig, f: Facility) -> int:
    """1 隻を半分使ったときの費用から見積もった運賃。"""
    cost = _ship_turn_cost(island, config)
    per_ship = config.get("trade.ship_capacity") * config.get("trade.ship_speed") / 4  # トン×区画
    return max(1, round_money(cost / per_ship * (1 + config.get("trade.cost_markup"))))


def _ship_turn_cost(island: IslandState, config: GameConfig) -> float:
    """船 1 隻を1ターン使う費用（船員の賃金・燃料・船の傷み）。"""
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    prices = island.economy.prices
    settings = config.get("trade.ship_fuel")
    fuel = min((prices.get(g, 0) * q for g, q in settings.items()), default=0)
    wear = prices.get(config.get("trade.ship_good"), 0) / config.get("trade.ship_life")
    return config.get("trade.ship_crew") * wage + fuel + wear


def close_turn(island: IslandState, config: GameConfig, carried: int, fuel_value: int) -> None:
    """船の運賃を見直す。carried はこのターンに運び出した量（トン × 区画。1000 分の 1）。"""
    f = shipping_of(island, config)
    if f is None:
        return
    keep = config.get("trade.cost_turns")
    wage_bill = f.economy.get("wage", 0) * f.workers
    wear = round_money(
        island.economy.prices.get(config.get("trade.ship_good"), 0)
        * from_milli(fleet(f))
        / config.get("trade.ship_life")
    )
    costs = [*f.economy.get("costs", []), wage_bill + fuel_value + wear][-keep:]
    f.economy["used"] = [*f.economy.get("used", []), busy(f)][-keep:]
    loads = [*f.economy.get("carried", []), carried][-keep:]
    f.economy["costs"] = costs
    f.economy["carried"] = loads
    # 運んだ量も下限も 1000 分の 1（トン × 区画）で数え、運賃は 1 トン × 区画あたりにする
    per_turn = config.get("trade.ship_capacity") * config.get("trade.ship_speed") / 4
    floor = fleet(f) * per_turn * len(loads)
    total = max(sum(loads), floor)
    if total <= 0:
        f.economy["rate"] = _estimate(island, config, f)
        return
    rate = sum(costs) * QUANTITY_SCALE / total * (1 + config.get("trade.cost_markup"))
    f.economy["rate"] = max(1, round_money(rate))


def port_fee(island: IslandState, config: GameConfig) -> int:
    """港の使用料（荷 1 トンあたり。自国通貨）。"""
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    return round_money(
        config.get("trade.port_labor") * wage * (1 + config.get("trade.cost_markup"))
    )


def fee_for(island: IslandState, config: GameConfig, load: int) -> int:
    """重さ load（1000 分の 1 トン）の荷の使用料。"""
    return port_fee(island, config) * load // MILLI


def port_room(island: IslandState, config: GameConfig, scratch: dict[str, Any]) -> int:
    """港でこのターンにまだ積み下ろしできる重さ（1000 分の 1 トン）。港がなければ 0。"""
    if port_of(island, config) is None:
        return 0
    used = scratch.get("port_used", {}).get(island.id, 0)
    return max(0, config.get("trade.port_capacity") * MILLI - used)


def use_port(island: IslandState, scratch: dict[str, Any], load: int) -> None:
    table = scratch.setdefault("port_used", {})
    table[island.id] = table.get(island.id, 0) + load


def close_port(island: IslandState, config: GameConfig, handled: int) -> None:
    """港の扱った量（1000 分の 1 トン）の記録を足す（働く人の数をここから決める）。"""
    f = port_of(island, config)
    if f is None:
        return
    keep = config.get("trade.cost_turns")
    f.economy["handled"] = [*f.economy.get("handled", []), handled][-keep:]


def port_workers(config: GameConfig, defs: Defs, f: Facility) -> int:
    """港が雇いたい人数: 直近の扱った量の平均（トン）× trade.port_labor（定義の人数まで、
    少なくともその 2%）。"""
    handled = f.economy.get("handled", [])
    average = sum(handled) / len(handled) / MILLI if handled else 0.0
    full = defs.facilities[f.kind]["workers"]
    want = round_money(average * config.get("trade.port_labor") + 0.999)
    return max(full // 50, min(full, want))


def crew_wanted(config: GameConfig, f: Facility) -> int:
    """海運業が雇いたい船員の数: 直近 trade.cost_turns ターンに航海に出ていた船の数の平均 ×
    trade.ship_crew（港につないでいる船は、少なくとも船団の trade.idle_crew の割合の船員で
    守る）。"""
    used = f.economy.get("used", [])
    sailing = sum(used) // len(used) // config.get("trade.ship_capacity") if used else 0
    idle = round_money(fleet(f) * config.get("trade.idle_crew"))
    return -(-max(sailing, idle) * config.get("trade.ship_crew") // MILLI)
