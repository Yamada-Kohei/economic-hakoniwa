"""お金と経済の始まり。建国と、フェーズ1の世界の移し替えの両方がここを使います。

1. 初めの値段をレシピから解き（economy/prices.py）、政策を game.yaml の既定にする。
2. 家計の層を作り、首都の近くにオフィス街を指定して銀行を置き、運送業（マスを持たない）を置く。
3. 施設ごとに賃金・希望価格・操業目標・設備の額（建設費）を決める。
4. お金を仕訳の列として作る（最初のターンから帳簿の確かめが通るように）:
   1. 政府が建国の国債を出し、中央銀行が引き受ける（国庫が増える）。
   2. 政府が家計の各層に建国の給付を払う（家計の預金と銀行の準備預金が増える）。
   3. 独立採算の施設と銀行に、持ち主が出資する（民間は資産を持つ層の預金から、国営は国庫から）。
   4. 銀行が中央銀行から国債を買う（準備預金と引き換え）。
"""

from __future__ import annotations

from hakoniwa_core import facilities as fac
from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import (
    GENERAL,
    OWNERS,
    Payer,
    default_accounting,
    facility_entity,
    funds,
    household,
    is_bank,
    is_self_supporting,
    owner_entity,
)
from hakoniwa_core.economy import freight as freight_rates
from hakoniwa_core.economy.debt import underwrite
from hakoniwa_core.economy.households import init_layers
from hakoniwa_core.economy.prices import construction_cost, initial_prices
from hakoniwa_core.economy.stats import BASE_CPI
from hakoniwa_core.geo import within
from hakoniwa_core.naming import assign_serial
from hakoniwa_core.state import (
    ACCOUNTING_SELF,
    ACCOUNTING_TREASURY,
    OWNER_PRIVATE,
    OWNER_STATE,
    ZONE_NONE,
    ZONE_OFFICE,
    Facility,
    IslandState,
    LogEntry,
    Policy,
)
from hakoniwa_core.units import BASIS, money_of, part, round_money, to_basis


def default_policy(defs: Defs, config: GameConfig, population: int) -> Policy:
    """建国のときの政策。年の予算は fiscal.budget（住民1人あたり）× 人口。"""
    fiscal = config.get("fiscal")
    terms = config.get("bonds.auto_terms")
    return Policy(
        income_tax=to_basis(fiscal["income_tax"]),
        corporate_tax=to_basis(fiscal["corporate_tax"]),
        consumption_tax=to_basis(fiscal["consumption_tax"]),
        budget={k: round_money(v * population) for k, v in fiscal["budget"].items()},
        minimum_wage=int(fiscal["minimum_wage"]),
        unemployment_benefit=to_basis(fiscal["unemployment_benefit"]),
        price_cap={},
        policy_rate=to_basis(config.get("central_bank.policy_rate")),
        deposit_rate=to_basis(config.get("banking.deposit_rate")),
        bond_terms={name: to_basis(share) for name, share in terms.items()},
    )


def start_economy(
    island: IslandState, defs: Defs, config: GameConfig, turn: int, migrated: bool = False
) -> None:
    """島のお金の経済を始める。すでに始まっていれば何もしない。"""
    if island.has_economy:
        return
    prices = initial_prices(defs, config, island.resources)
    wage = config.get("labor.initial_wage")
    economy = island.economy
    economy.started = turn
    economy.prices = dict(prices)
    economy.price_history = {g: [p] for g, p in sorted(prices.items())}
    economy.cpi_history = [BASE_CPI]
    economy.average_wage = wage
    economy.wage_history = [wage]
    economy.freight = {"surcharge": BASIS, "revenue": 0}
    economy.housing = {"want": {}, "stock": {}}
    economy.happiness_blend = config.get("population.blend_turns") if migrated else 0
    island.policy = default_policy(defs, config, sum(t.population for t in island.tiles))
    init_layers(island, defs, config)
    bank = _place_bank(island, defs, config)
    _place_transport(island, defs, config)
    for f in island.facilities:
        if defs.facilities[f.kind].get("public_service"):
            f.accounting = ACCOUNTING_TREASURY  # フェーズ1の島の中央省庁など
        init_facility(island, defs, config, f, prices, wage)
    economy.banks = [bank.id]
    _open_money(island, defs, config, bank, prices, wage, turn)
    island.log.append(LogEntry("economy_started", {}))


