"""お金の帳簿（複式の仕訳）。お金はこのファイルの post と pay を通してだけ動かします。

主体（お金を持つもの）は名前で呼びます。

- 家計の層 ``hh:<層>``（hh:general など）、独立採算の施設 ``fac:<番号>``、市中銀行 ``bank:<番号>``、
  政府 ``gov``（国庫直轄の施設のお金も政府のお金）、中央銀行 ``cb``

勘定の名前は「持ち手/持ち物/出し手」です（例: ``hh:general/deposit/bank:5`` は一般の層が銀行5に
持つ預金）。金融の持ち物は持ち手の資産であると同時に出し手の負債で、出し手の側は
``<出し手>/issued/<持ち物>`` という1つの勘定に、出している額の合計をマイナスで持ちます。
このため、持ち物（出し手と持ち物の組）ごとに、勘定の残高を足すといつも 0 になります。

仕訳は「借り方の残高が増え、貸し方の残高が減る」1行で、借り方と貸し方は必ず同じ持ち物の
勘定です（持ち手どうしで移すか、出し手が作る・消すか）。お金が生まれ・消えるのは、出し手の
勘定（issued）を使う仕訳だけです。

| 持ち物 | 持ち手 | 出し手 |
|---|---|---|
| deposit（預金） | 家計の層・施設 | 市中銀行 |
| reserve（準備預金） | 市中銀行 | 中央銀行 |
| treasury（政府預金・国庫） | 政府 | 中央銀行 |
| bond:<番号>（国債） | 市中銀行・中央銀行 | 政府 |
| equity（出資） | 資産を持つ層・政府 | 施設・銀行 |
"""

from __future__ import annotations

from collections.abc import Iterable

from hakoniwa_core.state import IslandState, JournalEntry
from hakoniwa_core.units import BASIS

GOV = "gov"
CB = "cb"
ISSUED = "issued"

DEPOSIT = "deposit"
RESERVE = "reserve"
TREASURY = "treasury"
EQUITY = "equity"

# 毎ターンの確かめで見る「お金」の量
MONEY_ITEMS = {"deposits": (DEPOSIT,), "cb_money": (RESERVE, TREASURY)}


class LedgerError(Exception):
    pass


# --- 名前 ---------------------------------------------------------------------


def account(holder: str, item: str, issuer: str) -> str:
    return f"{holder}/{item}/{issuer}"


def issued(issuer: str, item: str) -> str:
    return f"{issuer}/{ISSUED}/{item}"


def parse(name: str) -> tuple[str, str, str]:
    holder, item, issuer = name.split("/")
    return holder, item, issuer


def instrument(name: str) -> tuple[str, str]:
    """勘定の持ち物（持ち物, 出し手）。"""
    a, b, c = parse(name)
    return (c, a) if b == ISSUED else (b, c)


def is_issued(name: str) -> bool:
    return parse(name)[1] == ISSUED


def bond_item(number: int) -> str:
    """国債の銘柄の持ち物の名前。"""
    return f"bond:{number}"


def bank_entity(number: int) -> str:
    return f"bank:{number}"


def is_bank(entity: str) -> bool:
    return entity.startswith("bank:")


def is_depositor(entity: str) -> bool:
    """預金でお金を持つ主体（家計の層と独立採算の施設）。"""
    return entity.startswith(("hh:", "fac:"))


def bank_of(island: IslandState, entity: str) -> str:
    """主体が預金を置いている銀行（決めていなければ島の最初の銀行）。"""
    economy = island.economy
    number = economy.deposit_bank.get(entity)
    if number is None:
        if not economy.banks:
            raise LedgerError(f"島 {island.id} に銀行がありません")
        number = economy.banks[0]
    return bank_entity(number)


def deposit(island: IslandState, entity: str) -> str:
    return account(entity, DEPOSIT, bank_of(island, entity))


def reserve(bank: str) -> str:
    return account(bank, RESERVE, CB)


TREASURY_ACCOUNT = account(GOV, TREASURY, CB)


# --- 残高 ---------------------------------------------------------------------


def balance(island: IslandState, name: str) -> int:
    return island.accounts.get(name, 0)


def money(island: IslandState, entity: str) -> int:
    """主体がすぐに払えるお金（預金・国庫・準備預金）。"""
    if entity == GOV:
        return balance(island, TREASURY_ACCOUNT)
    if is_bank(entity):
        return balance(island, reserve(entity))
    if entity == CB:
        raise LedgerError("中央銀行はお金を作れるので、残高はありません")
    return sum(v for k, v in island.accounts.items() if k.startswith(f"{entity}/{DEPOSIT}/"))


