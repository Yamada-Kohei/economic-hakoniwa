"""品目ごとの国の倉庫の決まった売り買い（FS-28・FS-29）。"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import stock
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import plan
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import to_milli

KEY = "warehouse_standing"
PLACED = "warehouse_standing_placed"
PRIVATE_LOGS = frozenset(
    f"standing_{event}"
    for event in ("set", "changed", "stopped", "expired", "release_on", "release_off", "no_budget")
)


def settings(island: IslandState) -> dict[str, dict[str, Any]]:
    return island.economy.ext.get(KEY, {})


def protected(island: IslandState, good: str) -> int:
    """国営施設と国の工事が引き出せない備蓄の目安。"""
    item = settings(island).get(good, {})
    return item.get("quantity", 0) if item.get("kind") == "reserve" else 0


def kept_on_take_over(island: IslandState, good: str, held: int, qty: int) -> int:
    """閉じた施設から引き取る在庫 qty のうち、売りに出さずに倉庫に残す量（FS-30。報告 #9）。

    国が持つと決めた分だけ残す: 「買う」の設定なら全部、それ以外は、「備蓄」の目安と売りの
    二国間契約の 2 ターン分を合わせた量に、引き取る前の倉庫の量 held で足りない分。"""
    item = settings(island).get(good, {})
    if item.get("kind") == "buy":
        return qty
    from hakoniwa_core import trade

    contract = sum(
        c["qty"]
        for c in trade.island_state(island).get("contracts", [])
        if c.get("side") == "sell" and c.get("active") and c.get("good") == good
    )
    target = protected(island, good) + 2 * contract
    return max(0, min(qty, target - held))


def available(island: IslandState, good: str, want: int) -> int:
    return max(0, min(want, stock.total(island.warehouse, good) - protected(island, good)))


def expire(island: IslandState, turn: int) -> None:
    for good, item in list(settings(island).items()):
        if item.get("expires") is not None and item["expires"] < turn:
            del island.economy.ext[KEY][good]
            island.log.append(LogEntry("standing_expired", {"good": good}))


def check(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    good, kind = payload["good"], payload["kind"]
    if good not in defs.goods or defs.goods[good].get("shelf_life") == 0:
        raise OrderFailed("reason_unknown_good")
    if kind == "stop":
        return
    if payload.get("quantity", 0) <= 0:
        raise OrderFailed("reason_standing_quantity")
    if kind in ("buy", "reserve") and payload.get("limit", 0) <= 0:
        raise OrderFailed("reason_limit_required")
    if kind == "reserve" and payload.get("release", 0) <= payload.get("limit", 0):
        raise OrderFailed("reason_standing_release")
    from hakoniwa_core import politics

    if politics.trait(island, defs, "price_controls", False):
        cap, floor = island.policy.price_cap.get(good), island.policy.price_floor.get(good)
        ask = (
            payload.get("release", 0)
            if kind == "reserve"
            else payload.get("limit", 0)
            if kind == "sell"
            else 0
        )
        if cap and ask > cap:
            raise OrderFailed("reason_standing_cap")
        if floor and kind in ("buy", "reserve") and payload["limit"] < floor:
            raise OrderFailed("reason_standing_floor")


def check_settings(
    island: IslandState, payload: dict[str, Any], defs: Defs, config: GameConfig
) -> None:
    life = defs.goods[payload["good"]].get("shelf_life")
    if (
        payload["kind"] == "reserve"
        and life is not None
        and life < 2 * config.get("food_reserve.rotate")
    ):
        raise OrderFailed("reason_standing_life")


def execute(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    check(island, payload, ctx.defs)
    check_settings(island, payload, ctx.defs, ctx.config)
    expire(island, ctx.turn)
    state = island.economy.ext.setdefault(KEY, {})
    good, kind = payload["good"], payload["kind"]
    event = "changed" if good in state else "set"
    if kind == "stop":
        state.pop(good, None)
        event = "stopped"
    else:
        state[good] = {
            "kind": kind,
            "quantity": to_milli(payload["quantity"]),
            "limit": payload.get("limit", 0),
            "release": payload.get("release", 0),
            "expires": ctx.turn + payload["turns"] - 1 if "turns" in payload else None,
        }
    island.log.append(LogEntry(f"standing_{event}", {"good": good}))


def release(island: IslandState, ctx: StageContext, good: str, item: dict[str, Any]) -> bool:
    """放出するか。公定価格の財は前のターンの買えなかった量で判断する。"""
    if good in plan.official(island):
        return item.get("unmet", 0) > 0
    return island.economy.prices.get(good, 0) >= item["release"] * (
        1 - ctx.config.get("market.band")
    )


def note_release(island: IslandState, good: str, item: dict[str, Any], active: bool) -> None:
    if bool(item.get("releasing")) != active:
        island.log.append(
            LogEntry("standing_release_on" if active else "standing_release_off", {"good": good})
        )
    item["releasing"] = active


def rotating(island: IslandState, ctx: StageContext, good: str) -> int:
    life = ctx.defs.goods[good].get("shelf_life")
    if life is None:
        return 0
    return sum(
        q
        for made, q in island.warehouse.get(good, ())
        if ctx.turn - made >= life - ctx.config.get("food_reserve.rotate")
    )


def sellable(island: IslandState, ctx: StageContext, good: str) -> tuple[int, str]:
    from hakoniwa_core import autoplan

    held = stock.total(island.warehouse, good)
    military = autoplan._military_keep(island, ctx.config).get(good, 0)
    works = autoplan._reserved_materials(island, ctx.defs).get(good, 0)
    return max(0, held - military - works), "military" if military else "stock"
