"""計画経済と値段の規制の命令（フェーズ3-4）。

- set_official_price: 公定価格の上書き（計画経済だけ）。0 で上書きをやめ、決まりどおりの見直しに
  戻す。
- review_official_prices: 公定価格の改定（計画経済だけ。政治の決断なので行政力が重く、議会のある
  体制では採決が要る）。名指しした財（空なら上書きしていない財すべて）を今の原価に改める。
- set_official_price_rules: 公定価格の決まり（計画経済だけ）。見直す幅（%）と、原価を下回っても
  据え置いて補助金で埋める財。
- set_priority_industries: 重点産業（産業の分類。planning.max_priority まで。計画経済だけ）。
- set_staff_plan: 人員計画（計画経済だけ。PL-59）。国営施設ごとの人数の枠（上限）を、何施設分でも
  1つの命令で決める。枠を空（null）にするとその施設の枠を外す。マスのある施設は座標で、マスを
  持たない施設（商業・サービス業）は種類で指す。効き方は economy/plan.py の staff_limit と
  modules/employment。
- set_price_floor: 値段の下限（混合経済だけ。農産物の支えなど）。0 で下限をなくす。上限は今の
  set_price_cap（混合経済はどの財にも、ほかの体制は電気料金だけ。modules/market）。

公定価格・割当・生産目標・配給の中身は hakoniwa_core/economy/plan.py にあり、市場の段と生産の段が
使います。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core import politics
from hakoniwa_core.config import GameConfig
from hakoniwa_core.defs import Defs
from hakoniwa_core.economy import is_bank, is_transport, services
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.registry import OrderFailed, OrderHandler, Registry
from hakoniwa_core.state import OWNER_STATE, Facility, IslandState, LogEntry
from hakoniwa_core.trade import staff
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


# --- 人員計画（PL-59） ---------------------------------------------------------------


def staff_plannable(defs: Defs, config: GameConfig, f: Facility) -> bool:
    """人員の枠を決められる施設か: 建て終わった国営の、物を作る施設・公共サービス（役所・学校・警察
    など。働く人の数が行政力・教育・治安の効き目に出る）・商業・サービス業。人数を荷や預金で決める
    施設（運送業・港・海運業・商社・銀行）は外す（人数を絞っても荷が減らず、運賃の割り増しだけが
    残る）。軍の施設は働く人の数が効き目に出ないので外す。住宅と人のいらない施設も外す。"""
    if not f.built or f.owner != OWNER_STATE or fac.is_housing(config, f):
        return False
    if defs.facilities[f.kind].get("category") == "military":
        return False
    if is_transport(config, f) or is_bank(config, f) or staff.wanted(config, defs, f) is not None:
        return False
    return services.is_services(config, f) or fac.workers_of(defs, f) > 0


def staff_plan_target(island: IslandState, entry: dict[str, Any]) -> Facility | None:
    """人員計画の1行が指す施設（マスのある施設は座標、マスを持たない施設は種類）。"""
    if "x" in entry and "y" in entry:
        return island.facility_at(entry["x"], entry["y"])
    kind = entry.get("kind")
    return next((f for f in island.facilities if f.kind == kind and not fac.on_map(f)), None)


def check_staff_plan(
    island: IslandState, payload: dict[str, Any], defs: Defs, config: GameConfig
) -> None:
    _check_planned(island, defs)
    seen: set[int] = set()
    for entry in payload["plans"]:
        f = staff_plan_target(island, entry)
        if f is None:
            raise OrderFailed("reason_no_facility")
        if not staff_plannable(defs, config, f):
            raise OrderFailed(
                "reason_not_staff_plannable", facility=defs.name("facilities", f.kind)
            )
        if f.id in seen:
            raise OrderFailed(
                "reason_duplicate_staff_plan", facility=defs.name("facilities", f.kind)
            )
        seen.add(f.id)


def set_staff_plan(island: IslandState, payload: dict[str, Any], ctx: StageContext) -> None:
    """人員計画: 施設ごとの人数の枠を決める（null で外す）。次のターンの雇用の段から効く。"""
    count = 0
    for entry in payload["plans"]:
        f = staff_plan_target(island, entry)
        if f is None or not staff_plannable(ctx.defs, ctx.config, f):
            continue  # 命令を出した後に撤去・民営化された
        cap = entry.get("cap")
        if cap is None:
            f.ext.pop(planning.STAFF_CAP, None)
            f.ext.pop(planning.STAFF_CAPPED, None)
        else:
            f.ext[planning.STAFF_CAP] = int(cap)
        count += 1
    island.log.append(LogEntry("staff_plan_set", {"count": count}))


def check_price_floor(island: IslandState, payload: dict[str, Any], defs: Defs) -> None:
    good, price = payload["good"], payload["price"]
    if good not in defs.goods:
        raise OrderFailed("reason_unknown_good")
    # 下限をなくす（0）のは体制によらず通す（体制を変えた後に残った下限を消せるように）
    if price > 0 and not politics.trait(island, defs, "price_controls", False):
        raise OrderFailed("reason_price_controls_forbidden")
    # 上限より高い下限は出せない（両方あると下限が勝ち、上限を超えて売り買いされてしまう）
    cap = island.policy.price_cap.get(good)
    if price > 0 and cap and price > cap:
        raise OrderFailed("reason_floor_above_cap", good=good, price=price, cap=cap)


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
    registry.add_order(
        OrderHandler("set_staff_plan", execute=set_staff_plan, check_settings=check_staff_plan)
    )
