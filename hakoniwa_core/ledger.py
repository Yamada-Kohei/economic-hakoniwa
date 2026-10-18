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
| loan:<番号>（融資。フェーズ2-2） | 市中銀行 | 独立採算の施設 |
| interbank（銀行間の貸し借り） | 市中銀行 | 市中銀行 |
| cb_loan（中央銀行の貸し出し） | 中央銀行 | 市中銀行 |
| nr_deposit（非居住者の預金。フェーズ3-1） | 外国の主体 ``nr:<島>:<主体>`` | 中央銀行 |

家計の層と施設は預金を複数の銀行に持てます（払うときは取引銀行の預金から先に使う）。

外国の主体（ほかの島の商社や政府）が持つこの島の通貨は、中央銀行に置いた「非居住者の預金」です
（持ち手は ``nr:<持ち主の島>:<主体>``。準備預金や国庫と同じく中央銀行のお金で、利子は付かない）。
持ち主の島では、帳簿の外の「外貨の持ち高」として数えます（hakoniwa_core/trade/money.py）。
"""

from __future__ import annotations

from collections.abc import Iterable

from hakoniwa_core.dates import TURNS_PER_YEAR
from hakoniwa_core.state import Accounts, IslandState, JournalEntry
from hakoniwa_core.units import BASIS

GOV = "gov"
CB = "cb"
ISSUED = "issued"

DEPOSIT = "deposit"
RESERVE = "reserve"
TREASURY = "treasury"
EQUITY = "equity"
INTERBANK = "interbank"
CB_LOAN = "cb_loan"
NONRESIDENT = "nr_deposit"  # 非居住者の預金（フェーズ3-1）

# 毎ターンの確かめで見る「お金」の量（nonresident は外国の主体が持つこの島の通貨）
MONEY_ITEMS = {
    "deposits": (DEPOSIT,),
    "cb_money": (RESERVE, TREASURY),
    "nonresident": (NONRESIDENT,),
}


class LedgerError(Exception):
    pass


# --- 名前 ---------------------------------------------------------------------


def account(holder: str, item: str, issuer: str) -> str:
    return f"{holder}/{item}/{issuer}"


def issued(issuer: str, item: str) -> str:
    return f"{issuer}/{ISSUED}/{item}"


def parse(name: str) -> tuple[str, str, str]:
    found = _PARSED.get(name)
    if found is None:
        holder, item, issuer = name.split("/")
        if len(_PARSED) >= _PARSED_MAX:
            _PARSED.clear()
        found = _PARSED[name] = (holder, item, issuer)
    return found


# 勘定の名前を分けた結果（ターンの中で何百万回も分けるので覚えておく。名前は変わらない）
_PARSED: dict[str, tuple[str, str, str]] = {}
_PARSED_MAX = 1 << 16  # 返し終わった貸付などの名前が残り続けないよう、ここで全部捨てる


def instrument(name: str) -> tuple[str, str]:
    """勘定の持ち物（持ち物, 出し手）。"""
    a, b, c = parse(name)
    return (c, a) if b == ISSUED else (b, c)


def is_issued(name: str) -> bool:
    return parse(name)[1] == ISSUED


def bond_item(number: int) -> str:
    """国債の銘柄の持ち物の名前。"""
    return f"bond:{number}"


def loan_item(number: int) -> str:
    """融資の持ち物の名前。"""
    return f"loan:{number}"


def bank_entity(number: int) -> str:
    return f"bank:{number}"


def is_bank(entity: str) -> bool:
    return entity.startswith("bank:")


def is_depositor(entity: str) -> bool:
    """預金でお金を持つ主体（家計の層と独立採算の施設）。"""
    return entity.startswith(("hh:", "fac:"))


def nonresident(island_id: int, entity: str) -> str:
    """島 island_id の主体 entity を、ほかの島の帳簿で呼ぶ名前（非居住者。フェーズ3-1）。"""
    return f"nr:{island_id}:{entity}"


def is_nonresident(entity: str) -> bool:
    return entity.startswith("nr:")


def nonresident_account(entity: str) -> str:
    """非居住者 entity（nr:<島>:<主体>）の預金の勘定。"""
    return account(entity, NONRESIDENT, CB)


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


# --- 勘定の名前を引く -------------------------------------------------------------
#
# 帳簿（Accounts）の索引で、勘定を全部なめずに候補の名前を絞る。候補は条件に合う名前を
# すべて含み、帳簿の並びのままなので、呼ぶ側がいままでと同じ条件で選べば、結果も並びも
# 全部なめたときと同じになる。索引のない帳簿（ふつうの dict）や、名前に「/」を含む主体
# では、全部の名前を返す。


def names_of(island: IslandState, holder: str) -> Iterable[str]:
    """持ち手 holder の勘定の名前の候補（帳簿の並び）。"""
    accounts = island.accounts
    if isinstance(accounts, Accounts) and "/" not in holder:
        return accounts.of_holder(holder)
    return accounts.keys()


def names_with(island: IslandState, item: str) -> Iterable[str]:
    """持ち物 item を持つ側の勘定の名前の候補（帳簿の並び）。"""
    accounts = island.accounts
    if isinstance(accounts, Accounts) and "/" not in item:
        return accounts.of_item(item)
    return accounts.keys()


def names_of_item(island: IslandState, holder: str, item: str) -> Iterable[str]:
    """持ち手 holder の持ち物 item の勘定の名前の候補（帳簿の並び）。"""
    accounts = island.accounts
    if isinstance(accounts, Accounts) and "/" not in holder and "/" not in item:
        return accounts.of_pair(holder, item)
    return accounts.keys()


# --- 残高 ---------------------------------------------------------------------


def balance(island: IslandState, name: str) -> int:
    return island.accounts.get(name, 0)


def money(island: IslandState, entity: str) -> int:
    """主体がすぐに払えるお金（預金・国庫・準備預金）。"""
    if entity == GOV:
        return balance(island, TREASURY_ACCOUNT)
    if is_bank(entity):
        return balance(island, reserve(entity))
    if is_nonresident(entity):
        return balance(island, nonresident_account(entity))
    if entity == CB:
        raise LedgerError("中央銀行はお金を作れるので、残高はありません")
    accounts, prefix = island.accounts, f"{entity}/{DEPOSIT}/"
    return sum(accounts[k] for k in names_of_item(island, entity, DEPOSIT) if k.startswith(prefix))


def holdings(island: IslandState, holder: str, item_prefix: str) -> dict[str, int]:
    """持ち手 holder の、名前が item_prefix で始まる持ち物の勘定と残高。"""
    accounts, start = island.accounts, f"{holder}/{item_prefix}"
    return {k: accounts[k] for k in names_of(island, holder) if k.startswith(start) and accounts[k]}


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

    預金を複数の銀行に持つ主体は、取引銀行の預金から先に使い、足りない分をほかの銀行の預金
    （勘定の名前の順）から使う。受け取る側は取引銀行の預金に入る。
    """
    if amount <= 0 or payer == payee:
        return
    payee_bank = bank_of(island, payee) if is_depositor(payee) else None
    if not is_depositor(payer):
        if payee_bank is not None:
            post(island, deposit(island, payee), issued(payee_bank, DEPOSIT), amount, kind)
        _settle(island, payer, payee_bank or payee, amount, kind)
        return
    for source, take in _draw(island, payer, amount):
        bank = parse(source)[2]
        if bank == payee_bank:
            post(island, deposit(island, payee), source, take, kind)
            continue
        if payee_bank is not None:
            post(island, deposit(island, payee), issued(payee_bank, DEPOSIT), take, kind)
        post(island, issued(bank, DEPOSIT), source, take, kind)
        _settle(island, bank, payee_bank or payee, take, kind)


