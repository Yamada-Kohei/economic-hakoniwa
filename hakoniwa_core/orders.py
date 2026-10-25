"""命令の検証。受付時（画面から）と実行時（ターン処理）の両方でここを通します。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from hakoniwa_core.defs import Defs
from hakoniwa_core.defs.freeze import thaw
from hakoniwa_core.registry import OrderFailed, Registry
from hakoniwa_core.state import ORDER_FAILED, IslandState


@dataclass(frozen=True)
class OrderCheck:
    ok: bool
    reason: str | None = None  # messages の名前（reason_...）
    args: dict[str, Any] = field(default_factory=dict)
    details: tuple[str, ...] = ()  # 形の誤りの詳細（画面に出す）


def check_order(
    defs: Defs, registry: Registry, island: IslandState, kind: str, payload: Any
) -> OrderCheck:
    if kind not in defs.orders or kind not in registry.orders:
        return OrderCheck(False, "reason_unknown_order")
    schema = thaw(defs.orders[kind]["payload"])
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    if errors:
        details = tuple(
            f"{'/'.join(map(str, e.absolute_path)) or '(全体)'}: {e.message}" for e in errors
        )
        return OrderCheck(False, "reason_invalid_payload", details=details)
    handler = registry.orders[kind]
    if handler.check is not None:
        try:
            handler.check(island, payload, defs)
        except OrderFailed as e:
            return OrderCheck(False, e.reason, e.args_)
    return OrderCheck(True)


# --- 後の段へ申し送る命令（外交・部隊・貿易） ---------------------------------------------------

# island.scratch: 計画の実行の段が、いま実行している命令の番号
CURRENT = "current_order"


def defer(island: IslandState, key: str, kind: str, payload: dict[str, Any]) -> None:
    """命令の段から、後の世界の段への申し送り（island.scratch[key]）を積む。

    後の段で断られたとき命令を失敗に直せるよう、どの命令からかを同じ並びで覚えておく
    （断られても「実行済み」のままだと、遊ぶ人は命令が効いたと読んでしまう。W-185）。
    """
    island.scratch.setdefault(key, []).append({"kind": kind, **payload})
    island.scratch.setdefault(f"{key}.orders", []).append(island.scratch.get(CURRENT))


def take_deferred(island: IslandState, key: str) -> list[tuple[dict[str, Any], int | None]]:
    """積まれた申し送りと、積んだ命令の番号（命令の段の外から積まれたものは None）を取り出す。"""
    requests = island.scratch.pop(key, [])
    orders = island.scratch.pop(f"{key}.orders", [])
    return [(r, orders[n] if n < len(orders) else None) for n, r in enumerate(requests)]


def fail_deferred(
    island: IslandState, order_id: int | None, reason: str, args: dict[str, Any] | None = None
) -> None:
    """後の段で断られた命令を、実行済みから失敗に直す（理由つき。ログの「実行しました」も外す）。"""
    order = next((o for o in island.orders if order_id is not None and o.id == order_id), None)
    if order is None:
        return
    order.status = ORDER_FAILED
    order.reason = reason
    order.reason_args = dict(args or {})
    for n in range(len(island.log) - 1, -1, -1):
        entry = island.log[n]
        if entry.kind == "order_done" and entry.args.get("order") == order.kind:
            del island.log[n]
            break
