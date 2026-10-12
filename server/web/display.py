"""画面に出す文と数の組み立て。ルールの計算はせず、保存した値と定義データの表示名を使う。"""

from __future__ import annotations

from typing import Any

from django.db.models import Sum

from hakoniwa_core import facilities as fac
from hakoniwa_core.log import format_log
from hakoniwa_core.naming import facility_name
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.units import from_milli
from server.economy.models import MarketRecord
from server.game import get_game
from server.world import models as m
from server.world.repository import facility_from_row, island_from_row


def message(key: str, **args: Any) -> str:
    return format_log(get_game().defs, key, args)


def amount(milli: int) -> float:
    """1000 分の 1 単位の量を、表示用の数に（小数第1位まで）。"""
    return round(from_milli(milli), 1)


def island_for_naming(row: m.Island) -> IslandState:
    """自動の施設名を作るのに要るところだけを持った島の状態（マスは読まない）。"""
    return island_from_row(row)


def facility_info(row: m.Facility, island: IslandState) -> dict[str, Any]:
    game = get_game()
    defs, config = game.defs, game.config
    f: Facility = facility_from_row(row)
    fdef = defs.facilities.get(f.kind, {})
    info: dict[str, Any] = {
        "number": f.id,
        "kind": f.kind,
        "kind_name": defs.name("facilities", f.kind),
        "name": facility_name(defs, config, island, f) if fdef else f.kind,
        "custom_name": f.name,
        "owner": f.owner,
        "owner_name": message(f"owner_{f.owner}"),
        "accounting_name": message(f"accounting_{f.accounting}"),
        "built": f.built,
        "workers": f.workers,
        "operating_rate": f.operating_rate / fac.FULL,
        "priority": fac.priority_of(defs, config, f) if fdef else None,
        "priority_changed": f.priority is not None,
        "status": f.status,
        "status_text": status_text(f),
        "stock": [
            {
                "good": good,
                "name": defs.name("goods", good),
                "amount": amount(qty),
                "unit": defs.unit_name(good),
            }
            for good, qty in row.stocks.values("good")
            .annotate(qty=Sum("quantity"))
            .order_by("good")
            .values_list("good", "qty")
        ],
    }
    if not fdef:
        return info
    recipes = fdef["recipes"]
    if len(recipes) > 1:
        info["recipe"] = f.recipe
        info["recipe_name"] = defs.recipe_name(f.kind, f.recipe)
    if f.kind == fac.housing_kind(config):
        level = f.ext.get("level") or defs.settlement_levels()[0]
        info["level"] = level
        info["level_name"] = defs.name("settlements", level)
        info["capacity"] = defs.settlements[level]["capacity"]
        levels = defs.settlement_levels()
        if levels.index(level) + 1 < len(levels):
            following = levels[levels.index(level) + 1]
            info["next_level_name"] = defs.name("settlements", following)
            info["next_materials"] = goods_list(defs.settlements[following].get("materials", {}))
        return info
    if f.economy.get("wage"):
        info["wage"] = f.economy["wage"]
    if f.built:
        info["workers_needed"] = fdef["workers"]
        recipe = recipes.get(f.recipe, {})
        info["inputs"] = goods_list(recipe.get("inputs", {}))
        info["outputs"] = goods_list(recipe.get("outputs", {}))
    else:
        construction = fdef["construction"]
        info["workers_needed"] = construction.get("labor", 0)
        info["progress"] = f.progress / fac.FULL
        info["turns"] = construction["turns"]
        info["materials"] = goods_list(construction.get("materials", {}))
    if f.ext.get("halted_until") is not None:
        info["halted_until"] = f.ext["halted_until"]
    return info


def status_text(f: Facility) -> str:
    if not f.built:
        return message("status_construction")
    if not f.status:
        return ""
    kind, _, good = f.status.partition(":")
    text = message(f"status_{kind}")
    if good:
        text += f"（{get_game().defs.name('goods', good)}）"
    return text


