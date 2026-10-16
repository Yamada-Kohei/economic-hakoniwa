"""政治と計画の画面（フェーズ3-4）と、その JSON API の中身の組み立て。

ルールの計算はせず、政情の段と市場の段が島に残した状態（Island.ext["politics"]・ext["plan"]。
説明は hakoniwa_core/politics.py と hakoniwa_core/economy/plan.py）と、体制の特色（regimes.yaml の
traits）を読みます。

見せる範囲（設計「フェーズ3の画面」）:
- 体制・政情とその段階・支持率・議席・選挙・混乱期間・報道と集会の扱いは、誰にでも見せる（国全体の
  数）。政情の内訳・出来事・採決の履歴・たまった不満・警察・動員・命令の選択肢は島の持ち主だけ。
- 計画経済の公定価格・生産目標と達成率・重点産業・財ごとの行列と、混合経済の値段の上限と下限は島の
  持ち主だけ。ほかの人には国全体の行列と強制貯蓄だけ。
"""

from __future__ import annotations

from typing import Any

from hakoniwa_core import politics
from hakoniwa_core.economy import plan as planning
from hakoniwa_core.modules import migration
from hakoniwa_core.modules import politics as politics_orders
from hakoniwa_core.registry import OrderFailed
from hakoniwa_core.state import IslandState
from hakoniwa_core.units import from_milli
from server.game import get_game
from server.web import display
from server.world import models as m
from server.world.repository import island_from_row

# 政情の目標の内訳（hakoniwa_core/modules/politics の式 stability）の表示名
PART_NAMES = {
    "base": "基準",
    "happiness": "幸福度",
    "unemployment": "失業率",
    "inflation": "物価上昇率",
    "inequality": "格差",
    "regime": "体制",
    "events": "出来事",
    "press": "報道と集会の統制",
}
# 出来事の補正の表示名
EVENT_NAMES = {
    "default": "債務不履行",
    "transition": "体制を変えた後の混乱期間",
    "office": "首都の政府の建物が壊れている",
    "trapped": "出国を止められて出られない人",
}
# 政情の元にした数の表示名と単位（display.stat_text の単位）
FACT_NAMES = {
    "happiness": ("幸福度", ""),
    "unemployment": ("失業率", "percent"),
    "inflation": ("物価上昇率（年率）", "percent"),
    "inequality": ("格差（ジニ係数）", "fx"),
}
PRESS_NAMES = {mode: f"press_{mode}" for mode in politics.PRESS}
RULE_NAMES = {"majority": "過半数", "two_thirds": "3分の2"}
AXES = ("politics", "head_of_state", "economy")


def _state(island: m.Island) -> IslandState:
    return island_from_row(island)


def _allowed(check: Any, core: IslandState, payload: dict[str, Any]) -> bool:
    """命令の確かめ（コアの check）が通るか。入力欄を出すかどうかに使う。"""
    try:
        check(core, payload, get_game().defs)
    except OrderFailed:
        return False
    return True


def page_label(island: m.Island) -> str:
    """島の画面の見出しのリンクに出す、計画の画面の名前（計画経済は「計画」、混合経済は「値段の
    規制」、ほかの体制は空で、リンクを出さない）。"""
    found = politics.traits(_state(island), get_game().defs)
    if found.get("official_prices"):
        return "計画"
    if found.get("price_controls"):
        return "値段の規制"
    return ""


