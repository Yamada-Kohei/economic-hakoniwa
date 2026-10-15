"""外国の国債（フェーズ3-3）: 資本取引と、政府・中央銀行・市中銀行の外国の国債の売り買い。

国債の段（modules/bonds。3-3 から世界の段）が、発行した島ごとに利払いと償還、売買の市場、
入札を行うとき、外国の持ち手と買い手が「その島の通貨で」加わります（外貨建ての国債は入れない）。

持ち方: 島 H の主体 e が持つ島 X の国債は、X の帳簿の勘定 ``nr:<H>:<e>/bond:<番号>/gov`` です
（非居住者の預金と同じ名前の持ち手）。H の側は、主体ごと・発行した島ごとの合計を
``island.ext["trade"]["fx_bonds"]``（主体 → 島の番号の文字列 → [額面, 帳簿の値打ち, 時価]）に
持ちます。帳簿の値打ちは、買ったときに払った外貨の帳簿の値打ち（自国通貨）の合計で、償還・売却で
受け取った外貨にそのまま引き継ぎます（その外貨を売ったときに、国債と為替の損益をまとめて実現損益に
する。money.py）。時価は国債の段の終わりに、X の利回りの曲線で付け直します（X の通貨）。
世界の確かめ（reconcile）は、H の合計の額面と X の帳簿の nr:H:e の国債の額面が合うかを見ます。

利子は X の政府から H の持ち手の外貨の持ち高に払い（仕訳の種類 bond_interest。所得収支）、
市中銀行は損益の利子に、中央銀行は利益に入れます。債務不履行の切り下げは、外国の持ち手も同じ
割合で負います（市中銀行は帳簿の値打ちの減った分を為替の実現損益に入れる）。

だれが買えるか:
- 持ち手の島: 資本取引が「禁止」なら誰も新しく買えない。「政府と中央銀行だけ」なら市中銀行は
  買えない（policy.may_hold_foreign）。
- 発行した島: 「禁止」なら外国の誰にも売らない。「政府と中央銀行だけ」なら外国の政府と中央銀行
  だけに売る。
- どちらかの国が相手の国をすべての財で禁輸していれば、売り買いしない。
売ることと、持っている国債の利払いと償還は、制限を変えても続きます。

求める利回り（市中銀行）: 発行した島の bond_yield に、その通貨の前の 12 ターンの値下がりの率
（持ち手の島から見たレートの下がり方。上がったときは 0）を足した利回りより低くは買いません。

注文:
- 政府と中央銀行: 命令 set_foreign_bonds の額面を毎ターン、指値なしで買う（売買の市場で売り手が
  足りなければ、入札の一番低い段で）・売る。買うのに使えるのは持っている外貨だけです（政府は
  貿易の段で要る外貨を買う。中央銀行は外貨準備だけを使う）。
- 市中銀行（資本取引が自由な島）: 利回りの曲線の平均から値下がりの率を引いた値が、自国の平均より
  banking.foreign_margin 以上高い通貨のうち一番高い通貨を選ぶ。外国の資産（外貨と外国の国債）が
  預金の banking.foreign_share になるまで、1ターンに預金の banking.foreign_step（準備預金の余りの
  半分まで）ずつ外貨を買い（貿易の段。bank_needs）、その外貨で求める利回りで買う。当てのない外貨
  は売る。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import Payer, banks, books, debt
from hakoniwa_core.state import IslandState, WorldState
from hakoniwa_core.trade import balance, island_state, policy
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import BASIS, part, to_basis

KEY = "fx_bonds"  # island.ext["trade"]: 主体 → 発行した島 → [額面, 帳簿の値打ち, 時価]
HISTORY = "rate_history"  # island.ext["trade"]: 通貨 → 直近 13 ターンのレート
YEAR = 12
BANKS = "banks"  # 統計の持ち手の分け方（市中銀行の合計）


# --- 持ち高 -------------------------------------------------------------------------


def table(island: IslandState) -> dict[str, dict[str, list[int]]]:
    return island_state(island).setdefault(KEY, {})


def held(island: IslandState, entity: str, issuer: int) -> list[int]:
    """主体 entity が持つ島 issuer の国債の [額面, 帳簿の値打ち, 時価]。"""
    return list(table(island).get(entity, {}).get(str(issuer), [0, 0, 0]))


def _add(island: IslandState, entity: str, issuer: int, face: int, cost: int) -> None:
    mine = table(island).setdefault(entity, {})
    entry = mine.setdefault(str(issuer), [0, 0, 0])
    entry[0] += face
    entry[1] += cost
    if entry[0] <= 0:
        del mine[str(issuer)]
        if not mine:
            del table(island)[entity]


def _release(island: IslandState, entity: str, issuer: int, face: int) -> int:
    """額面 face を手放した分の帳簿の値打ち（平均で）を持ち高から引いて返す。"""
    have, cost, _ = held(island, entity, issuer)
    if face <= 0 or have <= 0:
        return 0
    gone = cost if face >= have else cost * face // have
    _add(island, entity, issuer, -face, -gone)
    return gone


def holder_name(island_id: int, entity: str) -> str:
    """島 island_id の主体 entity を、発行した島の帳簿で呼ぶ名前（非居住者の持ち手）。"""
    return ledger.nonresident(island_id, entity)


def parse_holder(name: str) -> tuple[int, str] | None:
    """非居住者の持ち手の名前なら (島, 主体)、そうでなければ None。"""
    if not ledger.is_nonresident(name):
        return None
    _, island_id, entity = name.split(":", 2)
    return int(island_id), entity


def value_of(island: IslandState, entity: str | None = None) -> int:
    """外国の国債の時価の合計（自国通貨）。entity を省くと島の主体すべて、BANKS なら市中銀行の
    合計。"""
    total = 0
    for who, issuers in table(island).items():
        if entity is not None and who != entity and not (entity == BANKS and ledger.is_bank(who)):
            continue
        for key, (_, _, value) in issuers.items():
            total += fxrates.to_own(island, int(key), value)
    return total


def foreign_assets(island: IslandState, entity: str) -> int:
    """主体 entity の外国の資産（外貨と外国の国債の、今のレートでの自国通貨の値打ち）。"""
    total = value_of(island, entity)
    for key, (amount, _) in fxmoney.holdings(island).get(entity, {}).items():
        total += fxrates.to_own(island, int(key), amount)
    return total


# --- レートの記録と求める利回り -----------------------------------------------------------


def keep_rates(island: IslandState) -> None:
    """そのターンのレートを通貨ごとの記録に足す（直近 13 ターン。貿易の段の終わり）。"""
    history = island_state(island).setdefault(HISTORY, {})
    now = fxrates.rates(island)
    for key in sorted(set(history) | set(now)):
        if key not in now:
            del history[key]
            continue
        history[key] = [*history.get(key, []), now[key]][-(YEAR + 1) :]


def depreciation(island: IslandState, currency: int) -> float:
    """通貨 currency の前の 12 ターンの値下がりの率（島から見たレートの下がり方。上がったときは 0）
    。"""
    history = island_state(island).get(HISTORY, {}).get(str(currency), [])
    if len(history) < 2 or history[0] <= 0:
        return 0.0
    return max(0.0, (history[0] - history[-1]) / history[0])


def mean_yield(island: IslandState) -> int | None:
    """利回りの曲線の区分の平均（1万分の1。まだなければ None）。"""
    yields = island.economy.finance.get("yields") or []
    return sum(yields) // len(yields) if yields else None


# --- だれが買えるか -------------------------------------------------------------------


def accepts(issuer: IslandState, defs: Defs, entity: str) -> bool:
    """島 issuer が、外国の主体 entity（gov・cb・銀行）に自国の国債を売るか。"""
    mode = policy.capital_controls(issuer, defs)
    if mode == policy.CLOSED:
        return False
    return mode == policy.FREE or entity in policy.HOLDERS


def may_buy(
    world: WorldState, defs: Defs, holder: IslandState, entity: str, issuer: IslandState
) -> bool:
    """島 holder の主体 entity が、島 issuer の国債を新しく買えるか。"""
    return (
        issuer.id != holder.id
        and issuer.has_economy
        and policy.may_hold_foreign(holder, defs, entity)
        and accepts(issuer, defs, entity)
        and not policy.blocked(world, holder.id, issuer.id, "")
    )


# --- 市中銀行の外貨（貿易の段） -------------------------------------------------------


def bank_pick(
    world: WorldState, defs: Defs, config: GameConfig, island: IslandState, turn: int
) -> int | None:
    """市中銀行が外国の国債を買う通貨（値下がりの率を引いた利回りが一番引き合う通貨。
    なければ None）。"""
    if not policy.may_hold_foreign(island, defs, ledger.bank_entity(0)):
        return None
    home = mean_yield(island)
    if home is None:
        return None
    margin = to_basis(config.get("banking.foreign_margin"))
    best, best_edge = None, 0
    for key in sorted(fxrates.rates(island), key=int):
        other = world.islands.get(int(key))
        if other is None or not may_buy(world, defs, island, ledger.bank_entity(0), other):
            continue
        if debt.market_closed(other, config, turn):
            continue
        there = mean_yield(other)
        if there is None:
            continue
        edge = there - to_basis(depreciation(island, other.id)) - home
        if edge >= margin and (best is None or edge > best_edge):
            best, best_edge = other.id, edge
    return best


def bank_needs(
    world: WorldState, defs: Defs, config: GameConfig, island: IslandState, turn: int
) -> dict[str, dict[int, int]]:
    """市中銀行がこのターンに持っていたい外貨（銀行 → 通貨 → 量）。持っている外貨のうちこれを超える
    分は、貿易の段で売る。"""
    pick = bank_pick(world, defs, config, island, turn)
    if pick is None:
        return {}
    share = to_basis(config.get("banking.foreign_share"))
    step = to_basis(config.get("banking.foreign_step"))
    floor = banks.regulatory(config) + config.get("banking.risk_buffer")
    out: dict[str, dict[int, int]] = {}
    for bank in banks.active_banks(island):
        if banks.capital_ratio(island, config, bank, turn) < floor:
            continue
        deposits = banks.deposits_of(island, bank)
        room = part(deposits, share) - foreign_assets(island, bank)
        have = fxmoney.holding(island, bank, pick)
        if room <= 0:
            want = max(0, have + fxrates.to_foreign(island, pick, room))
        else:
            spare = part(max(0, banks.excess_reserves(island, config, bank)), BASIS // 2)
            add = min(room, part(deposits, step), spare)
            want = have + fxrates.to_foreign(island, pick, add)
        if want > 0:
            out[bank] = {pick: want}
    return out


# --- 国債の段の注文 ---------------------------------------------------------------------


@dataclass
class Bid:
    """外国の持ち手の、1つの発行した島への注文（国債の段の1ターン分）。"""

    holder: int  # 持ち手の島
    entity: str  # 持ち手の主体（gov・cb・bank:<番号>）
    budget: int = 0  # 買いに使える外貨（発行した島の通貨）
    sell: int = 0  # 売る額面
    premium: int | None = None  # 求める利回りの上乗せ（値下がりの率。None は指値なしの買い）
    target: int = 0  # 命令の額面（政府と中央銀行。ログに出す）
    done: int = 0  # 買った・売った額面

    @property
    def name(self) -> str:
        return holder_name(self.holder, self.entity)


def orders(world: WorldState, ctx: StageContext) -> dict[int, list[Bid]]:
    """発行した島ごとの、外国の持ち手の注文。破綻した銀行の外国の資産は、先に政府に移す。"""
    defs, config = ctx.defs, ctx.config
    out: dict[int, list[Bid]] = {}
    for h in sorted(world.islands):  # 番号の順（破綻した銀行の分の付け替えの仕訳の順を決める）
        holder = world.islands[h]
        if not holder.has_economy:
            continue
        _orphans(world, holder)
        for entity in policy.HOLDERS:
            for key, amount in sorted(holder.policy.foreign_bonds.get(entity, {}).items()):
                issuer = world.islands.get(int(key))
                if issuer is None or issuer.id == h or not amount:
                    continue
                if amount > 0:
                    if not may_buy(world, defs, holder, entity, issuer):
                        continue
                    budget = min(amount, fxmoney.holding(holder, entity, issuer.id))
                    bid = Bid(h, entity, budget=budget, target=amount)
                else:
                    face = min(-amount, held(holder, entity, issuer.id)[0])
                    bid = Bid(h, entity, sell=face, target=-amount)
                out.setdefault(issuer.id, []).append(bid)
        for bank in banks.active_banks(holder):
            for key, (amount, _) in sorted(fxmoney.holdings(holder).get(bank, {}).items()):
                issuer = world.islands.get(int(key))
                if amount <= 0 or issuer is None or not may_buy(world, defs, holder, bank, issuer):
                    continue
                if debt.market_closed(issuer, config, ctx.turn):
                    continue
                premium = to_basis(depreciation(holder, issuer.id))
                out.setdefault(issuer.id, []).append(Bid(h, bank, budget=amount, premium=premium))
    return out


def _orphans(world: WorldState, holder: IslandState) -> None:
    """破綻して営業していない銀行の外貨と外国の国債を、政府に移す（清算で国が引き取る）。"""
    active = set(banks.active_banks(holder))
    gone = {e for e in [*fxmoney.holdings(holder), *table(holder)] if ledger.is_bank(e)} - active
    for bank in sorted(gone):
        for key, (amount, value) in sorted(fxmoney.holdings(holder).get(bank, {}).items()):
            fxmoney.transfer(
                world,
                int(key),
                (holder.id, bank),
                (holder.id, ledger.GOV),
                amount,
                "bank_transfer",
                value,
            )
        for key, (face, cost, _) in sorted(table(holder).get(bank, {}).items()):
            issuer = world.islands[int(key)]
            for name, amount in sorted(
                ledger.holdings(issuer, holder_name(holder.id, bank), "bond:").items()
            ):
                item = ledger.parse(name)[1]
                ledger.transfer(
                    issuer,
                    item,
                    ledger.GOV,
                    holder_name(holder.id, bank),
                    holder_name(holder.id, ledger.GOV),
                    amount,
                    "bank_transfer",
                )
            _add(holder, bank, int(key), -face, -cost)
            _add(holder, ledger.GOV, int(key), face, cost)


# --- 国債の段から呼ぶお金の動き ------------------------------------------------------------


def _values(world: WorldState, issuer: IslandState, holder: int, amount: int) -> dict[int, int]:
    """国際収支に入れる、島ごとのその島の通貨の値（発行した島は amount、持ち手の島は今の
    レートで直した値）。"""
    return {issuer.id: amount, holder: fxrates.to_own(world.islands[holder], issuer.id, amount)}


def pay_interest(
    world: WorldState, issuer: IslandState, payer: Payer, name: str, amount: int
) -> None:
    """発行した島の政府が、外国の持ち手 name に国債の利子を払う（外貨の持ち高に入る）。"""
    holder_id, entity = parse_holder(name) or (0, "")
    holder = world.islands[holder_id]
    values = _values(world, issuer, holder_id, amount)
    value = values[holder_id]
    fxmoney.transfer(
        world,
        issuer.id,
        (issuer.id, ledger.GOV),
        (holder_id, entity),
        amount,
        "bond_interest",
        value,
        payer=payer,
        values=values,
    )
    if ledger.is_bank(entity):
        books.add(holder, banks.bank_number(entity), books.INTEREST, value)
    elif entity == ledger.CB:
        books.add_sector(holder, books.CENTRAL_BANK, "interest", value)


def redeem(
    world: WorldState, issuer: IslandState, payer: Payer, name: str, number: int, face: int
) -> None:
    """発行した島の政府が、外国の持ち手 name の銘柄 number を額面 face で償還する。"""
    holder_id, entity = parse_holder(name) or (0, "")
    holder = world.islands[holder_id]
    values = _values(world, issuer, holder_id, face)
    cost = _release(holder, entity, issuer.id, face)
    fxmoney.transfer(
        world,
        issuer.id,
        (issuer.id, ledger.GOV),
        (holder_id, entity),
        face,
        "bond_redemption",
        cost,
        payer=payer,
        values=values,
    )
    ledger.cancel(issuer, name, ledger.bond_item(number), ledger.GOV, face, "bond_redemption")
    balance.bonds(world, issuer.id, holder_id, values, -1)


def write_down(world: WorldState, issuer: IslandState, name: str, cut: int) -> None:
    """債務不履行で、外国の持ち手 name の額面が cut だけ切り下げられた（勘定は呼ぶ側が消す）。
    市中銀行は、帳簿の値打ちの減った分を為替の実現損益（損）に入れる。"""
    holder_id, entity = parse_holder(name) or (0, "")
    holder = world.islands[holder_id]
    loss = _release(holder, entity, issuer.id, cut)
    if ledger.is_bank(entity) and loss:
        books.add(holder, banks.bank_number(entity), books.FX_REALIZED, -loss)


def buy_new(world: WorldState, issuer: IslandState, name: str, number: int, amount: int) -> None:
    """外国の持ち手 name が、入札で銘柄 number を額面 amount だけ額面の値段で買う。"""
    holder_id, entity = parse_holder(name) or (0, "")
    holder = world.islands[holder_id]
    values = _values(world, issuer, holder_id, amount)
    ledger.create(issuer, name, ledger.bond_item(number), ledger.GOV, amount, "bond_issue")
    spent = fxmoney.transfer(
        world,
        issuer.id,
        (holder_id, entity),
        (issuer.id, ledger.GOV),
        amount,
        "bond_issue",
        values=values,
    )
    _add(holder, entity, issuer.id, amount, spent)
    balance.bonds(world, issuer.id, holder_id, values, 1)


def trade_payment(
    world: WorldState, issuer: IslandState, seller: str, buyer: str, face: int, amount: int
) -> None:
    """売買の市場で、売り手が買い手に額面 face を渡し、買い手が代金 amount を払う（どちらかが外国の
    持ち手のとき。銘柄の勘定は呼ぶ側が動かす）。"""
    sold, bought = parse_holder(seller), parse_holder(buyer)
    source = bought or (issuer.id, buyer)
    target = sold or (issuer.id, seller)
    values = {issuer.id: amount}
    for side in (sold, bought):
        if side is not None:
            values[side[0]] = fxrates.to_own(world.islands[side[0]], issuer.id, amount)
    cost = _release(world.islands[sold[0]], sold[1], issuer.id, face) if sold else 0
    spent = fxmoney.transfer(
        world, issuer.id, source, target, amount, "bond_trade", cost, values=values
    )
    if bought is not None:
        _add(world.islands[bought[0]], bought[1], issuer.id, face, spent)
        balance.bonds(world, issuer.id, bought[0], values, 1)
    if sold is not None:
        balance.bonds(world, issuer.id, sold[0], values, -1)


# --- 時価と統計（国債の段の終わり） ----------------------------------------------------------


def revalue(world: WorldState, config: GameConfig, turn: int) -> None:
    """外国の持ち手の国債の時価を、発行した島の利回りの曲線で付け直し、統計を取る。"""
    terms = banks.bond_terms(config)
    for h in world.island_ids():
        holder = world.islands[h]
        for entity, issuers in table(holder).items():
            for key, entry in issuers.items():
                issuer = world.islands.get(int(key))
                if issuer is None:
                    continue
                entry[2] = debt.market_value(issuer, holder_name(h, entity), turn, terms)
    for h in world.island_ids():
        island = world.islands[h]
        if not island.has_economy:
            continue
        stats = island.stats
        for who in (ledger.GOV, ledger.CB, BANKS):
            stats[f"foreign_bonds.{who}"] = value_of(island, who)
        reserves = sum(foreign_assets(island, who) for who in (ledger.GOV, ledger.CB))
        stats["fx.reserves"] = reserves
        stats["bonds.foreign"] = sum(_foreign_faces(island).values())


def _foreign_faces(issuer: IslandState) -> dict[str, int]:
    """発行した島の帳簿の、外国の持ち手ごとの国債の額面の合計。"""
    out: dict[str, int] = {}
    for name, value in issuer.accounts.items():
        holder, item, owner = ledger.parse(name)
        if item.startswith("bond:") and owner == ledger.GOV and ledger.is_nonresident(holder):
            out[holder] = out.get(holder, 0) + value
    return {k: v for k, v in out.items() if v}


def reconcile(world: WorldState) -> list[str]:
    """世界の確かめ: 持ち手の島の外国の国債の額面と、発行した島の帳簿の nr の国債の額面が合うか。"""
    problems: list[str] = []
    held_faces: dict[tuple[int, str], int] = {}
    for h in world.island_ids():
        for entity, issuers in table(world.islands[h]).items():
            for key, (face, _, _) in issuers.items():
                held_faces[(int(key), holder_name(h, entity))] = face
    booked: dict[tuple[int, str], int] = {}
    for x in world.island_ids():
        for name, face in _foreign_faces(world.islands[x]).items():
            booked[(x, name)] = face
    for key in sorted(set(held_faces) | set(booked)):
        if held_faces.get(key, 0) != booked.get(key, 0):
            problems.append(
                f"{key[1]} の持つ島 {key[0]} の国債の額面 {held_faces.get(key, 0)} が、"
                f"島 {key[0]} の帳簿の {booked.get(key, 0)} と合いません"
            )
    return problems


def view(world: WorldState, island: IslandState) -> list[dict[str, Any]]:
    """画面向け: 島の主体が持つ外国の国債（発行した島・持ち手・額面・時価・自国通貨の値打ち）。"""
    rows = []
    for entity, issuers in sorted(table(island).items()):
        for key, (face, _, value) in sorted(issuers.items(), key=lambda kv: int(kv[0])):
            rows.append(
                {
                    "issuer": int(key),
                    "holder": entity,
                    "face": face,
                    "value": value,
                    "own": fxrates.to_own(island, int(key), value),
                }
            )
    return rows
