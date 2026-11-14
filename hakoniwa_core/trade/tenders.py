"""入札で結ぶ契約と、その罰金（W-268。タブ 9 FX-71・FX-72）。

公共調達の一般競争入札の形を1種類だけ入れます。買う国の政府が命令 open_tender で「品目・1ターンの
量・期間・上限の値段・締め切り・届いた物の行き先」を示すと、入札がすべての国に見えます。払う通貨は
出す国の通貨だけです。ほかの国の政府は命令 bid_tender で伏せた値段を入れ（上限 × trade.tender.floor
（切り上げ）〜上限。締め切りのターンまで出し直せ、値段を書かずに出すと取り下げ）、締め切りのターンの
貿易の段で、全島の命令を済ませた後に、いちばん安い札の国とその値段で二国間契約を結びます（次の
ターンから効く。同じ値段ならくじ）。落とした国と値段はすべての国に見せ、ほかの札は誰にも見せません。

入札で結んだ契約が届かなかった量は、契約の不足の上限の順に「第三国の封鎖（罰金なし）→ 売り手の物と
港 → 買い手のお金と船」で分け、わけのある国への罰金（量 × 契約の値段 × trade.tender.penalty_rate）を
請求できる罰金（claim）として残します。損をした国は行政力 0 の命令 settle_tender_default で、その
契約のまだ決めていない罰金をまとめて「取る」「許す」「やめる（取ったうえで契約を終える）」から選び、
選ばないまま CLAIM_TURNS ターンたつと取ります。取った罰金は、わけのある国の通貨に今のレートで換えた
払い
（debt）になり、次のターンの契約の履行の前に番号の順で払います（取り置きは予算の段の契約の項目
contract_supply）。払えない分は未払いとして残り、払い終わるまでその国は入札を出すことも札を入れる
こともできません（現実の指名停止）。

状態は ``world.ext["trade"]`` に持ちます:

- tenders: 入札。id・buyer（出した島）・good・qty（1ターンの量。1000 分の 1 単位）・turns（期間）・
  cap（上限の値段。出す国の通貨）・deadline（締め切りのターン）・delivery・created・
  state（open・awarded・lapsed・withdrawn）・bids（{島の番号の文字列: 値段}。伏せる）・
  winner・price・contract（落としたときの契約の番号）・closed（落とした・流れた・取り下げたターン）。
- tender_claims: 請求できる罰金。id・contract・payer（わけのある国）・payee（損をした国）・
  amount（契約の通貨）・currency・qty（届かなかった量。やめる罰金は 0）・why（goods・port・money・
  ships・blockade・embargo・cancel）・turn（出たターン）。
- tender_debts: 取ると決まった罰金の払い。id・contract・payer・payee・amount（払う国の通貨の残り）・
  due（払うターン）・late（1度払いきれなかった。これがあると入札に加われない）・turn。
"""

from __future__ import annotations

import math
import random
from typing import Any

from hakoniwa_core.config import GameConfig
from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.trade import currency_label, next_id, world_state
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.units import BASIS, QUANTITY_SCALE, from_milli, money_of, to_basis

OPEN = "open"
AWARDED = "awarded"
LAPSED = "lapsed"
WITHDRAWN = "withdrawn"
KEEP_CLOSED = 12  # 落とした・流れた入札を記録に残すターン数（契約と同じ）
CLAIM_TURNS = 12  # 決めないまま、この数のターンがたった罰金は取る（契約の申し込みと同じ）

COLLECT = "collect"  # 取る
FORGIVE = "forgive"  # 許す
TERMINATE = "terminate"  # 取ったうえで、契約を終える
CHOICES = (COLLECT, FORGIVE, TERMINATE)
# 札を入れた国だけに見せるログ（札の値段と、札を入れたこと。FX-71）
PRIVATE_LOGS = ("tender_bid", "tender_bid_withdrawn", "tender_request_failed")
PENALTY = "tender_penalty"  # 仕訳の種類（国際収支の移転）
DUE = "penalty_due"  # 島の貿易の状態: 次のターンに払う罰金（予算の段が契約の項目に取り置く）