def politics_data(island: m.Island, mine: bool) -> dict[str, Any]:
    """政治の画面と GET /api/islands/<id>/politics の中身。"""
    game = get_game()
    defs, config = game.defs, game.config
    core = _state(island)
    found = politics.traits(core, defs)
    state = (island.ext or {}).get(politics.KEY, {})
    level = state.get("level", politics.CALM)
    parliament = bool(found.get("parliament"))
    transition = state.get("transition")
    press = state.get("press", politics.FREE)
    data: dict[str, Any] = {
        "regime": [
            {
                "axis": axis,
                "axis_name": display.message(f"axis_{axis}"),
                "id": core.regime[axis],
                "name": defs.name("regimes", core.regime[axis]),
            }
            for axis in AXES
            if core.regime.get(axis) in defs.regimes
        ],
        "leader": (island.names or {}).get("leader", ""),
        "started": "stability" in state,
        "stability": state.get("stability"),
        "target": state.get("target"),
        "level": level,
        "level_name": display.message(f"politics_level_{level}"),
        "levels": config.get("politics.levels"),
        "support": state.get("support"),
        "parliament": parliament,
        "seats": round(state["seats"] * 100, 1) if parliament and "seats" in state else None,
        "next_election": state.get("election") if parliament else None,
        "elections": state.get("elections", []) if parliament else [],
        "transition": (
            {"since": transition["since"], "until": transition["until"]} if transition else None
        ),
        "productivity": state.get("productivity"),
        "press": press,
        "press_name": display.message(PRESS_NAMES.get(press, "press_free")),
        "building": politics.capital_building(core, defs),
        "rebuilding": bool(state.get("rebuild")),
        "monarch_age": state.get("monarch_age") if found.get("monarch") else None,
        "rules": _rules(config, found),
    }
    if not mine:
        return data
    parts = state.get("parts", {})
    facts = state.get("facts", {})
    events = state.get("events", {})
    data.update(
        parts=[
            {"key": k, "name": PART_NAMES.get(k, k), "value": v}
            for k, v in parts.items()
            if k in ("base", "regime") or v
        ],
        facts=[
            {"key": k, "name": name, "text": display.stat_text(k, facts[k], unit)}
            for k, (name, unit) in FACT_NAMES.items()
            if k in facts
        ],
        events=[{"key": k, "name": EVENT_NAMES.get(k, k), "value": v} for k, v in events.items()],
        votes=[
            {
                **v,
                "order_name": defs.name("orders", v.get("order", "")),
                "rule_name": RULE_NAMES.get(v.get("rule", ""), ""),
                "yes": round(v.get("yes", 0) * 100, 1),
            }
            for v in state.get("votes", [])
        ],
        grievance=state.get("grievance", 0.0),
        police=round(state.get("police", 0.0) * 100),
        mobilized=bool(state.get("mobilized")),
        rebuild=[
            {
                "good": g,
                "name": defs.name("goods", g),
                "quantity": display.amount(q),
                "unit": defs.unit_name(g),
            }
            for g, q in (state.get("rebuild") or {}).items()
        ],
        can_press=_allowed(politics_orders.check_press, core, {"mode": politics.CONTROLLED}),
        can_mobilize=_allowed(politics_orders.check_mobilize, core, {"on": True}),
        can_rename=_allowed(politics_orders.check_rename_leader, core, {"name": "x"}),
        choices={
            axis: [
                {"id": r, "name": defs.name("regimes", r)}
                for r, spec in defs.regimes.items()
                if spec.get("axis") == axis and r != core.regime.get(axis)
            ]
            for axis in AXES
        },
        presses=[{"id": p, "name": display.message(PRESS_NAMES[p])} for p in politics.PRESS],
        migration=_migration(core, defs, config),
        parliament_rules=[
            {"name": defs.name("orders", kind), "rule": RULE_NAMES.get(spec["parliament"], "")}
            for kind, spec in defs.orders.items()
            if parliament and spec.get("parliament")
        ],
    )
    return data


def _migration(core: IslandState, defs: Any, config: Any) -> dict[str, Any]:
    """移民の受け入れと出国の扱い（バランス調整の回3。hakoniwa_core/modules/migration）。出国の
    選択肢は、政治の仕組みが許す強さまで。"""
    immigration, emigration = migration.immigration(core), migration.emigration(core)
    limit = migration.EMIGRATION.index(migration.exit_limit(core, defs))
    default_cap = config.get("migration.default_cap")
    return {
        "immigration": immigration,
        "immigration_name": display.message(f"immigration_{immigration}"),
        "cap": f"{(core.policy.immigration_cap or default_cap) / 100:g}",
        "default_cap": f"{default_cap / 100:g}",
        "emigration": emigration,
        "emigration_name": display.message(f"emigration_{emigration}"),
        "quota": f"{core.policy.emigration_quota / 100:g}",
        "immigrations": [
            {"id": mode, "name": display.message(f"immigration_{mode}")}
            for mode in migration.IMMIGRATION
        ],
        "emigrations": [
            {"id": mode, "name": display.message(f"emigration_{mode}")}
            for mode in migration.EMIGRATION[: limit + 1]
        ],
        "trapped": config.get("migration.trapped"),
        "trapped_max": config.get("migration.trapped_max"),
    }


