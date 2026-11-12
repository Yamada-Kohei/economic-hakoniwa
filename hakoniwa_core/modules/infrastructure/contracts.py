"""土木業者（W-270）: 公共事業の工事と維持を請け負う業者への注文・単価・代金・参入と撤退。

公共事業の人手は、まず島の土木業者（施設 contractor.facility）に請け負わせ、業者で埋まらない分
だけ直営（modules/infrastructure の hire）で雇います。業者が売るのは持ち越せない「工事」で、
1 単位は直営の作業員 1 人・ターン分の工事です（量は 1000 分の 1 単位。人手の 1000 分の 1 人と
同じ数）。業者の 1 人は contractor.output_per_worker 単位を届けます。資材は今どおり公共事業が
用意して渡します（支給材）。

- 雇用の段の初め（place）: 公共事業が維持・工事の順に要る人手を決め、単価の安い業者から順に
  注文する（同じ単価なら番号の小さい順）。1 つの業者への注文は、今の人数で届けられる量に、前の
  ターンに通勤圏で雇い足せた見込み（REACH）を足した分までで、全力を超えない。予定価格（島の
  平均賃金 ×（1 + contractor.margin））を超える単価の業者には注文しない。
- 取り置き（reserve。modules/infrastructure の plan から）: 注文の量 × 単価を公共事業費から
  取り置く。維持の分は施設より先、工事の分は施設の後。取り置けない分は注文しない。国庫直轄の
  業者は代金のやり取りをせず、賃金を公共事業費から直に払う（取り置きは業者の施設の賃金の取り置き）。
- 業者は受けた量 ÷ 1 人あたりの量を雇いたい人数にする（雇用の段の _wanted）。独立採算の業者は、
  取り置かれた代金を払える賃金に数える（receivable と covered）。
- 届けた量（deliver）: 雇えた人 × 1 人あたりの量と、取り置いた注文の少ない方。維持の分から先に
  回す。
- 代金（settle。賃金の段の初め）: 届けた量 × 単価を、業者の賃金より先に公共事業費から払う
  （出来高）。業者の売上と、工事の部門の請負の項目（GDP の支出の側の投資）に記帳する。
- 単価（review_prices。市場の段）: 売り手の希望価格と同じ形で、頼まれた量（asked）が全力を
  超えれば上げ、手が空けば下げる（1 ターンに market.band まで）。下限は 1 単位の変動費（業者の
  賃金 ÷ 1 人あたりの量）、上限は予定価格。
- 参入（entry。参入と撤退の段）: 直近 contractor.entry_turns ターンの埋まらなかった工事の平均が
  新しい業者の全力の contractor.entry_share 以上で、予定価格で賃金を払えれば、資産を持つ層が
  出資して開く。
- 撤退（exit_signal）: 民営の独立採算の業者は、売上で賃金を払えない赤字が firms.exit_loss_turns
  ターン続くか、注文も人もないまま firms.exit_idle_turns ターン続けば閉鎖に入る。
- 建国と移し替え（prepare）: 首都の近くの空いた平地に contractor.start 個置く（1 度だけ）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    CONSTRUCTION,
    OWNERS,
    Payer,
    books,
    default_accounting,
    facility_entity,
    funds,
    household,
    is_contractor,
    is_self_supporting,
    is_stopped,
    owner_entity,
)
from hakoniwa_core.geo import within
from hakoniwa_core.state import OWNER_PRIVATE, OWNER_STATE, Facility, IslandState, LogEntry
from hakoniwa_core.units import money_of, part, round_money, to_basis, to_milli

CONTRACTS = "contracts"  # island.scratch: このターンの注文・取り置き・届いた量
PRICE = "contract_price"  # f.economy: 工事 1 単位の単価
REACH = "contract_reach"  # f.economy: 前のターンに通勤圏で雇い足せた見込み（失業者の数）
ORDERS = (
    "contract_orders"  # f.economy: このターンに受けた工事 [維持, 工事]（1000 分の 1 単位。画面）
)
DELIVERED = "contract_delivered"  # f.economy: このターンに届けた工事（1000 分の 1 単位。画面）
IDLE = "contract_idle"  # f.economy: 注文も人もないターンが続いた数（撤退）
UNFILLED = "unfilled"  # economy.infra: 直近の埋まらなかった工事（1000 分の 1 単位）の並び（参入）
SHORT_UPKEEP = "contract_short_upkeep"  # economy.infra: 前のターンに業者が届けられなかった維持の量
VERSION_KEY = "contractor"  # economy.ext: 建国と移し替えで業者を置いた印
PAYER = Payer(ledger.GOV, CONSTRUCTION)
UPKEEP, BUILD = 0, 1


def per_worker(config: GameConfig) -> int:
    """業者の 1 人が 1 ターンに届ける工事（1000 分の 1 単位）。"""
    return max(1, to_milli(config.get("contractor.output_per_worker")))


def _scratch(island: IslandState) -> dict[str, Any]:
    return island.scratch.setdefault(
        CONTRACTS,
        {
            "orders": {},
            "funded": {},
            "held": {},
            "asked": {},
            "covered": {},
            "delivered": {},
            "unordered": [0, 0],
            "reserved": [],  # 取り置きを済ませた注文の種類（UPKEEP・BUILD）
        },
    )


def _effective(state: dict[str, Any], fid: int) -> list[int]:
    """業者 fid の注文の量 [維持, 工事]: 取り置きを済ませた種類は取り置けた量、
    まだの種類は注文の量。"""
    orders = state.get("orders", {}).get(fid, [0, 0])
    funded = state.get("funded", {}).get(fid, [0, 0])
    done = state.get("reserved", [])
    return [funded[k] if k in done else orders[k] for k in (UPKEEP, BUILD)]


def contractors(island: IslandState, config: GameConfig) -> list[Facility]:
    """島の土木業者（建っているもの。番号の順）。"""
    return sorted(
        (f for f in island.facilities if f.built and is_contractor(config, f)), key=lambda f: f.id
    )


def working(island: IslandState, config: GameConfig, turn: int) -> list[Facility]:
    """注文を受けられる業者（閉鎖・差し押さえ・災害・占領で止まっていない）。"""
    return [
        f
        for f in contractors(island, config)
        if not is_stopped(f) and not fac.is_halted(f, turn) and not f.ext.get("occupied")
    ]


def full(defs: Defs, config: GameConfig, f: Facility) -> int:
    """全力で届けられる工事（規模 × 定義の人数 × 1 人あたりの量。設備の傷んだ分を除く）。"""
    workers = fac.scale(fac.workers_of(defs, f), fac.equipment_cap(f))
    return workers * per_worker(config)


def ceiling(island: IslandState, config: GameConfig) -> int:
    """予定価格（工事 1 単位）: 島の平均賃金 ×（1 + contractor.margin）。"""
    wage = max(island.economy.average_wage or config.get("labor.initial_wage"), 1)
    return wage + part(wage, to_basis(config.get("contractor.margin")))


def floor(config: GameConfig, f: Facility) -> int:
    """単価の下限（工事 1 単位の変動費）: 業者の賃金 ÷ 1 人あたりの量（切り上げ）。"""
    wage = f.economy.get("wage", 0)
    return -(-wage * 1000 // per_worker(config))


def price_of(island: IslandState, config: GameConfig, f: Facility) -> int:
    """業者の今の単価。まだなければ、賃金 ÷ 1 人あたりの量 ×（1 + margin）を予定価格までに。"""
    price = f.economy.get(PRICE)
    if price is None:
        low = floor(config, f)
        price = low + part(low, to_basis(config.get("contractor.margin")))
        f.economy[PRICE] = max(min(price, ceiling(island, config)), 1)
    return f.economy[PRICE]


def treasury(f: Facility) -> bool:
    """国庫直轄の業者か（代金のやり取りをせず、賃金を公共事業費から直に払う）。"""
    return not is_self_supporting(f)


def order_price(island: IslandState, config: GameConfig, f: Facility) -> int:
    """注文の順に使う単価（国庫直轄の業者は変動費）。"""
    return floor(config, f) if treasury(f) else price_of(island, config, f)


def takes_orders(island: IslandState, config: GameConfig, f: Facility) -> bool:
    """予定価格で受けられるか（下限か単価が予定価格を超える独立採算の業者は受けない）。"""
    if treasury(f):
        return True
    top = ceiling(island, config)
    return floor(config, f) <= top and price_of(island, config, f) <= top


def capacity(defs: Defs, config: GameConfig, f: Facility) -> int:
    """このターンに受ける注文の上限: 今の人数で届けられる量に、
    前のターンに通勤圏で雇い足せた見込みを
    足した分まで、全力まで。見込みのない初めのターンは全力。"""
    whole = full(defs, config, f)
    reach = f.economy.get(REACH)
    if reach is None:
        return whole
    return min(whole, (f.workers + reach) * per_worker(config))


# --- 雇用の段 -----------------------------------------------------------------------


def place(island: IslandState, defs: Defs, config: GameConfig, turn: int, need: list[int]) -> None:
    """雇用の段の初め: 維持と工事の要る人手 need（1000 分の 1 人）を、
    単価の安い業者から順に注文する。"""
    state = _scratch(island)
    firms = [f for f in working(island, config, turn) if takes_orders(island, config, f)]
    firms.sort(key=lambda f: (order_price(island, config, f), f.id))
    room = {f.id: capacity(defs, config, f) for f in firms}
    orders: dict[int, list[int]] = {f.id: [0, 0] for f in firms}
    asked: dict[int, int] = dict.fromkeys(orders, 0)
    left = list(need)
    for kind in (UPKEEP, BUILD):
        for f in firms:
            if left[kind] <= 0:
                break
            asked[f.id] += left[kind]
            take = min(left[kind], room[f.id] - sum(orders[f.id]))
            if take > 0:
                orders[f.id][kind] += take
                left[kind] -= take
    state["orders"] = orders
    state["asked"] = asked
    state["unordered"] = left
    for f in contractors(island, config):
        f.economy[ORDERS] = orders.get(f.id, [0, 0])


def ordered(island: IslandState, f: Facility) -> int:
    """業者 f がこのターンに受けた注文（取り置けずに減らした後。1000 分の 1 単位）。"""
    state = island.scratch.get(CONTRACTS) or {}
    return sum(_effective(state, f.id))


def wanted(island: IslandState, config: GameConfig, f: Facility) -> int:
    """業者の雇いたい人数: 受けた注文 ÷ 1 人あたりの量（切り上げ）。"""
    return -(-ordered(island, f) // per_worker(config))


def reserve(island: IslandState, config: GameConfig, upkeep: bool, labor: int) -> int:
    """維持（upkeep）か工事の注文の代金を、単価の安い業者から公共事業費に取り置く。工事の要る人手
    labor（1000 分の 1 人）を超えて注文しない。取り置けない分は注文しない。
    業者が受け持つ人手を返す。"""
    state = island.scratch.get(CONTRACTS)
    if not state:
        return 0
    kind = UPKEEP if upkeep else BUILD
    by_id = {f.id: f for f in contractors(island, config)}
    funded = state["funded"]
    total = 0
    for fid in state["orders"]:  # place が単価の順に並べてある
        f = by_id.get(fid)
        want = min(state["orders"][fid][kind], max(0, labor - total))
        got = 0
        if f is not None and want > 0:
            if treasury(f):
                got = want
            else:
                price = price_of(island, config, f)
                money = funds.available(island, PAYER)
                got = min(want, money * 1000 // price) if price > 0 else want
                cost = money_of(price, got)
                if cost > 0:
                    funds.commit(island, PAYER, cost)
                    state["held"][fid] = state["held"].get(fid, 0) + cost
        funded.setdefault(fid, [0, 0])[kind] = got
        total += got
    if kind not in state["reserved"]:
        state["reserved"].append(kind)
    for fid in state["orders"]:
        if fid in by_id:
            by_id[fid].economy[ORDERS] = _effective(state, fid)
    return total


def receivable(island: IslandState, config: GameConfig, f: Facility) -> int:
    """独立採算の業者が、受けた注文の代金のうち払える賃金に数える額（取り置き前の工事の分は見込み）。"""
    if treasury(f) or not is_contractor(config, f):
        return 0
    state = island.scratch.get(CONTRACTS) or {}
    if f.id not in state.get("orders", {}):
        return 0
    return money_of(price_of(island, config, f), sum(_effective(state, f.id)))


def set_covered(island: IslandState, f: Facility, amount: int) -> None:
    """業者の賃金のうち、受け取る代金で払う額（取り置かずに覚えておき、賃金の段で取り置く）。"""
    covered = _scratch(island)["covered"]
    if amount > 0:
        covered[f.id] = amount
    else:
        covered.pop(f.id, None)


def release(island: IslandState, payer: Payer, f: Facility, amount: int) -> None:
    """業者の賃金の取り置きを戻す（受け取る代金で払う分から先に減らす）。"""
    if amount <= 0:
        return
    covered = (island.scratch.get(CONTRACTS) or {}).get("covered", {})
    cut = min(amount, covered.get(f.id, 0))
    if cut:
        set_covered(island, f, covered[f.id] - cut)
    funds.release(island, payer, amount - cut)


def deliver(island: IslandState, config: GameConfig, workers: dict[int, int]) -> int:
    """業者ごとに届ける工事（雇えた人 × 1 人あたりの量と、取り置いた注文の少ない方。維持から先に）を
    決め、全部の量を返す。workers は業者の番号 → 雇えた人数。"""
    state = island.scratch.get(CONTRACTS)
    if not state:
        return 0
    each = per_worker(config)
    delivered: dict[int, list[int]] = {}
    for fid in state["orders"]:
        orders = _effective(state, fid)
        can = workers.get(fid, 0) * each
        upkeep = min(orders[UPKEEP], can)
        delivered[fid] = [upkeep, min(orders[BUILD], can - upkeep)]
    state["delivered"] = delivered
    return sum(sum(d) for d in delivered.values())


def delivered(island: IslandState) -> tuple[int, int]:
    """このターンに業者が届けた (維持の量, 全部の量)（1000 分の 1 単位）。"""
    state = island.scratch.get(CONTRACTS) or {}
    rows = state.get("delivered", {}).values()
    return sum(d[UPKEEP] for d in rows), sum(sum(d) for d in rows)


def finish_hiring(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    free: dict[tuple[int, int], int],
) -> None:
    """雇用の段の終わり: 雇えた人で届ける量を決め直し、
    通勤圏に残った失業者を次のターンの見込みに残す。"""
    firms = contractors(island, config)
    if not firms:
        return
    deliver(island, config, {f.id: f.workers for f in firms})
    state = island.scratch.get(CONTRACTS) or {}
    radius_of = fac.commute_radius
    for f in firms:
        if fac.located(f):
            area = within(island, fac.position(f), radius_of(defs, config, f.kind))
            f.economy[REACH] = sum(free.get(pos, 0) for pos in area)
        f.economy[DELIVERED] = sum(state.get("delivered", {}).get(f.id, [0, 0]))
        if f.id not in state.get("orders", {}):
            f.economy[ORDERS] = [0, 0]


# --- 賃金の段 -----------------------------------------------------------------------


def settle(island: IslandState, config: GameConfig) -> None:
    """賃金の段の初め: 届けた量 × 単価を、業者の賃金より先に公共事業費から払う（出来高）。取り置いた
    代金の残りは戻す。受け取る代金で払うつもりだった賃金（covered）を、受け取った後に取り置く。"""
    state = island.scratch.get(CONTRACTS)
    if not state:
        return
    by_id = {f.id: f for f in contractors(island, config)}
    for fid, held in sorted(state["held"].items()):
        f = by_id.get(fid)
        amount = 0
        if f is not None:
            done = sum(state["delivered"].get(fid, [0, 0]))
            amount = min(held, money_of(price_of(island, config, f), done))
        if amount > 0:
            funds.pay(island, PAYER, facility_entity(fid), amount, "contract", committed=True)
            books.add(island, fid, books.SALES, amount)
            books.add_sector(island, books.CONSTRUCTION, "contracted", amount)
        funds.release(island, PAYER, held - amount)
    state["held"] = {}
    for fid, amount in sorted(state["covered"].items()):
        f = by_id.get(fid)
        if f is None or amount <= 0:
            continue
        payer = Payer(facility_entity(fid))
        got = min(amount, funds.available(island, payer))
        funds.commit(island, payer, got)
        short = amount - got
        wage = f.economy.get("wage", 0)
        if short > 0 and wage > 0:
            # 代金で払えない分は払われないまま働いた人にする（未払い賃金。
            # 届けられずに代金が減ったとき）
            extra = -(-short // wage)
            f.economy["unpaid"] = f.economy.get("unpaid", 0) + extra
            f.economy["arrears_months"] = max(1, f.economy.get("arrears_months", 0))
            funds.release(island, payer, min(got, extra * wage - short))
    state["covered"] = {}


def remember(island: IslandState, works: dict[str, Any], planned: int, direct: int) -> None:
    """インフラの段: 前のターンに業者が届けられなかった維持の量と、埋まらなかった工事（要った量 −
    届いた量 − 直営）を残す（直営の取り置きの見込みと、民間の参入の見込み）。"""
    state = island.scratch.get(CONTRACTS) or {}
    upkeep_done, total = delivered(island)
    upkeep_ordered = sum(_effective(state, fid)[UPKEEP] for fid in state.get("orders", {}))
    short = max(0, upkeep_ordered - upkeep_done)
    if short:
        works[SHORT_UPKEEP] = short
    else:
        works.pop(SHORT_UPKEEP, None)
    unfilled = max(0, planned - total - direct)
    history = [*works.get(UNFILLED, []), unfilled]
    works[UNFILLED] = history[-12:]


def average_price(island: IslandState, config: GameConfig) -> int:
    """業者の単価の平均（届けた量で重みをつける。届けていなければ動いている業者の単純な平均）。"""
    state = island.scratch.get(CONTRACTS) or {}
    done = state.get("delivered", {})
    weights = [
        (price_of(island, config, f), sum(done.get(f.id, [0, 0])))
        for f in contractors(island, config)
    ]
    total = sum(w for _, w in weights)
    if total > 0:
        return sum(p * w for p, w in weights) // total
    prices = [p for p, _ in weights]
    return sum(prices) // len(prices) if prices else 0


# --- 市場の段 -----------------------------------------------------------------------


def review_prices(island: IslandState, defs: Defs, config: GameConfig) -> None:
    """業者の単価の見直し: P × (1 + k (D − S) / max(S, D))、1 ターンに値幅まで。S は全力、
    D は頼まれた
    量（届けられないほど頼まれれば上げ、手が空けば下げる）。下限は変動費、上限は予定価格。"""
    state = island.scratch.get(CONTRACTS) or {}
    asked = state.get("asked", {})
    k = config.get("market.price_speed")
    width = config.get("market.band")
    top = ceiling(island, config)
    for f in contractors(island, config):
        if treasury(f) or "wage" not in f.economy:
            continue
        price = price_of(island, config, f)
        s, d = float(full(defs, config, f)), float(asked.get(f.id, 0))
        if s > 0 and max(s, d) > 0:
            change = k * (d - s) / max(s, d)
            price = round(price * (1 + max(-width, min(width, change))))
        low = floor(config, f)
        f.economy[PRICE] = max(1, low, min(price, top))


# --- 帳簿と画面 ---------------------------------------------------------------------


def stats(island: IslandState, config: GameConfig, direct: int) -> None:
    """統計: works.contracted（業者が届けた量）・works.direct（直営の人数）・contractor.price。"""
    _, total = delivered(island)
    island.stats["works.contracted"] = round(total / 1000, 3)
    island.stats["works.direct"] = direct
    if contractors(island, config):
        island.stats["contractor.price"] = average_price(island, config)


# --- 建国と移し替え -------------------------------------------------------------------


def _owner(defs: Defs, island: IslandState, kind: str) -> str:
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    state = regime.get("owner", OWNER_STATE) == OWNER_STATE or kind in regime.get("state_owned", ())
    if not state and OWNERS not in island.economy.households:
        state = True
    return OWNER_STATE if state else OWNER_PRIVATE


def capital(defs: Defs, config: GameConfig, island: IslandState) -> int:
    """独立採算の業者に持ち主が出資する運転資金（全力の賃金の contractor.capital_turns
    ターン分）。"""
    kind = config.get("contractor.facility")
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    return wage * defs.facilities[kind]["workers"] * config.get("contractor.capital_turns")


def open_contractor(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    pos: tuple[int, int],
    owner: str,
    log: str,
) -> Facility:
    """建った業者を pos に置き、独立採算なら持ち主が運転資金を出資する。"""
    from hakoniwa_core.economy.start import init_facility, invest
    from hakoniwa_core.naming import assign_serial

    kind = config.get("contractor.facility")
    accounting = default_accounting(defs, island, kind, owner)
    f = Facility(
        id=island.next_facility_id(),
        kind=kind,
        x=pos[0],
        y=pos[1],
        owner=owner,
        accounting=accounting,
        built=True,
        status=fac.STATUS_STARTING,
    )
    assign_serial(island, f)
    island.facilities.append(f)
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    init_facility(island, defs, config, f, dict(island.economy.prices), wage)
    if is_self_supporting(f):
        source = owner_entity(f)
        payer = Payer(source) if source != ledger.GOV else Payer(ledger.GOV, CONSTRUCTION)
        amount = round_money(min(capital(defs, config, island), funds.available(island, payer)))
        if amount > 0:
            invest(island, source, facility_entity(f.id), amount)
    island.log.append(LogEntry(log, {"x": pos[0], "y": pos[1]}))
    return f


def spot(island: IslandState, defs: Defs, config: GameConfig) -> tuple[int, int] | None:
    """業者を置くマス: 首都の近くの、
    業者を建てられる空いた平地（工業団地・オフィス街の指定のないマス
    から先に）。"""
    kind = config.get("contractor.facility")
    center = island.capital or (island.size // 2, island.size // 2)
    occupied = island.facilities_by_tile()
    spots = [
        pos
        for pos in within(island, center, island.size)
        if pos not in occupied and fac.placement_problem(defs, island, kind, *pos) is None
    ]
    plain = [pos for pos in spots if not island.tile(*pos).zone]
    return (plain or spots or [None])[0]


def prepare(island: IslandState, defs: Defs, config: GameConfig) -> None:
    """建国と移し替え: 首都の近くの空いた平地に業者を contractor.start 個置く（1 度だけ）。"""
    economy = island.economy
    if economy.ext.get(VERSION_KEY) or not island.has_economy:
        return
    economy.ext[VERSION_KEY] = 1
    kind = config.get("contractor.facility")
    if kind not in defs.facilities:
        return
    have = len(contractors(island, config))
    for _ in range(max(0, config.get("contractor.start") - have)):
        pos = spot(island, defs, config)
        if pos is None:
            island.log.append(LogEntry("contractor_not_placed", {}))
            return
        open_contractor(island, defs, config, pos, _owner(defs, island, kind), "contractor_placed")


# --- 参入と撤退の段 -------------------------------------------------------------------


def entry(island: IslandState, defs: Defs, config: GameConfig, planned: bool) -> Facility | None:
    """民間の参入: 直近の埋まらなかった工事の平均が新しい業者の全力の contractor.entry_share
    以上で、予定価格で賃金を払え、資産を持つ層が出資でき、空いた平地があれば 1 つ開く（計画経済の
    島は開かない）。"""
    kind = config.get("contractor.facility")
    if planned or kind not in defs.facilities or OWNERS not in island.economy.households:
        return None
    from hakoniwa_core.modules.firms import private_allowed

    if not private_allowed(defs, island, kind):
        return None
    limit = defs.facilities[kind].get("max_per_island")
    current = [f for f in island.facilities if is_contractor(config, f)]
    if limit is not None and len(current) >= limit:
        return None
    if any(not f.built or "contract_new" in f.ext for f in current):
        return None  # 前に開いた業者が動き出して見込みが変わるのを待つ
    turns = config.get("contractor.entry_turns")
    history = island.economy.infra.get(UNFILLED, [])[-turns:]
    if len(history) < turns:
        return None
    probe = Facility(id=0, kind=kind, x=-1, y=-1, owner=OWNER_PRIVATE, built=True)
    whole = fac.workers_of(defs, probe) * per_worker(config)
    if sum(history) // turns < whole * config.get("contractor.entry_share"):
        return None
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    if ceiling(island, config) * per_worker(config) // 1000 <= wage:
        return None  # 予定価格では賃金を払えない
    if funds.available(island, Payer(household(OWNERS))) < capital(defs, config, island):
        return None
    pos = spot(island, defs, config)
    if pos is None:
        return None
    f = open_contractor(island, defs, config, pos, OWNER_PRIVATE, "contractor_entered")
    f.ext["contract_new"] = turns
    island.economy.infra.pop(UNFILLED, None)
    return f


def settle_new(island: IslandState, config: GameConfig) -> None:
    """開いて間もない業者の印を数え下ろす（その間は次の参入を見送る）。"""
    for f in contractors(island, config):
        left = f.ext.get("contract_new")
        if left is None:
            continue
        if left <= 1:
            f.ext.pop("contract_new")
        else:
            f.ext["contract_new"] = left - 1


def exit_signal(island: IslandState, config: GameConfig, f: Facility) -> bool:
    """撤退の決まりに当たったか: 売上で賃金を払えない赤字が firms.exit_loss_turns ターン続くか、
    注文も人もないまま firms.exit_idle_turns ターン続いた。"""
    settings = config.get("firms")
    items = books.facility_items(island).get(f.id, {})
    sales, wages = items.get(books.SALES, 0), items.get(books.WAGES, 0)
    margin = part(wages, to_basis(settings["exit_loss_margin"]))
    losing = f.economy.get("loss_streak", 0) + 1 if sales - wages < -margin else 0
    idle = f.economy.get(IDLE, 0) + 1 if f.workers <= 0 and sales <= 0 else 0
    f.economy["loss_streak"] = losing
    f.economy[IDLE] = idle
    return losing >= settings["exit_loss_turns"] or idle >= settings["exit_idle_turns"]
