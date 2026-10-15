"""中央銀行どうしの融通（スワップ。フェーズ3-3 の続き）。

為替の受け身の注文（fx.dealer_orders）を出すのは、その島の中央銀行だけです。中央銀行は、受け身の
売りに要る外貨 Z が手元に足りないとき、Z を借ります。

- 貸し手は、まず同じ貿易圏で Z を決済通貨にしている加盟国の中央銀行のうち、手元に置く分（自分の
  島の主体が Z で払い・受け取った額の平均の fx.swap_keep_turns ターン分）より多く Z を持ち、Z を
  借りていない国で、余りに比例して貸します。足りなければ、Z を発行した国の中央銀行が Z を新しく
  作って貸します（最後の手）。黒字の国に入った決済通貨が、赤字の国の輸入に回ります。
- 借りられるのは、借りている額の合計（自国通貨に直す）が年の GDP の fx.swap_quota までです。発行
  した国の中央銀行が新しく作って貸すのは、合わせてその国の年の GDP の fx.swap_issuer_share までで
  す。使い切ると受け身の売りが止まり、その国の通貨は安くなります。
- 利子は、Z を発行した国の政策金利に fx.swap_spread を足した年率で毎ターン（ledger.interest）、
  借り手の中央銀行が持つ Z で貸し手に払います（仕訳の種類 swap_interest。所得収支）。払えない分は
  借りた額に足します。
- 借り手の中央銀行が手元に置く分（同じ平均の 1 ターン分）より多く Z を持ったら、発行した国から先に
  返し、残りを貸し手の国に貸した額に比例して返します。

持ち方: 借りた側は ``island.ext["trade"]["swap_owed"]``（通貨 → 貸し手の島 → 額）、貸した側は
``island.ext["trade"]["swap_lent"]``（通貨 → 借り手の島 → 額）。発行した国が作って貸した分は、
貸し手の島が通貨と同じ番号になります。世界の確かめ（reconcile）は両方が合うかを見ます。貸している
外貨は外国の資産（abroad.foreign_assets）に入れ、受け身の買いの上限（abroad.reserve_room）は借りて
いる額を引いた正味で数えます。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.economy import debt
from hakoniwa_core.state import IslandState, WorldState
from hakoniwa_core.trade import balance, blocs, island_state
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import money as fxmoney
from hakoniwa_core.units import part, to_basis

OWED = "swap_owed"  # island.ext["trade"]: 通貨 → 貸し手の島 → 借りている額
LENT = "swap_lent"  # island.ext["trade"]: 通貨 → 借り手の島 → 貸している額
KIND = "fx_swap"  # 貸し借りと返済の仕訳の種類（金融収支）
INTEREST = "swap_interest"  # 利子の仕訳の種類（所得収支）


def owed(island: IslandState) -> dict[str, dict[str, int]]:
    return island_state(island).setdefault(OWED, {})


def lent(island: IslandState) -> dict[str, dict[str, int]]:
    return island_state(island).setdefault(LENT, {})


def debt_in(island: IslandState, currency: int) -> int:
    """通貨 currency で借りている額の合計。"""
    return sum(owed(island).get(str(currency), {}).values())


def debt_value(island: IslandState) -> int:
    """借りている額の合計（自国通貨に直す）。"""
    return sum(
        fxrates.to_own(island, int(key), sum(lenders.values()))
        for key, lenders in owed(island).items()
    )


def claims_value(island: IslandState, foreign_only: bool = True) -> int:
    """貸している額の合計（自国通貨に直す）。foreign_only なら外貨で貸している分だけ（発行した国が
    自国の通貨で貸した分は外国の資産でなく、外貨準備に入れない）。"""
    total = 0
    for key, borrowers in lent(island).items():
        if foreign_only and int(key) == island.id:
            continue
        total += fxrates.to_own(island, int(key), sum(borrowers.values()))
    return total


def issued(issuer: IslandState) -> int:
    """発行した国の中央銀行が、自国の通貨を新しく作って貸している額の合計。"""
    return sum(lent(issuer).get(str(issuer.id), {}).values())


def _book(world: WorldState, lender: int, borrower: int, currency: int, amount: int) -> None:
    """貸し借りの記録を両方の島で amount だけ増やす（マイナスで減らす。0 になったら消す）。"""
    for island_id, table, other in (
        (borrower, OWED, lender),
        (lender, LENT, borrower),
    ):
        mine = island_state(world.islands[island_id]).setdefault(table, {})
        row = mine.setdefault(str(currency), {})
        row[str(other)] = row.get(str(other), 0) + amount
        if row[str(other)] <= 0:
            del row[str(other)]
        if not row:
            del mine[str(currency)]


def _record(
    world: WorldState, lender: int, borrower: int, currency: int, amount: int, sign: int
) -> None:
    """国際収支: 貸した国の資産と、借りた国の負債に、貸し借りの増減を足す（その島の通貨の値）。"""
    for island_id, item in ((lender, "assets.swaps"), (borrower, "liabilities.swaps")):
        island = world.islands[island_id]
        balance.add(island, item, sign * balance.value_of(world, island, currency, amount))


def quota_room(island: IslandState, config: GameConfig) -> int | None:
    """まだ借りられる額（自国通貨）。GDP の記録がなければ None（上限なし。建国の最初のターン）。"""
    gdp = debt.annual_gdp(island.economy)
    if gdp <= 0:
        return None
    return max(0, part(gdp, to_basis(config.get("fx.swap_quota"))) - debt_value(island))


def issuer_room(issuer: IslandState, config: GameConfig) -> int | None:
    """発行した国の中央銀行が、まだ新しく作って貸せる額（自国通貨。GDP の記録がなければ None）。"""
    gdp = debt.annual_gdp(issuer.economy)
    if gdp <= 0:
        return None
    return max(0, part(gdp, to_basis(config.get("fx.swap_issuer_share"))) - issued(issuer))


def _keep(island: IslandState, currency: int, turns: int) -> int:
    """手元に置く分: その島の主体が通貨 currency で払い・受け取った額の平均の turns ターン分（市場
    の厚みの下限は入れない。小さい島で下限が大きく、貸せる国がなくなったため）。"""
    return fxrates.turnover(island, currency) * turns


def lenders(
    world: WorldState, config: GameConfig, borrower: int, currency: int
) -> list[tuple[int, int]]:
    """通貨 currency を貸せる加盟国の中央銀行と、貸せる額（手元に置く分の余り）。同じ圏で currency
    を決済通貨にしている国だけ（発行した国と借り手、currency を借りている国を除く。番号の順）。"""
    members: set[int] = set()
    for b in blocs.member_of(world, borrower):
        if b["currency"] == currency:
            members |= set(b["members"])
    turns = config.get("fx.swap_keep_turns")
    out = []
    for i in sorted(members - {borrower, currency}):
        island = world.islands.get(i)
        if island is None or not island.has_economy or debt_in(island, currency) > 0:
            continue
        spare = fxmoney.holding(island, ledger.CB, currency) - _keep(island, currency, turns)
        if spare > 0:
            out.append((i, spare))
    return out


def available(world: WorldState, config: GameConfig, borrower: int, currency: int) -> int:
    """借り手の中央銀行が、このターンにまだ借りられる通貨 currency の量。"""
    island = world.islands[borrower]
    issuer = world.islands.get(currency)
    if issuer is None or currency == borrower:
        return 0
    room = quota_room(island, config)
    quota = None if room is None else fxrates.to_foreign(island, currency, room)
    supply = sum(spare for _, spare in lenders(world, config, borrower, currency))
    if issuer.has_economy:
        made = issuer_room(issuer, config)
        supply = None if made is None else supply + made
    if quota is None:
        return supply if supply is not None else 0
    return quota if supply is None else min(quota, supply)


def draw(world: WorldState, config: GameConfig, borrower: int, currency: int, amount: int) -> int:
    """借り手の中央銀行が通貨 currency を amount まで借りる（加盟国の余りに比例して、足りない分は
    発行した国から）。借りた量を返す。"""
    amount = min(amount, available(world, config, borrower, currency))
    if amount <= 0:
        return 0
    got = 0
    pool = lenders(world, config, borrower, currency)
    spare = sum(s for _, s in pool)
    from_pool = min(amount, spare)
    left = from_pool
    for n, (lender, s) in enumerate(pool):
        take = left if n == len(pool) - 1 else min(left, from_pool * s // spare)
        take = min(take, s)
        if take <= 0:
            continue
        _lend(world, lender, borrower, currency, take)
        left -= take
        got += take
    rest = amount - got
    if rest > 0 and world.islands[currency].has_economy:
        _lend(world, currency, borrower, currency, rest)
        got += rest
    return got


def _lend(world: WorldState, lender: int, borrower: int, currency: int, amount: int) -> None:
    target = world.islands[borrower]
    fxmoney.transfer(
        world,
        currency,
        (lender, ledger.CB),
        (borrower, ledger.CB),
        amount,
        KIND,
        fxrates.to_own(target, currency, amount),
    )
    _book(world, lender, borrower, currency, amount)
    _record(world, lender, borrower, currency, amount, 1)


def rate(world: WorldState, config: GameConfig, currency: int) -> int:
    """融通の金利（年率、1万分の1）: 通貨を発行した国の政策金利 + fx.swap_spread。"""
    issuer = world.islands.get(currency)
    base = issuer.policy.policy_rate if issuer is not None else 0
    return max(0, base) + to_basis(config.get("fx.swap_spread"))


def service(world: WorldState, config: GameConfig) -> None:
    """毎ターンの利払いと返済（貿易の段の為替の後）。島の番号の順、通貨の番号の順、貸し手は発行した
    国から、加盟国は番号の順。"""
    for b in world.island_ids():
        island = world.islands[b]
        for key in sorted(owed(island), key=int):
            currency = int(key)
            _interest(world, config, island, currency)
            _repay(world, config, island, currency)


def _interest(world: WorldState, config: GameConfig, island: IslandState, currency: int) -> None:
    annual = rate(world, config, currency)
    for key, principal in sorted(owed(island).get(str(currency), {}).items(), key=_by_id):
        lender = int(key)
        due = ledger.interest(principal, annual)
        if due <= 0 or lender not in world.islands:
            continue
        have = fxmoney.holding(island, ledger.CB, currency)
        paid = min(due, have)
        if paid > 0:
            fxmoney.transfer(
                world,
                currency,
                (island.id, ledger.CB),
                (lender, ledger.CB),
                paid,
                INTEREST,
                fxrates.to_own(world.islands[lender], currency, paid)
                if lender != currency
                else None,
            )
        unpaid = due - paid
        if unpaid > 0:  # 払えない利子は借りた額に足す（所得収支の支払いと、負債の増加）
            _book(world, lender, island.id, currency, unpaid)
            _record(world, lender, island.id, currency, unpaid, 1)
            for who, sign in ((island, -1), (world.islands[lender], 1)):
                balance.add(who, "income", sign * balance.value_of(world, who, currency, unpaid))


def _repay(world: WorldState, config: GameConfig, island: IslandState, currency: int) -> None:
    spare = fxmoney.holding(island, ledger.CB, currency) - _keep(island, currency, 1)
    if spare <= 0:
        return
    loans = owed(island).get(str(currency), {})
    issuer_part = min(spare, loans.get(str(currency), 0))
    if issuer_part > 0:
        _pay_back(world, island, currency, currency, issuer_part)
        spare -= issuer_part
    pool = sorted(
        ((int(k), v) for k, v in owed(island).get(str(currency), {}).items() if int(k) != currency)
    )
    total = sum(v for _, v in pool)
    if spare <= 0 or total <= 0:
        return
    paying = min(spare, total)
    left = paying
    for n, (lender, v) in enumerate(pool):
        take = left if n == len(pool) - 1 else min(left, paying * v // total)
        take = min(take, v)
        if take > 0:
            _pay_back(world, island, lender, currency, take)
            left -= take


def _pay_back(
    world: WorldState, island: IslandState, lender: int, currency: int, amount: int
) -> None:
    if lender in world.islands:
        value = (
            fxrates.to_own(world.islands[lender], currency, amount) if lender != currency else None
        )
        fxmoney.transfer(
            world, currency, (island.id, ledger.CB), (lender, ledger.CB), amount, KIND, value
        )
        _record(world, lender, island.id, currency, amount, -1)
    _book(world, lender, island.id, currency, -amount)


def _by_id(item: tuple[str, int]) -> int:
    return int(item[0])


def reconcile(world: WorldState) -> list[str]:
    """世界の確かめ: 借りた側と貸した側の記録が合うか。問題を文で返す。"""
    problems: list[str] = []
    seen: dict[tuple[int, int, int], int] = {}
    for b in world.island_ids():
        for key, row in owed(world.islands[b]).items():
            for lender, amount in row.items():
                seen[(int(key), int(lender), b)] = amount
    booked: dict[tuple[int, int, int], int] = {}
    for a in world.island_ids():
        for key, row in lent(world.islands[a]).items():
            for borrower, amount in row.items():
                booked[(int(key), a, int(borrower))] = amount
    for k in sorted(set(seen) | set(booked)):
        if seen.get(k, 0) != booked.get(k, 0):
            currency, lender, borrower = k
            problems.append(
                f"島 {borrower} が島 {lender} から借りた通貨 {currency} の額 {seen.get(k, 0)} が、"
                f"貸した側の記録 {booked.get(k, 0)} と合いません"
            )
    return problems


def view(world: WorldState, island: IslandState, config: GameConfig) -> dict[str, Any]:
    """画面向け: 借りている相手と貸している相手ごとの額（その通貨と、自国通貨に直した額）と金利、
    まだ借りられる額。決済通貨を発行した国には、まだ新しく作って貸せる額も。"""

    def rows(table: dict[str, dict[str, int]], field: str) -> list[dict[str, Any]]:
        out = []
        for key, row in sorted(table.items(), key=_by_id):
            currency = int(key)
            for other, amount in sorted(row.items(), key=_by_id):
                out.append(
                    {
                        "currency": currency,
                        field: int(other),
                        "amount": amount,
                        "own": fxrates.to_own(island, currency, amount),
                        "rate": rate(world, config, currency),
                    }
                )
        return out

    issuer = any(b["currency"] == island.id for b in blocs.blocs(world))
    return {
        "owed": rows(owed(island), "lender"),
        "lent": rows(lent(island), "borrower"),
        "room": quota_room(island, config),
        "borrowed": debt_value(island),
        "issuer_room": issuer_room(island, config) if issuer else None,
        "issued": issued(island),
    }
