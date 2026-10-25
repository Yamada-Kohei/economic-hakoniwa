"""外交の段（diplomacy。フェーズ4-1。世界の段）と、外交の命令。

宣戦布告・条約（提案・署名・断り・取り下げ・予告つきの破棄）の命令は、命令の段で島の申し送りに積み、
この段が島の番号の順に決めます（貿易と同じ形）。民主主義の島の宣戦・署名・破棄は、命令の段の議会の
採決を通ったものだけが申し送りに積まれます。そのあと、流れた提案・予告のターンがたった破棄・講和の
賠償の期日を見張ります（hakoniwa_core/diplomacy/treaties.py）。

置く場所は、命令の段の後・国庫の取り置きの前です。戦争の状態は、4-2 以降の軍の段（貿易の段の前）が
同じターンのうちに読みます。

国際会議場（公開の声明）は、サーバーの表で持ちます（server/hall）。条約と戦争の出来事は、当事国の
ログに `hall` の印を付けて書き、サーバーがターンの後に国際会議場へ自動で載せます。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core.defs import Defs
from hakoniwa_core.diplomacy import is_inactive, war, world_state
from hakoniwa_core.diplomacy import treaties as tr
from hakoniwa_core.orders import defer, fail_deferred, take_deferred
from hakoniwa_core.registry import OrderFailed, OrderHandler, Registry, WorldStage
from hakoniwa_core.state import IslandState, LogEntry, WorldState
from hakoniwa_core.turn.context import StageContext

REQUESTS = "diplomacy_requests"


def request(island: IslandState, kind: str, payload: dict[str, Any]) -> None:
    """命令の段から、外交の段への申し送りを積む。"""
    defer(island, REQUESTS, kind, payload)


class Diplomacy(WorldStage):
    name = "diplomacy"

    def collect(self, island: IslandState, ctx: StageContext) -> Any:
        return None

    def resolve(self, requests: dict[int, Any], ctx: StageContext) -> dict[int, Any]:
        assert ctx.before is not None
        world = ctx.before
        world_state(world)
        for i in world.island_ids():
            island = world.islands[i]
            for req, order_id in take_deferred(island, REQUESTS):
                reason = _handle(world, ctx, island, req)
                if reason is not None:
                    fail_deferred(island, order_id, reason)
                    island.log.append(
                        LogEntry(
                            "diplomacy_request_failed", {"order": req["kind"], "reason": reason}
                        )
                    )
        tr.upkeep(world, ctx.config, ctx.turn)
        return {}

    def apply(self, island: IslandState, result: Any, ctx: StageContext) -> None:
        return None


def _handle(
    world: WorldState, ctx: StageContext, island: IslandState, req: dict[str, Any]
) -> str | None:
    kind, turn, config = req["kind"], ctx.turn, ctx.config
    if kind == "declare_war":
        return _declare(world, ctx, island, req)
    if kind == "propose_treaty":
        return tr.propose(world, config, island, req, turn)
    treaty = tr.find(world, req.get("treaty", -1))
    if kind == "sign_treaty":
        return tr.sign(world, config, island, treaty, turn)
    if kind == "refuse_treaty":
        return tr.refuse(world, island, treaty, turn)
    if kind == "withdraw_treaty":
        return tr.withdraw(world, island, treaty, turn)
    if kind == "denounce_treaty":
        return tr.denounce(world, config, island, treaty, turn)
    return "reason_unknown_order"


def _declare(
    world: WorldState, ctx: StageContext, island: IslandState, req: dict[str, Any]
) -> str | None:
    """宣戦布告。戦争は次のターンから始まる。結んでいる不可侵・同盟を破るなら違反として記録する。"""
    target = req["target"]
    other = world.islands.get(target)
    if other is None or target == island.id:
        return "reason_war_target"
    if is_inactive(other):
        return "reason_war_inactive"  # 休止中の島に宣戦はできない（停戦と講和は結べる。W-96）
    if war.open_war(world, island.id, target) is not None:
        return "reason_war_exists"
    w = war.declare(world, island.id, target, ctx.turn)
    tr.on_war_declared(world, ctx.config, island.id, target, ctx.turn)
    args: dict[str, Any] = {"turn": w["start"], "war": w["id"]}
    reason = (req.get("reason") or "").strip()[:200]
    island.log.append(
        LogEntry(
            "war_declared",
            {
                **args,
                "country": other.name,
                "hall": True,
                "a": island.name,
                "b": other.name,
                "text": reason,
            },
        )
    )
    other.log.append(LogEntry("war_declared_on", {**args, "country": island.name}))
    return None


# --- 命令 -------------------------------------------------------------------------------------


def check_declare_war(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if payload.get("target") == island.id:
        raise OrderFailed("reason_war_target")


def check_propose(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if payload.get("partner") == island.id:
        raise OrderFailed("reason_treaty_partner")
    for clause in payload.get("clauses", ()):
        if clause.get("type") == "contract":
            good = clause.get("good")
            if good not in defs.goods or "freight_weight" not in defs.goods[good]:
                raise OrderFailed("reason_not_tradable")


def _forward(kind: str):
    def execute(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
        request(island, kind, dict(payload))

    return execute


ORDERS = {
    "declare_war": check_declare_war,
    "propose_treaty": check_propose,
    "sign_treaty": None,
    "refuse_treaty": None,
    "withdraw_treaty": None,
    "denounce_treaty": None,
}


def register(registry: Registry) -> None:
    registry.add_stage(Diplomacy())
    for kind, checker in ORDERS.items():
        registry.add_order(OrderHandler(kind, execute=_forward(kind), check=checker))
