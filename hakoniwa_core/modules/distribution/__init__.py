"""配分の段（フェーズ1の市場の代わり）と、配分の優先順位の命令。

フェーズ1の島はお金を使わず、作った物はすべて島の共同倉庫に入ります。この段で倉庫の物を
「住民の食料 → 工事中の施設の資材 → 工場の投入財」の順に配ります。同じ順番の中では施設の
優先順位の高い所から配り、同じ優先順位の中で足りなければ欲しい量に比例して分けます。
工場が受け取った投入財は、次のターンの生産で使われます。

最後に、持ち越せる期間を過ぎた財を捨てます。フェーズ2でこの段を板寄せの市場に差し替えます。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import stock
from hakoniwa_core.allocation import by_priority
from hakoniwa_core.defs import Defs
from hakoniwa_core.registry import IslandStage, OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import Facility, IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext
from hakoniwa_core.units import from_milli


class Distribution(IslandStage):
    name = "distribution"

    def run_island(self, island: IslandState, ctx: StageContext) -> None:
        self._feed(island, ctx)
        sites = [f for f in island.facilities if not f.built]
        self._deliver(island, ctx, sites, lambda f: self._site_needs(ctx.defs, f))
        power = ctx.config.get("power.good")
        housing = fac.housing_kind(ctx.config)
        plants = [f for f in island.facilities if f.built and f.kind != housing]
        self._deliver(island, ctx, plants, lambda f: self._input_needs(ctx, f, power))
        for good, qty in stock.spoil(island.warehouse, ctx.turn, ctx.defs.goods).items():
            island.log.append(LogEntry("goods_spoiled", {"good": good, "amount": from_milli(qty)}))

    # --- 食料 ------------------------------------------------------------------

    def _feed(self, island: IslandState, ctx: StageContext) -> None:
        """住民の食べる分を倉庫から取り出す。質の高い物、傷みやすい物から先に食べる。"""
        food_need = ctx.formulas.get("food_need")
        need = food_need(island, ctx) if food_need else 0
        foods = sorted(
            (g for g, d in ctx.defs.goods.items() if "food" in d),
            key=lambda g: (
                -ctx.defs.goods[g]["food"].get("quality", 0),
                ctx.defs.goods[g].get("shelf_life", 10**9),
                g,
            ),
        )
        left = need
        quality = 0.0
        for good in foods:
            if left <= 0:
                break
            spec = ctx.defs.goods[good]["food"]
            nutrition = spec["nutrition"]
            want = -int(-left // nutrition)  # 切り上げ
            got = stock.take(island.warehouse, good, want)
            eaten = min(left, round(got * nutrition))
            left -= eaten
            quality += eaten * spec.get("quality", 0)
        supplied = need - left
        island.scratch["food"] = {
            "need": need,
            "supplied": supplied,
            "ratio": fac.ratio(supplied, need),
            "quality": quality / supplied if supplied else 0.0,
        }
        island.stats["food.need"] = from_milli(need)
        island.stats["food.supplied"] = from_milli(supplied)

    # --- 施設への配分 ------------------------------------------------------------

    @staticmethod
    def _site_needs(defs: Defs, f: Facility) -> dict[str, int]:
        """工事中の施設: 次の1ターン分の工事に要る資材のうち、まだ手元にない分。"""
        return {
            g: q - f.stock.get(g, 0)
            for g, q in fac.step_materials(defs, f).items()
            if q > f.stock.get(g, 0)
        }

    @staticmethod
    def _input_needs(ctx: StageContext, f: Facility, power: str) -> dict[str, int]:
        """動いている施設: 次のターンに、今の人手で動ける分の投入財のうち、まだ手元にない分。

        工事が済んだばかりの施設は、全力で動ける分を受け取る。
        """
        if f.status == fac.STATUS_STARTING:
            expected = fac.FULL
        elif fac.is_halted(f, ctx.turn + 1):
            expected = 0
        else:
            expected = fac.labor_ratio(ctx.defs, f)
        needs = {}
        for good, qty in fac.recipe_of(ctx.defs, f)["inputs"].items():
            if good == power:
                continue
            want = fac.scale(qty, expected) - f.stock.get(good, 0)
            if want > 0:
                needs[good] = want
        return needs

    @staticmethod
    def _deliver(
        island: IslandState, ctx: StageContext, facilities: list[Facility], needs_of: Any
    ) -> None:
        requests: dict[str, list[tuple[Facility, int, int]]] = {}
        for f in sorted(facilities, key=lambda f: f.id):
            priority = fac.priority_of(ctx.defs, ctx.config, f)
            for good, qty in sorted(needs_of(f).items()):
                requests.setdefault(good, []).append((f, qty, priority))
        for good in sorted(requests):
            available = stock.total(island.warehouse, good)
            if available <= 0:
                continue
            by_id = {f.id: f for f, _, _ in requests[good]}
            plan = by_priority(available, [(f.id, q, p) for f, q, p in requests[good]])
            for fid, qty in plan.items():
                got = stock.take(island.warehouse, good, qty)
                stock.stock_add(by_id[fid].stock, good, got)


def check_set_priority(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    f = island.facility_at(payload["x"], payload["y"])
    if f is None or defs.facilities[f.kind].get("buildable") is False:
        raise OrderFailed("reason_no_facility")


def set_priority(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    f = island.facility_at(payload["x"], payload["y"])
    assert f is not None
    f.priority = payload["priority"]
    island.log.append(
        LogEntry(
            "priority_changed",
            {"facility": f.kind, "x": f.x, "y": f.y, "priority": payload["priority"]},
        )
    )


def register(registry: Registry) -> None:
    registry.add_stage(Distribution())
    registry.add_order(OrderHandler("set_priority", execute=set_priority, check=check_set_priority))