def goods_list(amounts: dict[str, int]) -> list[dict[str, Any]]:
    defs = get_game().defs
    return [
        {"good": g, "name": defs.name("goods", g), "amount": amount(q), "unit": defs.unit_name(g)}
        for g, q in sorted(amounts.items())
    ]


def describe_order(kind: str, payload: dict[str, Any]) -> str:
    """命令の列に出す、命令の中身の短い説明。"""
    defs = get_game().defs
    name = defs.name("orders", kind)
    where = f"({payload['x']}, {payload['y']})" if "x" in payload and "y" in payload else ""
    if kind == "build":
        facility = defs.name("facilities", payload.get("facility", ""))
        recipe = payload.get("recipe")
        if recipe and payload.get("facility") in defs.facilities:
            facility += f"・{defs.recipe_name(payload['facility'], recipe)}"
        return f"{name}: {facility} {where}"
    if kind in ("build_power_line", "set_no_development"):
        count = len(payload.get("tiles", []))
        if kind == "set_no_development" and not payload.get("value", True):
            return f"{name}の解除: {count} マス"
        return f"{name}: {count} マス"
    if kind == "set_priority":
        return f"{name}: {where} → {payload.get('priority')}"
    if kind in ("rename_island", "rename_facility", "rename_place"):
        new = payload.get("name", "")
        return f"{name}: {where} 「{new}」" if where else f"{name}: 「{new}」"
    if kind in ("set_taxes", "set_labor_policy", "set_budget"):
        parts = []
        for k, v in payload.items():
            label = message(_POLICY_LABELS.get(k, f"budget_{k}"))
            parts.append(f"{label} {v:g}%" if k in _PERCENT else f"{label} {v:,}")
        return f"{name}: " + "・".join(parts)
    if kind == "warehouse_trade":
        good = payload.get("good", "")
        side = message("side_" + payload.get("side", ""))
        qty = f"{payload.get('quantity', 0):g} {defs.unit_name(good)}" if good in defs.goods else ""
        limit = f"（指値 {payload['limit']:,}）" if payload.get("limit") else ""
        return f"{name}: {defs.name('goods', good)}を{side} {qty}{limit}"
    if kind == "set_price_cap":
        good = payload.get("good", "")
        return f"{name}: {defs.name('goods', good)} {payload.get('price', 0):,}"
    return f"{name} {where}".strip()


_PERCENT = ("income_tax", "corporate_tax", "consumption_tax", "unemployment_benefit")
# 政策の命令の中身の項目の表示名（messages.yaml のキー）
_POLICY_LABELS = {
    "income_tax": "policy_income_tax",
    "corporate_tax": "policy_corporate_tax",
    "consumption_tax": "policy_consumption_tax",
    "minimum_wage": "policy_minimum_wage",
    "unemployment_benefit": "policy_unemployment_benefit",
}


def policy_view(island) -> dict[str, Any]:
    """島の今の政策（税率と失業手当は %、予算と最低賃金はお金）。命令の入力の初めの値に使う。"""
    policy = island.policy or {}
    budget_items = get_game().config.get("fiscal.budget_items")
    budget = policy.get("budget", {})
    return {
        "income_tax": policy.get("income_tax", 0) / 100,
        "corporate_tax": policy.get("corporate_tax", 0) / 100,
        "consumption_tax": policy.get("consumption_tax", 0) / 100,
        "minimum_wage": policy.get("minimum_wage", 0),
        "unemployment_benefit": policy.get("unemployment_benefit", 0) / 100,
        "budget": {
            item: {"name": message(f"budget_{item}"), "amount": budget.get(item, 0)}
            for item in budget_items
        },
        "price_cap": policy.get("price_cap", {}),
    }


def log_entries(logs) -> list[dict[str, Any]]:
    """ターンログの行を、文面とリンク先のマス付きで。"""
    defs = get_game().defs
    out = []
    for log in logs:
        args = log.args or {}
        entry = {"turn": log.turn, "kind": log.kind, "text": format_log(defs, log.kind, args)}
        if "x" in args and "y" in args:
            entry["tile"] = (args["x"], args["y"])
        out.append(entry)
    return out