def _draw(island: IslandState, entity: str, amount: int) -> list[tuple[str, int]]:
    """amount を払うのに使う預金の勘定と額（取引銀行の預金から先に）。足りなければ、
    足りない分も取引銀行の預金から払う（マイナスになり、記録の段の確かめで見つかる）。"""
    main = deposit(island, entity)
    have = island.accounts.get(main, 0)
    if have >= amount:
        return [(main, amount)]
    out = [(main, have)] if have > 0 else []
    left = amount - max(0, have)
    prefix = f"{entity}/{DEPOSIT}/"
    candidates = names_of_item(island, entity, DEPOSIT)
    for name in sorted(k for k in candidates if k.startswith(prefix) and k != main):
        value = island.accounts[name]
        if value <= 0:
            continue
        take = min(value, left)
        out.append((name, take))
        left -= take
        if left <= 0:
            return out
    out.append((main, left))
    return out


def deposits_by_bank(island: IslandState, entity: str) -> dict[str, int]:
    """主体が銀行ごとに持つ預金（銀行の主体 → 額）。"""
    accounts, prefix = island.accounts, f"{entity}/{DEPOSIT}/"
    return {
        parse(k)[2]: accounts[k]
        for k in sorted(names_of_item(island, entity, DEPOSIT))
        if k.startswith(prefix) and accounts[k]
    }


def move_deposit(
    island: IslandState, entity: str, source: str, target: str, amount: int, kind: str
) -> None:
    """主体 entity の預金を、銀行 source から銀行 target へ移す（準備預金も一緒に動く）。"""
    if amount <= 0 or source == target:
        return
    post(island, issued(source, DEPOSIT), account(entity, DEPOSIT, source), amount, kind)
    post(island, account(entity, DEPOSIT, target), issued(target, DEPOSIT), amount, kind)
    _settle(island, source, target, amount, kind)


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
    if is_nonresident(entity):
        return nonresident_account(entity)
    raise LedgerError(f"中央銀行のお金を持たない主体です: {entity}")


def interest(balance_: int, rate: int) -> int:
    """1ターンの利子: 残高 × 年利（1万分の1）÷ 12 の切り捨て。利子の丸めはここだけで行う。"""
    if balance_ <= 0 or rate <= 0:
        return 0
    return balance_ * rate // (BASIS * TURNS_PER_YEAR)


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
    4. 預金と国庫と非居住者の預金がマイナスになっていない
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
    # 全部の勘定を並べると重いので、合わない勘定だけを名前の順に並べる
    wrong = [name for name in replay if replay[name] != closing.get(name, 0)]
    wrong += [name for name in closing if name not in replay and closing[name] != 0]
    for name in sorted(wrong):
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
        if item in (DEPOSIT, TREASURY, NONRESIDENT) and value < 0:
            problems.append(f"勘定 {name} がマイナスです（{value}）")
    for key, total in sorted(rows.items()):
        if total != 0:
            problems.append(f"持ち物 {key} の残高の合計が 0 になりません（{total}）")
    return problems