def init_facility(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    f: Facility,
    prices: dict[str, int],
    wage: int,
) -> None:
    """施設の経済の状態（賃金・希望価格・操業目標・設備の額）を決める。"""
    if fac.is_housing(config, f):
        return
    recipe = fac.recipe_of(defs, f)
    cost = round_money(construction_cost(defs, f.kind, prices, wage))
    if not f.built:
        total = defs.facilities[f.kind]["construction"]["turns"] * fac.FULL
        f.economy = {"capital": cost * min(f.progress, total) // total}
        return
    f.economy = {
        "wage": wage,
        "target": fac.FULL,
        "ask": {g: prices.get(g, 0) for g in sorted(recipe["outputs"])},
        "capital": cost,
        "depreciated": 0,
        "staff": {},
    }


def operating_cost(
    defs: Defs,
    config: GameConfig,
    f: Facility,
    prices: dict[str, int],
    wage: int,
    freight: int,
) -> int:
    """全力で1ターン動いたときの支払い（賃金・投入財・電気代・運賃）。"""
    power = config.get("power.good")
    recipe = fac.recipe_of(defs, f)
    total = wage * fac.workers_needed(defs, f.kind)
    for good, qty in recipe["inputs"].items():
        amount = money_of(prices.get(good, 0), qty)
        total += amount if good == power else amount + part(amount, freight)
    for good, qty in recipe["outputs"].items():
        if good != power:
            total += part(money_of(prices.get(good, 0), qty), freight)
    return total


def next_payments(island: IslandState, defs: Defs, config: GameConfig, f: Facility) -> int:
    """独立採算の施設が次のターンに払う見込み（操業目標の分の支払い。運送業は雇いたい人数の賃金）。"""
    wage = f.economy.get("wage", 0)
    rate = freight_rates.facility_rates(island, defs, config).get(f.id, 0)
    cost = operating_cost(defs, config, f, island.economy.prices, wage, rate)
    return max(
        fac.scale(cost, f.economy.get("target", fac.FULL)), f.economy.get("wanted", 0) * wage
    )


# --- 銀行と運送業 -----------------------------------------------------------------


def _place_bank(island: IslandState, defs: Defs, config: GameConfig) -> Facility:
    """首都の近くの空いたマスをオフィス街にして、銀行を置く。

    首都のない島（フェーズ0の世界から残っている島など）は、島の真ん中に近いマスに置く。
    """
    kind = config.get("banking.facility")
    center = island.capital or (island.size // 2, island.size // 2)
    terrains = defs.facilities[kind]["terrain"]
    occupied = island.facilities_by_tile()
    spots = [
        pos
        for pos in within(island, center, island.size)
        if island.tile(*pos).terrain in terrains
        and pos not in occupied
        and island.tile(*pos).zone in (ZONE_NONE, ZONE_OFFICE)
    ][: config.get("banking.office_tiles")]
    for pos in spots:
        island.tile(*pos).zone = ZONE_OFFICE
    x, y = spots[0] if spots else (-1, -1)
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    owner = regime.get("bank_owner", OWNER_STATE)
    if owner == OWNER_PRIVATE and OWNERS not in island.economy.households:
        owner = OWNER_STATE
    bank = Facility(
        id=island.next_facility_id(),
        kind=kind,
        x=x,
        y=y,
        owner=owner,
        accounting=ACCOUNTING_SELF,
        built=True,
    )
    assign_serial(island, bank)
    island.facilities.append(bank)
    return bank


def _place_transport(island: IslandState, defs: Defs, config: GameConfig) -> Facility:
    kind = config.get("freight.facility")
    regime = defs.regimes.get(island.regime.get("economy", ""), {})
    state = regime.get("owner", OWNER_STATE) == OWNER_STATE or kind in regime.get("state_owned", ())
    f = Facility(
        id=island.next_facility_id(),
        kind=kind,
        x=-1,
        y=-1,
        owner=OWNER_STATE if state else OWNER_PRIVATE,
        accounting=default_accounting(defs, island, kind, OWNER_STATE if state else OWNER_PRIVATE),
        built=True,
    )
    assign_serial(island, f)
    island.facilities.append(f)
    return f


# --- お金 -------------------------------------------------------------------------


def _open_money(
    island: IslandState,
    defs: Defs,
    config: GameConfig,
    bank_facility: Facility,
    prices: dict[str, int],
    wage: int,
    turn: int,
) -> None:
    settings = config.get("founding.money")
    households = island.economy.households
    population = sum(t.population for t in island.tiles)
    bank = ledger.bank_entity(bank_facility.id)
    rates, _ = freight_rates.compute(island, defs, config)
    turns = config.get("firms.operating_turns")

    funds: dict[int, int] = {}
    for f in island.facilities:
        if is_self_supporting(f) and not is_bank(config, f) and fac.on_map(f):
            funds[f.id] = turns * operating_cost(defs, config, f, prices, wage, rates[f.id])
        elif is_self_supporting(f) and not is_bank(config, f):
            funds[f.id] = 0
    general = settings["general_deposit"] * households[GENERAL]["persons"]
    owners_keep = settings["owner_deposit"] * households.get(OWNERS, {}).get("persons", 0)
    deposits = general + owners_keep + sum(funds.values())
    bank_capital = part(deposits, to_basis(config.get("banking.capital_ratio")))
    bank_bonds = part(deposits, to_basis(config.get("banking.bond_ratio")))
    by_owner: dict[str, int] = {}
    for f in island.facilities:
        if f.id in funds:
            by_owner[owner_entity(f)] = by_owner.get(owner_entity(f), 0) + funds[f.id]
    bank_owner = owner_entity(bank_facility)
    by_owner[bank_owner] = by_owner.get(bank_owner, 0) + bank_capital
    owners_grant = owners_keep + by_owner.get(household(OWNERS), 0)
    treasury = settings["treasury"] * population
    face = general + owners_grant + by_owner.get(ledger.GOV, 0) + treasury

    term = defs.bond_terms()[settings["bond"]]
    rate = island.policy.policy_rate + to_basis(term["premium"])
    bond = underwrite(island, face, term["turns"], rate, turn)
    if bond is None:  # 住民も施設もない島（フェーズ0の世界から残っている島など）: 配るお金がない
        return
    item = ledger.bond_item(bond.id)

    ledger.pay(island, ledger.GOV, household(GENERAL), general, "grant")
    if OWNERS in households:
        ledger.pay(island, ledger.GOV, household(OWNERS), owners_grant, "grant")
    for f in sorted(island.facilities, key=lambda f: f.id):
        if f.id in funds:
            invest(island, owner_entity(f), facility_entity(f.id), funds[f.id])
    invest(island, bank_owner, bank, bank_capital)

    ledger.transfer(island, item, ledger.GOV, ledger.CB, bank, bank_bonds, "bond_purchase")
    ledger.pay(island, bank, ledger.CB, bank_bonds, "bond_purchase")


def fund_facility(
    island: IslandState, defs: Defs, config: GameConfig, f: Facility, payer: Payer
) -> int:
    """国営の独立採算の施設に、政府が運転資金（firms.operating_turns ターン分の支払い）を出資する。

    工事が済んだときと、会計の形を国庫直轄から独立採算に変えたときに使う。出資した額を返す。
    """
    rate = freight_rates.rate_of(island, defs, config, f)
    prices = dict(island.economy.prices)
    wage = f.economy.get("wage") or island.economy.average_wage
    want = config.get("firms.operating_turns") * operating_cost(defs, config, f, prices, wage, rate)
    amount = min(want, funds.available(island, payer))
    if amount > 0:
        funds.pay(island, payer, facility_entity(f.id), amount, "capital_injection")
        ledger.create(
            island,
            owner_entity(f),
            ledger.EQUITY,
            facility_entity(f.id),
            amount,
            "capital_injection",
        )
    return amount


def invest(island: IslandState, owner: str, target: str, amount: int) -> None:
    """持ち主 owner が target（施設・銀行）に出資する（お金を払い、出資の持ち物を作る）。"""
    if amount <= 0:
        return
    ledger.pay(island, owner, target, amount, "capital_injection")
    ledger.create(island, owner, ledger.EQUITY, target, amount, "capital_injection")