# 契約の不足のわけと、わけのある側（入札の罰金と、条約の契約の条項の見張りで 1 つの表。FX-72・
# PL-47）。上限の順に分ける: 当事者でない国のわけ（第三国の封鎖・外貨の市場の厚み）→ 売り手（物と
# 港）→ 買い手（お金と船）。条約の条項は、船と港を約束した国のせいにしない（PL-47。TREATY_FAULTS）
SHORT_BY_NOBODY = ("blockade", "fx")
SHORT_BY_SELLER = ("goods", "port")
SHORT_BY_BUYER = ("money", "ships")
TREATY_FAULTS = ("goods", "money")


def tenders(world: WorldState) -> list[dict[str, Any]]:
    return world_state(world).setdefault("tenders", [])


def claims(world: WorldState) -> list[dict[str, Any]]:
    return world_state(world).setdefault("tender_claims", [])


def debts(world: WorldState) -> list[dict[str, Any]]:
    return world_state(world).setdefault("tender_debts", [])


def find(world: WorldState, tender_id: int) -> dict[str, Any] | None:
    for t in tenders(world):
        if t["id"] == tender_id:
            return t
    return None


def barred(world: WorldState, island_id: int) -> bool:
    """未払いの罰金があって、入札を出すことも札を入れることもできないか（指名停止）。"""
    return any(d["payer"] == island_id and d["late"] for d in debts(world))


def floor_price(config: GameConfig, cap: int) -> int:
    """札の下限（上限 × trade.tender.floor の切り上げ。最低制限価格）。"""
    return math.ceil(cap * to_basis(config.get("trade.tender.floor")) / BASIS)


def _name(world: WorldState, island_id: int) -> str:
    island = world.islands.get(island_id)
    return island.name if island is not None else str(island_id)


def _args(world: WorldState, t: dict[str, Any]) -> dict[str, Any]:
    return {
        "tender": t["id"],
        "buyer": _name(world, t["buyer"]),
        "good": t["good"],
        "amount": from_milli(t["qty"]),
        "turns": t["turns"],
        "cap": t["cap"],
        "deadline": t["deadline"],
        "currency": currency_label(world, t["buyer"]),
    }


# --- 入札を出す・札を入れる・取り下げる ------------------------------------------------


def open_tender(
    world: WorldState, island: IslandState, payload: dict[str, Any], turn: int
) -> dict[str, Any]:
    """入札を出す（確かめは呼ぶ側）。締め切りは deadline ターン後。"""
    t = {
        "id": next_id(world_state(world), "tender"),
        "buyer": island.id,
        "good": payload["good"],
        "qty": round(payload["quantity"] * QUANTITY_SCALE),
        "turns": payload["turns"],
        "cap": payload["cap"],
        "deadline": turn + payload["deadline"],
        "delivery": payload.get("delivery") or "market",
        "created": turn,
        "state": OPEN,
        "bids": {},
        "winner": None,
        "price": None,
        "contract": None,
        "closed": None,
    }
    tenders(world).append(t)
    island.log.append(LogEntry("tender_opened", _args(world, t)))
    return t


def withdraw(world: WorldState, island: IslandState, t: dict[str, Any], turn: int) -> str | None:
    """出した国が締め切りまでに入札を取り下げる（札を入れた国にも知らせる）。"""
    if t["buyer"] != island.id:
        return "reason_tender_not_mine"
    if t["state"] != OPEN:
        return "reason_tender_closed"
    t["state"], t["closed"] = WITHDRAWN, turn
    # 札を入れた国には知らせない（公開のログに書くと、誰が札を入れたかが分かる。結果は入札の一覧に
    # 出る）
    island.log.append(LogEntry("tender_withdrawn", _args(world, t)))
    t["bids"] = {}
    return None


