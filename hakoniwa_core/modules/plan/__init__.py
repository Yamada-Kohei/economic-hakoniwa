"""計画経済と値段の規制の命令（フェーズ3-4）。

- set_official_price: 公定価格の上書き（計画経済だけ）。0 で上書きをやめ、決まりどおりの見直しに
  戻す。
- review_official_prices: 公定価格の改定（計画経済だけ。政治の決断なので行政力が重く、議会のある
  体制では採決が要る）。名指しした財（空なら上書きしていない財すべて）を今の原価に改める。
- set_official_price_rules: 公定価格の決まり（計画経済だけ）。見直す幅（%）と、原価を下回っても
  据え置いて補助金で埋める財。
- set_priority_industries: 重点産業（産業の分類。planning.max_priority まで。計画経済だけ）。
- set_price_floor: 値段の下限（混合経済だけ。農産物の支えなど）。0 で下限をなくす。上限は今の
  set_price_cap（混合経済はどの財にも、ほかの体制は電気料金だけ。modules/market）。

公定価格・割当・生産目標・配給の中身は hakoniwa_core/economy/plan.py にあり、市場の段と生産の段が
使います。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import politics
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.registry import OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import IslandState, LogEntry
from hakoniwa_core.turn.context import StageContext


def _check_planned(island: IslandState, defs: Defs) -> None:
    if not planning.is_planned(island, defs):
        raise OrderFailed("reason_not_planned")


def check_official_price(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_planned(island, defs)
    if payload["good"] not in defs.goods:
        raise OrderFailed("reason_unknown_good")


def set_official_price(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """公定価格を決める（その財は見直しで変えない）。0 で上書きをやめ、次の確かめから決まり
    どおりに見直す（原価との差が幅を超えたら改める）。"""
    state = planning.state_of(island)
    good, price = payload["good"], payload["price"]
    manual = state.setdefault("manual", {})
    if price > 0:
        manual[good] = price
        state.setdefault("prices", {})[good] = price
        island.log.append(LogEntry("official_price_set", {"good": good, "price": price}))
    else:
        manual.pop(good, None)
        island.log.append(LogEntry("official_price_auto", {"good": good}))


def check_goods(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    """公定価格の改定と決まり: 計画経済で、名指しした財がみな定義にあること。"""
    _check_planned(island, defs)
    for good in [*payload.get("goods", []), *payload.get("held", [])]:
        if good not in defs.goods:
            raise OrderFailed("reason_unknown_good")


def review_official_prices(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """公定価格の改定: 名指しした財（空なら上書きしていない財すべて）を今の原価に改める。"""
    goods = list(payload.get("goods", []))
    changed = planning.revise(island, ctx.defs, ctx.config, goods)
    planning.apply_prices(island)
    island.log.append(LogEntry("official_prices_reformed", {"goods": changed}))


def set_rules(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """公定価格の決まり: 見直す幅（%）と据え置く財。書いたものだけ変わる。"""
    state = planning.state_of(island)
    if "band" in payload:
        state["band"] = payload["band"] / 100
    if "held" in payload:
        state["held"] = [g for g in sorted(ctx.defs.goods) if g in payload["held"]]
    band, held = planning.rules(island, ctx.config)
    args = {"band": round(band * 100), "goods": held}
    island.log.append(LogEntry("official_price_rules_set", args))


def check_priority(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    _check_planned(island, defs)
    known = set(defs.industries())
    for name in payload["industries"]:
        if name not in known:
            raise OrderFailed("reason_unknown_industry", industry=name)


def set_priority(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    limit = ctx.config.get("planning.max_priority")
    chosen = list(payload["industries"])
    if len(chosen) > limit:
        raise OrderFailed("reason_too_many_priority", limit=limit)
    planning.state_of(island)["priority"] = chosen
    island.log.append(LogEntry("priority_industries_set", {"industries": chosen}))


def check_price_floor(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    if not politics.trait(island, defs, "price_controls", False):
        raise OrderFailed("reason_price_controls_forbidden")
    if payload["good"] not in defs.goods:
        raise OrderFailed("reason_unknown_good")


def set_price_floor(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    good, price = payload["good"], payload["price"]
    if price > 0:
        island.policy.price_floor[good] = price
        island.log.append(LogEntry("price_floor_set", {"good": good, "price": price}))
    else:
        island.policy.price_floor.pop(good, None)
        island.log.append(LogEntry("price_floor_cleared", {"good": good}))


def register(registry: Registry) -> None:
    registry.add_order(
        OrderHandler("set_official_price", execute=set_official_price, check=check_official_price)
    )
    registry.add_order(
        OrderHandler("review_official_prices", execute=review_official_prices, check=check_goods)
    )
    registry.add_order(
        OrderHandler("set_official_price_rules", execute=set_rules, check=check_goods)
    )
    registry.add_order(
        OrderHandler("set_priority_industries", execute=set_priority, check=check_priority)
    )
    registry.add_order(
        OrderHandler("set_price_floor", execute=set_price_floor, check=check_price_floor)
    )