def _rules(config: Any, found: dict[str, Any]) -> dict[str, Any]:
    """画面の説明に出す決まりの数（game.yaml の politics と、体制の特色）。"""
    weights = config.get("politics.weights")
    press = config.get("politics.press.bonus")
    riot = config.get("politics.riot")
    return {
        "happiness_mid": weights["happiness_mid"],
        "unemployment_free": weights["unemployment_free"],
        "inflation_free": weights["inflation_free"],
        "inequality_free": weights["inequality_free"],
        "press_limited": press.get(politics.LIMITED, 0),
        "press_controlled": press.get(politics.CONTROLLED, 0),
        "transition_turns": config.get("politics.transition.turns"),
        "transition_drop": round(config.get("politics.transition.productivity") * 100),
        "transition_event": -config.get("politics.events")["transition"],
        "police_per_1000": round(riot["police_per_person"] * 1000, 1),
        "police_effect": round(riot["police_effect"] * 100),
        "mobilization_share": round(config.get("politics.mobilization.share") * 100),
        "mobilization_wage": round(config.get("politics.mobilization.wage") * 100),
        "happiness_cap": found.get("happiness_cap"),
        "election_turns": config.get("politics.election_turns"),
        "swing": config.get("politics.parliament.swing"),
    }


def plan_data(island: m.Island, mine: bool, stats: dict[str, float]) -> dict[str, Any]:
    """計画の画面と GET /api/islands/<id>/plan の中身。stats は直近のターンの統計。"""
    game = get_game()
    defs, config = game.defs, game.config
    core = _state(island)
    found = politics.traits(core, defs)
    planned = bool(found.get("official_prices"))
    data: dict[str, Any] = {
        "planned": planned,
        "price_controls": bool(found.get("price_controls")),
        "queue": stats.get("plan.queue") if planned else None,
        "forced_saving": stats.get("plan.forced_saving") if planned else None,
    }
    if not mine:
        return data
    policy = island.policy or {}
    data["caps"] = _price_limits(policy.get("price_cap", {}), stats)
    data["floors"] = _price_limits(policy.get("price_floor", {}), stats)
    data["goods"] = [
        {"id": g, "name": defs.name("goods", g), "unit": defs.unit_name(g)} for g in defs.goods
    ]
    if not planned:
        return data
    state = (island.ext or {}).get(planning.KEY, {})
    prices = state.get("prices", {})
    manual = state.get("manual", {})
    costs = state.get("costs", {})
    goals = state.get("goals", {})
    achieved = state.get("achieved", {})
    queues = state.get("queues", {})
    band, held = planning.rules(core, config)
    rows = []
    for good in defs.goods:
        if good not in prices:
            continue
        cost = costs.get(good)
        rows.append(
            {
                "good": good,
                "name": defs.name("goods", good),
                "unit": defs.unit_name(good),
                "price": prices[good],
                "manual": good in manual,
                "cost": cost,
                # 公定価格と原価の差（原価に対する %。+ は原価より高い）
                "gap": round((prices[good] - cost) * 100 / cost) if cost else None,
                "outside": bool(cost) and planning.outside(prices[good], cost, band),
                "held": good in held,
                "goal": round(from_milli(goals[good]), 1) if good in goals else None,
                "made": round(stats.get(f"production.{good}", 0), 1),
                "achieved": round(achieved[good] * 100) if good in achieved else None,
                "queue": round(queues.get(good, 0) * 100),
            }
        )
    priority = state.get("priority", [])
    data.update(
        rows=rows,
        review=state.get("review"),
        band=round(band * 100),
        held=[{"id": g, "name": defs.name("goods", g)} for g in defs.goods if g in held],
        priority=[{"id": i, "name": defs.name("industries", i)} for i in priority],
        industries=[{"id": i, "name": defs.name("industries", i)} for i in defs.industries()],
        max_priority=config.get("planning.max_priority"),
    )
    return data


def _price_limits(limits: dict[str, int], stats: dict[str, float]) -> list[dict[str, Any]]:
    defs = get_game().defs
    return [
        {
            "good": g,
            "name": defs.name("goods", g),
            "unit": defs.unit_name(g),
            "price": p,
            "market": stats.get(f"price.{g}"),
        }
        for g, p in sorted(limits.items())
        if p and g in defs.goods
    ]