def bid(world: WorldState, island: IslandState, t: dict[str, Any], price: int | None) -> str | None:
    """札を入れる・出し直す（price が None なら取り下げる）。値段の幅は呼ぶ側が確かめる。"""
    if t["state"] != OPEN:
        return "reason_tender_closed"
    if t["buyer"] == island.id:
        return "reason_tender_own"
    key = str(island.id)
    args = {**_args(world, t), "price": price}
    if price is None:
        if key not in t["bids"]:
            return "reason_tender_no_bid"
        del t["bids"][key]
        island.log.append(LogEntry("tender_bid_withdrawn", args))
        return None
    t["bids"][key] = price
    island.log.append(LogEntry("tender_bid", args))
    return None


def ranked(world: WorldState, t: dict[str, Any], turn: int) -> list[int]:
    """札を入れた国を、安い順に並べる（同じ値段は世界の種のくじ。島の番号の順に引くので、命令の
    順番によらない。FX-01）。"""
    rng = random.Random(f"{world.seed}:{turn}:tender:{t['id']}")
    bids = sorted((int(k), p) for k, p in t["bids"].items())
    keyed = [(i, p, rng.random()) for i, p in bids]
    return [i for i, _, _ in sorted(keyed, key=lambda x: (x[1], x[2]))]


def award(
    world: WorldState, t: dict[str, Any], winner: int | None, turn: int
) -> dict[str, Any] | None:
    """締め切りの入札を閉じる。winner が None なら流れる。落としたら契約を作って返す（次のターンから
    効く）。結果は出した国と札を入れた国すべてに知らせる（落とした国と値段は誰にでも見せる）。"""
    from hakoniwa_core.trade import contracts

    # 知らせるのは出した国と落とした国だけ（札を入れた国に書くと、公開のログから誰が札を入れたかが
    # 分かる。落ちなかった国は入札の一覧の結果で分かる）
    buyer = world.islands.get(t["buyer"])
    t["closed"] = turn
    if winner is None:
        t["state"] = LAPSED
        t["bids"] = {}
        if buyer is not None:
            buyer.log.append(LogEntry("tender_lapsed", _args(world, t)))
        return None
    price = t["bids"][str(winner)]
    c = contracts.start_from_tender(world, t, winner, price, turn)
    t["state"], t["winner"], t["price"], t["contract"] = AWARDED, winner, price, c["id"]
    t["bids"] = {}  # 落ちなかった札は誰にも見せないので残さない
    args = {**_args(world, t), "winner": _name(world, winner), "price": price, "contract": c["id"]}
    for i in sorted({t["buyer"], winner}):
        if i in world.islands:
            world.islands[i].log.append(LogEntry("tender_awarded", args))
    return c


def close(world: WorldState, turn: int) -> None:
    """閉じてしばらくたった入札を消し、島が世界から消えたら、その国の入札・札・罰金・未払いを消す
    （FX-72）。"""
    state = world_state(world)
    gone = lambda i: i not in world.islands  # noqa: E731
    keep = []
    for t in tenders(world):
        if t["state"] == OPEN and gone(t["buyer"]):
            t["state"], t["closed"], t["bids"] = LAPSED, turn, {}
        if t["state"] == OPEN:
            t["bids"] = {k: p for k, p in t["bids"].items() if not gone(int(k))}
        if t["state"] != OPEN and turn - t["closed"] >= KEEP_CLOSED:
            continue
        keep.append(t)
    state["tenders"] = keep
    state["tender_claims"] = [
        c for c in claims(world) if not gone(c["payer"]) and not gone(c["payee"])
    ]
    state["tender_debts"] = [
        d for d in debts(world) if not gone(d["payer"]) and not gone(d["payee"])
    ]


# --- 届かなかった分のわけ ------------------------------------------------------------------


def split_short(want: int, limits: dict[str, int]) -> dict[str, int]:
    """契約の不足を、上限の順にわけごとの量に分ける（FX-72）: 当事者でない国のわけ → 売り手 →
    買い手。返すのは {わけ: 量}（量が 0 のわけは入れない）。"""
    out: dict[str, int] = {}
    level = want
    for group in (SHORT_BY_NOBODY, SHORT_BY_SELLER, SHORT_BY_BUYER):
        here = [(limits[k], k) for k in group if k in limits]
        if not here:
            continue
        cap, why = min(here)
        cap = max(0, cap)
        if cap < level:
            out[why] = level - cap
            level = cap
    return out


