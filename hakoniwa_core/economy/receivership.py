"""管財人と居抜きの売り（FM-59・FM-60・BK-50。2026-11-10）の部品。

倒産した施設は、差し押さえた銀行が管財人として働き手と操業目標をそのまま引き継いで動かし続け、売り値を
下げながら売りに出す（現実の会社更生・民事再生・米国の Chapter 11）。閉鎖に入った施設も居抜きで
売りに出す。ここには、売り値の下げ方と、管財人の運転資金（共益債権。DIP ファイナンス）の貸し方・
返し方を置く。
買い手を探すのは参入と撤退の段（modules/firms）、倒産させるのは破綻の段（modules/failures）。

- 売り値: 差し押さえたとき（閉鎖に入ったとき）の帳簿の設備の値打ち（book）の firms.foreclosure_price
  から始め、firms.sale_cut_turns ごとに初めの売り値の firms.sale_cut ずつ下げ、book の
  firms.sale_floor で止める（競売の二度目・三度目の入札）。
- 管財人の運転資金: 次のターンの支払いに施設の預金が足りなければ、変動費の利益が出ている間だけ、差し
  押さえた銀行（自己資本比率の規制か準備の最低限で貸せなければ、島のほかの銀行の預金の多い順）が足りない
  分を貸す。分割で返さず、利子は残高に足す（遅れにならない）。施設が売れたとき・取り壊すとき・国に移る
  ときに、施設のお金から未払い賃金・税・前からの貸し手より先に一度に返す。島に動いている銀行が1つも
  なければ、政府が出す（差し押さえの記録の advance。同じ順で返してもらう）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import ledger
from hakoniwa_core.config import GameConfig
from hakoniwa_core.economy import (
    SEIZED,
    STATE_FACILITIES,
    Payer,
    arrears,
    banks,
    facility_entity,
    funds,
    in_receivership,
)
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.units import part, to_basis

ADVANCE = "advance"  # 差し押さえの記録: 政府が出した管財人の運転資金（銀行がないとき）
SALE_BOOK = "sale_book"  # f.economy: 閉鎖に入ったときの帳簿の設備の値打ち（売り値のもと）


# --- 売り値 -------------------------------------------------------------------------


def first_price(config: GameConfig, book: int) -> int:
    """初めの売り値（帳簿の設備の値打ち × firms.foreclosure_price）。"""
    return part(book, to_basis(config.get("firms.foreclosure_price")))


def floor_price(config: GameConfig, book: int) -> int:
    return min(first_price(config, book), part(book, to_basis(config.get("firms.sale_floor"))))


def price_at(config: GameConfig, book: int, since: int, turn: int) -> int:
    """since に売りに出した施設の、turn の売り値。"""
    start = first_price(config, book)
    cut = part(start, to_basis(config.get("firms.sale_cut")))
    steps = max(0, turn - since) // max(1, config.get("firms.sale_cut_turns"))
    return max(floor_price(config, book), start - cut * steps)


def next_cut(config: GameConfig, book: int, since: int, turn: int) -> int | None:
    """次に売り値を下げるターン（下限に着いていれば None）。"""
    if price_at(config, book, since, turn) <= floor_price(config, book):
        return None
    every = max(1, config.get("firms.sale_cut_turns"))
    return since + (max(0, turn - since) // every + 1) * every


def sale_terms(f: Facility, config: GameConfig | None = None) -> tuple[int, int] | None:
    """売りに出している施設の (帳簿の値打ち, 売りに出したターン)。売りに出していなければ None。
    管財人の前の差し押さえ（帳簿の値打ちを持たない）は、売り値から戻す（config が要る）。"""
    seized = f.economy.get(SEIZED)
    if isinstance(seized, dict):
        book = seized.get("book")
        if book is None and config is not None:
            share = config.get("firms.foreclosure_price")
            book = round(seized.get("price", 0) / share) if share else 0
        return int(book or 0), int(seized.get("since", 0))
    if "closing" in f.economy:
        return int(f.economy.get(SALE_BOOK, banks.book_capital(f))), int(f.economy["closing"])
    return None


def asking_price(config: GameConfig, f: Facility, turn: int) -> int | None:
    """売りに出している施設の今の売り値（売りに出していなければ None）。"""
    terms = sale_terms(f, config)
    if terms is None:
        return None
    return max(1, price_at(config, terms[0], terms[1], turn))


# --- 管財人の運転資金 -----------------------------------------------------------------


def loans_of(island: IslandState, entity: str) -> list[Any]:
    return sorted(
        (x for x in banks.loans_by_borrower(island, entity) if x.purpose == banks.RECEIVER),
        key=lambda x: x.id,
    )


def owed(island: IslandState, f: Facility) -> int:
    """施設の管財人の運転資金の残り（銀行と政府の分）。"""
    seized = f.economy.get(SEIZED)
    advance = int(seized.get(ADVANCE, 0)) if isinstance(seized, dict) else 0
    entity = facility_entity(f.id)
    return advance + sum(banks.outstanding(island, x) for x in loans_of(island, entity))


def _lenders(
    island: IslandState, config: GameConfig, seized: dict[str, Any], turn: int
) -> list[str]:
    """貸す銀行の順: 差し押さえた銀行、次に島のほかの銀行の預金の多い順（破綻した銀行は
    貸さない）。"""
    active = [b for b in banks.active_banks(island) if not banks.lending_stopped(island, b, turn)]
    others = sorted(
        (b for b in active if b != seized.get("bank")),
        key=lambda b: (-banks.deposits_of(island, b), b),
    )
    first = [seized["bank"]] if seized.get("bank") in active else []
    return first + others


def _room(island: IslandState, config: GameConfig, bank: str, turn: int) -> int:
    """bank が管財人の運転資金を貸せる額: 自己資本比率の規制の残りと、準備の最低限を超えた分の
    小さい方。大口融資の規制にはかけない（先に返る。BK-50）。"""
    return max(
        0,
        min(
            banks.capital_room(island, config, bank, turn),
            banks.excess_reserves(island, config, bank),
        ),
    )


def lend(island: IslandState, ctx: Any, f: Facility, short: int) -> int:
    """管財人の施設 f の支払いの足りない分 short を貸す（呼ぶ側が変動費の利益を見る）。貸せなかった
    額を返す。島に動いている銀行がなければ、政府が出せる分を出す。"""
    seized = f.economy.get(SEIZED)
    if short <= 0 or not in_receivership(f):
        return max(0, short)
    entity = facility_entity(f.id)
    config = ctx.config
    if not banks.active_banks(island):
        payer = Payer(ledger.GOV, STATE_FACILITIES)
        amount = min(short, funds.available(island, payer))
        if amount > 0:
            funds.pay(island, payer, entity, amount, "receiver_advance")
            seized[ADVANCE] = int(seized.get(ADVANCE, 0)) + amount
        return short - amount
    left = short
    rate_of = ctx.formula("loan_rate")
    for bank in _lenders(island, config, seized, ctx.turn):
        piece = min(left, _room(island, config, bank, ctx.turn))
        if piece <= 0:
            continue
        rate = rate_of(island, ctx, bank, entity, piece)
        loan = next((x for x in loans_of(island, entity) if x.lender == bank), None)
        if loan is None:
            # 分割で返さないので、返す時期は来ない（売れたとき・取り壊すときに一度に返す）
            banks.lend(island, bank, entity, piece, banks.RECEIVER, 1, ctx.turn, rate, 10**9)
        else:
            ledger.create(island, bank, ledger.loan_item(loan.id), entity, piece, "loan")
            ledger.pay(island, bank, entity, piece, "loan")
            loan.principal = banks.outstanding(island, loan)
        left -= piece
        if left <= 0:
            break
    return left


def repay(island: IslandState, f: Facility, budget: int) -> int:
    """管財人の運転資金を、施設のお金から budget まで先に返す（政府の分と銀行の分を残りの割合で）。
    返した額を返す。"""
    entity = facility_entity(f.id)
    budget = min(budget, ledger.money(island, entity))
    seized = f.economy.get(SEIZED)
    rows: list[tuple[str, Any, int]] = [
        ("loan", x, banks.outstanding(island, x)) for x in loans_of(island, entity)
    ]
    if isinstance(seized, dict) and seized.get(ADVANCE, 0) > 0:
        rows.append(("gov", None, int(seized[ADVANCE])))
    total = sum(v for _, _, v in rows)
    if budget <= 0 or total <= 0:
        return 0
    use = min(budget, total)
    paid = 0
    for i, (who, loan, value) in enumerate(rows):
        share = use - paid if i == len(rows) - 1 else use * value // total
        share = min(share, value)
        if share <= 0:
            continue
        if who == "loan":
            banks.repay(island, loan, share, "receiver_repayment")
        else:
            ledger.pay(island, entity, ledger.GOV, share, "receiver_repayment")
            seized[ADVANCE] = int(seized[ADVANCE]) - share
        paid += share
    banks.drop_settled(island)
    return paid


def write_off(island: IslandState, f: Facility) -> int:
    """返しきれなかった管財人の運転資金を貸し手の損として消す。消した額を返す。"""
    entity = facility_entity(f.id)
    lost = 0
    for loan in loans_of(island, entity):
        left = banks.outstanding(island, loan)
        banks.write_off(island, loan, left)
        lost += left
    banks.drop_settled(island)
    seized = f.economy.get(SEIZED)
    if isinstance(seized, dict):
        lost += int(seized.pop(ADVANCE, 0))
    return lost


def claims_since(island: IslandState, f: Facility, seized: dict[str, Any]) -> None:
    """差し押さえの後にたまった未払い賃金と未払いの税を、差し押さえの記録の請求に足す（後で同じ順で分け
    る）。"""
    entity = facility_entity(f.id)
    later = arrears.write_off(island, entity)
    claims = seized.get("claims")
    if claims is None:  # 回3 より前の差し押さえ: 請求を持たず、残りは差し押さえた銀行に払う
        return
    wages = claims.setdefault(arrears.WAGES, {})
    for holder, value in later.get(arrears.WAGES, {}).items():
        wages[holder] = wages.get(holder, 0) + value
    claims[arrears.TAXES] = claims.get(arrears.TAXES, 0) + later.get(arrears.TAXES, 0)


def viable(island: IslandState, ctx: Any, f: Facility) -> bool:
    """今の値段で変動費の利益が出ているか（出ていなければ管財人は運転資金を借りない）。"""
    from hakoniwa_core.economy import freight as freight_rates
    from hakoniwa_core.economy.prices import variable_profit

    rate = freight_rates.facility_rates(island, ctx.defs, ctx.config).get(f.id, 0)
    wage = f.economy.get("wage", 0)
    return variable_profit(ctx.defs, ctx.config, f, island.economy.prices, wage, rate) > 0


def fund(island: IslandState, ctx: Any, f: Facility) -> int:
    """管財人の施設の次のターンの支払いに預金が足りなければ、変動費の利益が出ている間だけ管財人の運転
    資金を借りる。借りきれなかった額を返す（変動費の利益が出ていなければ借りずに足りない額）。"""
    from hakoniwa_core.economy.start import next_payments

    if not in_receivership(f) or not f.built:
        return 0
    entity = facility_entity(f.id)
    short = next_payments(island, ctx.defs, ctx.config, f) - ledger.money(island, entity)
    if short <= 0:
        return 0
    if not viable(island, ctx, f):
        return short
    return lend(island, ctx, f, short)
