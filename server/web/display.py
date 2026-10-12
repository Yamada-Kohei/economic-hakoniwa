"""画面に出す文と数の組み立て。ルールの計算はせず、保存した値と定義データの表示名を使う。"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import facilities as fac
from hakoniwa_core.log import format_log
from hakoniwa_core.naming import facility_name
from hakoniwa_core.state import Facility, IslandState
from hakoniwa_core.units import from_milli
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
            {"good": s.good, "name": defs.name("goods", s.good), "amount": amount(s.quantity)}
            for s in row.stocks.order_by("good")
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
        return info
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
        {"good": g, "name": defs.name("goods", g), "amount": amount(q)}
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
    if kind in ("rename_island", "rename_facility"):
        new = payload.get("name", "")
        return f"{name}: {where} 「{new}」" if where else f"{name}: 「{new}」"
    return f"{name} {where}".strip()


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


# 島の画面に出す統計の名前と表示名（StatRecord の name）
SUMMARY_STATS = [
    ("population", "人口", "人"),
    ("labor", "働き手", "人"),
    ("employed", "雇われている人", "人"),
    ("subsistence", "自給の農業", "人"),
    ("unemployment", "失業率", "%"),
    ("happiness", "幸福度", ""),
    ("food.ratio", "食料の足りている割合", "%"),
    ("power.capacity", "発電できる量", ""),
    ("power.demand", "電力を使いたい量", ""),
    ("admin_power", "次のターンの行政力", ""),
    ("facilities", "施設の数", ""),
    ("construction", "工事中の施設", ""),
]


def latest_stats(island: m.Island) -> tuple[int | None, dict[str, float]]:
    last = island.stats.order_by("-turn").values_list("turn", flat=True).first()
    if last is None:
        return None, {}
    rows = island.stats.filter(turn=last).values_list("name", "value")
    return last, dict(rows)


def summary(stats: dict[str, float]) -> list[dict[str, Any]]:
    out = []
    for key, label, unit in SUMMARY_STATS:
        if key not in stats:
            continue
        value = stats[key]
        if unit == "%":
            text = f"{value * 100:.0f}%"
        elif isinstance(value, float) and not value.is_integer():
            text = f"{value:,.1f}{unit}"
        else:
            text = f"{int(value):,}{unit}"
        out.append({"key": key, "label": label, "text": text})
    return out


def goods_table(stats: dict[str, float]) -> list[dict[str, Any]]:
    """財ごとの在庫と、このターンの生産量。"""
    defs = get_game().defs
    rows = []
    for good, gdef in defs.goods.items():
        stock = stats.get(f"stock.{good}", 0)
        made = stats.get(f"production.{good}", 0)
        if stock or made:
            rows.append(
                {
                    "good": good,
                    "name": defs.name("goods", good),
                    "tier": gdef.get("tier"),
                    "stock": round(stock, 1),
                    "production": round(made, 1),
                }
            )
    return rows