def holdings(island: IslandState, holder: str, item_prefix: str) -> dict[str, int]:
    """持ち手 holder の、名前が item_prefix で始まる持ち物の勘定と残高。"""
    start = f"{holder}/{item_prefix}"
    return {k: v for k, v in island.accounts.items() if k.startswith(start) and v}


# --- 仕訳 ---------------------------------------------------------------------


def post(island: IslandState, debit: str, credit: str, amount: int, kind: str) -> None:
    """仕訳を1行足す。debit の残高が amount 増え、credit の残高が amount 減る。"""
    if amount == 0:
        return
    if amount < 0:
        raise LedgerError(f"仕訳の金額がマイナスです: {kind} {debit} {credit} {amount}")
    if debit == credit:
        raise LedgerError(f"借り方と貸し方が同じ勘定です: {kind} {debit}")
    if instrument(debit) != instrument(credit):
        raise LedgerError(f"持ち物の違う勘定の間の仕訳です: {kind} {debit} / {credit}")
    accounts = island.accounts
    accounts[debit] = accounts.get(debit, 0) + amount
    accounts[credit] = accounts.get(credit, 0) - amount
    for name in (debit, credit):
        if accounts[name] == 0:
            del accounts[name]
    island.journal.append(JournalEntry(kind=kind, debit=debit, credit=credit, amount=amount))


def create(island: IslandState, holder: str, item: str, issuer: str, amount: int, kind: str):
    """出し手 issuer が持ち物 item を作って holder に持たせる（国債の発行・出資など）。"""
    post(island, account(holder, item, issuer), issued(issuer, item), amount, kind)


def cancel(island: IslandState, holder: str, item: str, issuer: str, amount: int, kind: str):
    """holder の持つ item を出し手が消す（償還など）。"""
    post(island, issued(issuer, item), account(holder, item, issuer), amount, kind)


def transfer(
    island: IslandState, item: str, issuer: str, source: str, target: str, amount: int, kind: str
) -> None:
    """持ち物 item を持ち手 source から target へ移す（国債の売り買いなど）。"""
    post(island, account(target, item, issuer), account(source, item, issuer), amount, kind)


def pay(island: IslandState, payer: str, payee: str, amount: int, kind: str) -> None:
    """payer から payee へお金を払う。

    預金どうしなら預金を付け替え、取引銀行が違えば銀行どうしの準備預金も付け替える。
    政府が払う・受け取るときは国庫と準備預金を付け替える。銀行が自分で払うときは預金を作り、
    自分が受け取るときは預金を消す。払えるかどうかは呼ぶ側が確かめる（確かめは記録の段）。
    """
    if amount <= 0 or payer == payee:
        return
    payer_bank = bank_of(island, payer) if is_depositor(payer) else None
    payee_bank = bank_of(island, payee) if is_depositor(payee) else None
    if payer_bank is not None and payer_bank == payee_bank:
        post(island, deposit(island, payee), deposit(island, payer), amount, kind)
        return
    if payee_bank is not None:
        post(island, deposit(island, payee), issued(payee_bank, DEPOSIT), amount, kind)
    if payer_bank is not None:
        post(island, issued(payer_bank, DEPOSIT), deposit(island, payer), amount, kind)
    _settle(island, payer_bank or payer, payee_bank or payee, amount, kind)


def _settle(island: IslandState, source: str, target: str, amount: int, kind: str) -> None:
    """中央銀行のお金（準備預金と国庫）で、source から target へ払う。"""
    if source == target:
        return
    source_acct = _cb_money_account(source)
    target_acct = _cb_money_account(target)
    if source_acct is not None and target_acct is not None:
        if instrument(source_acct) == instrument(target_acct):
            post(island, target_acct, source_acct, amount, kind)
            return
    if source_acct is not None:  # 中央銀行が受け取って消す
        post(island, issued(CB, instrument(source_acct)[0]), source_acct, amount, kind)
    if target_acct is not None:  # 中央銀行が作って渡す
        post(island, target_acct, issued(CB, instrument(target_acct)[0]), amount, kind)


