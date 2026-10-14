"""島に貿易の仕組みを置く（フェーズ3-1。建国の後と、前のフェーズの島の移し替えの1度だけ）。

移し替えの段（modules/upgrade）が、お金の経済のある島にまだ置いていなければ呼びます。

- 海運業（マスを持たない。島に1つ）。持ち主は経済体制の既定（混合経済は基幹産業として国営）。
  輸送船 trade.start_ships 隻と、燃料 trade.start_fuel（石炭）を持たせる。
- 港: 首都に一番近い、海か浅瀬に接する空いた平地に、工事の済んだ港を1つ置く（国営。会計は体制の
  既定）。置けるマスがなければ置かずにログに書く。
- 商社: 首都の近くの空いたオフィス街のマスに1つ（空いたオフィス街がなければ、首都の近くの空いた平地
  をオフィス街にして）。持ち主は経済体制の既定（市場経済と混合経済は民営、計画経済は国営）。
- 海外の商人は、このターンから foreign_merchant.turns ターン来る。

商社と海運業は、外貨と船の費用を施設ごとに分けるため、どの経済体制でも独立採算にします。持ち主が
運転資金を出資します（商社は平均賃金の trade.trader_capital 倍、海運業は船員の賃金と燃料の
firms.operating_turns ターン分。国が出すときは、1つにつき国庫の使えるお金の GOV_SHARE まで）。
"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger, stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    OWNERS,
    Payer,
    default_accounting,
    facility_entity,
    funds,
    household,
    owner_entity,
)
from hakoniwa_core.economy.start import init_facility, invest
from hakoniwa_core.geo import within
from hakoniwa_core.naming import assign_serial
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
    OWNER_PRIVATE,
    OWNER_STATE,
    ZONE_NONE,
    ZONE_OFFICE,
    Facility,
    IslandState,
    LogEntry,
)
from hakoniwa_core.trade import island_state, port_of, shipping_of, ships, started, traders_of
from hakoniwa_core.units import money_of, round_money, to_milli

GOV_SHARE = 0.1  # 国が貿易の施設1つに出す運転資金は、国庫の使えるお金のこの割合まで
# 海運業の位置。マスを持たない施設は負の位置に置く。運送業が (-1, -1) を使い、サーバーは島の中で
# 位置が重ならないように保存するので、別の位置にする
SHIPPING_POS = (-2, -2)


def prepare(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> None:
    """港・海運業・輸送船・商社を置き、海外の商人を来させる。もう置いてあれば何もしない。"""
    state = island_state(island)
    if "since" in state:
        return
    settings = config.get("trade")
    if shipping_of(island, config) is None:
        place_shipping(island, defs, config, turn)
    if port_of(island, config) is None:
        place_port(island, defs, config)
    if not traders_of(island, config):
        open_trader(island, defs, config, _office_spot(island, defs, config), None)
    state["since"] = turn
    state["merchant_from"] = turn
    state.setdefault("rates", {})
    island.log.append(
        LogEntry(
            "trade_started",
            {
                "ships": settings["start_ships"],
                "turns": config.get("foreign_merchant.turns"),
            },
        )
    )


def _owner(defs: Defs, island: IslandState, kind: str) -> str:
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    state = regime.get("owner", OWNER_STATE) == OWNER_STATE or kind in regime.get("state_owned", ())
    if not state and OWNERS not in island.economy.households:
        state = True
    return OWNER_STATE if state else OWNER_PRIVATE


def _fund(island: IslandState, f: Facility, amount: int) -> int:
    """持ち主が施設に運転資金を出資する（持ち主の使えるお金まで。国は国庫の使えるお金の
    GOV_SHARE まで）。出資した額を返す。"""
    owner = owner_entity(f)
    payer = Payer(owner) if owner != ledger.GOV else Payer(ledger.GOV, "trade")
    have = funds.available(island, payer)
    if owner == ledger.GOV:
        have = round_money(have * GOV_SHARE)
    amount = min(amount, have)
    if amount > 0:
        invest(island, owner, facility_entity(f.id), amount)
    return max(0, amount)


def _new(island: IslandState, kind: str, pos: tuple[int, int], owner: str, accounting: str):
    f = Facility(
        id=island.next_facility_id(),
        kind=kind,
        x=pos[0],
        y=pos[1],
        owner=owner,
        accounting=accounting,
        built=True,
        # マスのある施設（港・商社）は、初めて動いたときに「稼働を開始しました」と出す
        status=fac.STATUS_STARTING if pos[0] >= 0 and pos[1] >= 0 else "",
    )
    assign_serial(island, f)
    island.facilities.append(f)
    return f


def place_shipping(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> Facility:
    settings = config.get("trade")
    kind = settings["shipping_facility"]
    f = _new(island, kind, SHIPPING_POS, _owner(defs, island, kind), ACCOUNTING_SELF)
    prices = island.economy.prices
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    init_facility(island, defs, config, f, dict(prices), wage)
    good = settings["ship_good"]
    count = to_milli(settings["start_ships"])
    ships.add_ships(f, count, turn)
    f.economy["capital"] = money_of(prices.get(good, 0), count)
    for g, qty in sorted(settings["start_fuel"].items()):
        stock.add(f.stock, g, to_milli(qty), turn, defs.goods)
    per_turn = ships.crew_wanted(config, f) * wage
    per_turn += min(  # 燃料はどれか1つで動くので、安い方の費用で見る
        (
            money_of(prices.get(g, 0), q) * settings["fuel_turns"]
            for g, q in ships.fuel_per_turn(config, ships.capacity(config, f)).items()
        ),
        default=0,
    )
    _fund(island, f, per_turn * config.get("firms.operating_turns"))
    return f


def place_port(island: IslandState, defs: Defs, config: GameConfig) -> Facility | None:
    kind = config.get("trade.port_facility")
    center = island.capital or (island.size // 2, island.size // 2)
    for pos in within(island, center, island.size):
        if fac.placement_problem(defs, island, kind, *pos) is None:
            accounting = default_accounting(defs, island, kind, OWNER_STATE)
            f = _new(island, kind, pos, OWNER_STATE, accounting)
            wage = island.economy.average_wage or config.get("labor.initial_wage")
            init_facility(island, defs, config, f, dict(island.economy.prices), wage)
            if accounting == ACCOUNTING_SELF:
                _fund(island, f, wage * defs.facilities[kind]["workers"] // 2)
            island.log.append(LogEntry("port_placed", {"x": pos[0], "y": pos[1]}))
            return f
    island.log.append(LogEntry("port_not_placed", {}))
    return None


def _office_spot(island: IslandState, defs: Defs, config: GameConfig) -> tuple[int, int] | None:
    """商社を置くマス: 首都の近くの空いたオフィス街。なければ首都の近くの空いた平地をオフィス街に
    する。どちらもなければ None。"""
    kind = config.get("trade.trader_facility")
    center = island.capital or (island.size // 2, island.size // 2)
    occupied = island.facilities_by_tile()
    spots = within(island, center, island.size)
    for pos in spots:
        if island.tile(*pos).zone == ZONE_OFFICE and pos not in occupied:
            if fac.placement_problem(defs, island, kind, *pos) is None:
                return pos
    terrains = defs.facilities[kind]["terrain"]
    for pos in spots:
        tile = island.tile(*pos)
        if tile.terrain in terrains and tile.zone == ZONE_NONE and pos not in occupied:
            tile.zone = ZONE_OFFICE
            return pos
    return None


def open_trader(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    pos: tuple[int, int] | None,
    owner: str | None,
) -> Facility | None:
    """商社を開く（持ち主が運転資金を出資する）。置くマスがなければ None。"""
    if pos is None:
        island.log.append(LogEntry("trader_not_placed", {}))
        return None
    kind = config.get("trade.trader_facility")
    owner = owner or _owner(defs, island, kind)
    f = _new(island, kind, pos, owner, ACCOUNTING_SELF)
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    init_facility(island, defs, config, f, dict(island.economy.prices), wage)
    _fund(island, f, wage * config.get("trade.trader_capital"))
    return f


def owners_can_fund(island: IslandState, config: GameConfig) -> bool:
    """民営の商社を開く出資が、資産を持つ層の預金でまかなえるか。"""
    if OWNERS not in island.economy.households:
        return False
    wage = island.economy.average_wage or config.get("labor.initial_wage")
    need = wage * config.get("trade.trader_capital")
    return funds.available(island, Payer(household(OWNERS))) >= need


def entry(
    island: IslandState, defs: Defs, config: GameConfig, planned: bool, turn: int
) -> Facility | None:
    """商社の開業（参入と撤退の段）。

    - 島に商社が1つもなくなったら（倒産など）、trade.entry_turns ターン後に、経済体制の既定の
      持ち主で1つ開く（民営で資産を持つ層が出資できなければ待つ）。
    - 民営の商社: 島の商社の扱った重さが、直近 trade.entry_turns ターンの平均で上限の
      trade.entry_threshold を超え、商社の数が上限に届いておらず、空いたオフィス街のマスがあり、
      資産を持つ層が出資できれば、もう1つ開く。計画経済の島では開かない。
    """
    from hakoniwa_core.trade import traders  # traders.py がこのモジュールを読まないように

    if not started(island) or port_of(island, config) is None:
        return None
    kind = config.get("trade.trader_facility")
    current = [f for f in island.facilities if f.kind == kind]
    if not current:
        return _reopen(island, defs, config, turn)
    island_state(island).pop("trader_gone", None)
    limit = defs.facilities[kind].get("max_per_island")
    if planned or (limit is not None and len(current) >= limit):
        return None
    if any(not f.built for f in current):
        return None
    busy = [traders.volume_share(config, f) for f in traders_of(island, config)]
    full = [len(traders.state_of(f).get("volume", [])) for f in traders_of(island, config)]
    if not busy or min(full) < config.get("trade.entry_turns"):
        return None
    if sum(busy) / len(busy) <= config.get("trade.entry_threshold"):
        return None
    if not owners_can_fund(island, config):
        return None
    spot = _free_office(island, defs, kind)
    if spot is None:
        return None
    f = open_trader(island, defs, config, spot, OWNER_PRIVATE)
    if f is not None:
        island.log.append(LogEntry("trader_opened", {"x": spot[0], "y": spot[1]}))
    return f


def _reopen(island: IslandState, defs: Defs, config: GameConfig, turn: int) -> Facility | None:
    """商社がなくなった島に、trade.entry_turns ターン後に既定の持ち主で商社を開く。"""
    state = island_state(island)
    gone = state.setdefault("trader_gone", turn)
    if turn - gone < config.get("trade.entry_turns"):
        return None
    kind = config.get("trade.trader_facility")
    owner = _owner(defs, island, kind)
    if owner == OWNER_PRIVATE and not owners_can_fund(island, config):
        return None
    spot = _office_spot(island, defs, config)
    if spot is None:
        return None
    f = open_trader(island, defs, config, spot, owner)
    if f is not None:
        state.pop("trader_gone", None)
        key = "trader_opened" if owner == OWNER_PRIVATE else "trader_opened_state"
        island.log.append(LogEntry(key, {"x": spot[0], "y": spot[1]}))
    return f


def _free_office(island: IslandState, defs: Defs, kind: str) -> tuple[int, int] | None:
    occupied = island.facilities_by_tile()
    for tile in island.tiles:
        if tile.zone == ZONE_OFFICE and (tile.x, tile.y) not in occupied:
            if fac.placement_problem(defs, island, kind, tile.x, tile.y) is None:
                return (tile.x, tile.y)
    return None
