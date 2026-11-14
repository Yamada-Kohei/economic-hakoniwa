"""品目のページ（UI-72・UI-73・W-247）: 1 つの品目の「見る」所と「打てる手」の箱に出す中身。

見る所は市場の表の 1 行（値段・値段の種類・売買の量・注文・生産・国の倉庫）と、産業の鎖の 1 行
（作る施設・使う施設・原料・輸出入・品不足の続き）、出ている補助金。打てる手の箱は持ち主だけで、
この品目に出ている命令（待っている列）と、前のターンに実行した命令の結果、国の倉庫の決まった売り
買いの設定と前のターンの結果。値段と量の推移・約定・買えずに止まった施設は市場の API から JS で
読む（js/reports/good.js）。計算は変えない（UI-81）。
"""

from __future__ import annotations

from typing import Any

from server.game import get_game
from server.web import chain, display, reports
from server.world import models as m

# 品目を対象に取る命令（payload の good でこの品目に出ている命令を探す）
GOOD_ORDERS = ("warehouse_trade", "set_warehouse_standing", "set_subsidy")


def good_view(
    island: m.Island,
    good: str,
    stats: dict[str, float],
    currency: str,
    mine: bool,
    viewer: int | None,
) -> dict[str, Any]:
    """品目のページの中身。市場の記録が無い品目でも名前と単位は出す（無い欄は出さない）。"""
    defs = get_game().defs
    row = next(
        (r for r in reports.market_table(island, stats, currency, mine) if r["good"] == good), None
    )
    links = chain.chain_view(island, stats, currency, mine, viewer)
    made = next((g for t in links["tiers"] for g in t["goods"] if g["good"] == good), None)
    view: dict[str, Any] = {
        "good": good,
        "name": defs.name("goods", good),
        "unit": defs.unit_name(good),
        "row": row,
        "chain": made,
        "subsidies": [
            display.subsidy_text(s)
            for s in (island.policy or {}).get("subsidies", [])
            if s.get("good") == good
        ],
    }
    if mine:
        view["orders"] = _orders_for(island, good)
    return view


def _orders_for(island: m.Island, good: str) -> dict[str, Any]:
    """この品目に出ている命令（待っている列）と、直近に実行したターンの結果（持ち主だけ）。"""
    pending = [
        display.describe_order(o.kind, o.payload)
        for o in island.orders.filter(status="pending", kind__in=GOOD_ORDERS).order_by("seq", "id")
        if (o.payload or {}).get("good") == good
    ]
    done = island.orders.exclude(status="pending").filter(kind__in=GOOD_ORDERS)
    last = done.order_by("-executed_turn").values_list("executed_turn", flat=True).first()
    recent = [
        {
            "text": display.describe_order(o.kind, o.payload),
            "status": o.status,
            "reason": display.message(o.reason, **(o.reason_args or {})) if o.reason else "",
        }
        for o in done.filter(executed_turn=last).order_by("seq")
        if (o.payload or {}).get("good") == good
    ]
    return {"pending": pending, "recent": recent, "recent_turn": last if recent else None}