def _cb_money_account(entity: str) -> str | None:
    if entity == CB:
        return None
    if entity == GOV:
        return TREASURY_ACCOUNT
    if is_bank(entity):
        return reserve(entity)
    raise LedgerError(f"中央銀行のお金を持たない主体です: {entity}")


def interest(balance_: int, rate: int) -> int:
    """1ターンの利子: 残高 × 年利（1万分の1）÷ 12 の切り捨て。利子の丸めはここだけで行う。"""
    if balance_ <= 0 or rate <= 0:
        return 0
    return balance_ * rate // (BASIS * 12)


# --- 毎ターンの確かめ ---------------------------------------------------------


def aggregates(accounts: dict[str, int]) -> dict[str, int]:
    """お金の量: 預金の合計（deposits）と、中央銀行のお金（準備預金と国庫）の合計（cb_money）。"""
    totals = dict.fromkeys(MONEY_ITEMS, 0)
    for name, value in accounts.items():
        holder, item, _ = parse(name)
        for key, items in MONEY_ITEMS.items():
            if item in items:
                totals[key] += value
    return totals


def created_by_kind(journal: Iterable[JournalEntry]) -> dict[str, dict[str, int]]:
    """お金の量ごとに、仕訳の種類ごとの「生まれた額 − 消えた額」。

    お金が生まれ・消えるのは、出し手の勘定（issued）を使う仕訳だけ。
    """
    out: dict[str, dict[str, int]] = {key: {} for key in MONEY_ITEMS}
    for e in journal:
        for name, sign in ((e.debit, 1), (e.credit, -1)):
            holder, item, _ = parse(name)
            if item == ISSUED:
                continue
            for key, items in MONEY_ITEMS.items():
                if item in items and is_issued(e.credit if sign == 1 else e.debit):
                    by_kind = out[key]
                    by_kind[e.kind] = by_kind.get(e.kind, 0) + sign * e.amount
    return out


def check(
    island: IslandState, opening: dict[str, int], kinds: Iterable[str] | None = None
) -> list[str]:
    """帳簿の確かめ。問題を文で返す（空なら問題なし）。

    1. 仕訳の借り方と貸し方の合計が一致し、どの仕訳も同じ持ち物の勘定の間にある
    2. 前のターンの残高にこのターンの仕訳を足した値が、今の残高と一致する
    3. 預金と中央銀行のお金の増減が、お金が生まれる・消える仕訳の合計と一致する
    4. 預金と国庫がマイナスになっていない
    （ほかに、持ち物ごとの残高の合計が 0 で、仕訳の種類が定義されていること）
    """
    problems: list[str] = []
    known = set(kinds) if kinds is not None else None
    debit_total = credit_total = 0
    replay = dict(opening)
    for e in island.journal:
        debit_total += e.amount
        credit_total += e.amount
        if e.amount <= 0 or instrument(e.debit) != instrument(e.credit):
            problems.append(f"形の正しくない仕訳: {e}")
        if known is not None and e.kind not in known:
            problems.append(f"定義されていない仕訳の種類: {e.kind}")
        replay[e.debit] = replay.get(e.debit, 0) + e.amount
        replay[e.credit] = replay.get(e.credit, 0) - e.amount
    if debit_total != credit_total:
        problems.append(f"借り方 {debit_total} と貸し方 {credit_total} が一致しません")
    closing = island.accounts
    for name in sorted(set(replay) | set(closing)):
        if replay.get(name, 0) != closing.get(name, 0):
            problems.append(
                f"勘定 {name} の残高 {closing.get(name, 0)} が、前の残高と仕訳から求めた"
                f" {replay.get(name, 0)} と違います"
            )
    before, after = aggregates(opening), aggregates(closing)
    created = created_by_kind(island.journal)
    for key in MONEY_ITEMS:
        if after[key] - before[key] != sum(created[key].values()):
            problems.append(
                f"{key} の増減 {after[key] - before[key]} が、生まれた・消えた額"
                f" {sum(created[key].values())} と一致しません"
            )
    rows: dict[tuple[str, str], int] = {}
    for name, value in closing.items():
        key = instrument(name)
        rows[key] = rows.get(key, 0) + value
        holder, item, _ = parse(name)
        if item in (DEPOSIT, TREASURY) and value < 0:
            problems.append(f"勘定 {name} がマイナスです（{value}）")
    for key, total in sorted(rows.items()):
        if total != 0:
            problems.append(f"持ち物 {key} の残高の合計が 0 になりません（{total}）")
    return problems
