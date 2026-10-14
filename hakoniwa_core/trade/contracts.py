"""二国間契約（フェーズ3-1）。

一方の政府が命令で「相手・財・1ターンの量・値段・通貨（どちらかの国の通貨）・期間」を示し、相手が
命令で受けると、次のターンから効きます。売り手の国は国の倉庫から出し（足りなければ島の市場で優先して
買う）、買い手の国は国庫から契約の通貨で払い、届いた物は国の倉庫に入ります。外貨が要るときは、政府
が為替の市場で買います。物かお金が足りないターンは払える分だけ履行し、不足を両方のログと契約の
記録に残します（罰はない）。どちらからでも命令でやめられ、やめるのは
trade.contract_cancel_turns ターン後です。

契約は ``world.ext["trade"]["contracts"]`` に辞書で持ちます: id・seller・buyer（島）・proposer・
good・qty（1ターンの量。1000 分の 1 単位）・price（1 単位の値段。契約の通貨）・currency・
turns（期間）・state（proposed・active・ended）・start・end（このターンの前まで効く）・
delivered・paid・short（直近の不足の量）。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.trade import fx as fxrates
from hakoniwa_core.trade import next_id, world_state
from hakoniwa_core.units import QUANTITY_SCALE, from_milli

PROPOSED = "proposed"
ACTIVE = "active"
ENDED = "ended"
KEEP_ENDED = 12  # 終わった契約を記録に残すターン数


def contracts(world: WorldState) -> list[dict[str, Any]]:
    return world_state(world)["contracts"]


def find(world: WorldState, contract_id: int) -> dict[str, Any] | None:
    for c in contracts(world):
        if c["id"] == contract_id:
            return c
    return None


def active(world: WorldState, turn: int) -> list[dict[str, Any]]:
    """このターンに履行する契約（番号の順）。"""
    return [c for c in contracts(world) if c["state"] == ACTIVE and c["start"] <= turn < c["end"]]


def of_island(world: WorldState, island_id: int) -> list[dict[str, Any]]:
    return [c for c in contracts(world) if island_id in (c["seller"], c["buyer"])]


def propose(
    world: WorldState, island: IslandState, payload: dict[str, Any], turn: int
) -> str | None:
    partner = payload["partner"]
    if partner == island.id or partner not in world.islands:
        return "reason_contract_partner"
    currency = payload.get("currency", island.id)
    if currency not in (island.id, partner):
        return "reason_contract_currency"
    seller, buyer = (island.id, partner) if payload["side"] == "sell" else (partner, island.id)
    contract = {
        "id": next_id(world_state(world), "contract"),
        "seller": seller,
        "buyer": buyer,
        "proposer": island.id,
        "good": payload["good"],
        "qty": round(payload["quantity"] * QUANTITY_SCALE),
        "price": payload["price"],
        "currency": currency,
        "turns": payload["turns"],
        "state": PROPOSED,
        "created": turn,
        "start": 0,
        "end": 0,
        "delivered": 0,
        "paid": 0,
        "short": 0,
    }
    contracts(world).append(contract)
    args = _args(world, contract)
    island.log.append(LogEntry("contract_proposed", args))
    world.islands[partner].log.append(LogEntry("contract_offer", args))
    return None


def accept(
    world: WorldState, island: IslandState, contract: dict[str, Any], turn: int
) -> str | None:
    if contract["state"] != PROPOSED:
        return "reason_contract_state"
    if (
        island.id not in (contract["seller"], contract["buyer"])
        or island.id == contract["proposer"]
    ):
        return "reason_contract_party"
    contract["state"] = ACTIVE
    contract["start"] = turn + 1
    contract["end"] = turn + 1 + contract["turns"]
    for side in ("seller", "buyer"):
        other = world.islands[contract[side]]
        if other.id != contract["currency"]:
            fxrates.ensure(world, other, contract["currency"])
        other.log.append(LogEntry("contract_started", _args(world, contract)))
    return None


def cancel(
    world: WorldState, island: IslandState, contract: dict[str, Any], turn: int, wait: int
) -> str | None:
    if island.id not in (contract["seller"], contract["buyer"]):
        return "reason_contract_party"
    if contract["state"] == PROPOSED:
        contract["state"] = ENDED
        contract["end"] = turn
    elif contract["state"] == ACTIVE:
        contract["end"] = min(contract["end"], turn + 1 + wait)
    else:
        return "reason_contract_state"
    for side in ("seller", "buyer"):
        world.islands[contract[side]].log.append(
            LogEntry("contract_cancelled", {**_args(world, contract), "turns": wait})
        )
    return None


def close(world: WorldState, turn: int) -> None:
    """期間の終わった契約を終わりにし、終わってしばらくたった契約を消す。"""
    keep = []
    for c in contracts(world):
        if c["state"] == ACTIVE and turn + 1 >= c["end"]:
            c["state"] = ENDED
            for side in ("seller", "buyer"):
                island = world.islands.get(c[side])
                if island is not None:
                    island.log.append(LogEntry("contract_ended", _args(world, c)))
        if c["state"] == PROPOSED and turn - c["created"] >= KEEP_ENDED:
            c["state"] = ENDED
            c["end"] = turn
        if c["state"] == ENDED and turn - c["end"] >= KEEP_ENDED:
            continue
        keep.append(c)
    world_state(world)["contracts"] = keep


def _args(world: WorldState, c: dict[str, Any]) -> dict[str, Any]:
    def name(island_id: int) -> str:
        island = world.islands.get(island_id)
        return island.name if island is not None else str(island_id)

    def currency(island_id: int) -> str:
        island = world.islands.get(island_id)
        return island.names.get("currency", island.name) if island is not None else str(island_id)

    return {
        "contract": c["id"],
        "seller": name(c["seller"]),
        "buyer": name(c["buyer"]),
        "good": c["good"],
        "amount": from_milli(c["qty"]),
        "price": c["price"],
        "currency": currency(c["currency"]),
    }


def short_args(world: WorldState, c: dict[str, Any], missing: int, why: str) -> dict[str, Any]:
    return {**_args(world, c), "missing": from_milli(missing), "reason": f"contract_short_{why}"}