# 島の画面に出す統計の名前と表示名（StatRecord の name）と単位。単位の good: は資源の単位
SUMMARY_STATS = [
    ("population", "人口", "人"),
    ("labor", "労働者", "人"),
    ("employed", "就業者", "人"),
    ("subsistence", "自給農業従事者", "人"),
    ("unemployment", "失業率", "%"),
    ("happiness", "幸福度", ""),
    ("food.ratio", "食料充足率", "%"),
    ("power.capacity", "発電量", "good:electricity"),
    ("power.demand", "電力需要", "good:electricity"),
    ("admin_power", "行政力（次ターンの命令実行数）", ""),
    ("facilities", "施設数", ""),
    ("construction", "建設中の施設", ""),
    ("gdp", "GDP（1ターン）", "money"),
    ("cpi", "物価指数（建国時 100）", "index"),
    ("inflation", "物価上昇率（年率）", "%"),
    ("wage.average", "平均賃金（1人1ターン）", "money"),
    ("treasury", "国庫", "money"),
    ("debt", "国債の残高", "money"),
    ("policy_rate", "政策金利", "rate"),
]


def latest_stats(island: m.Island) -> tuple[int | None, dict[str, float]]:
    last = island.stats.order_by("-turn").values_list("turn", flat=True).first()
    if last is None:
        return None, {}
    rows = island.stats.filter(turn=last).values_list("name", "value")
    return last, dict(rows)


def summary(stats: dict[str, float], currency: str = "") -> list[dict[str, Any]]:
    """国勢の表の行。currency はお金の額に付ける通貨記号。"""
    out = []
    for key, label, unit in SUMMARY_STATS:
        if key not in stats:
            continue
        value = stats[key]
        if unit.startswith("good:"):
            unit = " " + get_game().defs.unit_name(unit.removeprefix("good:"))
        if unit == "money":
            text = f"{currency}{int(value):,}"
        elif unit == "index":
            text = f"{value:.1f}"
        elif unit == "rate":  # 統計に % の数で入っている率
            text = f"{value:.2f}%"
        elif key == "inflation":
            text = f"{value * 100:+.1f}%"
        elif unit == "%":
            text = f"{value * 100:.0f}%"
        elif isinstance(value, float) and not value.is_integer():
            text = f"{value:,.1f}{unit}"
        else:
            text = f"{int(value):,}{unit}"
        out.append({"key": key, "label": label, "text": text})
    return out


def goods_table(
    stats: dict[str, float], markets: dict[str, Any] | None = None, currency: str = ""
) -> list[dict[str, Any]]:
    """資源ごとの国の倉庫の在庫と、このターンの生産量・市場の値段と売買の量。

    markets は財 → そのターンの市場の記録（server.economy.models.MarketRecord）。
    """
    defs = get_game().defs
    markets = markets or {}
    rows = []
    for good, gdef in defs.goods.items():
        stock = stats.get(f"stock.{good}", 0)
        made = stats.get(f"production.{good}", 0)
        record = markets.get(good)
        if stock or made or (record is not None and record.volume):
            rows.append(
                {
                    "good": good,
                    "name": defs.name("goods", good),
                    "unit": defs.unit_name(good),
                    "tier": gdef.get("tier"),
                    "stock": round(stock, 1),
                    "production": round(made, 1),
                    "price": f"{currency}{record.price:,}" if record is not None else "",
                    "volume": amount(record.volume) if record is not None else "",
                }
            )
    return rows


def latest_markets(island: m.Island, turn: int | None) -> dict[str, Any]:
    """ターン turn の財ごとの市場の記録。"""
    if turn is None:
        return {}
    return {r.good: r for r in MarketRecord.objects.filter(island=island, turn=turn)}
