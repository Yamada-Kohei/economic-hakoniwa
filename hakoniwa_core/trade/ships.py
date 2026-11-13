"""港と海運業（フェーズ3-1）。

海運業（島に1つ、マスを持たない）は輸送船を設備として持ちます（``economy["fleet"]``: [使い始めた
ターン, 1000 分の 1 隻] の組）。1 隻で 1 航海に trade.ship_capacity トン運べ、航海に出ている
船の数に合わせて船員（trade.ship_crew）を雇います（港につないでいる船は少しの船員で守る）。市場で買った船（造船所の船や
輸入した船）と前注文で届いた船は、在庫から船団に移して使い始めます。

寿命（FX-11。2026-11-13 に直した）: trade.ship_life ターン使った船は、代わりの船が足りるまで使い続け
ます。寿命の前の船（このターンに船団に入れた船を含む）で直近に航海に出ていた船の数が足りる分だけ、
古い順に解体します（寿命を過ぎた船そのものは足りる側に数えない）。寿命の trade.ship_life_limit 倍で
必ず解体します。寿命を過ぎた船は傷みの費用（運賃の資本費）に数えません（償却済み）。

買い替えと買い増し（FX-11・FX-80）: 目標の船団 = 直近に一番多く航海に出ていた船（ほかの国に貸した
分を含む）÷ trade.ship_target_use。寿命まで trade.ship_order_turns ターン以内の船を除いた船団が
目標に届かなければ、その分を買う（前注文する）。

荷は買う側の島の船で運びます。船は空で売り手の港へ行き、積んで戻ります。往復の間（片道の 2 倍）は、
実際に走る時間（区画 ÷ 船の速さ）の分の船を使います（``economy["busy"]``: 荷の番号 → 使っている
重さ。荷の重さ × 区画 ÷（片道のターン数 × 船の速さ）。近い島へは 1 ターンに何度も行き来できる）。
船出のときに、その船の往復の分の燃料
（trade.ship_fuel。1隻1ターンあたり）を使います。燃料が足りなければ、その分だけ運べる重さが減ります。
trade.ship_fuel に財が複数あれば、どれか1つで動きます（フェーズ3-2 から燃料と石炭）。使うときも買う
ときも、今の値段で1隻1ターンあたりの費用が安い財から先にします。

運賃は「荷 1 トン × 区画 1 つ」あたりで、（1 + trade.cost_markup）×（運航費 ＋ 資本費 ×
min(1, 2u)）÷ 1 隻を 1 ターン全部使ったときに運ぶ量です（タブ 9 FX-74。2026-11-12、W-233）。運航費は
1 隻 1 ターンの船員の賃金と一番安い燃料、資本費は直近 trade.cost_turns ターンの船の傷みと沈んだ船の
値段 ÷ 延べ隻数、u は同じターンの運んだ量 ÷ 船団を全部使ったときの量。船が余れば運航費まで下がり、
半分より多く使えば、船の傷みまで回収する全部の費用で止まります。

港は荷の積み下ろしを 1 ターンに trade.port_capacity トンまで扱い、直近の扱った量の平均 ×
trade.port_labor 人を雇います。使用料は荷 1 トンあたり
「trade.port_labor × 平均賃金 ×（1 + trade.cost_markup）」です。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import books
from hakoniwa_core.state import Facility, IslandState, LogEntry
from hakoniwa_core.trade import port_of, shipping_of
from hakoniwa_core.units import (
    BASIS,
    QUANTITY_SCALE,
    from_milli,
    money_of,
    round_money,
    to_basis,
    to_milli,
)

MILLI = QUANTITY_SCALE
# 海運業の economy: 前注文で届いた船 [量, 払った額]（船団に入れるときの設備の額）
PAID = "ships_paid"


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


ABROAD = "ships_on_order_abroad"  # 海運業の economy: 圏のほかの島の造船所に注文して届いていない船
# 海運業の economy: 島に造船所がなく、圏のほかの島の造船所に注文できる（FX-78）
REACH = "yard_abroad"


def used_ships(config: GameConfig, f: Facility) -> int:
    """直近 trade.cost_turns ターンに一番多く航海に出ていた船（1000 分の 1 隻。ほかの国に貸して
    航海に出た船も数える。借りた船で運んだ荷は貸した国の数）。"""
    return ships_of_load(config, max(f.economy.get("used", []), default=0))


def retiring(config: GameConfig, f: Facility, turn: int) -> int:
    """寿命まで trade.ship_order_turns ターン以内の船と、寿命を過ぎて使い続けている船（1000 分の
    1 隻）。"""
    life = config.get("trade.ship_life")
    ahead = config.get("trade.ship_order_turns")
    return sum(q for since, q in f.economy.get("fleet", []) if turn + ahead - since >= life)


def fleet_target(config: GameConfig, f: Facility) -> int:
    """目標の船団（1000 分の 1 隻）: 直近に航海に出ていた船 ÷ 目標の混み具合（FX-80。船主は船が
    混んで運賃が上がると新造を頼む。使った分ちょうどしか替えないと、使う分が減るたびに船団も減って
    戻らなかった）。"""
    used = used_ships(config, f)
    if used <= 0:
        return 0
    return -(-used * BASIS // max(1, to_basis(config.get("trade.ship_target_use"))))


def renewal(config: GameConfig, f: Facility, turn: int) -> int:
    """買い替えと買い増し（1000 分の 1 隻）: 寿命の来る船を除いた船団が、目標の船団に足りない分。"""
    return max(0, fleet_target(config, f) - (fleet(f) - retiring(config, f, turn)))


def ships_wanted(config: GameConfig, f: Facility, turn: int) -> int:
    """海運業が市場で買う輸送船（1000 分の 1 隻。買って船団に入れる前の船と、圏のほかの島の造船所に
    注文して届いていない船があれば、その分を引く）。

    - 直近 trade.cost_turns ターンの半分以上で、船が足りずに荷を残していたら 1 隻（S2: 圏の市場や
      契約で約定した荷が、どの船にも載らなかったとき）。
    - 買い替えと買い増し（renewal。FX-11・FX-80）。

    圏のほかの島の造船所に注文できる海運業（前の貿易の段で見た）は、市場では買わず、貿易の段で
    注文する（FX-78。市場の買いと注文で同じ船を二重に頼まない）。
    """
    if f.economy.get(REACH):
        return 0
    want = 0
    history = f.economy.get("ships_short", [])
    turns = config.get("trade.cost_turns")
    if len(history) >= turns and sum(history) * 2 >= len(history):
        want = MILLI
    want = max(want, renewal(config, f, turn))
    have = stock.total(f.stock, config.get("trade.ship_good")) + f.economy.get(ABROAD, 0)
    return max(0, want - have)


def ships_of_load(config: GameConfig, load: int) -> int:
    """重さ load を運ぶ船の数（1000 分の 1 隻）。"""
    return load // max(1, config.get("trade.ship_capacity"))


def lose_ships(island: IslandState, config: GameConfig, f: Facility, qty: int) -> int:
    """沈んだ船 qty（1000 分の 1 隻）を古い順に船団から除く。設備の額と償却済みの額から沈んだ船の
    割合を除き、まだ償却していなかった分を船の傷みと同じ費用に数えて運賃に乗せる（close_turn）。除いた量を返す。"""
    before = fleet(f)
    taken = 0
    keep = []
    for since, q in sorted(f.economy.get("fleet", []), key=lambda e: e[0]):
        n = min(q, qty - taken)
        taken += n
        if q - n > 0:
            keep.append([since, q - n])
    f.economy["fleet"] = keep
    if before > 0 and taken > 0:  # 設備の額と償却済みの額から、沈んだ船の分の割合を除く
        capital, done = f.economy.get("capital", 0), f.economy.get("depreciated", 0)
        gone_capital, gone_done = capital * taken // before, done * taken // before
        f.economy["capital"] = capital - gone_capital
        f.economy["depreciated"] = done - gone_done
        f.economy["sunk_value"] = f.economy.get("sunk_value", 0) + max(0, gone_capital - gone_done)
    return taken


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
    # 設備への投資として残す（在庫の減りに数えない）。前注文で届いた船は払った額で、ほかは島の値段で
    # 付ける（島に船の市場がないと、島の値段は初めのまま動かない）
    paid_qty, paid_value = f.economy.pop(PAID, [0, 0])
    if paid_qty > qty:  # 念のため（届いた船は同じターンにみな船団に入る）
        paid_value, paid_qty = paid_value * qty // paid_qty, qty
    value = paid_value + money_of(island.economy.prices.get(good, 0), qty - paid_qty)
    f.economy["capital"] = f.economy.get("capital", 0) + value
    add_ships(f, qty, turn)
    island.log.append(LogEntry("ships_commissioned", {"count": from_milli(qty)}))
    return qty


def scrap(island: IslandState, config: GameConfig, turn: int) -> int:
    """寿命を過ぎた船を、代わりの船が足りる分だけ解体する（寿命の trade.ship_life_limit 倍の船は
    必ず）。在庫の船を船団に入れた後に呼ぶ。解体した量を返し、使い続ける寿命を過ぎた船の量を
    ``economy["overdue"]`` に残す（運賃の資本費から外す）。"""
    f = shipping_of(island, config)
    if f is None:
        return 0
    life = config.get("trade.ship_life")
    limit = round_money(life * config.get("trade.ship_life_limit"))
    entries = f.economy.get("fleet", [])
    young = sum(q for since, q in entries if turn - since < life)
    keep_old = max(0, used_ships(config, f) - young)  # 寿命を過ぎても使い続ける船
    before = fleet(f)
    keep, gone, overdue = [], 0, 0
    # 寿命を過ぎた船は新しい順に残す
    for since, qty in sorted(entries, key=lambda e: -e[0]):
        age = turn - since
        if age < life:
            keep.append([since, qty])
            continue
        kept = 0 if age >= limit else min(qty, keep_old)
        keep_old -= kept
        overdue += kept
        gone += qty - kept
        if kept > 0:
            keep.append([since, kept])
    if overdue > 0:
        f.economy["overdue"] = overdue
    else:
        f.economy.pop("overdue", None)
    if gone:
        f.economy["fleet"] = sorted(keep, key=lambda e: e[0])
        # 解体した船の割合を設備の額と償却済みの額から除く（固定資産の除却。FX-74。前は除かず、
        # 1 隻になっても解体した船の分の減価償却が続いた）
        capital, done = f.economy.get("capital", 0), f.economy.get("depreciated", 0)
        f.economy["capital"] = capital - capital * gone // before
        f.economy["depreciated"] = done - done * gone // before
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
    """実績がないときの運賃: 船を半分使ったとき（u = 0.5）の今の値段での見積もり。船がなければ
    資本費は 0（FX-74）。"""
    wear = _ship_wear(island, config) if fleet(f) > 0 else 0
    return _rate(config, _ship_run_cost(island, config), wear, 0.5)


def _full_use(config: GameConfig) -> float:
    """1 隻を 1 ターン全部使ったときに運ぶ量（トン × 区画）。往復で片道の 2 倍走るので、速さの
    半分。"""
    return config.get("trade.ship_capacity") * config.get("trade.ship_speed") / 2


def _rate(config: GameConfig, run_cost: float, wear: float, use: float) -> int:
    """運賃 =（1 + 上乗せ）×（運航費 + 資本費 × min(1, 2u)）÷ 1 隻を全部使ったときに運ぶ量
    （FX-74）。船が余れば運航費まで下がり、半分より多く使えば船の傷みまで回収する全部の費用で止まる（足りない
    ときの割増は付けない。2026-11-12 の X870 の比べで、割増が輸入を減らし、使った船の数で決める
    買い替えが減って船が細る回り道に入った）。"""
    cost = run_cost + wear * min(1.0, 2 * max(0.0, use))
    return max(1, round_money(cost / _full_use(config) * (1 + config.get("trade.cost_markup"))))


def _ship_run_cost(island: IslandState, config: GameConfig) -> float:
    """船 1 隻を 1 ターン走らせる費用（船員の賃金と一番安い燃料。今の値段）。"""
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    prices = island.economy.prices
    settings = config.get("trade.ship_fuel")
    fuel = min((prices.get(g, 0) * q for g, q in settings.items()), default=0)
    return config.get("trade.ship_crew") * wage + fuel


def _ship_wear(island: IslandState, config: GameConfig) -> float:
    """船 1 隻の 1 ターンの傷み（今の船の値段 ÷ 寿命）。"""
    return island.economy.prices.get(config.get("trade.ship_good"), 0) / config.get(
        "trade.ship_life"
    )


def close_turn(island: IslandState, config: GameConfig, carried: int, fuel_value: int) -> None:
    """船の運賃を見直す。carried はこのターンに運び出した量（トン × 区画。1000 分の 1）。"""
    f = shipping_of(island, config)
    if f is None:
        return
    keep = config.get("trade.cost_turns")
    wage_bill = f.economy.get("wage", 0) * f.workers
    # 寿命を過ぎて使い続けている船は償却済みなので、傷みに数えない（延べ隻数には数える）
    wear = round_money(
        _ship_wear(island, config) * from_milli(max(0, fleet(f) - f.economy.get("overdue", 0)))
    )
    sunk = f.economy.pop("sunk_value", 0)  # 沈んだ船の値段も船の傷みと同じ費用（MI-35）
    costs = [*f.economy.get("costs", []), wage_bill + fuel_value + wear + sunk][-keep:]
    f.economy["used"] = [*f.economy.get("used", []), busy(f)][-keep:]
    loads = [*f.economy.get("carried", []), carried][-keep:]
    # 資本費（傷みと沈んだ船）と船団の数はターンごとに残す（解体の後も、その時の船団で数える）
    capital = [*f.economy.get("capital_costs", []), wear + sunk][-keep:]
    fleets = [*f.economy.get("fleets", []), fleet(f)][-keep:]
    f.economy["costs"] = costs
    f.economy["carried"] = loads
    f.economy["capital_costs"] = capital
    f.economy["fleets"] = fleets
    ship_turns = sum(fleets) / MILLI  # 延べ隻数
    if ship_turns <= 0:
        f.economy["rate"] = _rate(config, _ship_run_cost(island, config), 0, 0)
        return
    # 運んだ量も船団を全部使ったときの量も 1000 分の 1（トン × 区画）で数える
    use = sum(loads) / (sum(fleets) * _full_use(config))
    f.economy["rate"] = _rate(
        config, _ship_run_cost(island, config), sum(capital) / ship_turns, use
    )


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
    port = port_of(island, config)
    if port is None:
        return 0
    used = scratch.get("port_used", {}).get(island.id, 0)
    return max(0, port_capacity(config, port) * MILLI - used)


def port_capacity(config: GameConfig, port: Facility) -> int:
    """港が 1 ターンに積み下ろしできる重さ（トン）。建て増した港は規模の分だけ多い（段6 A3）。"""
    return config.get("trade.port_capacity") * fac.size_of(port)


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
    full = fac.workers_of(defs, f)
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