def penalty_of(config: GameConfig, c: dict[str, Any], qty: int) -> int:
    """届かなかった量 qty の罰金（契約の通貨）。"""
    return money_of(c["price"], qty) * to_basis(config.get("trade.tender.penalty_rate")) // BASIS


def add_claim(
    world: WorldState,
    config: GameConfig,
    c: dict[str, Any],
    payer: int,
    amount: int,
    qty: int,
    why: str,
    turn: int,
) -> None:
    """わけのある国 payer への罰金を、請求できる罰金として残す（休止中の国からは取らない）。"""
    from hakoniwa_core.diplomacy import is_inactive

    if amount <= 0:
        return
    island = world.islands.get(payer)
    if island is None or is_inactive(island):
        return
    payee = c["buyer"] if payer == c["seller"] else c["seller"]
    claim = {
        "id": next_id(world_state(world), "tender_claim"),
        "contract": c["id"],
        "payer": payer,
        "payee": payee,
        "amount": amount,
        "currency": c["currency"],
        "qty": qty,
        "why": why,
        "turn": turn,
    }
    claims(world).append(claim)
    args = {
        "contract": c["id"],
        "payer": _name(world, payer),
        "payee": _name(world, payee),
        "amount": amount,
        "currency": currency_label(world, c["currency"]),
        "reason": f"tender_why_{why}",
    }
    for i in (payer, payee):
        if i in world.islands:
            world.islands[i].log.append(LogEntry("tender_claim", args))


def record_short(
    world: WorldState,
    config: GameConfig,
    c: dict[str, Any],
    parts: dict[str, int],
    turn: int,
) -> None:
    """入札の契約の届かなかった量（わけごと）から、わけのある国への罰金を残す。封鎖は、当事者どうしの
    戦争なら宣戦した国のわけ、第三国なら罰金なし。外貨の市場の厚みは誰のわけにもしない。"""
    from hakoniwa_core.diplomacy import war

    for why, qty in sorted(parts.items()):
        if why in SHORT_BY_SELLER:
            payer = c["seller"]
        elif why in SHORT_BY_BUYER:
            payer = c["buyer"]
        elif why == "blockade":
            w = war.open_war(world, c["seller"], c["buyer"])
            if w is None or not war.fighting(world, c["seller"], c["buyer"], turn):
                continue  # 第三国の封鎖（不可抗力）
            payer = w["attacker"]
        else:
            continue
        add_claim(world, config, c, payer, penalty_of(config, c, qty), qty, why, turn)


def record_embargo(world: WorldState, config: GameConfig, c: dict[str, Any], turn: int) -> None:
    """禁輸で届かなかった入札の契約: 禁輸を出した国のわけ（両方が出していれば、どちらからも
    取らない）。"""
    from hakoniwa_core.trade import policy

    seller, buyer = world.islands.get(c["seller"]), world.islands.get(c["buyer"])
    if seller is None or buyer is None:
        return
    by_seller = policy.embargoes(seller, buyer.id, c["good"])
    by_buyer = policy.embargoes(buyer, seller.id, c["good"])
    if by_seller == by_buyer:
        return
    payer = seller.id if by_seller else buyer.id
    add_claim(world, config, c, payer, penalty_of(config, c, c["qty"]), c["qty"], "embargo", turn)


def cancel_claim(
    world: WorldState, config: GameConfig, c: dict[str, Any], by: int, turn: int
) -> None:
    """やめる命令で抜けた国 by への罰金（1 ターンの代金 × trade.contract_cancel_turns × 罰金の
    割合）。相手が by に未払いの罰金を残しているときは取らない（相手のわけで抜けた）。"""
    payee = c["buyer"] if by == c["seller"] else c["seller"]
    if any(d["payer"] == payee and d["payee"] == by and d["late"] for d in debts(world)):
        return
    turns = config.get("trade.contract_cancel_turns")
    amount = penalty_of(config, c, c["qty"] * turns)
    add_claim(world, config, c, by, amount, 0, "cancel", turn)


