"""命令の検証。受付時（画面から）と実行時（ターン処理）の両方でここを通します。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from hakoniwa_core.defs import Defs
from hakoniwa_core.defs.freeze import thaw
from hakoniwa_core.registry import OrderFailed, Registry
from hakoniwa_core.state import IslandState


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
