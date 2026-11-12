"""持ち主のページの上に出す、命令の列の帯（UI-74）。"""

from __future__ import annotations

from typing import Any

from django import template

from server.game import get_game
from server.web import display
from server.world import models as m

register = template.Library()


def bar_text(costs: list[int], admin_power: int) -> str:
    """帯の文（static/js/island/orders.js の queueSummary と同じ数え方と文）。先頭の命令は、1 ターン
    の行政力を超えてもそのターンの行政力をすべて使って実行される。"""
    used = late = 0
    for cost in costs:
        used += min(cost, admin_power) if used == 0 else cost
        if used > admin_power:
            late += 1
    rest = f"・{late} 件は次のターン以降に回ります" if late else ""
    return f"次のターンの命令 {len(costs)} 件（行政力 {sum(costs)}／{admin_power}）{rest}"


@register.simple_tag
def orders_bar(island: m.Island) -> dict[str, Any]:
    """帯に出す値: 待っている命令の数・要る行政力と、次のターンの行政力。"""
    game = get_game()
    _, stats = display.latest_stats(island)
    admin_power = stats.get("admin_power")
    if admin_power is None:
        admin_power = game.config.get("orders.base_admin_power")
    costs = [
        game.defs.orders[kind]["admin_cost"] if kind in game.defs.orders else 0
        for kind in island.orders.filter(status="pending")
        .order_by("seq", "id")
        .values_list("kind", flat=True)
    ]
    return {"count": len(costs), "text": bar_text(costs, int(admin_power))}