# --- 取る・許す・やめる -------------------------------------------------------------------


def pending_for(world: WorldState, payee: int, contract_id: int) -> list[dict[str, Any]]:
    return [c for c in claims(world) if c["payee"] == payee and c["contract"] == contract_id]


def _collect(world: WorldState, claim: dict[str, Any], turn: int) -> None:
    """罰金を取ると決める: わけのある国の通貨に今のレートで換えた払いにする（次のターンに払う）。"""
    from hakoniwa_core.diplomacy import is_inactive

    payer = world.islands.get(claim["payer"])
    payee = world.islands.get(claim["payee"])
    if payer is None or payee is None or is_inactive(payer):
        return  # 休止中の国からは取らない（条約の違反と同じ）
    amount = claim["amount"]
    if claim["currency"] != payer.id:
        fxrates.ensure(world, payer, claim["currency"])
        amount = fxrates.to_own(payer, claim["currency"], amount)
    if amount <= 0:
        return
    debts(world).append(
        {
            "id": next_id(world_state(world), "tender_debt"),
            "contract": claim["contract"],
            "payer": payer.id,
            "payee": payee.id,
            "amount": amount,
            "due": turn + 1,
            "late": False,
            "turn": turn,
        }
    )
    args = {
        "contract": claim["contract"],
        "payer": payer.name,
        "payee": payee.name,
        "amount": amount,
        "currency": currency_label(world, payer.id),
    }
    payer.log.append(LogEntry("tender_penalty_charged", args))
    payee.log.append(LogEntry("tender_penalty_charged", args))


def settle(
    world: WorldState, island: IslandState, contract: dict[str, Any], choice: str, turn: int
) -> str | None:
    """損をした国が、その契約のまだ決めていない罰金をまとめて取る・許す・やめるから選ぶ。"""
    if not contract.get("tender"):
        return "reason_tender_contract"
    pending = pending_for(world, island.id, contract["id"])
    if not pending:
        return "reason_tender_no_claim"
    for claim in pending:
        if choice in (COLLECT, TERMINATE):
            _collect(world, claim, turn)
    ids = {c["id"] for c in pending}
    world_state(world)["tender_claims"] = [c for c in claims(world) if c["id"] not in ids]
    other = contract["buyer"] if island.id == contract["seller"] else contract["seller"]
    args = {
        "contract": contract["id"],
        "country": _name(world, other),
        "reason": f"tender_choice_{choice}",
    }
    island.log.append(LogEntry("tender_settled", args))
    if other in world.islands:
        told = {**args, "country": island.name}
        world.islands[other].log.append(LogEntry("tender_settled", told))
    if choice == TERMINATE:
        from hakoniwa_core.trade import contracts

        if contract["state"] == contracts.ACTIVE and (
            contract["end"] is None or contract["end"] > turn + 1
        ):
            contract["end"] = turn + 1  # このターンの履行の後に終わる
            contract["terminated_by"] = island.id
    return None


def lapse_claims(world: WorldState, turn: int) -> None:
    """決めないまま CLAIM_TURNS ターンたった罰金は取る（遊べない間に権利が消えないように）。"""
    old = [c for c in claims(world) if turn - c["turn"] >= CLAIM_TURNS]
    if not old:
        return
    for claim in old:
        _collect(world, claim, turn)
    ids = {c["id"] for c in old}
    world_state(world)["tender_claims"] = [c for c in claims(world) if c["id"] not in ids]


def due_of(world: WorldState, island_id: int, turn: int) -> int:
    """島が turn までに払う罰金（自国の通貨）。予算の段が契約の項目に取り置く。"""
    return sum(d["amount"] for d in debts(world) if d["payer"] == island_id and d["due"] <= turn)
